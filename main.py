#!/usr/bin/env python3
import asyncio
import os
import sys
from dotenv import load_dotenv
import uvicorn
from src.logger import setup_logging
from src.app_manager import AppManager
from src.web_ui import WebUI

load_dotenv()


async def main():
    log_level = os.getenv("LOG_LEVEL", "INFO")
    logger = setup_logging(log_level=log_level)
    
    logger.info("=" * 60)
    logger.info("Hamid's Pulse Auto News - Starting...")
    logger.info("=" * 60)
    
    app_manager = AppManager()
    
    try:
        app_manager.initialize()
        logger.info("App manager initialized")
        
    except Exception as e:
        logger.error(f"Failed to initialize app manager: {e}")
        logger.error("Please check your configuration and environment variables")
        sys.exit(1)
    
    web_ui = WebUI(app_manager)
    
    host = os.getenv("WEB_HOST", "0.0.0.0")
    port = int(os.getenv("WEB_PORT", "8000"))
    
    logger.info(f"Starting web UI at http://{host}:{port}")
    logger.info("=" * 60)
    logger.info("🚀 Application is ready!")
    logger.info("📱 Open your browser and navigate to the web UI to control the bot")
    logger.info("=" * 60)
    
    config = uvicorn.Config(
        web_ui.app,
        host=host,
        port=port,
        log_level=log_level.lower(),
        access_log=False
    )
    server = uvicorn.Server(config)
    
    try:
        await server.serve()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        await app_manager.stop()
        logger.info("Goodbye!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown complete.")
