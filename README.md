# 🔭 Hamid's Pulse Auto News

**Automated Telegram news channel using GPT-powered triage and content generation**

> چیزایی که با دیروز فرق دارن

## 📋 Overview

This project automates a Persian Telegram news channel (@hamidspulse) by:
- **Monitoring** multiple source Telegram channels for news
- **Triaging** messages with GPT-4o-mini (importance scoring)
- **Generating** high-quality Persian posts with GPT-4o
- **Publishing** automatically with smart rate limiting
- **Summarizing** medium-importance news hourly

## ✨ Features

- ✅ **MTProto User Account** (Telethon) - not a bot
- ✅ **Smart Triage** - GPT-4o-mini scores importance (0-100)
- ✅ **Three-Tier System**:
  - **HIGH** (≥85): Immediate post with ironic title
  - **MEDIUM** (≥55): Queue for hourly digest
  - **LOW** (<55): Discard (logged)
- ✅ **Context-Aware** - Maintains "Situation Brief" memory
- ✅ **Web Dashboard** - Real-time control panel with WebSocket
- ✅ **Configurable** - YAML-based settings
- ✅ **Rate Limiting** - Prevents spam
- ✅ **Beautiful UI** - Modern, Persian-friendly web interface

## 🏗️ Architecture

```
┌─────────────────┐
│ Source Channels │
│  (Monitoring)   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  GPT Triage     │◄──── Situation Brief (Memory)
│  (4o-mini)      │
└────────┬────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
┌───────┐ ┌────────┐ ┌──────┐
│ HIGH  │ │ MEDIUM │ │ LOW  │
│ ≥85   │ │ ≥55    │ │ <55  │
└───┬───┘ └───┬────┘ └──┬───┘
    │         │          │
    ▼         │          ▼
┌────────────┐│      ┌──────┐
│GPT Content ││      │ Log  │
│  (4o)      ││      │ Only │
└─────┬──────┘│      └──────┘
      │       │
      ▼       ▼
┌──────────────────┐
│ Post to Channel  │
│   @hamidspulse   │
└──────────────────┘
      │
      ▼
┌──────────────────┐
│ Update Memory    │
└──────────────────┘
```

## 📦 Installation

### Prerequisites

