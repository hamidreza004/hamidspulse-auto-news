import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
from src.config import Config
from src.news_processor import NewsProcessor

logger = logging.getLogger(__name__)


class DigestScheduler:
    def __init__(self, config: Config, processor: NewsProcessor):
        self.config = config
        self.processor = processor
        self.scheduler = AsyncIOScheduler()
        self.is_running = False
    
    def start(self):
        if self.is_running:
            logger.warning("Scheduler already running")
            return
        
        timezone = pytz.timezone(self.config.timezone)
        schedule_minute = self.config.get('hourly_digest.schedule_minute', 0)
        
        self.scheduler.add_job(
            self.processor.process_hourly_digest,
            trigger=CronTrigger(minute=schedule_minute, timezone=timezone),
            id='hourly_digest',
            name='Hourly News Digest',
            replace_existing=True
        )
        
        self.scheduler.start()
        self.is_running = True
        logger.info(f"Scheduler started - hourly digest at minute {schedule_minute} ({timezone})")
    
    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown()
            self.is_running = False
            logger.info("Scheduler stopped")
    
    async def trigger_digest_now(self):
        logger.info("Manual trigger: hourly digest")
        await self.processor.process_hourly_digest()
