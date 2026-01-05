from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Float, Boolean, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
import os

Base = declarative_base()

# Import IncomingMessageQueue from message_queue module after Base is defined
# This is done at the end of the file to avoid circular imports


class NewsState(Base):
    __tablename__ = "news_state"
    
    id = Column(Integer, primary_key=True)
    situation_brief = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)


class MediumQueue(Base):
    __tablename__ = "medium_queue"
    
    id = Column(Integer, primary_key=True)
    source_channel = Column(String(200), nullable=False)
    source_url = Column(String(500), nullable=False)
    message_text = Column(Text, nullable=False)
    triage_json = Column(JSON, nullable=False)
    importance_score = Column(Float, nullable=False)
    triage_time_ms = Column(Integer, default=0)
    received_at = Column(DateTime, default=datetime.utcnow)
    processed = Column(Boolean, default=False)
    processed_at = Column(DateTime, nullable=True)


class MessageLog(Base):
    __tablename__ = "message_log"
    
    id = Column(Integer, primary_key=True)
    source_channel = Column(String(200), nullable=False)
    source_url = Column(String(500), nullable=False)
    message_text = Column(Text, nullable=False)
    importance_bucket = Column(String(20), nullable=False)
    importance_score = Column(Float, nullable=False)
    triage_json = Column(JSON, nullable=False)
    action_taken = Column(String(50), nullable=False)
    triage_time_ms = Column(Integer, default=0)
    received_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime, default=datetime.utcnow)


class CachedChannel(Base):
    __tablename__ = "cached_channels"
    
    id = Column(Integer, primary_key=True)
    channel_id = Column(String(100), unique=True, nullable=False)
    username = Column(String(200), nullable=False)
    title = Column(String(500), nullable=False)
    participants_count = Column(Integer, default=0)
    cached_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PublishedPost(Base):
    __tablename__ = "published_posts"
    
    id = Column(Integer, primary_key=True)
    post_type = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    source_urls = Column(JSON, nullable=True)
    published_at = Column(DateTime, default=datetime.utcnow)
    message_id = Column(Integer, nullable=True)


class RateLimitCounter(Base):
    __tablename__ = "rate_limit_counter"
    
    id = Column(Integer, primary_key=True)
    hour_window = Column(DateTime, nullable=False, unique=True)
    post_count = Column(Integer, default=0)


class SourceChannel(Base):
    __tablename__ = "source_channels"
    
    id = Column(Integer, primary_key=True)
    username = Column(String(200), nullable=False, unique=True)
    title = Column(String(300), nullable=True)
    participants_count = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    added_at = Column(DateTime, default=datetime.utcnow)
    last_message_at = Column(DateTime, nullable=True)


