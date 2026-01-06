import asyncio
import logging
from typing import Optional
from src.config import Config
from src.database import DatabaseManager
from src.gpt_service import GPTService
from src.telegram_service import TelegramService
from src.news_processor import NewsProcessor
from src.scheduler import DigestScheduler
from src.message_queue import MessageQueueManager

logger = logging.getLogger(__name__)


class AppManager:
    def __init__(self):
        self.config: Optional[Config] = None
        self.db: Optional[DatabaseManager] = None
        self.gpt: Optional[GPTService] = None
        self.telegram: Optional[TelegramService] = None
        self.processor: Optional[NewsProcessor] = None
        self.scheduler: Optional[DigestScheduler] = None
        self.message_queue: Optional[MessageQueueManager] = None
        self.is_running = False
        self._telegram_task: Optional[asyncio.Task] = None
    
    def initialize(self, config_path: str = "config.yaml"):
        logger.info("Initializing application components...")
        
        self.config = Config(config_path)
        
        import os
        db_path = os.getenv("DATABASE_PATH", "./data/news.db")
        self.db = DatabaseManager(db_path)
        
        self.gpt = GPTService(self.config)
        
        self.telegram = TelegramService(self.config)
        
        self.processor = NewsProcessor(
            config=self.config,
            db=self.db,
            gpt=self.gpt,
            telegram=self.telegram
        )
        
        # Initialize message queue with processor callback
        self.message_queue = MessageQueueManager(
            db_manager=self.db,
            processor_callback=self.processor.process_new_message
        )
        
        self.scheduler = DigestScheduler(
            config=self.config,
            processor=self.processor
        )
        
        logger.info("Application components initialized")
    
    async def start(self):
        if self.is_running:
            logger.warning("Application already running")
            return
        
        logger.info("Starting application...")
        
        await self.telegram.start()
        
        # Start message queue worker BEFORE setting up telegram listener
        # This ensures the queue is ready to receive messages
        self.message_queue.start()
        logger.info("Message queue worker started - no messages will be missed!")
        
        # Set telegram to enqueue messages instead of processing directly
        # This ensures telegram event handler NEVER blocks
        self.telegram.set_message_handler(self.message_queue.enqueue)
        
        source_channels = self.db.get_active_source_channels()
        if source_channels:
            await self.telegram.listen_to_sources(source_channels)
            logger.info(f"Listening to {len(source_channels)} source channels")
            
            # Auto-replay disabled - use web UI button to replay manually
            # logger.info("Replaying messages from past 10 minutes...")
            # await self._replay_recent_messages(source_channels)
        else:
            logger.warning("No source channels configured")
        
        self.scheduler.start()
        
        self.is_running = True
        logger.info("Application started successfully")
    
    async def stop(self):
        if not self.is_running:
            return
        
        self.is_running = False
        logger.info("Stopping application...")
        
        if self.scheduler:
            self.scheduler.stop()
        
        # Stop message queue worker (let it finish current processing)
        if self.message_queue:
            await self.message_queue.stop()
            logger.info("Message queue worker stopped")
        
        if self.telegram:
            await self.telegram.stop()
        
        if self._telegram_task and not self._telegram_task.done():
            self._telegram_task.cancel()
            try:
                await self._telegram_task
            except asyncio.CancelledError:
                pass
        
        self.is_running = False
        logger.info("Application stopped")
    
    async def get_all_telegram_channels(self) -> list:
        if not self.telegram.is_running:
            logger.warning("Telegram client not running - cannot fetch channels")
            raise Exception("Telegram not connected. Click START SYSTEM first.")
        try:
            channels = await self.telegram.get_all_subscribed_channels()
            logger.info(f"Retrieved {len(channels)} channels from Telegram")
            return channels
        except Exception as e:
            logger.error(f"Failed to get Telegram channels: {e}")
            raise
    
    async def add_source_channel(self, username: str):
        if not username.startswith('@'):
            username = f'@{username}'
        
        username_clean = username.lstrip('@')
        
        # Get channel info via Telegram - use same logic as get_all_subscribed_channels
        try:
            # Parse channel identifier - could be @username or @c/ID
            if username_clean.startswith('c/'):
                # Extract numeric ID from c/ID format
                channel_id = int(username_clean.split('/', 1)[1])
                logger.info(f"[ADD] Detected ID-based channel, using ID: {channel_id}")
                entity = await self.telegram.client.get_entity(channel_id)
            else:
                # Regular username
                entity = await self.telegram.client.get_entity(username)
            
            # Get username from entity (might be None for ID-based channels)
            entity_username = getattr(entity, 'username', None)
            
            # If username is None, fetch full entity for complete info
            if not entity_username:
                try:
                    from telethon import utils
                    full_entity = await self.telegram.client.get_entity(entity.id)
                    entity_username = getattr(full_entity, 'username', None)
                    entity = full_entity  # Use full entity for title
                    logger.info(f"[RESOLVE] Fetched full entity for channel, title: {getattr(entity, 'title', 'Unknown')}")
                except Exception as e:
                    logger.warning(f"[RESOLVE] Could not fetch full entity: {e}")
            
            # Get title (should now have proper title from full entity)
            title = getattr(entity, 'title', username_clean)
            members = 0
            
            # Try to get member count
            try:
                from telethon.tl.functions.channels import GetFullChannelRequest
                full_channel = await self.telegram.client(GetFullChannelRequest(entity))
                members = getattr(full_channel.full_chat, 'participants_count', 0)
                logger.info(f"Got member count for {username}: {members}")
            except Exception as e:
                logger.warning(f"Could not get member count for {username}: {e}")
        except Exception as e:
            logger.warning(f"Could not fetch channel info for {username}: {e}")
            title = username_clean
            members = 0
        
        self.db.add_source_channel(username_clean, title=title, participants_count=members)
        
        if self.is_running:
            source_channels = self.db.get_active_source_channels()
            await self.telegram.listen_to_sources(source_channels)
        
        logger.info(f"Added source channel: {username_clean} (title: {title}, members: {members})")
    
    async def remove_source_channel(self, username: str):
        # Normalize username - add @ if missing, then remove it for database
        if not username.startswith('@'):
            username = f'@{username}'
        
        username_clean = username.lstrip('@')
        
        self.db.remove_source_channel(username_clean)
        
        logger.info(f"Removed source channel: {username_clean}")
    
    def list_source_channels(self):
        return self.db.list_all_source_channels()
    
    def get_current_state(self):
        return self.db.get_current_state()
    
    def get_state(self):
        """Alias for get_current_state for consistency"""
        return self.get_current_state()
    
    def set_state(self, new_state: str):
        if self.db:
            self.db.update_state(new_state)
            logger.info(f"State updated ({len(new_state)} chars)")
            # Broadcast state update to web UI
            asyncio.create_task(self._broadcast_state_update(new_state))
    
    async def _broadcast_state_update(self, new_state: str):
        """Broadcast situation brief update to connected clients"""
        # Note: WebSocket broadcast is handled by web_ui instance, not accessible here
        # The state is stored in DB and will be fetched by frontend
        logger.info(f"State update ready for broadcast ({len(new_state)} chars)")
    
    async def initialize_situation_from_24h(self, broadcast_callback=None):
        """Initialize situation brief from past 24 hours of news across all sources"""
        from datetime import datetime, timedelta, timezone
        
        async def log_broadcast(message, level="info"):
            if broadcast_callback:
                await broadcast_callback({"type": "log", "data": {"message": message, "level": level}})
            logger.info(message)
        
        try:
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=24)
            all_messages = []
            source_channels = self.db.get_active_source_channels()
            
            await log_broadcast(f"📊 Gathering news from {len(source_channels)} sources over past 24 hours...")
            
            for channel in source_channels:
                try:
                    channel_username = channel if isinstance(channel, str) else channel.get('username', '')
                    if not channel_username:
                        continue
                    
                    await log_broadcast(f"📥 Fetching from @{channel_username}...")
                    messages = await self.telegram.get_recent_messages(channel_username, limit=1000)
                    
                    for msg in messages:
                        msg_date = msg.get('date')
                        if msg_date:
                            if msg_date.tzinfo is None:
                                msg_date = msg_date.replace(tzinfo=timezone.utc)
                            if msg_date > cutoff_time:
                                text = msg.get('text', '').strip()
                                if text:
                                    all_messages.append(f"[@{channel_username}] {text}")
                    
                    await log_broadcast(f"✓ Collected {len([m for m in messages if m.get('date') and (m.get('date').replace(tzinfo=timezone.utc) if m.get('date').tzinfo is None else m.get('date')) > cutoff_time])} messages from @{channel_username}")
                    
                except Exception as e:
                    await log_broadcast(f"⚠️ Failed to fetch from @{channel_username}: {str(e)}", "warning")
                    continue
            
            await log_broadcast(f"📝 Total messages collected: {len(all_messages)}")
            
            if not all_messages:
                await log_broadcast("⚠️ No messages found in past 24 hours", "warning")
                return "No news available from the past 24 hours."
            
            # Concatenate all messages (no limit)
            news_content = "\n\n".join(all_messages)
            
            await log_broadcast(f"🤖 Analyzing {len(all_messages)} messages with GPT ({self.gpt.config.content_model})...")
            
            # Get content style characteristics
            characteristics = "\n".join([f"- {char}" for char in self.config.get('content_style.core_characteristics', [])])
            
            # Create prompt for GPT to analyze and create situation brief
            system_prompt = f"""You are an intelligent news analyst for the Telegram channel "Hamid's Pulse".

Your task: Analyze the provided news messages from the past 24 hours and create a comprehensive situation brief.

Channel characteristics:
{characteristics}

Instructions:
1. Read and analyze ALL provided news messages
2. Identify the most important themes, trends, and developments
3. Consider current global context and recent web news related to these topics
4. Synthesize everything into a clear, coherent situation brief in Persian
5. Focus on what matters most to the audience based on the channel characteristics
6. Make it comprehensive and detailed (up to 10,000 words if needed)
7. Write in a natural, engaging Persian style

Return ONLY the situation brief text in Persian, no JSON, no extra formatting."""

            user_prompt = f"""Here are the news messages from the past 24 hours from our source channels:

{news_content}

Based on these messages and your knowledge of current events, create a comprehensive situation brief in Persian that captures the current state of important developments. Focus on themes and trends, not individual messages."""

            try:
                response = self.gpt.client.chat.completions.create(
                    model=self.gpt.config.content_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=1.0,
                    max_tokens=50000
                )
                
                new_brief = response.choices[0].message.content.strip()
                
                if new_brief:
                    # Update the state with the new brief
                    self.set_state(new_brief)
                    await log_broadcast(f"✅ Situation brief created ({len(new_brief)} characters)", "success")
                    return new_brief
                else:
                    await log_broadcast("⚠️ GPT returned empty response", "warning")
                    return "Failed to generate situation brief."
                    
            except Exception as e:
                await log_broadcast(f"❌ GPT analysis failed: {str(e)}", "error")
                logger.error(f"GPT situation analysis error: {e}", exc_info=True)
                raise
                
        except Exception as e:
            await log_broadcast(f"❌ Initialization failed: {str(e)}", "error")
            logger.error(f"Initialize situation error: {e}", exc_info=True)
            raise
    
    async def _replay_recent_messages(self, source_channels: list, minutes: int = 10, broadcast_callback=None):
        """Replay messages from the past N minutes to simulate streaming"""
        from datetime import datetime, timedelta, timezone
        import asyncio
        
        async def log_broadcast(message, level="info"):
            if broadcast_callback:
                await broadcast_callback({"type": "log", "data": {"message": message, "level": level}})
            logger.info(message)
        
        try:
            cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=minutes)
            
            # Step 1: Gather all messages from all channels first
            await log_broadcast(f"📥 Gathering messages from {len(source_channels)} channels...")
            all_messages = []
            
            for channel in source_channels:
                try:
                    channel_username = channel if isinstance(channel, str) else channel.get('username', '')
                    if not channel_username:
                        continue
                    
                    messages = await self.telegram.get_recent_messages(channel_username, limit=20)
                    
                    for msg in messages:
                        msg_date = msg.get('date')
                        if msg_date:
                            # Ensure msg_date is timezone-aware
                            if msg_date.tzinfo is None:
                                msg_date = msg_date.replace(tzinfo=timezone.utc)
                            if msg_date > cutoff_time:
                                message_data = {
                                    'source_channel': channel_username,
                                    'source_url': msg.get('url', ''),
                                    'message_text': msg.get('text', ''),
                                    'timestamp': msg_date
                                }
                                if message_data['message_text']:
                                    all_messages.append(message_data)
                
                except Exception as e:
                    logger.warning(f"Error fetching from {channel}: {e}")
                    continue
            
            # Step 2: Sort all messages by timestamp (chronological order)
            all_messages.sort(key=lambda x: x['timestamp'])
            
            await log_broadcast(f"🔄 Processing {len(all_messages)} messages in chronological order...")
            total_processed = 0
            
            # Step 3: Process messages in time order
            for message_data in all_messages:
                await asyncio.sleep(0.3)  # Simulate streaming
                
                # Process and get result to show category
                result = await self.processor.process_new_message(message_data)
                total_processed += 1
                
                # Show which category it went to
                msg_preview = message_data['message_text'][:50] + "..." if len(message_data['message_text']) > 50 else message_data['message_text']
                if result and 'bucket' in result:
                    category_emoji = {"high": "🔴", "medium": "🟡", "low": "⚪"}
                    emoji = category_emoji.get(result['bucket'], "📝")
                    await log_broadcast(f"{emoji} {result['bucket'].upper()} @{message_data['source_channel']}: {msg_preview}", "info")
                else:
                    await log_broadcast(f"⚠️ FAILED: {msg_preview}", "warning")
            
            await log_broadcast(f"✅ Replay complete! Processed {total_processed} messages", "success")
        except Exception as e:
            await log_broadcast(f"❌ Replay error: {str(e)}", "error")
    
    def get_status(self):
        status = {
            'is_running': self.is_running,
            'telegram_connected': self.telegram.is_running if self.telegram else False,
            'scheduler_running': self.scheduler.is_running if self.scheduler else False,
        }
        
        # Add message queue stats
        if self.message_queue:
            status['message_queue'] = self.message_queue.get_stats()
        
        # Add daily news statistics
        if self.db:
            status['daily_stats'] = self.db.get_daily_statistics()
            status['source_channels_count'] = len(self.db.get_active_source_channels())
        
        return status
    
    async def trigger_hourly_digest(self):
        if self.scheduler:
            await self.scheduler.trigger_digest_now()
    
    async def process_and_clear_queue(self):
        """Process 3-hour digest and clear queue - same as automatic behavior"""
        if self.scheduler:
            # Generate and publish digest from medium queue
            await self.scheduler.trigger_digest_now()
            # Queue is automatically cleared by process_hourly_digest after publishing
            logger.info("Manual digest processed and queue cleared")
        return True
    
    async def promote_message(self, message_id: int, current_bucket: str, broadcast_callback=None):
        """Promote a message to higher priority bucket"""
        async def log_broadcast(message, level="info"):
            if broadcast_callback:
                await broadcast_callback({"type": "log", "data": {"message": message, "level": level}})
            logger.info(message)
        
        try:
            # Get message from database
            if current_bucket == 'medium':
                # Medium -> High: Post to channel immediately
                message = self.db.get_message_from_medium_queue(message_id)
                if not message:
                    return {"success": False, "error": "Message not found in medium queue"}
                
                await log_broadcast(f"⬆️ Promoting to HIGH: {message['title'][:50]}...")
                
                # Generate post content and post to channel
                if self.gpt and self.telegram:
                    current_state = self.db.get_current_state()
                    
                    # Check for similar posts
                    recent_posts = self.db.get_recent_high_posts(limit=5)
                    similar_post = None
                    
                    # Filter out posts longer than 1600 characters from similarity check
                    if recent_posts:
                        recent_posts = [p for p in recent_posts if len(p.get('content', '')) <= 1600]
                    
                    if recent_posts:
                        from src.news_processor_helpers import find_similar_post_with_gpt
                        message_data = {
                            'message_text': message['text'],
                            'source_channel': message['source'],
                            'source_url': message['url']
                        }
                        similar_post = await find_similar_post_with_gpt(
                            self.gpt, message_data, message['triage_json'], recent_posts, current_state
                        )
                    
                    # Generate high priority post
                    from concurrent.futures import ThreadPoolExecutor
                    import asyncio
                    executor = ThreadPoolExecutor(max_workers=1)
                    loop = asyncio.get_event_loop()
                    
                    post_content = await loop.run_in_executor(
                        executor,
                        self.gpt.generate_high_post,
                        message['text'],
                        message['source'],
                        message['url'],
                        message['triage_json'],
                        current_state
                    )
                    
                    if post_content:
                        telegram_message_id = None
                        
                        if similar_post and similar_post.get('message_id'):
                            # Edit existing message
                            await log_broadcast(f"📝 Editing similar post instead of creating new one...")
                            combined_content = f"{similar_post['content']}\n\n---\n\n{post_content}"
                            combined_sources = similar_post.get('source_urls', []) + [message['url']]
                            
                            success = await self.telegram.edit_message(similar_post['message_id'], combined_content)
                            if success:
                                self.db.update_published_post(
                                    post_id=similar_post['id'],
                                    new_content=combined_content,
                                    source_urls=combined_sources
                                )
                                telegram_message_id = similar_post['message_id']
                                await log_broadcast(f"✅ Edited existing post (message_id={telegram_message_id})", "success")
                        else:
                            # Post new message
                            telegram_message_id = await self.telegram.post_to_channel(post_content)
                            
                            if telegram_message_id:
                                self.db.log_published_post(
                                    post_type='high',
                                    content=post_content,
                                    source_urls=[message['url']],
                                    message_id=telegram_message_id
                                )
                                await log_broadcast(f"✅ Posted new message to channel (message_id={telegram_message_id})", "success")
                        
                        if telegram_message_id:
                            # Remove from medium queue using the correct ID
                            self.db.remove_from_medium_queue(message['id'])
                            self.db.log_message(
                                message['source'],
                                message['url'],
                                message['text'],
                                'high',
                                message['score'],
                                message['triage_json'],
                                'promoted_and_posted',
                                message.get('triage_time', 0)
                            )
                            return {"success": True, "message": "Promoted to high and posted"}
                        else:
                            return {"success": False, "error": "Failed to post to channel"}
                    else:
                        return {"success": False, "error": "Failed to generate post content"}
                else:
                    return {"success": False, "error": "GPT or Telegram not available"}
            
            elif current_bucket == 'low':
                # Low -> Medium: Move to medium queue
                message = self.db.get_message_from_logs(message_id)
                if not message:
                    return {"success": False, "error": "Message not found in logs"}
                
                await log_broadcast(f"⬆️ Promoting to MEDIUM: {message['title'][:50]}...")
                
                # Add to medium queue
                self.db.add_to_medium_queue(
                    message['source'],
                    message['url'],
                    message['text'],
                    message['triage_json'],
                    message['score'],
                    message.get('triage_time', 0)
                )
                
                # Remove from logs
                self.db.delete_message_from_logs(message_id)
                
                await log_broadcast(f"✅ Promoted to medium queue", "success")
                return {"success": True, "message": "Promoted to medium"}
            
            else:
                return {"success": False, "error": f"Cannot promote from {current_bucket}"}
        
        except Exception as e:
            logger.error(f"Error promoting message: {e}")
            return {"success": False, "error": str(e)}
    
    async def demote_message(self, message_id: int, current_bucket: str, broadcast_callback=None):
        """Demote a message to lower priority bucket or remove it"""
        async def log_broadcast(message, level="info"):
            if broadcast_callback:
                await broadcast_callback({"type": "log", "data": {"message": message, "level": level}})
            logger.info(message)
        
        try:
            if current_bucket == 'high':
                # High -> Medium: Move to medium queue
                message = self.db.get_message_from_logs(message_id)
                if not message:
                    return {"success": False, "error": "Message not found in logs"}
                
                await log_broadcast(f"⬇️ Demoting to MEDIUM: {message['title'][:50]}...")
                
                # Add to medium queue
                self.db.add_to_medium_queue(
                    message['source'],
                    message['url'],
                    message['text'],
                    message['triage_json'],
                    message['score'],
                    message.get('triage_time', 0)
                )
                
                # Remove from high logs
                self.db.delete_message_from_logs(message_id)
                
                await log_broadcast(f"✅ Demoted to medium queue", "success")
                return {"success": True, "message": "Demoted to medium"}
            
            elif current_bucket == 'medium':
                # Medium -> Low: Remove from queue, log as low
                message = self.db.get_message_from_medium_queue(message_id)
                if not message:
                    return {"success": False, "error": "Message not found in medium queue"}
                
                await log_broadcast(f"⬇️ Demoting to LOW: {message['title'][:50]}...")
                
                # Remove from medium queue
                self.db.remove_from_medium_queue(message_id)
                
                # Log as low priority
                self.db.log_message(
                    message['source'],
                    message['url'],
                    message['text'],
                    'low',
                    message['score'],
                    message['triage_json'],
                    'demoted_to_low',
                    message.get('triage_time', 0)
                )
                
                await log_broadcast(f"✅ Demoted to low priority", "success")
                return {"success": True, "message": "Demoted to low"}
            
            elif current_bucket == 'low':
                # Low -> Remove: Delete from logs
                await log_broadcast(f"🗑️ Removing message from queue...")
                
                self.db.delete_message_from_logs(message_id)
                
                await log_broadcast(f"✅ Message removed", "success")
                return {"success": True, "message": "Message removed"}
            
            else:
                return {"success": False, "error": f"Cannot demote from {current_bucket}"}
        
        except Exception as e:
            logger.error(f"Error demoting message: {e}")
            return {"success": False, "error": str(e)}
