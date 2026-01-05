import logging
import time
from datetime import datetime
from src.database import DatabaseManager
from src.gpt_service import GPTService
from src.telegram_service import TelegramService
from src.config import Config
import asyncio
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


class NewsProcessor:
    def __init__(self, config: Config, db: DatabaseManager, 
                 gpt: GPTService, telegram: TelegramService):
        self.config = config
        self.db = db
        self.gpt = gpt
        self.telegram = telegram
        self.executor = ThreadPoolExecutor(max_workers=3)
    
    def _is_similar_topic(self, headline: str, post_content: str) -> bool:
        """Check if headline is similar to existing post content"""
        import re
        
        # Persian stopwords to ignore
        stopwords = {'از', 'به', 'در', 'با', 'که', 'این', 'آن', 'را', 'و', 'یک', 'برای', 
                    'تا', 'یا', 'اما', 'اگر', 'چون', 'هم', 'می', 'است', 'شد', 'بود'}
        
        # Extract meaningful words (3+ chars, not stopwords)
        def extract_keywords(text):
            # Remove markdown, emojis, URLs
            text = re.sub(r'\*\*|\[|\]|\(http[^\)]+\)|@\w+|🔭|📡|[^\w\s]', ' ', text.lower())
            words = [w for w in re.findall(r'\w{3,}', text) if w not in stopwords]
            return set(words)
        
        headline_words = extract_keywords(headline)
        content_words = extract_keywords(post_content)
        
        if not headline_words:
            return False
        
        overlap = headline_words.intersection(content_words)
        similarity = len(overlap) / len(headline_words)
        
        logger.info(f"Similarity check: headline={headline[:50]}, overlap={len(overlap)}/{len(headline_words)} ({similarity:.2%})")
        
        # Lower threshold to 25% for better matching
        return similarity >= 0.25
    
    async def process_new_message(self, message_data: dict):
        logger.info(f"[PROCESSOR] process_new_message called")
        try:
            source_channel = message_data['source_channel']
            source_url = message_data['source_url']
            message_text = message_data['message_text']
            
            logger.info(f"[PROCESSOR] Processing message from {source_channel}")
            logger.info(f"[PROCESSOR] Message text length: {len(message_text)}")
            
            current_state = self.db.get_current_state()
            logger.info(f"[PROCESSOR] Got current state, length: {len(current_state)}")
            
            # Time the triage call - run in thread pool to avoid blocking
            logger.info(f"[PROCESSOR] Calling GPT triage...")
            triage_start = time.time()
            loop = asyncio.get_event_loop()
            triage_result = await loop.run_in_executor(
                self.executor,
                self.gpt.triage_message,
                message_text,
                source_channel,
                source_url,
                current_state
            )
            triage_time_ms = int((time.time() - triage_start) * 1000)
            logger.info(f"[PROCESSOR] Triage completed in {triage_time_ms}ms")
            
            if not triage_result:
                logger.error("Triage failed")
                return None
            
            bucket = triage_result.get('bucket', 'low')
            
            logger.info(f"Triage result: bucket={bucket}, time={triage_time_ms}ms")
            
            if bucket == 'high':
                await self._handle_high_importance(
                    message_data, triage_result, current_state, triage_time_ms
                )
            elif bucket == 'medium':
                await self._handle_medium_importance(
                    message_data, triage_result, triage_time_ms
                )
            else:
                await self._handle_low_importance(
                    message_data, triage_result, triage_time_ms
                )
            
            return {"bucket": bucket}
                
        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
            return None
    
    async def _handle_high_importance(self, message_data: dict, 
                                     triage_result: dict, current_state: str, triage_time_ms: int = 0):
        # Check last 10 high posts and ask GPT which one is related
        recent_posts = self.db.get_recent_high_posts(limit=5)
        similar_post = None
        
        if recent_posts:
            similar_post = await self._find_similar_post_with_gpt(message_data, triage_result, recent_posts, current_state)
        
        logger.info("Generating HIGH importance post")
        
        loop = asyncio.get_event_loop()
        post_content = await loop.run_in_executor(
            self.executor,
            self.gpt.generate_high_post,
            message_data['message_text'],
            message_data['source_channel'],
            message_data['source_url'],
            triage_result,
            current_state
        )
        
        if not post_content:
            logger.error("Failed to generate HIGH post")
            return
        
        if similar_post and similar_post.get('message_id'):
            # Edit existing message
            combined_content = f"{similar_post['content']}\n\n---\n\n{post_content}"
            combined_sources = similar_post.get('source_urls', []) + [message_data['source_url']]
            
            success = await self.telegram.edit_message(similar_post['message_id'], combined_content)
            if success:
                self.db.update_published_post(
                    post_id=similar_post['id'],
                    new_content=combined_content,
                    source_urls=combined_sources
                )
                logger.info(f"HIGH post edited: message_id={similar_post['message_id']}")
        else:
            # Post new message
            message_id = await self.telegram.post_to_channel(post_content)
            
            if message_id:
                self.db.log_published_post(
                    post_type='high',
                    content=post_content,
                    source_urls=[message_data['source_url']],
                    message_id=message_id
                )
                
                logger.info(f"HIGH post published: message_id={message_id}")
        
        # Note: Situation brief is only updated via Initialize button, not automatically
        
        self.db.log_message(
            source_channel=message_data['source_channel'],
            source_url=message_data['source_url'],
            message_text=message_data['message_text'],
            bucket='high',
            score=0,
            triage_json=triage_result,
            action='posted_high',
            triage_time_ms=triage_time_ms
        )
    
    async def _handle_medium_importance(self, message_data: dict, triage_result: dict, triage_time_ms: int = 0):
        logger.info("Queueing MEDIUM importance message")
        
        self.db.add_to_medium_queue(
            source_channel=message_data['source_channel'],
            source_url=message_data['source_url'],
            message_text=message_data['message_text'],
            triage_json=triage_result,
            triage_time_ms=triage_time_ms,
            score=0
        )
        
        self.db.log_message(
            source_channel=message_data['source_channel'],
            source_url=message_data['source_url'],
            message_text=message_data['message_text'],
            bucket='medium',
            score=0,
            triage_json=triage_result,
            action='queued_medium',
            triage_time_ms=triage_time_ms
        )
        
        logger.info("Message queued for hourly digest")
    
    async def _handle_low_importance(self, message_data: dict, triage_result: dict, triage_time_ms: int = 0):
        logger.info("Discarding LOW importance message")
        
        self.db.log_message(
            source_channel=message_data['source_channel'],
            source_url=message_data['source_url'],
            message_text=message_data['message_text'],
            bucket='low',
            score=0,
            triage_json=triage_result,
            action='discarded_low',
            triage_time_ms=triage_time_ms
        )
    
    async def _find_similar_post_with_gpt(self, message_data: dict, triage_result: dict, recent_posts: list, current_state: str):
        """Use GPT to find similar posts"""
        from src.news_processor_helpers import find_similar_post_with_gpt
        return await find_similar_post_with_gpt(self.gpt, message_data, triage_result, recent_posts, current_state)
    
    async def process_hourly_digest(self):
        logger.info("Processing hourly digest")
        
        try:
            pending_items = self.db.get_pending_medium_items()
            
            if not pending_items:
                logger.info("No MEDIUM items to digest")
                return
            
            max_items = self.config.get('rate_limits.max_queue_items', 50)
            items_to_process = pending_items[:max_items]
            
            logger.info(f"Processing {len(items_to_process)} MEDIUM items")
            
            from datetime import datetime, timedelta
            import pytz
            
            tz = pytz.timezone(self.config.timezone)
            now = datetime.now(tz)
            start_time = now.replace(minute=0, second=0, microsecond=0)
            end_time = start_time + timedelta(hours=1)
            
            current_state = self.db.get_current_state()
            
            loop = asyncio.get_event_loop()
            digest_content = await loop.run_in_executor(
                self.executor,
                self.gpt.generate_hourly_digest,
                items_to_process,
                current_state,
                start_time,
                end_time
            )
            
            if not digest_content:
                logger.error("Failed to generate hourly digest")
                return
            
            message_id = await self.telegram.post_to_channel(digest_content)
            
            if message_id:
                self.db.log_published_post(
                    post_type='hourly_digest',
                    content=digest_content,
                    source_urls=[item['source_url'] for item in items_to_process],
                    message_id=message_id
                )
                
                item_ids = [item['id'] for item in items_to_process]
                self.db.mark_medium_items_processed(item_ids)
                
                logger.info(f"Hourly digest published: message_id={message_id}")
                
                # Note: Situation brief is only updated via Initialize button, not automatically
                
        except Exception as e:
            logger.error(f"Error processing hourly digest: {e}", exc_info=True)