class DatabaseManager:
    def __init__(self, db_path: str = "./data/news.db"):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.engine = create_engine(f"sqlite:///{db_path}", echo=False)
        self.SessionLocal = sessionmaker(bind=self.engine)
        
        # Import IncomingMessageQueue here to register it with Base
        from src.message_queue import IncomingMessageQueue
        
        Base.metadata.create_all(self.engine)
        self._init_state()
    
    def _init_state(self):
        with self.get_session() as session:
            state = session.query(NewsState).first()
            if not state:
                state = NewsState(
                    situation_brief="هنوز خبری پوشش داده نشده. در حال رصد کانال‌های خبری..."
                )
                session.add(state)
                session.commit()
    
    @contextmanager
    def get_session(self) -> Session:
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    
    def get_current_state(self) -> str:
        with self.get_session() as session:
            state = session.query(NewsState).first()
            return state.situation_brief if state else ""
    
    def update_state(self, new_brief: str):
        with self.get_session() as session:
            state = session.query(NewsState).first()
            if state:
                state.situation_brief = new_brief
                state.updated_at = datetime.utcnow()
            else:
                state = NewsState(situation_brief=new_brief)
                session.add(state)
    
    def add_to_medium_queue(self, source_channel: str, source_url: str, 
                           message_text: str, triage_json: dict, score: float, triage_time_ms: int = 0):
        with self.get_session() as session:
            item = MediumQueue(
                source_channel=source_channel,
                source_url=source_url,
                message_text=message_text,
                triage_json=triage_json,
                importance_score=score,
                triage_time_ms=triage_time_ms
            )
            session.add(item)
    
    def get_pending_medium_items(self):
        with self.get_session() as session:
            items = session.query(MediumQueue).filter(
                MediumQueue.processed == False
            ).order_by(MediumQueue.received_at).all()
            return [{
                'id': item.id,
                'source_channel': item.source_channel,
                'source_url': item.source_url,
                'message_text': item.message_text,
                'triage_json': item.triage_json,
                'importance_score': item.importance_score,
                'received_at': item.received_at,
                'triage_time': getattr(item, 'triage_time_ms', 0)
            } for item in items]
    
    def get_medium_queue(self, limit: int = 50):
        with self.get_session() as session:
            items = session.query(MediumQueue).filter(
                MediumQueue.processed == False
            ).order_by(MediumQueue.received_at.desc()).limit(limit).all()
            return [{
                'id': item.id,
                'source': item.source_channel,
                'title': item.message_text[:100],
                'timestamp': item.received_at.isoformat() + 'Z' if item.received_at else None,
                'triage_time': getattr(item, 'triage_time_ms', 0),
                'score': item.importance_score
            } for item in items]
    
    def get_high_queue(self, limit: int = 50):
        """Get recent high priority messages from logs"""
        with self.get_session() as session:
            items = session.query(MessageLog).filter(
                MessageLog.importance_bucket == 'high'
            ).order_by(MessageLog.processed_at.desc()).limit(limit).all()
            
            return [{
                'id': item.id,
                'title': item.triage_json.get('headline') or item.message_text[:100] if item.message_text else 'No headline',
                'source': item.source_channel,
                'url': item.source_url,
                'timestamp': item.received_at.isoformat() + 'Z' if item.received_at else None,
                'score': item.importance_score,
                'triage_time': getattr(item, 'triage_time_ms', 0)
            } for item in items]
    
    def get_low_queue(self, limit: int = 50):
        """Get recent low priority messages from logs"""
        with self.get_session() as session:
            items = session.query(MessageLog).filter(
                MessageLog.importance_bucket == 'low'
            ).order_by(MessageLog.processed_at.desc()).limit(limit).all()
            
            return [{
                'id': item.id,
                'title': item.triage_json.get('headline') or item.message_text[:100] if item.message_text else 'No headline',
                'source': item.source_channel,
                'url': item.source_url,
                'timestamp': item.received_at.isoformat() + 'Z' if item.received_at else None,
                'score': item.importance_score,
                'triage_time': getattr(item, 'triage_time_ms', 0)
            } for item in items]
    
    def clear_high_queue(self):
        """Clear high priority messages from logs"""
        with self.get_session() as session:
            session.query(MessageLog).filter(
                MessageLog.importance_bucket == 'high'
            ).delete()
    
    def clear_low_queue(self):
        """Clear low priority messages from logs"""
        with self.get_session() as session:
            session.query(MessageLog).filter(
                MessageLog.importance_bucket == 'low'
            ).delete()
    
    def clear_medium_queue(self):
        """Clear medium priority messages"""
        with self.get_session() as session:
            session.query(MediumQueue).delete()
    
    def mark_medium_items_processed(self, item_ids: list):
        with self.get_session() as session:
            session.query(MediumQueue).filter(
                MediumQueue.id.in_(item_ids)
            ).update({
                'processed': True,
                'processed_at': datetime.utcnow()
            }, synchronize_session=False)
    
    def clear_medium_queue(self):
        """Clear all unprocessed items from medium queue"""
        with self.get_session() as session:
            count = session.query(MediumQueue).filter(
                MediumQueue.processed == False
            ).count()
            session.query(MediumQueue).filter(
                MediumQueue.processed == False
            ).delete(synchronize_session=False)
            return count
    
    def log_message(self, source_channel: str, source_url: str, message_text: str,
                   bucket: str, score: float, triage_json: dict, action: str, triage_time_ms: int = 0):
        with self.get_session() as session:
            log = MessageLog(
                source_channel=source_channel,
                source_url=source_url,
                message_text=message_text,
                importance_bucket=bucket,
                importance_score=score,
                triage_json=triage_json,
                action_taken=action,
                triage_time_ms=triage_time_ms
            )
            session.add(log)
    
    def get_recent_logs(self, limit: int = 100):
        """Get recent message logs"""
        with self.get_session() as session:
            items = session.query(MessageLog).order_by(
                MessageLog.received_at.desc()
            ).limit(limit).all()
            return [{
                'id': item.id,
                'source': item.source_channel,
                'title': item.message_text[:100],
                'timestamp': item.received_at.isoformat() + 'Z' if item.received_at else None,
                'triage_time': getattr(item, 'triage_time_ms', 0),
                'score': item.importance_score,
                'importance': item.importance_bucket,
                'action': item.action_taken
            } for item in items]
    
    def log_published_post(self, post_type: str, content: str, 
                          source_urls: list = None, message_id: int = None):
        with self.get_session() as session:
            post = PublishedPost(
                post_type=post_type,
                content=content,
                source_urls=source_urls,
                message_id=message_id
            )
            session.add(post)
    
    def get_published_posts_24h(self):
        """Get published posts from the last 24 hours"""
        from datetime import datetime, timedelta
        cutoff_time = datetime.utcnow() - timedelta(hours=24)
        
        with self.get_session() as session:
            posts = session.query(PublishedPost).filter(
                PublishedPost.published_at >= cutoff_time
            ).order_by(PublishedPost.published_at.desc()).all()
            
            return [{
                'id': post.id,
                'type': post.post_type,
                'content': post.content,
                'timestamp': post.published_at.isoformat() + 'Z' if post.published_at else None,
                'message_id': post.message_id,
                'source_urls': post.source_urls
            } for post in posts]
    
    def get_recent_high_posts(self, limit: int = 10):
        """Get last N high priority published posts"""
        with self.get_session() as session:
            posts = session.query(PublishedPost).filter(
                PublishedPost.post_type == 'high'
            ).order_by(PublishedPost.published_at.desc()).limit(limit).all()
            
            return [{
                'id': post.id,
                'content': post.content,
                'message_id': post.message_id,
                'source_urls': post.source_urls,
                'published_at': post.published_at
            } for post in posts]
    
    def update_published_post(self, post_id: int, new_content: str, source_urls: list = None):
        """Update an existing published post"""
        with self.get_session() as session:
            post = session.query(PublishedPost).filter(
                PublishedPost.id == post_id
            ).first()
            if post:
                post.content = new_content
                if source_urls:
                    post.source_urls = source_urls
    
    def delete_published_post(self, post_id: int):
        """Delete a published post from the database"""
        with self.get_session() as session:
            session.query(PublishedPost).filter(
                PublishedPost.id == post_id
            ).delete()
    
    def check_rate_limit(self, max_posts: int) -> bool:
        from datetime import datetime, timedelta
        current_hour = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
        
        with self.get_session() as session:
            counter = session.query(RateLimitCounter).filter(
                RateLimitCounter.hour_window == current_hour
            ).first()
            
            if not counter:
                counter = RateLimitCounter(hour_window=current_hour, post_count=0)
                session.add(counter)
                session.flush()
            
            return counter.post_count < max_posts
    
    def increment_rate_limit(self):
        from datetime import datetime
        current_hour = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
        
        with self.get_session() as session:
            counter = session.query(RateLimitCounter).filter(
                RateLimitCounter.hour_window == current_hour
            ).first()
            
            if counter:
                counter.post_count += 1
            else:
                counter = RateLimitCounter(hour_window=current_hour, post_count=1)
                session.add(counter)
    
    def add_source_channel(self, username: str, title: str = None, participants_count: int = 0):
        with self.get_session() as session:
            channel = SourceChannel(username=username, title=title, participants_count=participants_count)
            session.add(channel)
    
    def remove_source_channel(self, username: str):
        with self.get_session() as session:
            result = session.query(SourceChannel).filter(
                SourceChannel.username == username
            ).delete(synchronize_session=False)
            session.commit()
            return result
    
    def get_medium_queue_count(self) -> int:
        """Get count of pending unprocessed items in medium queue"""
        with self.get_session() as session:
            return session.query(MediumQueue).filter(
                MediumQueue.processed == False
            ).count()
    
    def get_daily_statistics(self):
        """Get today's news statistics by bucket"""
        from datetime import datetime, timedelta
        
        with self.get_session() as session:
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            
            # Count messages by bucket for today
            high_count = session.query(MessageLog).filter(
                MessageLog.received_at >= today_start,
                MessageLog.importance_bucket == 'high'
            ).count()
            
            medium_count = session.query(MessageLog).filter(
                MessageLog.received_at >= today_start,
                MessageLog.importance_bucket == 'medium'
            ).count()
            
            low_count = session.query(MessageLog).filter(
                MessageLog.received_at >= today_start,
                MessageLog.importance_bucket == 'low'
            ).count()
            
            total_count = high_count + medium_count + low_count
            
            # Get current pending medium queue count
            medium_queue_pending = self.get_medium_queue_count()
            
            return {
                'today': {
                    'total': total_count,
                    'high': high_count,
                    'medium': medium_count,
                    'low': low_count
                },
                'medium_queue_pending': medium_queue_pending,
                'date': today_start.isoformat() + 'Z'
            }
    
    def get_active_source_channels(self):
        with self.get_session() as session:
            channels = session.query(SourceChannel).filter(
                SourceChannel.is_active == True
            ).all()
            return [ch.username for ch in channels]
    
    def cache_channel(self, channel_id: str, username: str, title: str, participants_count: int):
        with self.get_session() as session:
            cached = session.query(CachedChannel).filter(CachedChannel.channel_id == channel_id).first()
            if cached:
                cached.username = username
                cached.title = title
                cached.participants_count = participants_count
                cached.updated_at = datetime.utcnow()
            else:
                cached = CachedChannel(
                    channel_id=channel_id,
                    username=username,
                    title=title,
                    participants_count=participants_count
                )
                session.add(cached)
    
    def get_cached_channels(self):
        with self.get_session() as session:
            channels = session.query(CachedChannel).all()
            return [{
                'id': ch.channel_id,
                'username': ch.username,
                'title': ch.title,
                'participants_count': ch.participants_count,
                'cached_at': ch.cached_at.isoformat() if ch.cached_at else None
            } for ch in channels]
    
    def list_all_source_channels(self):
        with self.get_session() as session:
            channels = session.query(SourceChannel).all()
            return [{
                'username': ch.username,
                'title': ch.title,
                'is_active': ch.is_active,
                'added_at': ch.added_at.isoformat() if ch.added_at else None,
                'last_message_at': ch.last_message_at.isoformat() if ch.last_message_at else None,
                'participants_count': ch.participants_count if hasattr(ch, 'participants_count') else 0
            } for ch in channels]
