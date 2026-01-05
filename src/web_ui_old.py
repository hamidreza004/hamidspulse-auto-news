import asyncio
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse
from typing import List
import json
from datetime import datetime
import yaml

logger = logging.getLogger(__name__)


class WebUI:
    def __init__(self, app_manager):
        self.app = FastAPI(title="Hamid's Pulse Auto News - Control Panel")
        self.app_manager = app_manager
        self.active_connections: List[WebSocket] = []
        self.connection_counter = 0
        
        self._setup_routes()
    
    def _setup_routes(self):
        @self.app.get("/", response_class=HTMLResponse)
        async def home():
            return self._get_html()
        
        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            await self._handle_websocket(websocket)
        
        @self.app.get("/api/status")
        async def get_status():
            status = self.app_manager.get_status()
            status['websocket_connected'] = len(self.active_connections) > 0
            return status
        
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
                return {"success": True, "message": "Config updated"}
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        @self.app.post("/api/start")
        async def start_app():
            try:
                await self.app_manager.start()
                await self._broadcast({"type": "status", "data": self.app_manager.get_status()})
                return {"success": True, "message": "Application started"}
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        @self.app.post("/api/stop")
        async def stop_app():
            try:
                await self.app_manager.stop()
                await self._broadcast({"type": "status", "data": self.app_manager.get_status()})
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
                return {"success": True, "message": f"Removed {username}"}
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        @self.app.get("/api/state")
        async def get_state():
            return {"state": self.app_manager.get_current_state()}
        
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
                return {"success": True, "message": "State updated"}
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        @self.app.post("/api/digest/trigger")
        async def trigger_digest():
            try:
                await self.app_manager.trigger_hourly_digest()
                await self._broadcast({"type": "log", "data": {"message": "Manual digest triggered", "level": "info"}})
                return {"success": True, "message": "Digest triggered"}
            except Exception as e:
                return {"success": False, "error": str(e)}
        
        @self.app.get("/api/channels/all")
        async def get_all_channels():
            try:
                all_channels = self.app_manager.list_source_channels()
                return {"success": True, "channels": all_channels}
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
                await asyncio.sleep(5)
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
    
    async def notify_log(self, message: str, level: str = "info"):
        await self._broadcast({
            "type": "log",
            "data": {
                "message": message,
                "level": level,
                "timestamp": datetime.now().isoformat()
            }
        })
    
    def _get_html(self) -> str:
        return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Hamid's Pulse - Control Panel</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js" defer></script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;700&display=swap" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/gh/rastikerdar/vazir-font@v30.1.0/dist/font-face.css" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: 'JetBrains Mono', monospace;
            background: #000000;
            color: #00ff00;
            overflow-x: hidden;
        }
        .persian-text { 
            font-family: 'Vazir', sans-serif !important;
            direction: rtl;
            text-align: right;
        }
        .glow { text-shadow: 0 0 10px #00ff00, 0 0 20px #00ff00; }
        .pulse-dot { animation: pulse 2s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
        .border-glow { box-shadow: 0 0 10px #00ff0033; }
        .scrollbar::-webkit-scrollbar { width: 8px; }
        .scrollbar::-webkit-scrollbar-track { background: #001100; }
        .scrollbar::-webkit-scrollbar-thumb { background: #00ff00; border-radius: 4px; }
        .grid-bg {
            background-image: 
                linear-gradient(#00ff0011 1px, transparent 1px),
                linear-gradient(90deg, #00ff0011 1px, transparent 1px);
            background-size: 20px 20px;
        }
    </style>
</head>
<body class="grid-bg min-h-screen">
    <div x-data="app()" x-init="init()" class="container mx-auto px-4 py-8 max-w-6xl">
        <!-- Header -->
        <div class="mb-8">
            <h1 class="text-4xl font-bold mb-2 bg-gradient-to-r from-purple-400 to-pink-600 bg-clip-text text-transparent">
                🔭 Hamid's Pulse Auto News
            </h1>
            <p class="text-slate-400">کنترل پنل اتوماسیون کانال خبری</p>
        </div>

        <!-- Status Card -->
        <div class="bg-white/10 backdrop-blur-lg rounded-2xl p-6 mb-6 border border-white/20 shadow-2xl">
            <div class="flex items-center justify-between mb-4">
                <h2 class="text-2xl font-bold">وضعیت سیستم</h2>
                <div class="flex items-center gap-2">
                    <div :class="status.is_running ? 'bg-green-500' : 'bg-red-500'" class="w-3 h-3 rounded-full animate-pulse"></div>
                    <span x-text="status.is_running ? 'در حال اجرا' : 'متوقف'" class="font-medium"></span>
                </div>
            </div>
            
            <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
                <div class="bg-white/5 p-4 rounded-xl">
                    <div class="text-slate-400 text-sm mb-1">تلگرام</div>
                    <div :class="status.telegram_connected ? 'text-green-400' : 'text-red-400'" class="font-bold" x-text="status.telegram_connected ? '✓ متصل' : '✗ قطع'"></div>
                </div>
                <div class="bg-white/5 p-4 rounded-xl">
                    <div class="text-slate-400 text-sm mb-1">زمان‌بندی</div>
                    <div :class="status.scheduler_running ? 'text-green-400' : 'text-red-400'" class="font-bold" x-text="status.scheduler_running ? '✓ فعال' : '✗ غیرفعال'"></div>
                </div>
                <div class="bg-white/5 p-4 rounded-xl">
                    <div class="text-slate-400 text-sm mb-1">کانال‌های منبع</div>
                    <div class="text-blue-400 font-bold" x-text="status.source_channels_count + ' کانال'"></div>
                </div>
            </div>

            <div class="flex gap-3">
                <button @click="startApp()" :disabled="status.is_running" 
                    class="bg-gradient-to-r from-green-500 to-emerald-600 hover:from-green-600 hover:to-emerald-700 disabled:from-slate-600 disabled:to-slate-700 disabled:cursor-not-allowed px-6 py-3 rounded-xl font-bold transition-all shadow-lg hover:shadow-xl">
                    ▶ شروع
                </button>
                <button @click="stopApp()" :disabled="!status.is_running"
                    class="bg-gradient-to-r from-red-500 to-rose-600 hover:from-red-600 hover:to-rose-700 disabled:from-slate-600 disabled:to-slate-700 disabled:cursor-not-allowed px-6 py-3 rounded-xl font-bold transition-all shadow-lg hover:shadow-xl">
                    ■ توقف
                </button>
                <button @click="triggerDigest()"
                    class="bg-gradient-to-r from-blue-500 to-cyan-600 hover:from-blue-600 hover:to-cyan-700 px-6 py-3 rounded-xl font-bold transition-all shadow-lg hover:shadow-xl">
                    🔄 خلاصه دستی
                </button>
            </div>
        </div>

        <!-- Sources Management -->
        <div class="bg-white/10 backdrop-blur-lg rounded-2xl p-6 mb-6 border border-white/20 shadow-2xl">
            <h2 class="text-2xl font-bold mb-4">مدیریت منابع</h2>
            
            <div class="flex gap-3 mb-4">
                <input x-model="newSource" @keyup.enter="addSource()" type="text" 
                    placeholder="@channel_username" 
                    class="flex-1 bg-white/10 border border-white/20 rounded-xl px-4 py-3 rtl-input focus:outline-none focus:ring-2 focus:ring-purple-500">
                <button @click="addSource()"
                    class="bg-gradient-to-r from-purple-500 to-pink-600 hover:from-purple-600 hover:to-pink-700 px-6 py-3 rounded-xl font-bold transition-all shadow-lg hover:shadow-xl">
                    + افزودن
                </button>
            </div>

            <div class="space-y-2 max-h-64 overflow-y-auto">
                <template x-for="source in sources" :key="source.username">
                    <div class="bg-white/5 p-4 rounded-xl flex items-center justify-between">
                        <div>
                            <div class="font-bold" x-text="'@' + source.username"></div>
                            <div class="text-sm text-slate-400" x-text="source.title || 'بدون عنوان'"></div>
                        </div>
                        <button @click="removeSource(source.username)"
                            class="bg-red-500/20 hover:bg-red-500 text-red-300 hover:text-white px-4 py-2 rounded-lg transition-all">
                            حذف
                        </button>
                    </div>
                </template>
                <div x-show="sources.length === 0" class="text-center text-slate-400 py-8">
                    هیچ منبعی اضافه نشده
                </div>
            </div>
        </div>

        <!-- State Management -->
        <div class="bg-white/10 backdrop-blur-lg rounded-2xl p-6 mb-6 border border-white/20 shadow-2xl">
            <h2 class="text-2xl font-bold mb-4">مدیریت حافظه (Situation Brief)</h2>
            
            <div class="mb-3">
                <button @click="showState = !showState"
                    class="text-purple-400 hover:text-purple-300 font-medium">
                    <span x-text="showState ? '▼ مخفی کردن' : '▶ نمایش وضعیت فعلی'"></span>
                </button>
            </div>
            
            <div x-show="showState" class="mb-4">
                <div class="bg-black/30 p-4 rounded-xl rtl-input text-slate-300 max-h-48 overflow-y-auto" x-text="currentState"></div>
            </div>

            <textarea x-model="newState" 
                placeholder="متن جدید برای Situation Brief..."
                class="w-full bg-white/10 border border-white/20 rounded-xl px-4 py-3 rtl-input h-32 focus:outline-none focus:ring-2 focus:ring-purple-500 mb-3"></textarea>
            
            <button @click="setState()"
                class="bg-gradient-to-r from-yellow-500 to-orange-600 hover:from-yellow-600 hover:to-orange-700 px-6 py-3 rounded-xl font-bold transition-all shadow-lg hover:shadow-xl">
                💾 ذخیره وضعیت جدید
            </button>
        </div>

        <!-- Logs -->
        <div class="bg-white/10 backdrop-blur-lg rounded-2xl p-6 border border-white/20 shadow-2xl">
            <h2 class="text-2xl font-bold mb-4">رخدادها</h2>
            <div class="space-y-2 max-h-64 overflow-y-auto">
                <template x-for="(log, index) in logs" :key="index">
                    <div :class="{
                        'bg-blue-500/20 border-blue-500': log.level === 'info',
                        'bg-green-500/20 border-green-500': log.level === 'success',
                        'bg-yellow-500/20 border-yellow-500': log.level === 'warning',
                        'bg-red-500/20 border-red-500': log.level === 'error'
                    }" class="p-3 rounded-lg border">
                        <div class="flex justify-between items-start">
                            <span class="rtl-input flex-1" x-text="log.message"></span>
                            <span class="text-xs text-slate-400" x-text="new Date(log.timestamp).toLocaleTimeString('fa-IR')"></span>
                        </div>
                    </div>
                </template>
                <div x-show="logs.length === 0" class="text-center text-slate-400 py-8">
                    هیچ رخدادی ثبت نشده
                </div>
            </div>
        </div>
    </div>

    <script>
        function app() {
            return {
                ws: null,
                status: {
                    is_running: false,
                    telegram_connected: false,
                    scheduler_running: false,
                    source_channels_count: 0
                },
                sources: [],
                newSource: '',
                currentState: '',
                newState: '',
                showState: false,
                logs: [],

                init() {
                    this.connectWebSocket();
                    this.loadStatus();
                    this.loadSources();
                    this.loadState();
                },

                connectWebSocket() {
                    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                    this.ws = new WebSocket(`${protocol}//${window.location.host}/ws`);
                    
                    this.ws.onmessage = (event) => {
                        const data = JSON.parse(event.data);
                        this.handleWebSocketMessage(data);
                    };

                    this.ws.onclose = () => {
                        this.addLog('اتصال WebSocket قطع شد. تلاش برای اتصال مجدد...', 'warning');
                        setTimeout(() => this.connectWebSocket(), 3000);
                    };
                },

                handleWebSocketMessage(data) {
                    if (data.type === 'status') {
                        this.status = data.data;
                    } else if (data.type === 'sources_updated') {
                        this.sources = data.data;
                    } else if (data.type === 'state_updated') {
                        this.currentState = data.data;
                    } else if (data.type === 'log') {
                        this.addLog(data.data.message, data.data.level);
                    } else if (data.type === 'connected') {
                        this.status = data.data.status;
                        this.addLog('اتصال برقرار شد', 'success');
                    }
                },

                async loadStatus() {
                    const res = await fetch('/api/status');
                    this.status = await res.json();
                },

                async startApp() {
                    const res = await fetch('/api/start', { method: 'POST' });
                    const data = await res.json();
                    this.addLog(data.message || data.error, data.success ? 'success' : 'error');
                },

                async stopApp() {
                    const res = await fetch('/api/stop', { method: 'POST' });
                    const data = await res.json();
                    this.addLog(data.message || data.error, data.success ? 'success' : 'error');
                },

                async loadSources() {
                    const res = await fetch('/api/sources');
                    const data = await res.json();
                    this.sources = data.sources;
                },

                async addSource() {
                    if (!this.newSource.trim()) return;
                    const res = await fetch('/api/sources/add', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ username: this.newSource })
                    });
                    const data = await res.json();
                    this.addLog(data.message || data.error, data.success ? 'success' : 'error');
                    if (data.success) {
                        this.newSource = '';
                        this.loadSources();
                    }
                },

                async removeSource(username) {
                    const res = await fetch('/api/sources/remove', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ username })
                    });
                    const data = await res.json();
                    this.addLog(data.message || data.error, data.success ? 'success' : 'error');
                    if (data.success) this.loadSources();
                },

                async loadState() {
                    const res = await fetch('/api/state');
                    const data = await res.json();
                    this.currentState = data.state;
                },

                async setState() {
                    if (!this.newState.trim()) return;
                    const res = await fetch('/api/state/set', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ state: this.newState })
                    });
                    const data = await res.json();
                    this.addLog(data.message || data.error, data.success ? 'success' : 'error');
                    if (data.success) {
                        this.newState = '';
                        this.loadState();
                    }
                },

                async triggerDigest() {
                    const res = await fetch('/api/digest/trigger', { method: 'POST' });
                    const data = await res.json();
                    this.addLog(data.message || data.error, data.success ? 'success' : 'error');
                },

                addLog(message, level = 'info') {
                    this.logs.unshift({
                        message,
                        level,
                        timestamp: new Date().toISOString()
                    });
                    if (this.logs.length > 50) this.logs.pop();
                }
            }
        }
    </script>
</body>
</html>
        """
