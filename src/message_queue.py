import asyncio
import logging
from datetime import datetime
from typing import Optional, Callable
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, JSON
from src.database import Base

logger = logging.getLogger(__name__)


class IncomingMessageQueue(Base):
    """Table to persist incoming messages before processing"""
    __tablename__ = "incoming_message_queue"
    
    id = Column(Integer, primary_key=True)
    source_channel = Column(String(200), nullable=False)
    source_username = Column(String(200), nullable=False)
    source_url = Column(String(500), nullable=False)
    message_text = Column(Text, nullable=False)
    message_id = Column(Integer, nullable=False)
    message_date = Column(DateTime, nullable=False)
    raw_data = Column(JSON, nullable=False)  # Full message_data dict
    received_at = Column(DateTime, default=datetime.utcnow)
    processed = Column(Boolean, default=False)
    processed_at = Column(DateTime, nullable=True)
    processing_started_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)


class MessageQueueManager:
    """
    Manages incoming message queue with:
    - In-memory async queue for fast access
    - Database persistence for reliability
    - Separate worker for processing to never block message receipt
    """
    
    def __init__(self, db_manager, processor_callback: Callable, broadcast_callback: Optional[Callable] = None):
        self.db = db_manager
        self.processor = processor_callback
        self.broadcast = broadcast_callback
        self.queue: asyncio.Queue = asyncio.Queue()
        self.worker_task: Optional[asyncio.Task] = None
        self.is_running = False
        self.processed_urls = set()  # Track recently processed URLs
        self.max_url_cache = 1000  # Keep last 1000 URLs in memory
        self._stats = {
            'total_queued': 0,
            'total_processed': 0,
            'total_errors': 0
        }
    
    def start(self):
        """Start the queue worker"""
        if not self.is_running:
            self.is_running = True
            self.worker_task = asyncio.create_task(self._worker_loop())
            logger.info("MessageQueue worker started")
            
            # Load any unprocessed messages from DB on startup
            asyncio.create_task(self._load_unprocessed_from_db())
    
    async def stop(self):
        """Stop the queue worker gracefully"""
        self.is_running = False
        
        if self.worker_task and not self.worker_task.done():
            # Wait for current processing to finish
            try:
                await asyncio.wait_for(self.worker_task, timeout=30)
            except asyncio.TimeoutError:
                self.worker_task.cancel()
                logger.warning("Queue worker forced to stop")
        
        logger.info("MessageQueue worker stopped")
    
    async def enqueue(self, message_data: dict) -> bool:
        """
        Instantly queue a message (non-blocking, fast)
        """
        source_channel = message_data.get('source_channel', 'unknown')
        logger.info(f"[QUEUE] Enqueue called for message from {source_channel}")
        
        # Check if this message URL was already processed recently
        source_url = message_data.get('source_url', '')
        if source_url and self._is_duplicate(source_url):
            logger.info(f"[QUEUE] ⚠️ Duplicate message detected (already processed): {source_url}")
            if self.broadcast:
                await self.broadcast({"type": "log", "data": {"message": f"⚠️ Duplicate from {source_channel}", "level": "warning"}})
            return
        
        # Save to database first
        logger.info(f"[QUEUE] Saving to database...")
        db_id = self._save_to_db(message_data)
        logger.info(f"[QUEUE] Saved to DB with ID: {db_id}")
        
        # Add to in-memory queue
        await self.queue.put({'db_id': db_id, 'data': message_data})
        logger.info(f"[QUEUE] Added to in-memory queue")
        
        queue_size = self.queue.qsize()
        logger.info(f"[QUEUE] ✓ Message queued: {source_channel} (queue size: {queue_size})")
        if self.broadcast:
            await self.broadcast({"type": "log", "data": {"message": f"📥 Queued: {source_channel}", "level": "info"}})
        return True
            
    def _is_duplicate(self, source_url: str) -> bool:
        """Check if this URL was already processed"""
        if source_url in self.processed_urls:
            return True
        
        # Also check database for recent messages with same URL (last hour)
        from datetime import datetime, timedelta
        cutoff = datetime.now() - timedelta(hours=1)
        
        with self.db.get_session() as session:
            existing = session.query(IncomingMessageQueue).filter(
                IncomingMessageQueue.source_url == source_url,
                IncomingMessageQueue.received_at >= cutoff
            ).first()
            
            return existing is not None
    
    def _save_to_db(self, message_data: dict) -> int:
        """Save message to database, return ID"""
        with self.db.get_session() as session:
            # Convert datetime to string for JSON serialization
            raw_data_copy = message_data.copy()
            if 'date' in raw_data_copy and raw_data_copy['date']:
                raw_data_copy['date'] = raw_data_copy['date'].isoformat()
            
            queue_item = IncomingMessageQueue(
                source_channel=message_data.get('source_channel', 'Unknown'),
                source_username=message_data.get('source_username', ''),
                source_url=message_data.get('source_url', ''),
                message_text=message_data.get('message_text', ''),
                message_id=message_data.get('message_id', 0),
                message_date=message_data.get('date', datetime.utcnow()),
                raw_data=raw_data_copy,
                processed=False
            )
            session.add(queue_item)
            session.commit()
            return queue_item.id
    
    async def _load_unprocessed_from_db(self):
        """Load unprocessed messages from DB on startup"""
        try:
            with self.db.get_session() as session:
                unprocessed = session.query(IncomingMessageQueue).filter(
                    IncomingMessageQueue.processed == False
                ).order_by(IncomingMessageQueue.received_at).all()
                
                for item in unprocessed:
                    await self.queue.put({'db_id': item.id, 'data': item.raw_data})
                    logger.info(f"Reloaded unprocessed message from DB: ID {item.id}")
                
                if unprocessed:
                    logger.info(f"Loaded {len(unprocessed)} unprocessed messages from database")
        
        except Exception as e:
            logger.error(f"Error loading unprocessed messages: {e}", exc_info=True)
    
    async def _worker_loop(self):
        """
        Continuously process messages from queue
        This runs in a separate task and never blocks message receipt
        """
        logger.info("Queue worker loop started")
        
        while self.is_running:
            try:
                # Wait for next message (with timeout to check is_running)
                try:
                    item = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                
                db_id = item['db_id']
                message_data = item['data']
                
                # Mark as processing started
                self._mark_processing_started(db_id)
                
                # Process the message (this can take time, but doesn't block new messages)
                try:
                    source_ch = message_data['source_channel']
                    logger.info(f"[WORKER] Processing message from queue: {source_ch}")
                    logger.info(f"[WORKER] Calling processor function...")
                    
                    if self.broadcast:
                        await self.broadcast({"type": "log", "data": {"message": f"⚙️ Processing: {source_ch}", "level": "info"}})
                    
                    await self.processor(message_data)
                    
                    logger.info(f"[WORKER] Processor completed")
                    # Mark as processed in DB
                    self._mark_processed(db_id, success=True)
                    self._stats['total_processed'] += 1
                    
                    # Add URL to processed set to prevent duplicates
                    source_url = message_data.get('source_url', '')
                    if source_url:
                        self.processed_urls.add(source_url)
                        # Limit cache size
                        if len(self.processed_urls) > self.max_url_cache:
                            self.processed_urls.pop()
                    
                    logger.info(f"[WORKER] ✓ Message processed successfully (queue size: {self.queue.qsize()})")
                    if self.broadcast:
                        await self.broadcast({"type": "log", "data": {"message": f"✅ Processed: {source_ch}", "level": "success"}})
                
                except Exception as e:
                    logger.error(f"[WORKER] ✗ Error processing message: {e}", exc_info=True)
                    self._mark_processed(db_id, success=False, error=str(e))
                    self._stats['total_errors'] += 1
                
                finally:
                    self.queue.task_done()
            
            except Exception as e:
                logger.error(f"Error in worker loop: {e}", exc_info=True)
                await asyncio.sleep(1)
        
        logger.info("Queue worker loop ended")
    
    def _mark_processing_started(self, db_id: int):
        """Mark message as processing started"""
        try:
            with self.db.get_session() as session:
                item = session.query(IncomingMessageQueue).filter(IncomingMessageQueue.id == db_id).first()
                if item:
                    item.processing_started_at = datetime.utcnow()
                    session.commit()
        except Exception as e:
            logger.error(f"Error marking processing started: {e}")
    
    def _mark_processed(self, db_id: int, success: bool, error: str = None):
        """Mark message as processed in database"""
        try:
            with self.db.get_session() as session:
                item = session.query(IncomingMessageQueue).filter(IncomingMessageQueue.id == db_id).first()
                if item:
                    item.processed = True
                    item.processed_at = datetime.utcnow()
                    if not success:
                        item.error = error
                    session.commit()
        except Exception as e:
            logger.error(f"Error marking processed: {e}")
    
    def get_stats(self) -> dict:
        """Get queue statistics"""
        return {
            'queue_size': self.queue.qsize(),
            'is_running': self.is_running,
            'total_queued': self._stats['total_queued'],
            'total_processed': self._stats['total_processed'],
            'total_errors': self._stats['total_errors'],
            'pending': self._stats['total_queued'] - self._stats['total_processed']
        }
