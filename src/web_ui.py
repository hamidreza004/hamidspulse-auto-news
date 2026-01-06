import asyncio
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import asyncio
import json
from datetime import datetime, timedelta
import logging
import secrets
import hashlib
import yaml

logger = logging.getLogger(__name__)


class WebUI:
    def __init__(self, app_manager):
        self.app = FastAPI()
        self.app_manager = app_manager
        self.active_connections = []
        self.sessions = {}  # session_token -> {username, expires_at}
        self.connection_counter = 0
        
        # Mount static files directory for serving images
        self.app.mount("/data", StaticFiles(directory="data"), name="data")
        
        self._setup_routes()
    
    def _setup_routes(self):
        @self.app.get("/", response_class=HTMLResponse)
        async def home(request: Request):
            # Check authentication
            session_token = request.cookies.get('session_token')
            if not self._verify_session(session_token):
                return self._get_login_html()
            return self._get_html()
        
        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            await self._handle_websocket(websocket)
        
        @self.app.get("/api/status")
        async def get_status():
            status = self.app_manager.get_status()
            status['websocket_connected'] = len(self.active_connections) > 0
            return status
        
        @self.app.get("/api/state")
        async def get_state():
            return {"state": self.app_manager.get_state()}
        
        @self.app.get("/api/telegram/channels")
        async def get_telegram_channels(refresh: bool = False):
            # Always return cached first for instant load
            cached = self.app_manager.db.get_cached_channels()
            
            # If not refreshing or Telegram not connected, return cache immediately
            if not refresh or not self.app_manager.telegram or not self.app_manager.telegram.is_running:
                return {"success": True, "channels": cached, "from_cache": True}
            
            # Only fetch fresh data if explicitly refreshing
            try:
                channels = await self.app_manager.telegram.get_all_subscribed_channels()
                # Update cache
                for ch in channels:
                    self.app_manager.db.cache_channel(
                        channel_id=str(ch.get('id', '')),
                        username=ch.get('username', ''),
                        title=ch.get('title', ''),
                        participants_count=ch.get('participants_count', 0)
                    )
                return {"success": True, "channels": channels, "from_cache": False}
            except Exception as e:
                # Return cache on error
                return {"success": True, "channels": cached, "from_cache": True, "error": str(e)}
        
        @self.app.get("/api/config")
        async def get_config():
            try:
                with open('config.yaml', 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f)
                return {"success": True, "config": config}
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        @self.app.post("/api/config")
        async def update_config(request: Request):
            try:
                data = await request.json()
                with open('config.yaml', 'w', encoding='utf-8') as f:
                    yaml.dump(data['config'], f, allow_unicode=True, default_flow_style=False)
                self.app_manager.config.load()
                await self._broadcast({"type": "config_updated"})
                await self._broadcast({"type": "log", "data": {"message": "Configuration updated", "level": "success"}})
                return {"success": True, "message": "Config updated"}
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        @self.app.post("/api/start")
        async def start_app():
            try:
                await self.app_manager.start()
                await self._broadcast({"type": "status", "data": self.app_manager.get_status()})
                await self._broadcast({"type": "log", "data": {"message": "System started", "level": "success"}})
                return {"success": True, "message": "Application started"}
            except Exception as e:
                await self._broadcast({"type": "log", "data": {"message": f"Start failed: {str(e)}", "level": "error"}})
                return {"success": False, "error": str(e)}
        
        @self.app.post("/api/stop")
        async def stop_app():
            try:
                await self.app_manager.stop()
                await self._broadcast({"type": "status", "data": self.app_manager.get_status()})
                await self._broadcast({"type": "log", "data": {"message": "System stopped", "level": "warning"}})
                return {"success": True, "message": "Application stopped"}
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        @self.app.get("/api/sources")
        async def list_sources():
            return {"sources": self.app_manager.list_source_channels()}
        
        @self.app.post("/api/sources/add")
        async def add_source(request: Request):
            data = await request.json()
            username = data.get("username", "").strip()
            if not username:
                return {"success": False, "error": "Username required"}
            try:
                await self.app_manager.add_source_channel(username)
                await self._broadcast({
                    "type": "sources_updated",
                    "data": self.app_manager.list_source_channels()
                })
                await self._broadcast({"type": "log", "data": {"message": f"Added source: {username}", "level": "success"}})
                return {"success": True, "message": f"Added {username}"}
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        @self.app.post("/api/sources/remove")
        async def remove_source(request: Request):
            data = await request.json()
            username = data.get("username", "").strip()
            if not username:
                return {"success": False, "error": "Username required"}
            try:
                await self.app_manager.remove_source_channel(username)
                await self._broadcast({
                    "type": "sources_updated",
                    "data": self.app_manager.list_source_channels()
                })
                await self._broadcast({"type": "log", "data": {"message": f"Removed source: {username}", "level": "warning"}})
                return {"success": True, "message": f"Removed {username}"}
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        @self.app.get("/api/state")
        async def get_state():
            return {"state": self.app_manager.get_current_state()}
        
        @self.app.post("/api/login")
        async def login(request: Request, response: Response):
            try:
                body = await request.json()
                username = body.get('username')
                password = body.get('password')
                
                # Verify credentials from config
                if username == self.app_manager.config.auth_username and password == self.app_manager.config.auth_password:
                    # Create session token
                    session_token = secrets.token_urlsafe(32)
                    expires_at = datetime.now() + timedelta(days=10)
                    
                    self.sessions[session_token] = {
                        'username': username,
                        'expires_at': expires_at
                    }
                    
                    response.set_cookie(
                        key='session_token',
                        value=session_token,
                        max_age=10 * 24 * 60 * 60,  # 10 days in seconds
                        httponly=True,
                        samesite='lax'
                    )
                    
                    return {"success": True, "message": "Login successful"}
                else:
                    return {"success": False, "error": "Invalid credentials"}
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        @self.app.post("/api/logout")
        async def logout(request: Request, response: Response):
            session_token = request.cookies.get('session_token')
            if session_token in self.sessions:
                del self.sessions[session_token]
            response.delete_cookie('session_token')
            return {"success": True}
        
        @self.app.get("/api/queue")
        async def get_queue():
            try:
                high_queue = self.app_manager.db.get_high_queue()
                medium_queue = self.app_manager.db.get_medium_queue()
                low_queue = self.app_manager.db.get_low_queue()
                return {
                    "success": True,
                    "high": high_queue,
                    "medium": medium_queue,
                    "low": low_queue
                }
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        @self.app.get("/api/posts/24h")
        async def get_posts_24h():
            try:
                posts = self.app_manager.db.get_published_posts_24h()
                return {"success": True, "posts": posts}
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        @self.app.post("/api/posts/delete")
        async def delete_post(request: Request):
            try:
                body = await request.json()
                post_id = body.get('post_id')
                message_id = body.get('message_id')
                
                if not post_id:
                    return {"success": False, "error": "post_id required"}
                
                # Try to delete from Telegram if message_id exists
                telegram_deleted = False
                if message_id and self.app_manager.telegram and self.app_manager.telegram.is_running:
                    try:
                        telegram_deleted = await self.app_manager.telegram.delete_message(message_id)
                    except Exception as e:
                        logger.warning(f"Failed to delete from Telegram: {e}")
                
                # Delete from database
                self.app_manager.db.delete_published_post(post_id)
                
                msg = "Deleted from database"
                if telegram_deleted:
                    msg = "Deleted from Telegram and database"
                elif message_id:
                    msg = "Deleted from database (Telegram deletion failed)"
                
                await self._broadcast({"type": "log", "data": {"message": msg, "level": "success"}})
                return {"success": True, "message": msg, "telegram_deleted": telegram_deleted}
                
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        @self.app.post("/api/replay")
        async def replay_messages(request: Request):
            if not self.app_manager.telegram or not self.app_manager.telegram.is_running:
                return {"success": False, "error": "System must be running"}
            try:
                body = await request.json()
                minutes = body.get('minutes', 10)
                await self._broadcast({"type": "log", "data": {"message": f"Starting replay of past {minutes} minutes...", "level": "info"}})
                source_channels = self.app_manager.db.get_active_source_channels()
                await self.app_manager._replay_recent_messages(source_channels, minutes=minutes, broadcast_callback=self._broadcast)
                await self._broadcast({"type": "log", "data": {"message": "Replay complete!", "level": "success"}})
                return {"success": True, "message": "Replay completed"}
            except Exception as e:
                await self._broadcast({"type": "log", "data": {"message": f"Replay failed: {str(e)}", "level": "error"}})
                return {"success": False, "error": str(e)}
        
        @self.app.post("/api/message/promote")
        async def promote_message(request: Request):
            if not self.app_manager.telegram or not self.app_manager.telegram.is_running:
                return {"success": False, "error": "System must be running"}
            try:
                body = await request.json()
                message_id = body.get('message_id')
                current_bucket = body.get('current_bucket')
                result = await self.app_manager.promote_message(message_id, current_bucket, broadcast_callback=self._broadcast)
                return result
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        @self.app.post("/api/message/demote")
        async def demote_message(request: Request):
            if not self.app_manager.telegram or not self.app_manager.telegram.is_running:
                return {"success": False, "error": "System must be running"}
            try:
                body = await request.json()
                message_id = body.get('message_id')
                current_bucket = body.get('current_bucket')
                result = await self.app_manager.demote_message(message_id, current_bucket, broadcast_callback=self._broadcast)
                return result
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        @self.app.post("/api/initialize-situation")
        async def initialize_situation():
            if not self.app_manager.telegram or not self.app_manager.telegram.is_running:
                return {"success": False, "error": "System must be running"}
            try:
                await self._broadcast({"type": "log", "data": {"message": "🌍 Fetching past 24 hours of news...", "level": "info"}})
                new_brief = await self.app_manager.initialize_situation_from_24h(broadcast_callback=self._broadcast)
                await self._broadcast({"type": "log", "data": {"message": f"✅ Situation initialized: {len(new_brief)} characters", "level": "success"}})
                return {"success": True, "message": "Situation initialized", "brief": new_brief}
            except Exception as e:
                await self._broadcast({"type": "log", "data": {"message": f"❌ Initialize failed: {str(e)}", "level": "error"}})
                return {"success": False, "error": str(e)}
        
        @self.app.post("/api/state/set")
        async def set_state(request: Request):
            data = await request.json()
            new_state = data.get("state", "").strip()
            if not new_state:
                return {"success": False, "error": "State required"}
            try:
                self.app_manager.set_state(new_state)
                await self._broadcast({
                    "type": "state_updated",
                    "data": new_state
                })
                await self._broadcast({"type": "log", "data": {"message": "State updated", "level": "success"}})
                return {"success": True, "message": "State updated"}
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        @self.app.post("/api/queue/clear")
        async def clear_queue():
            try:
                await self.app_manager.trigger_hourly_digest()
                await self._broadcast({"type": "log", "data": {"message": "Queue cleared and digest published", "level": "success"}})
                return {"success": True, "message": "Queue cleared and digest published"}
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        @self.app.post("/api/queue/clear-high")
        async def clear_high_queue():
            try:
                self.app_manager.db.clear_high_queue()
                await self._broadcast({"type": "log", "data": {"message": "High priority queue cleared", "level": "success"}})
                return {"success": True, "message": "High queue cleared"}
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        @self.app.post("/api/queue/clear-low")
        async def clear_low_queue():
            try:
                self.app_manager.db.clear_low_queue()
                await self._broadcast({"type": "log", "data": {"message": "Low priority queue cleared", "level": "success"}})
                return {"success": True, "message": "Low queue cleared"}
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        @self.app.post("/api/queue/clear-medium")
        async def clear_medium_queue():
            try:
                self.app_manager.db.clear_medium_queue()
                await self._broadcast({"type": "log", "data": {"message": "Medium priority queue cleared", "level": "success"}})
                return {"success": True, "message": "Medium queue cleared"}
            except Exception as e:
                return {"success": False, "error": str(e)}
    
    async def _handle_websocket(self, websocket: WebSocket):
        await websocket.accept()
        connection_id = self.connection_counter
        self.connection_counter += 1
        self.active_connections.append(websocket)
        logger.info(f"WebSocket connected (ID: {connection_id}). Total: {len(self.active_connections)}")
        
        try:
            status = self.app_manager.get_status()
            status['websocket_connected'] = True
            await websocket.send_json({
                "type": "connected",
                "data": {
                    "connection_id": connection_id,
                    "status": status,
                    "timestamp": datetime.now().isoformat()
                }
            })
            
            asyncio.create_task(self._send_heartbeat(websocket))
            
            while True:
                data = await websocket.receive_text()
                try:
                    msg = json.loads(data)
                    if msg.get('type') == 'ping':
                        await websocket.send_json({"type": "pong"})
                except:
                    pass
                
        except WebSocketDisconnect:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
            logger.info(f"WebSocket disconnected (ID: {connection_id}). Total: {len(self.active_connections)}")
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
    
    async def _send_heartbeat(self, websocket: WebSocket):
        try:
            while websocket in self.active_connections:
                await asyncio.sleep(3)
                if websocket in self.active_connections:
                    status = self.app_manager.get_status()
                    status['websocket_connected'] = True
                    await websocket.send_json({
                        "type": "heartbeat",
                        "data": {
                            "status": status,
                            "timestamp": datetime.now().isoformat()
                        }
                    })
        except Exception as e:
            logger.debug(f"Heartbeat stopped: {e}")
    
    async def _broadcast(self, message: dict):
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error broadcasting to WebSocket: {e}")
                disconnected.append(connection)
        
        for conn in disconnected:
            if conn in self.active_connections:
                self.active_connections.remove(conn)
    
    async def log_broadcast(self, message: str, level: str = "info", broadcast_type: str = "log", state_data: str = None):
        await self._broadcast({
            "type": broadcast_type,
            "data": {
                "message": message,
                "level": level,
                "state_data": state_data,
                "timestamp": datetime.now().isoformat()
            }
        })
    
    def _verify_session(self, session_token: str) -> bool:
        """Verify if session token is valid and not expired"""
        if not session_token or session_token not in self.sessions:
            return False
        
        session = self.sessions[session_token]
        if datetime.now() > session['expires_at']:
            # Session expired, remove it
            del self.sessions[session_token]
            return False
        
        return True
    
    def _get_login_html(self) -> str:
        """Return login page HTML"""
        return r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HAMID'S PULSE - LOGIN</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * {
            font-family: 'IBM Plex Mono', monospace;
        }
        body {
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .login-card {
            background: rgba(30, 41, 59, 0.8);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(148, 163, 184, 0.1);
            border-radius: 16px;
            padding: 48px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
            max-width: 400px;
            width: 100%;
        }
        .input-field {
            background: rgba(15, 23, 42, 0.6);
            border: 1px solid rgba(148, 163, 184, 0.2);
            color: #e2e8f0;
            padding: 12px 16px;
            border-radius: 8px;
            width: 100%;
            transition: all 0.3s;
        }
        .input-field:focus {
            outline: none;
            border-color: #3b82f6;
            box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1);
        }
        .btn-login {
            background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
            color: white;
            padding: 12px 24px;
            border-radius: 8px;
            border: none;
            cursor: pointer;
            width: 100%;
            font-weight: 600;
            transition: all 0.3s;
        }
        .btn-login:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(59, 130, 246, 0.4);
        }
        .btn-login:active {
            transform: translateY(0);
        }
        .error-message {
            background: rgba(239, 68, 68, 0.1);
            border: 1px solid rgba(239, 68, 68, 0.3);
            color: #fca5a5;
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 16px;
            display: none;
        }
        .logo {
            font-size: 24px;
            font-weight: 700;
            background: linear-gradient(135deg, #60a5fa 0%, #3b82f6 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            text-align: center;
            margin-bottom: 32px;
        }
    </style>
</head>
<body>
    <div class="login-card">
        <div class="logo">🔒 HAMID'S PULSE</div>
        <div id="errorMessage" class="error-message"></div>
        <form id="loginForm" onsubmit="handleLogin(event)">
            <div style="margin-bottom: 20px;">
                <label style="color: #94a3b8; font-size: 14px; margin-bottom: 8px; display: block;">Username</label>
                <input type="text" id="username" class="input-field" placeholder="Enter username" required autofocus>
            </div>
            <div style="margin-bottom: 24px;">
                <label style="color: #94a3b8; font-size: 14px; margin-bottom: 8px; display: block;">Password</label>
                <input type="password" id="password" class="input-field" placeholder="Enter password" required>
            </div>
            <button type="submit" class="btn-login">LOGIN</button>
        </form>
    </div>

    <script>
        async function handleLogin(event) {
            event.preventDefault();
            
            const username = document.getElementById('username').value;
            const password = document.getElementById('password').value;
            const errorDiv = document.getElementById('errorMessage');
            
            errorDiv.style.display = 'none';
            
            try {
                const res = await fetch('/api/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, password })
                });
                
                const data = await res.json();
                
                if (data.success) {
                    window.location.href = '/';
                } else {
                    errorDiv.textContent = data.error || 'Login failed';
                    errorDiv.style.display = 'block';
                }
            } catch (e) {
                errorDiv.textContent = 'Connection error: ' + e.message;
                errorDiv.style.display = 'block';
            }
        }
    </script>
