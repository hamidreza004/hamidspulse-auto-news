import os
import asyncio
from typing import Optional, Callable
from telethon import TelegramClient, events, utils
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
        
        # Handler state tracking to prevent duplicates
        self._monitored_channel_ids = set()
        self._handler_registered = False
        
        # Channel info cache to avoid API calls in handler (O(1) lookups)
        self._channel_info = {}  # {channel_id: {'username': str, 'title': str}}
    
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
            
            try:
                # Parse channel identifier - could be username or c/ID
                if username.startswith('c/'):
                    # Extract numeric ID from c/ID format
                    channel_id = int(username.split('/', 1)[1])
                    logger.info(f"Validating ID-based channel: {username} (ID: {channel_id})")
                    entity = await self.client.get_entity(channel_id)
                else:
                    # Regular username - add @ if missing
                    if not username.startswith('@'):
                        username = f'@{username}'
                    entity = await self.client.get_entity(username)
                
                valid_channels.append(entity)
                logger.info(f"Validated channel: {getattr(entity, 'title', username)}")
            except Exception as e:
                logger.error(f"Could not add channel {username}: {e}")
                continue
        
        if not valid_channels:
            logger.warning("No valid channels to listen to")
            return
        
        logger.info(f"Starting to listen to {len(valid_channels)} source channels")
        logger.info(f"Registering message handler for channels: {[c.username or c.title for c in valid_channels]}")
        logger.info(f"Channel entities: {[(c.id, c.username, c.title) for c in valid_channels]}")
        
        # Update monitored channel IDs and cache channel info
        self._monitored_channel_ids = {c.id for c in valid_channels}
        
        # Build channel info cache for O(1) lookups in handler
        for channel in valid_channels:
            username = getattr(channel, 'username', None)
            if not username:
                # Resolve real ID for private channels
                real_id, _ = utils.resolve_id(channel.id)
                username = f"c/{real_id}"
            
            self._channel_info[channel.id] = {
                'username': username,
                'title': getattr(channel, 'title', 'Unknown')
            }
        
        logger.info(f"Monitored source channel IDs: {self._monitored_channel_ids}")
        logger.info(f"Cached info for {len(self._channel_info)} channels")
        
        # Register handler ONLY ONCE
        if not self._handler_registered:
            @self.client.on(events.NewMessage(incoming=True))
            async def new_message_handler(event: events.NewMessage.Event):
                # O(1) handler - no API calls, just queue work
                logger.info(f"[HANDLER] ⚡ Event triggered!")
                try:
                    # Get chat_id from event (has -100 prefix for channels)
                    chat_id_marked = event.chat_id if hasattr(event, 'chat_id') else event.message.peer_id.channel_id
                    
                    # Resolve to real ID (strip -100 prefix) for cache lookup
                    real_id, _ = utils.resolve_id(chat_id_marked)
                    
                    logger.info(f"[HANDLER] Event from chat_id: {chat_id_marked} (real: {real_id})")
                    
                    # Only process messages from our monitored source channels (compare real IDs)
                    if real_id not in self._monitored_channel_ids:
                        logger.info(f"[HANDLER] Chat {real_id} not in monitored list, skipping")
                        return
                    
                    # Get cached channel info (O(1) lookup, no API call)
                    channel_info = self._channel_info.get(real_id, {})
                    channel_username = channel_info.get('username', f'c/{real_id}')
                    channel_title = channel_info.get('title', 'Unknown')
                    
                    # Calculate message latency
                    import datetime
                    from datetime import timezone
                    latency = (datetime.datetime.now(timezone.utc) - event.message.date).total_seconds()
                    logger.info(f"[SOURCE ✓] {channel_title} | Latency: {latency:.1f}s")
                    
                    # Build URL (no API calls)
                    if channel_username.startswith('c/'):
                        message_url = f"https://t.me/{channel_username}/{event.message.id}"
                    else:
                        message_url = f"https://t.me/{channel_username}/{event.message.id}"
                    
                    # Get message text
                    message_text = event.message.message
                    
                    # Handle media messages
                    if not message_text and event.message.media:
                        # Check if media has a caption
                        if hasattr(event.message.media, 'caption') and event.message.media.caption:
                            message_text = event.message.media.caption
                        else:
                            # Skip messages with media but no caption (multi-image post fragments)
                            logger.info(f"[HANDLER] Skipping media-only message (no caption) from {channel_title}")
                            return
                    
                    # Skip if still no text
                    if not message_text:
                        logger.info(f"[HANDLER] Skipping message with no text from {channel_title}")
                        return
                    
                    # Prepare data (all O(1) operations)
                    message_data = {
                        'source_channel': channel_title,
                        'source_username': channel_username,
                        'source_url': message_url,
                        'message_text': str(message_text),
                        'message_id': event.message.id,
                        'date': event.message.date
                    }
                    
                    # Queue message (non-blocking)
                    if self.message_handler:
                        await self.message_handler(message_data)
                    
                    # Schedule read ack with 2s delay to avoid first message miss
                    async def delayed_read_ack():
                        await asyncio.sleep(2.0)
                        try:
                            await self.client.send_read_acknowledge(chat_id_marked, max_id=event.message.id)
                        except Exception as e:
                            logger.debug(f"[READ] Delayed ack failed: {e}")
                    
                    asyncio.create_task(delayed_read_ack())
                    
                except Exception as e:
                    logger.error(f"Error in message handler: {e}", exc_info=True)
            
            self._handler_registered = True
            logger.info("[HANDLER] ✓ Message handler registered (will not register again)")
        
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
                    
                    # If username is None, try fetching full entity
                    if not username:
                        try:
                            full_entity = await self.client.get_entity(entity.id)
                            username = getattr(full_entity, 'username', None)
                            if username:
                                logger.debug(f"[RESOLVE] Got username for {getattr(entity, 'title', 'channel')}: @{username}")
                        except Exception as e:
                            logger.debug(f"[RESOLVE] Could not fetch full entity: {e}")
                    
                    # Build username string
                    if username:
                        username_str = username
                    else:
                        # Use real ID (strip -100 prefix)
                        real_id, _ = utils.resolve_id(entity.id)
                        username_str = f"c/{real_id}"
                    
                    channels.append({
                        'id': entity.id,
                        'username': username_str,
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
    
    async def download_channel_photo(self, username: str, save_dir: str = "./static/profile_photos") -> Optional[str]:
        """Download channel profile photo and return relative path"""
        if not self.client or not self.is_running:
            return None
        
        try:
            os.makedirs(save_dir, exist_ok=True)
            
            # Get entity
            if not username.startswith('@'):
                username = f'@{username}'
            
            username_clean = username.lstrip('@')
            
            # Parse channel identifier
            if username_clean.startswith('c/'):
                channel_id = int(username_clean.split('/', 1)[1])
                entity = await self.client.get_entity(channel_id)
            else:
                entity = await self.client.get_entity(username)
            
            # Download profile photo
            photo_path = os.path.join(save_dir, f"{username_clean}.jpg")
            photo_file = await self.client.download_profile_photo(entity, file=photo_path)
            
            if photo_file:
                # Return relative path for web access
                return f"/static/profile_photos/{username_clean}.jpg"
            else:
                logger.warning(f"No profile photo available for {username_clean}")
                return None
                
        except Exception as e:
            logger.error(f"Error downloading profile photo for {username}: {e}")
            return None
    
    async def get_recent_messages(self, channel_username: str, limit: int = 20):
        """Fetch recent messages from a channel"""
        if not self.client or not self.is_running:
            return []
        
        try:
            # Strip @ prefix for checking (handles both @c/ID and c/ID)
            username_clean = channel_username.lstrip('@')
            
            # Parse channel identifier - could be username or c/ID
            if username_clean.startswith('c/'):
                # Extract numeric ID from c/ID format
                channel_id = int(username_clean.split('/', 1)[1])
                logger.debug(f"[REPLAY] Fetching messages from ID-based channel: {username_clean} (ID: {channel_id})")
                entity = await self.client.get_entity(channel_id)
                username_for_url = username_clean  # Keep c/ID format for fallback URL
            else:
                # Regular username
                if not channel_username.startswith('@'):
                    channel_username = f'@{channel_username}'
                entity = await self.client.get_entity(channel_username)
                username_for_url = channel_username.lstrip('@')
            messages = []
            max_message_id = 0
            
            async for message in self.client.iter_messages(entity, limit=limit):
                if message.text:
                    # Use entity username if available, otherwise use the input identifier
                    actual_username = getattr(entity, 'username', None)
                    if actual_username:
                        msg_url = f"https://t.me/{actual_username}/{message.id}"
                    else:
                        # For private/ID-based channels, use c/ID format
                        msg_url = f"https://t.me/{username_for_url}/{message.id}"
                    
                    messages.append({
                        'id': message.id,
                        'text': message.text,
                        'date': message.date,
                        'url': msg_url
                    })
                    # Track max message ID for read acknowledgment
                    if message.id > max_message_id:
                        max_message_id = message.id
            
            # Mark fetched messages as read
            if max_message_id > 0:
                try:
                    await self.client.send_read_acknowledge(entity, max_id=max_message_id)
                    logger.debug(f"[READ] Marked replay messages as read in {channel_username} (up to {max_message_id})")
                except Exception as e:
                    logger.debug(f"[READ] Failed to mark replay messages as read: {e}")
            
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
