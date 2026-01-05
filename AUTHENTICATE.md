# Telegram Authentication Required

## Run this command in your terminal:

```bash
cd /Users/hamidreza/hamidspulse-auto-news
source venv/bin/activate
python setup_telegram.py
```

## What will happen:

1. You'll see: "📱 Sending login code to +989121136106..."
2. Check your Telegram app for the code
3. Enter the code when prompted
4. If you have 2FA, enter your password
5. Session will be saved to `secrets/telegram.session`

## After authentication succeeds:

```bash
python main.py
```

Then open http://localhost:8000 and click Start!
