#!/usr/bin/env python3
import asyncio
import os
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

load_dotenv()


async def setup_telegram_session():
    print("=" * 60)
    print("Telegram Session Setup")
    print("=" * 60)
    
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    phone = os.getenv("TELEGRAM_PHONE")
    session_path = os.getenv("SESSION_PATH", "./secrets/telegram.session")
    
    if not all([api_id, api_hash, phone]):
        print("❌ Error: Missing Telegram credentials in .env file")
        print("\nPlease ensure you have set:")
        print("  - TELEGRAM_API_ID")
        print("  - TELEGRAM_API_HASH")
        print("  - TELEGRAM_PHONE")
        print("\nGet your API credentials from: https://my.telegram.org")
        return
    
    os.makedirs(os.path.dirname(session_path), exist_ok=True)
    
    client = TelegramClient(session_path, int(api_id), api_hash)
    
    await client.connect()
    
    if not await client.is_user_authorized():
        print(f"\n📱 Sending login code to {phone}...")
        await client.send_code_request(phone)
        
        code = input("\n🔑 Enter the code you received: ")
        
        try:
            await client.sign_in(phone, code)
        except SessionPasswordNeededError:
            password = input("\n🔐 Two-factor authentication enabled. Enter your password: ")
            await client.sign_in(password=password)
        
        print("\n✅ Successfully logged in!")
    else:
        print("\n✅ Session already exists and is valid!")
    
    me = await client.get_me()
    print(f"\n👤 Logged in as: {me.first_name} {me.last_name or ''}")
    print(f"📞 Phone: {me.phone}")
    print(f"🆔 User ID: {me.id}")
    
    print(f"\n💾 Session saved to: {session_path}")
    print("\n✨ Setup complete! You can now run the main application.")
    print("=" * 60)
    
    await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(setup_telegram_session())
    except KeyboardInterrupt:
        print("\n\nSetup cancelled.")
    except Exception as e:
        print(f"\n❌ Error: {e}")
