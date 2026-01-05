import os
import asyncio
from typing import Optional, Callable
from telethon import TelegramClient, events
from telethon.tl.types import Message
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.errors import SessionPasswordNeededError, RPCError
from src.config import Config
import logging

logger = logging.getLogger(__name__)


class TelegramService:
    def __init__(self, config: Config):
        self.config = config
        self.api_id = os.getenv("TELEGRAM_API_ID")
        self.api_hash = os.getenv("TELEGRAM_API_HASH")
        self.phone = os.getenv("TELEGRAM_PHONE")
        self.session_path = os.getenv("SESSION_PATH", "./secrets/telegram.session")
        self.target_channel = os.getenv("TARGET_CHANNEL", config.target_channel)
        
        os.makedirs(os.path.dirname(self.session_path), exist_ok=True)
        
        self.client: Optional[TelegramClient] = None
        self.is_running = False
        self.message_handler: Optional[Callable] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._keep_alive_task: Optional[asyncio.Task] = None
        self._max_reconnect_attempts = 10
        self._reconnect_delay = 5
    
    async def start(self):
        if not all([self.api_id, self.api_hash, self.phone]):
            raise ValueError("Missing Telegram credentials in environment variables")
        
        self.client = TelegramClient(
            self.session_path,
            int(self.api_id),
            self.api_hash,
            connection_retries=10,
            retry_delay=3,
            timeout=30,
            auto_reconnect=True
        )
        
        await self.client.start(phone=self.phone)
        
        if not await self.client.is_user_authorized():
            logger.info("Telegram authorization required")
            await self.client.send_code_request(self.phone)
            raise Exception("Please check your Telegram app for the login code")
        
        logger.info("Telegram client started successfully")
        self.is_running = True
    
    async def stop(self):
        self.is_running = False
        
        if self._keep_alive_task and not self._keep_alive_task.done():
            self._keep_alive_task.cancel()
            self._keep_alive_task = None
        
        if self._reconnect_task:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        
        if self.client:
            await self.client.disconnect()
            logger.info("Telegram client stopped")
    
    def set_message_handler(self, handler: Callable):
        self.message_handler = handler
    
    async def listen_to_sources(self, source_channels: list):
        if not self.client or not self.is_running:
            raise Exception("Telegram client not started")
        
        valid_channels = []
        for channel in source_channels:
            if isinstance(channel, dict):
                username = channel.get('username', '')
            else:
                username = channel
            
            if not username:
                continue
            
            if not username.startswith('@'):
                username = f'@{username}'
            
            try:
                entity = await self.client.get_entity(username)
                valid_channels.append(entity)
                logger.info(f"Validated channel: {username.lstrip('@')}")
            except Exception as e:
                logger.error(f"Could not add channel {username}: {e}")
        
        if not valid_channels:
            logger.warning("No valid channels to listen to")
            return
        
        logger.info(f"Starting to listen to {len(valid_channels)} source channels")
        logger.info(f"Registering message handler for channels: {[c.username or c.title for c in valid_channels]}")
        logger.info(f"Channel entities: {[(c.id, c.username, c.title) for c in valid_channels]}")
        
        # Store channel IDs for filtering
        monitored_channel_ids = {c.id for c in valid_channels}
        logger.info(f"Monitored source channel IDs: {monitored_channel_ids}")
        
        # Register handler for ALL incoming messages (including muted channels)
        @self.client.on(events.NewMessage(incoming=True))
        async def new_message_handler(event: events.NewMessage.Event):
            chat = await event.get_chat()
            chat_id = getattr(chat, 'id', None)
            chat_username = getattr(chat, 'username', None)
            chat_title = getattr(chat, 'title', None) or getattr(chat, 'username', None) or str(chat_id)
            
            # Log ALL incoming messages for visibility
            logger.info(f"[INCOMING] {chat_title} (ID: {chat_id})")
            
            # Only process messages from our monitored source channels
            if chat_id not in monitored_channel_ids:
                return
            
            logger.info(f"[SOURCE ✓] Processing message from {chat_title}")
            try:
                message: Message = event.message
                
                chat = await event.get_chat()
                channel_username = getattr(chat, 'username', None)
                channel_title = getattr(chat, 'title', 'Unknown')
                
                if not channel_username:
                    channel_username = f"c/{chat.id}"
                
                message_text = message.message or message.media
                if not message_text:
                    return
                
                if hasattr(message.media, 'caption'):
                    message_text = message.media.caption or str(message.media)
                
                message_url = f"https://t.me/{channel_username}/{message.id}"
                
                message_data = {
                    'source_channel': channel_title,
                    'source_username': channel_username,
                    'source_url': message_url,
                    'message_text': str(message_text),
                    'message_id': message.id,
                    'date': message.date
                }
                
                logger.info(f"Message data prepared: channel={channel_title}, text_length={len(str(message_text))}")
                
                if self.message_handler:
                    await self.message_handler(message_data)
                    logger.info(f"[QUEUE] Message sent to handler: {channel_title}")
                
                # Mark message as read in Telegram
                try:
                    await self.client.send_read_acknowledge(chat, message)
                    logger.debug(f"[READ] Marked message as read in {channel_title}")
                except Exception as read_err:
                    logger.warning(f"[READ] Failed to mark as read: {read_err}")
            except Exception as e:
                logger.error(f"Error in message handler: {e}")
        
        logger.info("=" * 80)
        logger.info("✓ Handler registered with events.NewMessage(incoming=True)")
        logger.info(f"✓ Will log ALL incoming messages")
        logger.info(f"✓ Will process messages from {len(valid_channels)} source channels")
        logger.info(f"✓ Muted channels WILL be captured (mute is client-side only)")
        logger.info(f"✓ Messages will be marked as READ in Telegram")
        logger.info("=" * 80)
    
    async def post_to_channel(self, content: str) -> Optional[int]:
        if not self.client or not self.is_running:
            raise Exception("Telegram client not started")
        
        try:
            target = self.target_channel
            if not target.startswith('@'):
                target = f'@{target}'
            
            message = await self.client.send_message(target, content)
            logger.info(f"Posted to {target}: message_id={message.id}")
            return message.id
            
        except Exception as e:
            logger.error(f"Error posting to channel: {e}")
            return None
    
    async def delete_message(self, message_id: int) -> bool:
        """Delete a message from the target channel by message ID"""
        if not self.client or not self.is_running:
            raise Exception("Telegram client not started")
        
        try:
            target = self.target_channel
            if not target.startswith('@'):
                target = f'@{target}'
            
            entity = await self.client.get_entity(target)
            await self.client.delete_messages(entity, message_id)
            logger.info(f"Deleted message {message_id} from {target}")
            return True
            
        except Exception as e:
            logger.warning(f"Could not delete message {message_id} from Telegram: {e}")
            return False
    
    async def edit_message(self, message_id: int, new_content: str) -> bool:
        """Edit an existing message in the target channel"""
        if not self.client or not self.is_running:
            raise Exception("Telegram client not started")
        
        try:
            target = self.target_channel
            if not target.startswith('@'):
                target = f'@{target}'
            
            entity = await self.client.get_entity(target)
            await self.client.edit_message(entity, message_id, new_content)
            logger.info(f"Edited message {message_id} in {target}")
            return True
            
        except Exception as e:
            logger.warning(f"Could not edit message {message_id} in Telegram: {e}")
            return False
    
    async def get_all_subscribed_channels(self) -> list:
        if not self.client:
            raise Exception("Telegram client not started")
        
        if not self.is_running:
            raise Exception("Telegram client not connected - please START the system first")
        
        try:
            channels = []
            async for dialog in self.client.iter_dialogs():
                if dialog.is_channel or dialog.is_group:
                    entity = dialog.entity
                    username = getattr(entity, 'username', None)
                    if not username:
                        username = f"c/{entity.id}"
                    
                    channels.append({
                        'id': entity.id,
                        'username': username,
                        'title': getattr(entity, 'title', 'Unknown'),
                        'participants_count': getattr(entity, 'participants_count', 0),
                        'is_broadcast': getattr(entity, 'broadcast', False),
                        'is_megagroup': getattr(entity, 'megagroup', False)
                    })
            logger.info(f"Found {len(channels)} subscribed channels/groups")
            return channels
        except Exception as e:
            logger.error(f"Error fetching subscribed channels: {e}", exc_info=True)
            return []
    
    async def get_channel_info(self, username: str) -> dict:
        if not self.client:
            raise Exception("Telegram client not started")
        
        try:
            if not username.startswith('@'):
                username = f'@{username}'
            
            entity = await self.client.get_entity(username)
            
            # Try to get full channel info for participant count
            participants_count = 0
            try:
                full_channel = await self.client(GetFullChannelRequest(channel=entity))
                participants_count = full_channel.full_chat.participants_count
            except:
                # Fallback to basic attributes
                participants_count = getattr(entity, 'participants_count', 0)
            
            return {
                'id': entity.id,
                'title': getattr(entity, 'title', username),
                'username': getattr(entity, 'username', username),
                'participants_count': participants_count
            }
        except Exception as e:
            logger.error(f"Error getting channel info: {e}")
            return None
    
    async def get_recent_messages(self, channel_username: str, limit: int = 20):
        """Fetch recent messages from a channel"""
        if not self.client or not self.is_running:
            return []
        
        try:
            if not channel_username.startswith('@'):
                channel_username = f'@{channel_username}'
            
            entity = await self.client.get_entity(channel_username)
            messages = []
            
            async for message in self.client.iter_messages(entity, limit=limit):
                if message.text:
                    messages.append({
                        'id': message.id,
                        'text': message.text,
                        'date': message.date,
                        'url': f"https://t.me/{channel_username.lstrip('@')}/{message.id}"
                    })
            
            return messages
        except Exception as e:
            logger.error(f"Error fetching messages from {channel_username}: {e}")
            return []
    
    async def _on_disconnect(self, event):
        """Handle disconnect events and trigger reconnection"""
        if self.is_running and not self._reconnect_task:
            logger.warning("Telegram connection lost - initiating auto-reconnect")
            self._reconnect_task = asyncio.create_task(self._reconnect())
    
    async def _reconnect(self):
        """Automatic reconnection with exponential backoff"""
        attempt = 0
        base_delay = self._reconnect_delay
        
        while self.is_running and attempt < self._max_reconnect_attempts:
            attempt += 1
            delay = min(base_delay * (2 ** (attempt - 1)), 300)  # Max 5 minutes
            
            logger.info(f"Reconnection attempt {attempt}/{self._max_reconnect_attempts} in {delay}s")
            await asyncio.sleep(delay)
            
            try:
                if not self.client.is_connected():
                    await self.client.connect()
                    logger.info("Telegram reconnected successfully")
                    self._reconnect_task = None
                    return
                else:
                    logger.info("Already connected")
                    self._reconnect_task = None
                    return
            except Exception as e:
                logger.error(f"Reconnection attempt {attempt} failed: {e}")
        
        if attempt >= self._max_reconnect_attempts:
            logger.error(f"Failed to reconnect after {self._max_reconnect_attempts} attempts")
            self.is_running = False
        
        self._reconnect_task = None
    
    async def _keep_alive_loop(self):
        """Send periodic pings to keep connection alive"""
        while self.is_running:
            try:
                await asyncio.sleep(300)  # Ping every 5 minutes
                
                if self.client and self.client.is_connected():
                    # Send a lightweight request to keep connection alive
                    try:
                        await self.client.get_me()
                        logger.debug("Keep-alive ping sent successfully")
                    except Exception as e:
                        logger.warning(f"Keep-alive ping failed: {e}")
                        if not self._reconnect_task:
                            self._reconnect_task = asyncio.create_task(self._reconnect())
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Keep-alive loop error: {e}")
    
    async def run_until_disconnected(self):
        if self.client and self.is_running:
            await self.client.run_until_disconnected()