</body>
</html>
        """
    
    def _get_html(self) -> str:
        return r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HAMID'S PULSE - CONTROL PANEL</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js" defer></script>
    <script src="https://unpkg.com/lucide@latest"></script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=VT323&display=swap" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/gh/rastikerdar/vazir-font@v30.1.0/dist/font-face.css" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body { 
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background: linear-gradient(135deg, #0a0a0f 0%, #1a1a2e 50%, #16213e 100%);
            background-attachment: fixed;
            color: #e2e8f0;
            overflow-x: hidden;
            line-height: 1.6;
        }
        
        /* Glassmorphism Panels */
        .glass-panel {
            background: rgba(255, 255, 255, 0.05);
            backdrop-filter: blur(40px) saturate(200%);
            -webkit-backdrop-filter: blur(40px) saturate(200%);
            border-radius: 20px;
            border: 1px solid rgba(255, 255, 255, 0.12);
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.4), inset 0 1px 0 rgba(255, 255, 255, 0.1);
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }
        
        .glass-panel:hover {
            border-color: rgba(255, 255, 255, 0.2);
            box-shadow: 0 12px 48px 0 rgba(0, 0, 0, 0.5), inset 0 1px 0 rgba(255, 255, 255, 0.15);
            transform: translateY(-2px);
        }
        
        /* Config Panel Variant */
        .glass-config {
            background: rgba(147, 51, 234, 0.08);
            border: 1px solid rgba(147, 51, 234, 0.25);
            box-shadow: 0 8px 32px 0 rgba(147, 51, 234, 0.15), inset 0 1px 0 rgba(255, 255, 255, 0.08);
        }
        
        .glass-config:hover {
            border-color: rgba(147, 51, 234, 0.35);
            box-shadow: 0 12px 48px 0 rgba(147, 51, 234, 0.25), inset 0 1px 0 rgba(255, 255, 255, 0.12);
        }
        
        /* Status Panel Variant */
        .glass-status {
            background: rgba(59, 130, 246, 0.06);
            border: 1px solid rgba(59, 130, 246, 0.2);
            box-shadow: 0 8px 32px 0 rgba(59, 130, 246, 0.12), inset 0 1px 0 rgba(255, 255, 255, 0.08);
        }
        
        .glass-status:hover {
            border-color: rgba(59, 130, 246, 0.3);
            box-shadow: 0 12px 48px 0 rgba(59, 130, 246, 0.2), inset 0 1px 0 rgba(255, 255, 255, 0.12);
        }
        
        /* Persian Text */
        .persian-text { 
            font-family: 'Vazir', sans-serif !important;
            direction: rtl;
            text-align: right;
            line-height: 1.8;
        }
        
        /* Status Indicators */
        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            display: inline-block;
            position: relative;
        }
        
        .status-on { 
            background: #10b981;
            box-shadow: 0 0 12px rgba(16, 185, 129, 0.6);
        }
        
        .status-on::after {
            content: '';
            position: absolute;
            width: 100%;
            height: 100%;
            border-radius: 50%;
            background: inherit;
            animation: pulse-ring 1.5s cubic-bezier(0.4, 0, 0.6, 1) infinite;
        }
        
        .status-off { 
            background: #ef4444;
            box-shadow: 0 0 12px rgba(239, 68, 68, 0.6);
        }
        
        @keyframes pulse-ring {
            0% { transform: scale(1); opacity: 1; }
            100% { transform: scale(2); opacity: 0; }
        }
        
        /* Buttons */
        .btn {
            background: linear-gradient(135deg, rgba(96, 165, 250, 0.15), rgba(147, 51, 234, 0.15));
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.25);
            color: #ffffff;
            padding: 11px 22px;
            border-radius: 10px;
            cursor: pointer;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            font-family: 'VT323', monospace;
            font-weight: 400;
            font-size: 1.25rem;
            letter-spacing: 0.5px;
            line-height: 1.2;
            box-shadow: 0 4px 16px rgba(0, 0, 0, 0.2), inset 0 1px 0 rgba(255, 255, 255, 0.1);
            position: relative;
            overflow: hidden;
        }
        
        .btn::before {
            content: '';
            position: absolute;
            top: 0;
            left: -100%;
            width: 100%;
            height: 100%;
            background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.1), transparent);
            transition: left 0.5s;
        }
        
        .btn:hover:not(:disabled)::before {
            left: 100%;
        }
        
        .btn:hover:not(:disabled) {
            background: linear-gradient(135deg, rgba(96, 165, 250, 0.25), rgba(147, 51, 234, 0.25));
            border-color: rgba(255, 255, 255, 0.4);
            transform: translateY(-2px);
            box-shadow: 0 8px 24px rgba(96, 165, 250, 0.3), inset 0 1px 0 rgba(255, 255, 255, 0.15);
        }
        
        .btn:active:not(:disabled) {
            transform: translateY(0);
        }
        
        .btn:disabled {
            opacity: 0.4;
            cursor: not-allowed;
        }
        
        .btn-danger { 
            background: linear-gradient(135deg, rgba(239, 68, 68, 0.2), rgba(220, 38, 38, 0.2));
            border-color: rgba(239, 68, 68, 0.5);
            color: #fca5a5;
        }
        
        .btn-danger:hover:not(:disabled) { 
            background: linear-gradient(135deg, rgba(239, 68, 68, 0.3), rgba(220, 38, 38, 0.3));
            border-color: rgba(239, 68, 68, 0.7);
            box-shadow: 0 8px 24px rgba(239, 68, 68, 0.4), inset 0 1px 0 rgba(255, 255, 255, 0.15);
        }
        
        .btn-sm {
            padding: 8px 16px;
            font-size: 1.1rem;
        }
        
        /* Input Fields */
        .input-field {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.15);
            color: #e2e8f0;
            padding: 10px 14px;
            border-radius: 8px;
            font-family: 'Inter', sans-serif;
            font-size: 1.1rem;
            transition: all 0.2s;
        }
        
        .input-field::placeholder {
            color: rgba(226, 232, 240, 0.4);
        }
        
        .input-field:focus {
            outline: none;
            border-color: rgba(96, 165, 250, 0.5);
            background: rgba(255, 255, 255, 0.08);
            box-shadow: 0 0 0 3px rgba(96, 165, 250, 0.1);
        }
        
        textarea {
            resize: vertical;
            font-size: 1.1rem;
            line-height: 1.5;
        }
        
        /* Section Titles */
        .section-title {
            color: #f1f5f9;
            font-family: 'VT323', monospace;
            font-weight: 400;
            font-size: 2rem;
            letter-spacing: 1px;
            padding-bottom: 10px;
            margin-bottom: 14px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            line-height: 1.2;
        }
        
        /* Scrollbar */
        .scrollbar::-webkit-scrollbar { width: 6px; height: 6px; }
        .scrollbar::-webkit-scrollbar-track { 
            background: rgba(255, 255, 255, 0.05);
            border-radius: 3px;
        }
        .scrollbar::-webkit-scrollbar-thumb { 
            background: rgba(255, 255, 255, 0.2);
            border-radius: 3px;
        }
        .scrollbar::-webkit-scrollbar-thumb:hover { 
            background: rgba(255, 255, 255, 0.3);
        }
        
        /* Message Cards */
        .message-card {
            background: rgba(255, 255, 255, 0.04);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 12px;
            padding: 14px;
            transition: all 0.2s;
        }
        
        .message-card:hover {
            background: rgba(255, 255, 255, 0.07);
            border-color: rgba(255, 255, 255, 0.15);
            transform: translateX(2px);
        }
        
        .message-card.high {
            border-left: 3px solid #ef4444;
            background: rgba(239, 68, 68, 0.08);
        }
        
        .message-card.medium {
            border-left: 3px solid #f59e0b;
            background: rgba(245, 158, 11, 0.08);
        }
        
        .message-card.low {
            border-left: 3px solid #6366f1;
            background: rgba(99, 102, 241, 0.08);
        }
        
        .message-card.published {
            border-left: 3px solid #10b981;
            background: rgba(16, 185, 129, 0.08);
        }
        
        /* Priority Badge */
        .priority-badge {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 8px 16px;
            border-radius: 8px;
            font-size: 1.4rem;
            font-weight: 600;
            font-family: 'VT323', monospace;
            line-height: 1.2;
        }
        
        .priority-badge.high {
            background: rgba(239, 68, 68, 0.15);
            color: #fca5a5;
        }
        
        .priority-badge.medium {
            background: rgba(245, 158, 11, 0.15);
            color: #fcd34d;
        }
        
        .priority-badge.low {
            background: rgba(99, 102, 241, 0.15);
            color: #c7d2fe;
        }
        
        /* Log Levels - Terminal Style */
        .log-terminal {
            background: rgba(0, 0, 0, 0.4);
            border: 1px solid rgba(0, 255, 65, 0.2);
            border-left: 3px solid #00ff41;
            padding: 8px 12px;
            border-radius: 4px;
            font-family: 'VT323', monospace;
            font-size: 1.15rem;
            line-height: 1.3;
            color: #00ff41;
        }
        
        .log-info { 
            border-left-color: #60a5fa;
            color: #60a5fa;
        }
        .log-success { 
            border-left-color: #10b981;
            color: #10b981;
        }
        .log-warning { 
            border-left-color: #f59e0b;
            color: #f59e0b;
        }
        .log-error { 
            border-left-color: #ef4444;
            color: #ef4444;
        }
        
        /* Header Gradient */
        .header-gradient {
            background: linear-gradient(135deg, rgba(147, 51, 234, 0.1) 0%, rgba(59, 130, 246, 0.1) 100%);
            border-radius: 16px;
            padding: 24px;
            margin-bottom: 24px;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }
        
        /* Tab Button */
        .tab-btn {
            padding: 10px 20px;
            border-radius: 8px;
            transition: all 0.2s;
            font-family: 'VT323', monospace;
            font-size: 1.3rem;
            font-weight: 400;
            line-height: 1.2;
        }
        
        .tab-btn.active {
            background: rgba(96, 165, 250, 0.2);
            color: #93c5fd;
        }
        
        /* Icons */
        .icon {
            width: 18px;
            height: 18px;
            display: inline-block;
            vertical-align: middle;
            stroke-width: 2;
        }
        
        .icon-sm {
            width: 16px;
            height: 16px;
        }
        
        .icon-lg {
            width: 20px;
            height: 20px;
        }
        
        /* Priority Action Icons */
        .priority-action-icon {
            width: 16px;
            height: 16px;
            cursor: pointer;
            opacity: 0.5;
            transition: all 0.2s;
        }
        
        .priority-action-icon:hover {
            opacity: 1;
            filter: drop-shadow(0 0 4px currentColor);
            transform: scale(1.15);
        }
        
        /* Terminal Text */
        .terminal-text {
            font-family: 'VT323', monospace;
            font-size: 2rem;
            letter-spacing: 0.5px;
            line-height: 1.4;
        }
        
        .mono {
            font-family: 'VT323', monospace;
            font-size: 2.25rem;
            line-height: 1.3;
        }
        
        /* Scanning Effect */
        @keyframes scan {
            0% { opacity: 0.8; }
            50% { opacity: 1; }
            100% { opacity: 0.8; }
        }
        
        .scanning {
            animation: scan 2s ease-in-out infinite;
        }
        
        /* Utility */
        .text-muted { color: rgba(226, 232, 240, 0.6); }
        .text-bright { color: #f1f5f9; }
        .text-terminal { color: #60a5fa; }
    </style>
</head>
<body class="min-h-screen p-6">
    <!-- Fixed Left Sidebar -->
    <div class="fixed left-0 top-0 h-full w-24 flex items-start justify-center pt-8" style="z-index: 1000; background: rgba(15, 23, 42, 0.8); backdrop-filter: blur(10px); border-right: 1px solid rgba(96, 165, 250, 0.2);">
        <img src="/data/channel.jpg" alt="Channel Logo" class="w-16 h-16 rounded-full border-2 border-terminal/30">
    </div>
    
    <div x-data="app()" x-init="init()" class="max-w-7xl" style="margin: 0 auto; padding-left: 96px;">
        
        <!-- Header -->
        <div class="header-gradient text-center mb-6">
            <h1 class="text-4xl font-bold text-terminal mb-2 flex items-center justify-center gap-3 mono">
                <i data-lucide="radio" class="icon-lg scanning"></i>
                HAMID'S PULSE CONTROL PANEL
                <i data-lucide="radio" class="icon-lg scanning"></i>
            </h1>
            <div class="text-terminal mono scanning" style="font-size: 1.15rem;">[ AUTOMATED NEWS CHANNEL MANAGEMENT SYSTEM v2.0 ]</div>
        </div>

        <!-- Top Row: Status + Quick Controls -->
        <div class="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-4">
            
            <!-- System Status -->
            <div class="glass-panel glass-status p-6">
                <div class="section-title flex items-center gap-2">
                    <i data-lucide="activity" class="icon"></i>
                    System Status
                </div>
                <div class="space-y-2.5 terminal-text">
                    <div class="flex justify-between items-center">
                        <span class="text-terminal flex items-center gap-2">
                            <i data-lucide="wifi" class="icon-sm"></i>
                            WEBSOCKET:
                        </span>
                        <div class="flex items-center gap-2">
                            <span :class="status.websocket_connected ? 'status-on' : 'status-off'" class="status-dot pulse-dot"></span>
                            <span x-text="status.websocket_connected ? 'CONNECTED' : 'DISCONNECTED'"></span>
                        </div>
                    </div>
                    <div class="flex justify-between items-center">
                        <span class="text-terminal flex items-center gap-2">
                            <i data-lucide="send" class="icon-sm"></i>
                            TELEGRAM:
                        </span>
                        <div class="flex items-center gap-2">
                            <span :class="status.telegram_connected ? 'status-on' : 'status-off'" class="status-dot pulse-dot"></span>
                            <span x-text="status.telegram_connected ? 'ONLINE' : 'OFFLINE'"></span>
                        </div>
                    </div>
                    <div class="flex justify-between items-center">
                        <span class="text-terminal flex items-center gap-2">
                            <i data-lucide="clock" class="icon-sm"></i>
                            SCHEDULER:
                        </span>
                        <div class="flex items-center gap-2">
                            <span :class="status.scheduler_running ? 'status-on' : 'status-off'" class="status-dot pulse-dot"></span>
                            <span x-text="status.scheduler_running ? 'ACTIVE' : 'INACTIVE'"></span>
                        </div>
                    </div>
                    <div class="flex justify-between items-center">
                        <span class="text-terminal flex items-center gap-2">
                            <i data-lucide="rss" class="icon-sm"></i>
                            SOURCES:
                        </span>
                        <span x-text="status.source_channels_count || 0"></span>
                    </div>
                </div>
            </div>

            <!-- Quick Controls -->
            <div class="glass-panel p-6">
                <div class="section-title flex items-center gap-2">
                    <i data-lucide="zap" class="icon"></i>
                    Quick Controls
                </div>
                <div class="space-y-2">
                    <button @click="startApp()" :disabled="status.is_running || startingApp" class="btn w-full flex items-center justify-center gap-2">
                        <i :data-lucide="startingApp ? 'loader-2' : 'play'" :class="{'animate-spin': startingApp}" class="icon-sm"></i>
                        <span x-text="startingApp ? 'STARTING...' : 'START SYSTEM'"></span>
                    </button>
                    <button @click="stopApp()" :disabled="!status.is_running" class="btn btn-danger w-full flex items-center justify-center gap-2">
                        <i data-lucide="square" class="icon-sm"></i>
                        STOP SYSTEM
                    </button>
                    <div class="flex gap-2 w-full items-center">
                        <input x-model.number="replayMinutes" type="number" min="1" max="1440" class="input-field" style="width: 70px; padding: 8px; font-size: 1.1rem;" placeholder="10">
                        <button @click="replayPastMessages()" :disabled="!status.is_running || replayingMessages" class="btn flex-1 flex items-center justify-center gap-2">
                            <i :data-lucide="replayingMessages ? 'loader-2' : 'rotate-ccw'" :class="{'animate-spin': replayingMessages}" class="icon-sm"></i>
                            <span x-text="replayingMessages ? 'REPLAYING...' : 'REPLAY PAST ' + replayMinutes + ' MIN'"></span>
                        </button>
                    </div>
                    <button @click="initializeSituation()" :disabled="!status.is_running || initializingSituation" class="btn w-full flex items-center justify-center gap-2">
                        <i :data-lucide="initializingSituation ? 'loader-2' : 'globe'" :class="{'animate-spin': initializingSituation}" class="icon-sm"></i>
                        <span x-text="initializingSituation ? 'INITIALIZING...' : 'INITIALIZE SITUATION'"></span>
                    </button>
                    <button @click="clearQueue()" :disabled="!status.is_running || clearingQueue" class="btn w-full flex items-center justify-center gap-2">
                        <i :data-lucide="clearingQueue ? 'loader-2' : 'trash-2'" :class="{'animate-spin': clearingQueue}" class="icon-sm"></i>
                        <span x-text="clearingQueue ? 'CLEARING...' : 'EMPTY QUEUE'"></span>
                    </button>
                </div>
            </div>

            <!-- Daily News Statistics -->
            <div class="glass-panel glass-status p-6">
                <div class="section-title flex items-center gap-2">
                    <i data-lucide="bar-chart-3" class="icon"></i>
                    Today's News
                </div>
                <div class="space-y-3 terminal-text">
                    <div class="grid grid-cols-2 gap-3">
                        <div class="text-center p-3 bg-white/5 rounded-lg">
                            <div class="text-terminal text-5xl font-bold" x-text="status.daily_stats?.today?.total || 0"></div>
                            <div class="text-muted text-base mt-1 font-semibold">TOTAL</div>
                        </div>
                        <div class="text-center p-3 bg-green-500/10 rounded-lg border border-green-500/20">
                            <div class="text-success text-5xl font-bold" x-text="status.daily_stats?.today?.high || 0"></div>
                            <div class="text-success/70 text-base mt-1 font-semibold">HIGH</div>
                        </div>
                    </div>
                    <div class="grid grid-cols-2 gap-3">
                        <div class="text-center p-3 bg-yellow-500/10 rounded-lg border border-yellow-500/20">
                            <div class="text-warning text-5xl font-bold" x-text="status.daily_stats?.medium_queue_pending || 0"></div>
                            <div class="text-warning/70 text-base mt-1 font-semibold">MEDIUM QUEUE</div>
                        </div>
                        <div class="text-center p-3 bg-white/5 rounded-lg">
                            <div class="text-muted text-5xl font-bold" x-text="status.daily_stats?.today?.low || 0"></div>
                            <div class="text-muted/70 text-base mt-1 font-semibold">LOW</div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Middle Row: Channels + Situation Brief -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
            
            <!-- Channel Management -->
            <div class="glass-panel p-6">
                <div class="section-title flex items-center gap-2">
                    <i data-lucide="radio" class="icon"></i>
                    Source Channels
                </div>
                
                <!-- Tab Toggle -->
                <div class="flex gap-2 mb-4">
                    <button @click="channelTab = 'active'" :class="channelTab === 'active' ? 'tab-btn active' : 'tab-btn opacity-50'" class="flex-1 flex items-center justify-center gap-2">
                        <i data-lucide="check-circle" class="icon-sm"></i>
                        Active (<span x-text="filteredSources.length"></span>)
                    </button>
                    <button @click="switchToAvailableTab()" :class="channelTab === 'available' ? 'tab-btn active' : 'tab-btn opacity-50'" class="flex-1 flex items-center justify-center gap-2">
                        <i data-lucide="list" class="icon-sm"></i>
                        All Subscribed (<span x-text="filteredTelegramChannels.length"></span>)
                    </button>
                </div>

                <!-- Search and Refresh -->
                <div class="flex gap-2 mb-3">
                    <input x-model="channelSearchQuery" 
                        type="text" 
                        placeholder="Search channels..." 
                        class="input-field flex-1 text-xs" 
                        @input="filterChannels()">
                    <button @click="loadTelegramChannels(true)" 
                        x-show="channelTab === 'available'"
                        :disabled="loadingChannels"
                        class="btn text-xs px-3 whitespace-nowrap flex items-center gap-2">
                        <i :data-lucide="loadingChannels ? 'loader-2' : 'refresh-cw'" :class="{'animate-spin': loadingChannels}" class="icon-sm"></i>
                        <span x-text="loadingChannels ? 'LOADING...' : 'REFRESH'"></span>
                    </button>
                </div>

                <!-- Active Sources -->
                <div x-show="channelTab === 'active'" class="space-y-2" style="max-height: 600px; overflow-y: auto;">
                    <template x-for="source in filteredSources" :key="source.username">
                        <div class="border border-glow p-3 flex justify-between items-center gap-4">
                            <div class="flex-1">
                                <div class="font-bold">@<span x-text="source.username"></span></div>
                                <div class="text-xs opacity-60 persian-text" x-text="source.title || 'NO TITLE'"></div>
                                <div class="text-xs opacity-40" x-text="(source.participants_count || 0) + ' members'"></div>
                            </div>
                            <button @click="removeSource(source.username)" class="btn btn-danger btn-sm text-xs px-3 flex items-center gap-1">
                                <i data-lucide="x" class="icon-sm"></i>
                                REMOVE
                            </button>
                        </div>
                    </template>
                    <div x-show="filteredSources.length === 0 && sources.length > 0" class="text-center opacity-50 py-8">
                        NO MATCHES FOUND
                    </div>
                    <div x-show="sources.length === 0" class="text-center opacity-50 py-8">
                        NO SOURCES CONFIGURED
                    </div>
                </div>

                <!-- All Telegram Channels -->
                <div x-show="channelTab === 'available'" class="space-y-2" style="max-height: 600px; overflow-y: auto;">
                    <template x-for="channel in filteredTelegramChannels" :key="channel.id">
                        <div class="border border-glow p-3 flex justify-between items-center gap-4">
                            <div class="flex-1">
                                <div class="font-bold">@<span x-text="channel.username"></span></div>
                                <div class="text-xs opacity-60 persian-text" x-text="channel.title"></div>
                                <div class="text-xs opacity-40" x-text="channel.participants_count.toLocaleString() + ' members'"></div>
                            </div>
                            <button @click="addSourceByName(channel.username)" 
                                :disabled="isSourceActive(channel.username)"
                                :class="isSourceActive(channel.username) ? 'opacity-30' : ''"
                                class="btn btn-sm text-xs px-3 flex items-center gap-1">
                                <i :data-lucide="isSourceActive(channel.username) ? 'check' : 'plus'" class="icon-sm"></i>
                                <span x-text="isSourceActive(channel.username) ? 'ADDED' : 'ADD'"></span>
                            </button>
                        </div>
                    </template>
                    <div x-show="filteredTelegramChannels.length === 0 && telegramChannels.length > 0" class="text-center opacity-50 py-8">
                        NO MATCHES FOUND
                    </div>
                    <div x-show="telegramChannels.length === 0" class="text-center opacity-50 py-8">
                        <div x-show="!status.telegram_connected">TELEGRAM NOT CONNECTED</div>
                        <div x-show="status.telegram_connected">NO CHANNELS FOUND</div>
                    </div>
                </div>
            </div>

            <!-- Situation Brief -->
            <div class="glass-panel p-6">
                <div class="section-title flex items-center gap-2">
                    <i data-lucide="file-text" class="icon"></i>
                    Situation Brief
                </div>
                
                <!-- Tab Toggle -->
                <div class="flex gap-2 mb-4">
                    <button @click="briefTab = 'current'" :class="briefTab === 'current' ? 'tab-btn active' : 'tab-btn opacity-50'" class="flex-1 flex items-center justify-center gap-2">
                        <i data-lucide="file-text" class="icon-sm"></i>
                        Current
                    </button>
                    <button @click="briefTab = 'character'" :class="briefTab === 'character' ? 'tab-btn active' : 'tab-btn opacity-50'" class="flex-1 flex items-center justify-center gap-2">
                        <i data-lucide="user" class="icon-sm"></i>
                        Character
                    </button>
                    <button @click="briefTab = 'emoji'" :class="briefTab === 'emoji' ? 'tab-btn active' : 'tab-btn opacity-50'" class="flex-1 flex items-center justify-center gap-2">
                        <i data-lucide="smile" class="icon-sm"></i>
                        Emoji
                    </button>
                </div>

                <!-- Current State Tab -->
                <div x-show="briefTab === 'current'" class="space-y-2">
                    <div class="text-xs opacity-70 mb-1">CURRENT SITUATION BRIEF:</div>
                    <textarea x-model="currentState" @input="stateChanged = true" class="w-full p-4 border border-terminal/30 rounded-lg text-bright persian-text resize-none focus:border-terminal focus:outline-none text-xs" style="height: 576px; background: rgba(15, 23, 42, 0.4);" placeholder="وضعیت فعلی خبری را وارد کنید..."></textarea>
                    <button @click="saveCurrentState()" :disabled="!stateChanged" class="btn w-full flex items-center justify-center gap-2">
                        <i :data-lucide="stateChanged ? 'save' : 'check'" class="icon-sm"></i>
                        <span x-text="stateChanged ? 'SAVE CHANGES' : 'NO CHANGES'"></span>
                    </button>
                </div>

                <!-- Character Config Tab -->
                <div x-show="briefTab === 'character'" class="space-y-2">
                    <div class="text-xs opacity-70 mb-1">CORE CHARACTERISTICS:</div>
                    <textarea x-model="characterConfig" @input="characterChanged = true"
                        placeholder="Enter channel personality and writing style..."
                        class="w-full p-4 border border-terminal/30 rounded-lg text-bright persian-text resize-none focus:border-terminal focus:outline-none text-xs" style="height: 576px; background: rgba(15, 23, 42, 0.4);"></textarea>
                    <button @click="saveCharacterConfig()" :disabled="!characterChanged" class="btn w-full flex items-center justify-center gap-2">
                        <i :data-lucide="characterChanged ? 'save' : 'check'" class="icon-sm"></i>
                        <span x-text="characterChanged ? 'SAVE CHANGES' : 'NO CHANGES'"></span>
                    </button>
                </div>

                <!-- Emoji Config Tab -->
                <div x-show="briefTab === 'emoji'" class="space-y-2">
                    <div class="text-xs opacity-70 mb-1">EMOJI COUNT (HIGH NEWS):</div>
                    <input x-model.number="emojiCount" @input="emojiChanged = true"
                        class="w-full p-3 border border-terminal/30 rounded-lg text-bright text-xs focus:border-terminal focus:outline-none" style="background: rgba(15, 23, 42, 0.4);" type="number" min="0" max="10">
                    <div class="text-xs opacity-70 mb-1 mt-4">EMOJI SELECTION GUIDELINES:</div>
                    <textarea x-model="emojiGuidelines" @input="emojiChanged = true"
                        placeholder="Explain how emojis should be selected..."
                        class="w-full p-4 border border-terminal/30 rounded-lg text-bright persian-text resize-none focus:border-terminal focus:outline-none text-xs" style="height: 520px; background: rgba(15, 23, 42, 0.4);"></textarea>
                    <button @click="saveEmojiConfig()" :disabled="!emojiChanged" class="btn w-full flex items-center justify-center gap-2">
                        <i :data-lucide="emojiChanged ? 'save' : 'check'" class="icon-sm"></i>
                        <span x-text="emojiChanged ? 'SAVE CHANGES' : 'NO CHANGES'"></span>
                    </button>
                </div>
            </div>
        </div>

        <!-- Message Queue Row -->
        <div class="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-4">
            <!-- High Priority Queue -->
            <div class="glass-panel p-6">
                <div class="section-title flex justify-between items-center mb-3">
                    <span class="flex items-center gap-2">
                        <i data-lucide="alert-circle" class="icon"></i>
                        High Priority
                    </span>
                    <div class="flex items-center gap-2">
                        <span class="priority-badge high" x-text="highQueue.length"></span>
                        <button @click="clearHighQueue()" :disabled="highQueue.length === 0" class="btn text-xs px-2 py-1">
                            <i data-lucide="trash-2" class="icon-sm"></i>
                        </button>
                    </div>
                </div>
                <div class="space-y-2 max-h-[600px] overflow-y-auto scrollbar">
                    <template x-for="msg in highQueue" :key="msg.id">
                        <div class="message-card high">
                            <div class="flex justify-between items-start mb-2">
                                <div class="font-semibold persian-text text-bright flex-1" x-text="msg.title"></div>
                                <div class="flex items-center gap-2 ml-2">
                                    <i @click="demoteMessage(msg.id, 'high')" data-lucide="chevron-down" class="priority-action-icon text-red-400" title="Demote to Medium"></i>
                                </div>
                            </div>
                            <div class="text-xs text-muted mb-2"><span x-text="msg.source"></span></div>
                            <div class="text-xs text-muted flex justify-between items-center">
                                <span x-text="new Date(msg.timestamp).toLocaleString('en-US', {timeZone: 'Asia/Tehran', hour: '2-digit', minute: '2-digit', month: 'short', day: 'numeric'})"></span>
                                <span x-show="msg.triage_time" class="text-blue-400" x-text="'⚡ ' + msg.triage_time + 'ms'"></span>
                            </div>
                        </div>
                    </template>
                    <div x-show="highQueue.length === 0" class="text-center text-muted py-12">
                        No high priority messages
                    </div>
                </div>
            </div>

            <!-- Medium Priority Queue -->
            <div class="glass-panel p-6">
                <div class="section-title flex justify-between items-center mb-3">
                    <span class="flex items-center gap-2">
                        <i data-lucide="alert-triangle" class="icon"></i>
                        Medium Priority
                    </span>
                    <div class="flex items-center gap-2">
                        <span class="priority-badge medium" x-text="mediumQueue.length"></span>
                        <button @click="clearMediumQueue()" :disabled="mediumQueue.length === 0" class="btn text-xs px-2 py-1">
                            <i data-lucide="trash-2" class="icon-sm"></i>
                        </button>
                    </div>
                </div>
                <div class="space-y-2 max-h-[600px] overflow-y-auto scrollbar">
                    <template x-for="msg in mediumQueue" :key="msg.id">
                        <div class="message-card medium">
                            <div class="flex justify-between items-start mb-2">
                                <div class="font-semibold persian-text text-bright flex-1" x-text="msg.title"></div>
                                <div class="flex flex-col items-center gap-1 ml-2">
                                    <i @click="promoteMessage(msg.id, 'medium')" data-lucide="chevron-up" class="priority-action-icon text-green-400" title="Promote to High"></i>
                                    <i @click="demoteMessage(msg.id, 'medium')" data-lucide="chevron-down" class="priority-action-icon text-red-400" title="Demote to Low"></i>
                                </div>
                            </div>
                            <div class="text-xs text-muted mb-2"><span x-text="msg.source"></span></div>
                            <div class="text-xs text-muted flex justify-between items-center">
                                <span x-text="new Date(msg.timestamp).toLocaleString('en-US', {timeZone: 'Asia/Tehran', hour: '2-digit', minute: '2-digit', month: 'short', day: 'numeric'})"></span>
                                <span x-show="msg.triage_time" class="text-blue-400" x-text="'⚡ ' + msg.triage_time + 'ms'"></span>
                            </div>
                        </div>
                    </template>
                    <div x-show="mediumQueue.length === 0" class="text-center text-muted py-12">
                        No medium priority messages
                    </div>
                </div>
            </div>

            <!-- Low Priority Messages -->
            <div class="glass-panel p-6">
                <div class="section-title flex justify-between items-center mb-3">
                    <span class="flex items-center gap-2">
                        <i data-lucide="info" class="icon"></i>
                        Low Priority
                    </span>
                    <div class="flex items-center gap-2">
                        <span class="priority-badge low" x-text="lowQueue.length"></span>
                        <button @click="clearLowQueue()" :disabled="lowQueue.length === 0" class="btn text-xs px-2 py-1">
                            <i data-lucide="trash-2" class="icon-sm"></i>
                        </button>
                    </div>
                </div>
                <div class="space-y-2 max-h-[600px] overflow-y-auto scrollbar">
                    <template x-for="msg in lowQueue" :key="msg.id">
                        <div class="message-card low">
                            <div class="flex justify-between items-start mb-2">
                                <div class="font-semibold persian-text text-bright flex-1" x-text="msg.title"></div>
                                <div class="flex flex-col items-center gap-1 ml-2">
                                    <i @click="promoteMessage(msg.id, 'low')" data-lucide="chevron-up" class="priority-action-icon text-green-400" title="Promote to Medium"></i>
                                    <i @click="demoteMessage(msg.id, 'low')" data-lucide="x" class="priority-action-icon text-red-400" title="Remove from Queue"></i>
                                </div>
                            </div>
                            <div class="text-xs text-muted mb-2"><span x-text="msg.source"></span></div>
                            <div class="text-xs text-muted flex justify-between items-center">
                                <span x-text="new Date(msg.timestamp).toLocaleString('en-US', {timeZone: 'Asia/Tehran', hour: '2-digit', minute: '2-digit', month: 'short', day: 'numeric'})"></span>
                                <span x-show="msg.triage_time" class="text-blue-400" x-text="'⚡ ' + msg.triage_time + 'ms'"></span>
                            </div>
                        </div>
                    </template>
                    <div x-show="lowQueue.length === 0" class="text-center text-muted py-12">
                        No low priority messages
                    </div>
                </div>
            </div>
        </div>

        <!-- Published Posts (24h) -->
        <div class="grid grid-cols-1 mb-4">
            <div class="glass-panel p-6">
                <div class="section-title flex justify-between items-center">
                    <span class="flex items-center gap-2">
                        <i data-lucide="send" class="icon"></i>
                        Published Posts (24 Hours)
                    </span>
                    <span class="priority-badge high" style="background: rgba(16, 185, 129, 0.15); color: #6ee7b7;" x-text="publishedPosts.length"></span>
                </div>
                <div class="space-y-2 scrollbar" style="max-height: 600px; overflow-y: auto;">
                    <template x-for="post in publishedPosts" :key="post.id">
                        <div class="message-card published">
                            <div class="flex justify-between items-start mb-2">
                                <span class="font-semibold text-green-400" x-text="post.type.toUpperCase()"></span>
                                <div class="flex items-center gap-2">
                                    <span class="text-xs text-muted" x-text="new Date(post.timestamp).toLocaleString('en-US', {timeZone: 'Asia/Tehran', hour: '2-digit', minute: '2-digit', month: 'short', day: 'numeric'})"></span>
                                    <button @click="deletePost(post.id)" class="text-red-400 hover:text-red-300 text-xs">
                                        <i data-lucide="trash-2" class="icon-sm"></i>
                                    </button>
                                </div>
                            </div>
                            <div class="text-sm persian-text mb-2" style="line-height: 1.6;" x-html="formatTelegramText(post.content)"></div>
                            <div class="text-xs text-muted" x-show="post.message_id">
                                Msg ID: <span x-text="post.message_id"></span>
                            </div>
                        </div>
                    </template>
                    <div x-show="publishedPosts.length === 0" class="text-center text-muted py-12">
                        No posts published in last 24 hours
                    </div>
                </div>
            </div>
        </div>

        <!-- Bottom Row: Advanced Config + Logs -->
        <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
            
            <!-- Advanced Settings -->
            <div class="glass-panel p-6">
                <div class="section-title flex items-center gap-2">
                    <i data-lucide="sliders" class="icon"></i>
                    Advanced Settings
                </div>
                <div x-show="config" class="space-y-3 text-xs max-h-96 overflow-y-auto scrollbar">
                    <div>
                        <div class="opacity-70 mb-1">TRIAGE MODEL:</div>
                        <input x-model="config.gpt_models.triage_model" @input="configChanged = true" 
                            class="input-field w-full text-xs" type="text">
                    </div>
                    
                    <div>
                        <div class="opacity-70 mb-1">CONTENT MODEL:</div>
                        <input x-model="config.gpt_models.content_model" @input="configChanged = true" 
                            class="input-field w-full text-xs" type="text">
                    </div>
                    
                    <div>
                        <div class="opacity-70 mb-1">TIMEZONE:</div>
                        <input x-model="config.timezone" @input="configChanged = true" 
                            class="input-field w-full text-xs" type="text">
                    </div>
                    
                    <div>
                        <div class="opacity-70 mb-1">TARGET CHANNEL:</div>
                        <input x-model="config.target_channel" @input="configChanged = true" 
                            class="input-field w-full text-xs" type="text" placeholder="hamidspulse">
                    </div>
                    
                    <div>
                        <div class="opacity-70 mb-1">MAX QUEUE ITEMS:</div>
                        <input x-model.number="config.rate_limits.max_queue_items" @input="configChanged = true" 
                            class="input-field w-full text-xs" type="number">
                    </div>
                    
                    <div class="flex gap-2 mt-2">
                        <button @click="saveConfig()" :disabled="!configChanged" class="btn flex-1 text-xs flex items-center justify-center gap-1">
                            <i :data-lucide="configChanged ? 'save' : 'check'" class="icon-sm"></i>
                            <span x-text="configChanged ? 'SAVE' : 'NO CHANGES'"></span>
                        </button>
                        <button @click="loadConfig()" class="btn flex-1 text-xs flex items-center justify-center gap-1">
                            <i data-lucide="refresh-cw" class="icon-sm"></i>
                            RELOAD
                        </button>
                    </div>
                </div>
            </div>

            <!-- System Logs -->
            <div class="glass-panel p-6 lg:col-span-2" style="background: rgba(0, 0, 0, 0.3);">
                <div class="section-title flex items-center gap-2">
                    <i data-lucide="terminal" class="icon"></i>
                    System Logs
                </div>
                <div class="space-y-1.5 max-h-96 overflow-y-auto scrollbar">
                    <template x-for="(log, index) in logs" :key="index">
                        <div :class="{
                            'log-info': log.level === 'info',
                            'log-success': log.level === 'success',
                            'log-warning': log.level === 'warning',
                            'log-error': log.level === 'error'
                        }" class="log-terminal flex justify-between items-start gap-3">
                            <span class="flex-1" x-text="log.message"></span>
                            <span class="opacity-60 whitespace-nowrap" style="font-size: 1rem;" x-text="formatTime(log.timestamp)"></span>
                        </div>
                    </template>
                    <div x-show="logs.length === 0" class="text-center py-12" style="font-family: 'VT323', monospace; font-size: 1.3rem; color: #00ff41; opacity: 0.5;">
                        NO LOGS AVAILABLE
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        function app() {
            return {
                ws: null,
                reconnectAttempts: 0,
                status: {
                    is_running: false,
                    telegram_connected: false,
                    scheduler_running: false,
                    source_channels_count: 0,
                    websocket_connected: false
                },
                sources: [],
                telegramChannels: [],
                filteredSources: [],
                filteredTelegramChannels: [],
                channelSearchQuery: '',
                channelTab: 'active',
                loadingChannels: false,
                replayingMessages: false,
                initializingSituation: false,
                startingApp: false,
                stoppingApp: false,
                clearingQueue: false,
                publishingDigest: false,
                
                saveLoadingStates() {
                    localStorage.setItem('app_loading_states', JSON.stringify({
                        startingApp: this.startingApp,
                        stoppingApp: this.stoppingApp,
                        replayingMessages: this.replayingMessages,
                        initializingSituation: this.initializingSituation,
                        publishingDigest: this.publishingDigest,
                        clearingQueue: this.clearingQueue
                    }));
                },
                replayMinutes: 10,
                newSource: '',
                currentState: '',
                newState: '',
                stateChanged: false,
                logs: [],
                config: {
                    gpt_models: { triage_model: '', content_model: '' },
                    timezone: '',
                    target_channel: '',
                    rate_limits: { max_queue_items: 50 }
                },
                configChanged: false,
                briefTab: 'current',
                characterConfig: '',
                characterChanged: false,
                emojiCount: 3,
                emojiGuidelines: '',
                emojiChanged: false,
                highQueue: [],
                mediumQueue: [],
                lowQueue: [],
                publishedPosts: [],

                restoreLoadingStates() {
                    // Clear all loading states on page load to prevent stuck states
                    localStorage.removeItem('app_loading_states');
                    this.startingApp = false;
                    this.stoppingApp = false;
                    this.replayingMessages = false;
                    this.initializingSituation = false;
                    this.publishingDigest = false;
                    this.clearingQueue = false;
                },

                init() {
                    this.restoreLoadingStates();
                    this.connectWebSocket();
                    this.loadStatus();
                    this.loadState();
                    this.loadSources();
                    this.loadQueue();
                    this.loadConfig();
                    this.loadPublishedPosts();
                    this.loadTelegramChannels(false); // Load cached channels on init
                    
                    setInterval(() => this.loadStatus(), 3000);
                    setInterval(() => this.loadQueue(), 5000);
                    setInterval(() => this.loadState(), 10000);
                    setInterval(() => this.loadPublishedPosts(), 30000);
                },

                switchToAvailableTab() {
                    this.channelTab = 'available';
                    // Channels already loaded from cache on init
                },

                filterChannels() {
                    const query = this.channelSearchQuery.toLowerCase();
                    
                    // Filter and sort active sources
                    this.filteredSources = this.sources
                        .filter(s => {
                            const username = (s.username || '').toLowerCase();
                            const title = (s.title || '').toLowerCase();
                            return username.includes(query) || title.includes(query);
                        })
                        .sort((a, b) => (b.participants_count || 0) - (a.participants_count || 0));
                    
                    // Filter and sort telegram channels
                    this.filteredTelegramChannels = this.telegramChannels
                        .filter(c => {
                            const username = (c.username || '').toLowerCase();
                            const title = (c.title || '').toLowerCase();
                            return username.includes(query) || title.includes(query);
                        })
                        .sort((a, b) => (b.participants_count || 0) - (a.participants_count || 0));
                },

                connectWebSocket() {
                    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                    this.ws = new WebSocket(`${protocol}//${window.location.host}/ws`);
                    
                    this.ws.onopen = () => {
                        this.reconnectAttempts = 0;
                        this.addLog('WebSocket connected', 'success');
                    };

                    this.ws.onmessage = (event) => {
                        const data = JSON.parse(event.data);
                        this.handleWebSocketMessage(data);
                    };

                    this.ws.onerror = (error) => {
                        this.addLog('WebSocket error', 'error');
                    };

                    this.ws.onclose = () => {
                        this.status.websocket_connected = false;
                        this.addLog('WebSocket disconnected. Reconnecting...', 'warning');
                        this.reconnectAttempts++;
                        const delay = Math.min(1000 * this.reconnectAttempts, 10000);
                        setTimeout(() => this.connectWebSocket(), delay);
                    };
                },

                handleWebSocketMessage(data) {
                    if (data.type === 'heartbeat' || data.type === 'connected') {
                        this.status = data.data.status;
                    } else if (data.type === 'status') {
                        this.status = data.data;
                    } else if (data.type === 'sources_updated') {
                        this.sources = data.data;
                    } else if (data.type === 'state_updated') {
                        this.currentState = data.data;
                    } else if (data.type === 'log') {
                        this.addLog(data.data.message, data.data.level);
                    } else if (data.type === 'config_updated') {
                        this.loadConfig();
                    }
                },

                async loadStatus() {
                    try {
                        const res = await fetch('/api/status');
                        this.status = await res.json();
                    } catch (e) {
                        console.error('Failed to load status:', e);
                    }
                },

                async startApp() {
                    if (this.startingApp) return;
                    
                    try {
                        this.startingApp = true;
                        this.saveLoadingStates();
                        const res = await fetch('/api/start', { method: 'POST' });
                        const data = await res.json();
                        if (!data.success) this.addLog(data.error, 'error');
                    } catch (e) {
                        this.addLog('Failed to start system', 'error');
                        console.error('Start error:', e);
                    } finally {
                        setTimeout(() => {
                            this.startingApp = false;
                            this.saveLoadingStates();
                        }, 2000);
                    }
                },

                async stopApp() {
                    this.stoppingApp = true;
                    this.saveLoadingStates();
                    const res = await fetch('/api/stop', { method: 'POST' });
                    const data = await res.json();
                    if (!data.success) this.addLog(data.error, 'error');
                },

                async replayPastMessages() {
                    if (this.replayingMessages) return;
                    
                    try {
                        this.replayingMessages = true;
                        this.saveLoadingStates();
                        const res = await fetch('/api/replay', { 
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ minutes: this.replayMinutes })
                        });
                        const data = await res.json();
                        if (data.success) {
                            this.loadQueue(); // Refresh queues to show new messages
                        } else {
                            this.addLog(data.error || 'Replay failed', 'error');
                        }
                    } catch (e) {
                        this.addLog('Failed to replay messages', 'error');
                        console.error('Replay error:', e);
                    } finally {
                        this.replayingMessages = false;
                    }
                },

                async loadState() {
                    try {
                        const res = await fetch('/api/state');
                        const data = await res.json();
                        this.currentState = data.state || '';
                    } catch (e) {
                        console.error('Failed to load state:', e);
                    }
                },

                async loadSources() {
                    try {
                        const res = await fetch('/api/sources');
                        const data = await res.json();
                        this.sources = data.sources;
                        this.filterChannels();
                    } catch (e) {
                        console.error('Failed to load sources:', e);
                    }
                },

                async addSource() {
                    if (!this.newSource.trim()) return;
                    const res = await fetch('/api/sources/add', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ username: this.newSource })
                    });
                    const data = await res.json();
                    if (data.success) {
                        this.newSource = '';
                        this.loadSources();
                    } else {
                        this.addLog(data.error, 'error');
                    }
                },

                async removeSource(username) {
                    const res = await fetch('/api/sources/remove', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ username })
                    });
                    const data = await res.json();
                    if (data.success) {
                        this.loadSources();
                    } else {
                        this.addLog(data.error, 'error');
                    }
                },

                async loadTelegramChannels(refresh = false) {
                    if (this.loadingChannels) return;
                    
                    try {
                        this.loadingChannels = true;
                        if (refresh) {
                            this.addLog('Refreshing Telegram channels...', 'info');
                        }
                        const res = await fetch(`/api/telegram/channels?refresh=${refresh}`);
                        const data = await res.json();
                        if (data.success) {
                            this.telegramChannels = data.channels;
                            this.filterChannels();
                            const source = data.from_cache ? '(cached)' : '(fresh)';
                            if (refresh || data.channels.length > 0) {
                                this.addLog(`Found ${data.channels.length} channels ${source}`, 'success');
                            }
                        } else if (data.error) {
                            this.addLog(data.error, 'error');
                        }
                    } catch (e) {
                        this.addLog('Failed to load Telegram channels', 'error');
                        console.error('Failed to load Telegram channels:', e);
                    } finally {
                        this.loadingChannels = false;
                    }
                },

                async addSourceByName(username) {
                    if (!username) return;
                    const res = await fetch('/api/sources/add', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ username: username })
                    });
                    const data = await res.json();
                    if (data.success) {
                        this.loadSources();
                        this.loadTelegramChannels();
                    } else {
                        this.addLog(data.error, 'error');
                    }
                },

                isSourceActive(username) {
                    return this.sources.some(s => s.username === username || s.username === username.replace('@', ''));
                },

                async loadState() {
                    try {
                        const res = await fetch('/api/state');
                        const data = await res.json();
                        this.currentState = data.state;
                    } catch (e) {
                        console.error('Failed to load state:', e);
                    }
                },

                async initializeSituation() {
                    if (this.initializingSituation) return;
                    try {
                        this.initializingSituation = true;
                        this.saveLoadingStates();
                        this.addLog('🌍 Initializing situation from past 24 hours...', 'info');
                        const res = await fetch('/api/initialize-situation', {
                            method: 'POST'
                        });
                        const data = await res.json();
                        if (data.success) {
                            this.addLog('✅ Situation initialized successfully!', 'success');
                            await this.loadState();
                        } else {
                            this.addLog(data.error || 'Failed to initialize situation', 'error');
                        }
                    } catch (e) {
                        this.addLog('Failed to initialize situation', 'error');
                        console.error('Initialize situation error:', e);
                    } finally {
                        this.initializingSituation = false;
                    }
                },

                async saveCurrentState() {
                    if (!this.currentState.trim() || !this.stateChanged) return;
                    const res = await fetch('/api/state/set', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ state: this.currentState })
                    });
                    const data = await res.json();
                    if (data.success) {
                        this.stateChanged = false;
                        this.addLog('Situation brief updated', 'success');
                    } else {
                        this.addLog(data.error, 'error');
                    }
                },

                async loadQueue() {
                    try {
                        const res = await fetch('/api/queue');
                        const data = await res.json();
                        if (data.success) {
                            this.highQueue = data.high || [];
                            this.mediumQueue = data.medium || [];
                            this.lowQueue = data.low || [];
                        }
                    } catch (e) {
                        console.error('Failed to load queue:', e);
                    }
                },
                
                async loadPublishedPosts() {
                    try {
                        const res = await fetch('/api/posts/24h');
                        const data = await res.json();
                        if (data.success) {
                            this.publishedPosts = data.posts || [];
                            console.log('Published posts loaded:', this.publishedPosts.length);
                        } else {
                            console.error('Failed to load posts:', data.error);
                        }
                    } catch (e) {
                        console.error('Failed to load published posts:', e);
                    }
                },
                
                async deletePost(postId, messageId) {
                    if (!confirm('Delete this post? This will remove it from the database' + (messageId ? ' and attempt to delete from Telegram.' : '.'))) {
                        return;
                    }
                    
                    try {
                        const res = await fetch('/api/posts/delete', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ post_id: postId, message_id: messageId })
                        });
                        const data = await res.json();
                        if (data.success) {
                            this.addLog(data.message, 'success');
                            await this.loadPublishedPosts();
                        } else {
                            this.addLog(data.error || 'Failed to delete post', 'error');
                        }
                    } catch (e) {
                        this.addLog('Failed to delete post', 'error');
                        console.error('Delete post error:', e);
                    }
                },

                async saveCharacterConfig() {
                    if (!this.characterChanged) return;
                    try {
                        this.config.content_style.core_characteristics = this.characterConfig.split('\\n').filter(line => line.trim());
                        
                        const res = await fetch('/api/config', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ config: this.config })
                        });
                        const data = await res.json();
                        if (data.success) {
                            this.characterChanged = false;
                            this.addLog('Character config saved', 'success');
                        } else {
                            this.addLog(data.error, 'error');
                        }
                    } catch (e) {
                        this.addLog('Failed to save character config', 'error');
                    }
                },

                async saveEmojiConfig() {
                    if (!this.emojiChanged) return;
                    try {
                        this.config.content_style.emoji_logic.high_news_emoji_count = parseInt(this.emojiCount);
                        this.config.content_style.emoji_logic.emoji_guidelines = this.emojiGuidelines;
                        
                        const res = await fetch('/api/config', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ config: this.config })
                        });
                        const data = await res.json();
                        if (data.success) {
                            this.emojiChanged = false;
                            this.addLog('Emoji config saved', 'success');
                        } else {
                            this.addLog(data.error, 'error');
                        }
                    } catch (e) {
                        this.addLog('Failed to save emoji config', 'error');
                    }
                },

                async loadConfig() {
                    try {
                        const res = await fetch('/api/config');
                        const data = await res.json();
                        if (data.success) {
                            this.config = data.config;
                            this.configChanged = false;
                            
                            // Load character and emoji configs
                            if (this.config.content_style?.core_characteristics) {
                                this.characterConfig = this.config.content_style.core_characteristics.join('\\n');
                            }
                            if (this.config.content_style?.emoji_logic) {
                                this.emojiCount = this.config.content_style.emoji_logic.high_news_emoji_count || 3;
                                this.emojiGuidelines = this.config.content_style.emoji_logic.emoji_guidelines || '';
                            }
                            this.characterChanged = false;
                            this.emojiChanged = false;
                        }
                    } catch (e) {
                        console.error('Failed to load config:', e);
                    }
                },

                async saveConfig() {
                    if (!this.configChanged) return;
                    
                    try {
                        const res = await fetch('/api/config', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ config: this.config })
                        });
                        const data = await res.json();
                        if (data.success) {
                            this.configChanged = false;
                            this.addLog('Config saved successfully', 'success');
                            await this.loadConfig(); // Reload config to ensure consistency
                        } else {
                            this.addLog(data.error, 'error');
                        }
                    } catch (e) {
                        this.addLog('Failed to save config', 'error');
                    }
                },

                async clearQueue() {
                    if (this.clearingQueue) return;
                    
                    try {
                        this.clearingQueue = true;
                        const res = await fetch('/api/queue/clear', { method: 'POST' });
                        const data = await res.json();
                        if (data.success) {
                            this.addLog(data.message, 'success');
                            this.loadQueue(); // Refresh the queues display
                        } else {
                            this.addLog(data.error, 'error');
                        }
                    } catch (e) {
                        this.addLog('Failed to clear queue', 'error');
                        console.error('Clear queue error:', e);
                    } finally {
                        this.clearingQueue = false;
                    }
                },
                
                async clearHighQueue() {
                    if (!confirm('Clear all high priority messages?')) return;
                    try {
                        const res = await fetch('/api/queue/clear-high', { method: 'POST' });
                        const data = await res.json();
                        if (data.success) {
                            this.addLog(data.message, 'success');
                            this.loadQueue();
                        } else {
                            this.addLog(data.error, 'error');
                        }
                    } catch (e) {
                        this.addLog('Failed to clear high queue', 'error');
                    }
                },
                
                async clearLowQueue() {
                    if (!confirm('Clear all low priority messages?')) return;
                    try {
                        const res = await fetch('/api/queue/clear-low', { method: 'POST' });
                        const data = await res.json();
                        if (data.success) {
                            this.addLog(data.message, 'success');
                            this.loadQueue();
                        } else {
                            this.addLog(data.error, 'error');
                        }
                    } catch (e) {
                        this.addLog('Failed to clear low queue', 'error');
                    }
                },
                
                formatTelegramText(text) {
                    if (!text) return '';
                    
                    console.log('[FORMAT] Original text:', text.substring(0, 100));
                    
                    // Escape HTML first
                    let formatted = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
                    
                    // Parse Telegram markdown links [text](url)
                    formatted = formatted.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" class="text-blue-400 hover:text-blue-300 underline">$1</a>');
                    
                    // Parse **bold**
                    formatted = formatted.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
                    
                    // Parse __bold__ (alternative)
                    formatted = formatted.replace(/__([^_]+)__/g, '<strong>$1</strong>');
                    
                    // Parse *italic* or _italic_
                    formatted = formatted.replace(/\*([^*]+)\*/g, '<em>$1</em>');
                    formatted = formatted.replace(/_([^_]+)_/g, '<em>$1</em>');
                    
                    // Parse ~~strikethrough~~
                    formatted = formatted.replace(/~~([^~]+)~~/g, '<s>$1</s>');
                    
                    // Parse `code`
                    formatted = formatted.replace(/`([^`]+)`/g, '<code class="bg-gray-700 px-1 rounded">$1</code>');
                    
                    // Convert line breaks to <br>
                    formatted = formatted.replace(/\n/g, '<br>');
                    
                    console.log('[FORMAT] Formatted text:', formatted.substring(0, 100));
                    return formatted;
                },
                
                async promoteMessage(messageId, currentBucket) {
                    try {
                        const res = await fetch('/api/message/promote', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ message_id: messageId, current_bucket: currentBucket })
                        });
                        const data = await res.json();
                        if (data.success) {
                            await this.loadQueue();
                            await this.loadPublishedPosts();
                        } else {
                            alert('Failed to promote: ' + (data.error || 'Unknown error'));
                        }
                    } catch (e) {
                        console.error('Failed to promote message:', e);
                        alert('Failed to promote message');
                    }
                },
                
                async demoteMessage(messageId, currentBucket) {
                    try {
                        const res = await fetch('/api/message/demote', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ message_id: messageId, current_bucket: currentBucket })
                        });
                        const data = await res.json();
                        if (data.success) {
                            await this.loadQueue();
                        } else {
                            alert('Failed to demote: ' + (data.error || 'Unknown error'));
                        }
                    } catch (e) {
                        console.error('Failed to demote message:', e);
                        alert('Failed to demote message');
                    }
                },
                
                async clearMediumQueue() {
                    if (!confirm('Clear all medium priority messages?')) return;
                    try {
                        const res = await fetch('/api/queue/clear-medium', { method: 'POST' });
                        const data = await res.json();
                        if (data.success) {
                            this.addLog(data.message, 'success');
                            this.loadQueue();
                        } else {
                            this.addLog(data.error, 'error');
                        }
                    } catch (e) {
                        this.addLog('Failed to clear medium queue', 'error');
                    }
                },

                addLog(message, level = 'info') {
                    // Prevent duplicate logs (same message within 1 second)
                    const now = new Date();
                    const recentDuplicate = this.logs.find(log => {
                        const logTime = new Date(log.timestamp);
                        const timeDiff = Math.abs(now - logTime);
                        return log.message === message && timeDiff < 1000;
                    });
                    
                    if (!recentDuplicate) {
                        this.logs.unshift({
                            message,
                            level,
                            timestamp: now.toISOString()
                        });
                        if (this.logs.length > 100) this.logs = this.logs.slice(0, 100);
                    }
                },

                formatTime(timestamp) {
                    const date = new Date(timestamp);
                    return date.toLocaleString('en-US', {
                        timeZone: 'Asia/Tehran',
                        hour: '2-digit',
                        minute: '2-digit',
                        second: '2-digit',
                        month: 'short',
                        day: 'numeric'
                    });
                }
            }
        }
        
        // Initialize Lucide icons after Alpine.js and DOM are ready
        document.addEventListener('alpine:init', () => {
            setTimeout(() => {
                lucide.createIcons();
            }, 100);
        });
        
        // Re-initialize icons when Alpine updates the DOM
        setInterval(() => {
            lucide.createIcons();
        }, 1000);
    </script>
</body>
</html>
        """
