# 🚀 Quick Start Guide

Get **Hamid's Pulse Auto News** running in 5 minutes!

## Step 1: Prerequisites

✅ **Get Telegram API Credentials**
1. Go to https://my.telegram.org
2. Login with your phone number
3. Click "API development tools"
4. Create an app (any name)
5. Copy your `api_id` and `api_hash`

✅ **Get OpenAI API Key**
1. Go to https://platform.openai.com/api-keys
2. Create a new secret key
3. Copy it (starts with `sk-`)

## Step 2: Installation

```bash
# Clone the repository
git clone https://github.com/hamidreza004/hamidspulse-auto-news.git
cd hamidspulse-auto-news

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## Step 3: Configuration

```bash
# Copy environment template
cp .env.example .env

# Edit .env with your credentials
nano .env  # or use any text editor
```

**Required values in `.env`:**
```env
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef1234567890abcdef1234567890
TELEGRAM_PHONE=+989123456789
OPENAI_API_KEY=sk-your-openai-api-key-here
TARGET_CHANNEL=hamidspulse
```

## Step 4: Telegram Authentication

```bash
# Run the setup script (ONLY ONCE)
python setup_telegram.py
```

**What happens:**
1. You'll receive a login code on Telegram
2. Enter the code when prompted
3. If you have 2FA, enter your password
4. Session will be saved automatically ✅

## Step 5: Add Source Channels

Edit `config.yaml`:
```yaml
source_channels:
  - "@BBCPersian"
  - "@VOANewsFA"
  - "@your_favorite_channel"
```

Or add them later via the web UI!

## Step 6: Run! 🎉

```bash
# Start the application
python main.py

# Or use the convenience script
./run.sh
```

## Step 7: Open Web Dashboard

1. Open your browser
2. Go to: **http://localhost:8000**
3. Click the **"▶ شروع"** button
4. Watch the magic happen! 🔭

## 🎯 What Happens Next

The bot will:
- ✅ Monitor your source channels
- ✅ Triage each message with GPT-4o-mini
- ✅ Post HIGH importance news immediately
- ✅ Queue MEDIUM news for hourly digest
- ✅ Discard LOW importance (but log it)

## 📱 Web Dashboard Features

- **Status Panel** - See if everything is running
- **Start/Stop** - Control the bot
- **Sources** - Add/remove channels on the fly
- **State Management** - View/edit the AI's memory
- **Manual Digest** - Trigger hourly summary anytime
- **Live Logs** - Watch events in real-time

## 🔧 Common Issues

### "Session not found"
Run `python setup_telegram.py` first!

### "Invalid API credentials"
Double-check your `.env` file values.

### "OpenAI rate limit"
You may need to add credits to your OpenAI account.

### Port 8000 already in use
Change `WEB_PORT=8001` in `.env`

## 🎨 Customization

### Change importance thresholds
Edit `config.yaml`:
```yaml
thresholds:
  high_threshold: 85    # Lower = more posts
  medium_threshold: 55  # Lower = more digests
```

### Adjust post style
Edit `config.yaml`:
```yaml
content_style:
  emoji_logic:
    high_news_emoji_count: 3  # More/fewer emojis
```

### Change hourly digest time
Edit `config.yaml`:
```yaml
hourly_digest:
  schedule_minute: 0  # 0 = every hour at :00
```

## 📊 Monitoring

**View logs:**
```bash
tail -f logs/app.log
```

**Check database:**
```bash
sqlite3 data/news.db
> SELECT * FROM message_log LIMIT 10;
```

## 🐳 Docker (Optional)

```bash
# Build and run with Docker
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down
```

## 🆘 Need Help?

1. Check the full [README.md](README.md)
2. Look at [Troubleshooting](README.md#-troubleshooting)
3. Open an issue on GitHub

---

**That's it! You're now running an AI-powered news channel!** 🎉

Questions? Check the full documentation in [README.md](README.md)