- Python 3.10+
- Telegram API credentials ([my.telegram.org](https://my.telegram.org))
- OpenAI API key
- Your Telegram account

### Step 1: Clone the Repository

```bash
git clone https://github.com/hamidreza004/hamidspulse-auto-news.git
cd hamidspulse-auto-news
```

### Step 2: Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 4: Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_PHONE=+989123456789
OPENAI_API_KEY=sk-your-key
TARGET_CHANNEL=hamidspulse
```

### Step 5: Setup Telegram Session

**IMPORTANT:** Run this once to authenticate your Telegram account:

```bash
python setup_telegram.py
```

Follow the prompts:
1. Enter the code sent to your Telegram app
2. If you have 2FA, enter your password
3. Session will be saved to `./secrets/telegram.session`

### Step 6: Configure Sources

Edit `config.yaml` to add your source channels:

```yaml
source_channels:
  - "@BBCPersian"
  - "@VOANewsFA"
  - "@your_channel"
```

## 🚀 Usage

### Start the Application

```bash
python main.py
```

This will:
1. Start the web UI at `http://localhost:8000`
2. Initialize all services
3. Wait for you to click "Start" in the web dashboard

### Web Dashboard

Open your browser to `http://localhost:8000`:

- **🟢 Start/Stop** - Control the bot
- **📡 Sources** - Add/remove source channels
- **💾 State** - View/edit Situation Brief
- **🔄 Manual Digest** - Trigger hourly summary on-demand
- **📊 Logs** - Real-time event monitoring

### Auto-Start on Boot (Optional)

Create a systemd service (Linux):

```bash
sudo nano /etc/systemd/system/hamidspulse.service
```

```ini
[Unit]
Description=Hamid's Pulse Auto News
After=network.target

[Service]
Type=simple
User=your_username
WorkingDirectory=/path/to/hamidspulse-auto-news
Environment="PATH=/path/to/hamidspulse-auto-news/venv/bin"
ExecStart=/path/to/hamidspulse-auto-news/venv/bin/python main.py
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable hamidspulse
sudo systemctl start hamidspulse
```

## ⚙️ Configuration

### `config.yaml` Structure

```yaml
# Importance Thresholds
thresholds:
  high_threshold: 85      # Immediate post
  medium_threshold: 55    # Queue for digest

# Rate Limits
rate_limits:
  max_posts_per_hour: 5   # Prevent spam

# Content Style
content_style:
  core_characteristics:
    - "کنجکاو و کنکاش‌گر"
    - "بی‌طرف اما تحلیل‌گر"
  
  emoji_logic:
    high_news_emoji_count: 3
    use_ironic_emojis: true

# GPT Models
gpt_models:
  triage_model: "gpt-4o-mini"    # Fast triage
  content_model: "gpt-4o"        # Quality content
```

### Adjusting Behavior

- **More posts**: Lower `high_threshold` (e.g., 75)
- **Fewer posts**: Raise `high_threshold` (e.g., 90)
- **More digestible**: Lower `medium_threshold`
- **Change tone**: Edit `content_style` characteristics

## 📊 How It Works

### 1. Message Ingestion

When a new message arrives from a source channel:
- Extract text, media caption, channel name
- Generate message URL: `https://t.me/channel/message_id`

### 2. GPT Triage (GPT-4o-mini)

Sends to GPT with:
- Message text
- Source info
- Current Situation Brief (context)

Returns JSON:
```json
{
  "importance_score": 78,
  "bucket": "medium",
  "novelty_delta": "تنش جدید بین ایران و اسرائیل",
  "reason": "تحولات ژئوپلیتیک مهم",
  "key_points": ["نکته 1", "نکته 2"]
}
```

### 3. Action Based on Bucket

**HIGH** (score ≥ 85):
- Check rate limit
- Generate post with GPT-4o
- Post immediately
- Update Situation Brief

**MEDIUM** (55 ≤ score < 85):
- Queue in database
- Wait for hourly digest

**LOW** (score < 55):
- Log only
- Discard

### 4. Hourly Digest

Every hour (configurable):
- Fetch all queued MEDIUM items
- Generate summary with GPT-4o
- Post as single digest
- Clear queue
- Update Situation Brief

### 5. Situation Brief Updates

After each HIGH post or digest:
- GPT updates the brief
- Keeps it ≤1200 chars
- Provides context for future triage

## 🎨 Post Format

### HIGH Post Example

```
🔥 ترامپ باز هم رکورد زد

دونالد ترامپ با ۹۱ اتهام جنایی، پرمحکوم‌ترین 
رئیس‌جمهور تاریخ آمریکا شد. 🎪🍿🤡

منبع:
BBC Persian: https://t.me/BBCPersian/12345

@hamidspulse
```

### Hourly Digest Example

```
جمع‌بندی یک ساعته | 14:00–15:00

• تنش جدید در خاورمیانه بعد از... (BBC: link)
• تورم آمریکا به بالاترین حد... (VOA: link)
• اعتراضات دانشجویی در... (CNN: link)

💭 روند کلی: افزایش ناآرامی‌های منطقه‌ای

@hamidspulse
```

## 🗄️ Database Schema

SQLite database (`./data/news.db`):

- **news_state** - Current Situation Brief
- **medium_queue** - MEDIUM messages awaiting digest
- **message_log** - All processed messages
- **published_posts** - All published content
- **rate_limit_counter** - Hourly post tracking
- **source_channels** - Configured sources

## 🔒 Security

- ✅ Secrets in `.env` (git-ignored)
- ✅ Session file in `./secrets/` (git-ignored)
- ✅ No hardcoded credentials
- ✅ Public repo safe (see `.env.example`)

## 🐛 Troubleshooting

### "SessionPasswordNeededError"
Run `python setup_telegram.py` again and enter your 2FA password.

### "FloodWaitError"
Telegram rate limiting. Reduce `max_posts_per_hour` in config.

### "OpenAI API Error"
Check your API key and billing status.

### No messages being processed
- Verify source channels in config
- Check web UI logs
- Ensure Telegram session is valid

### Web UI not loading
- Check port 8000 is not in use
- Try changing `WEB_PORT` in `.env`

## 📝 Development

### Project Structure

```
hamidspulse-auto-news/
├── main.py                 # Entry point
├── setup_telegram.py       # First-time auth
├── config.yaml            # Main configuration
├── requirements.txt       # Dependencies
├── .env.example          # Template for secrets
├── src/
│   ├── __init__.py
│   ├── app_manager.py    # Application orchestrator
│   ├── config.py         # Config loader
│   ├── database.py       # SQLAlchemy models
│   ├── gpt_service.py    # OpenAI integration
│   ├── logger.py         # Logging setup
│   ├── news_processor.py # Message processing
│   ├── scheduler.py      # Hourly digest cron
│   ├── telegram_service.py # Telethon wrapper
│   └── web_ui.py         # FastAPI + WebSocket UI
├── data/                 # SQLite database
├── logs/                 # Application logs
└── secrets/              # Telegram session
```

### Adding New Features

1. **New triage criteria**: Edit `src/gpt_service.py` prompts
2. **Custom post format**: Modify `generate_high_post()` method
3. **Additional sources**: Use web UI or edit `config.yaml`
4. **Different schedule**: Change `hourly_digest.schedule_minute`

## 🤝 Contributing

This is a personal project for @hamidspulse, but suggestions are welcome:

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push and create a Pull Request

## 📄 License

MIT License - feel free to use and modify for your own projects.

## 🙏 Acknowledgments

- **Telethon** - MTProto Telegram client
- **OpenAI** - GPT-4o models
- **FastAPI** - Modern web framework
- **Alpine.js** - Reactive UI
- **Tailwind CSS** - Beautiful styling

## 📞 Support

For issues related to:
- **Setup**: Check this README's troubleshooting section
- **Telegram API**: Visit [Telegram's documentation](https://core.telegram.org/)
- **OpenAI**: Check [OpenAI's status page](https://status.openai.com/)

## 🎯 Roadmap

- [ ] Multi-language support
- [ ] Analytics dashboard
- [ ] Custom GPT prompts per channel
- [ ] Image/video processing
- [ ] Sentiment analysis
- [ ] Topic clustering

---

**Made with ❤️ for Hamid's Pulse** | [GitHub](https://github.com/hamidreza004/hamidspulse-auto-news)
