import logging
import os
from logging.handlers import RotatingFileHandler
import colorlog


def setup_logging(log_level: str = "INFO", log_to_file: bool = True, 
                  log_file_path: str = "./logs/app.log"):
    os.makedirs(os.path.dirname(log_file_path), exist_ok=True)
    
    log_format = '%(log_color)s%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    
    handler = colorlog.StreamHandler()
    handler.setFormatter(colorlog.ColoredFormatter(
        log_format,
        log_colors={
            'DEBUG': 'cyan',
            'INFO': 'green',
            'WARNING': 'yellow',
            'ERROR': 'red',
            'CRITICAL': 'red,bg_white',
        }
    ))
    
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))
    root_logger.addHandler(handler)
    
    if log_to_file:
        file_handler = RotatingFileHandler(
            log_file_path,
            maxBytes=10*1024*1024,
            backupCount=5,
            encoding='utf-8'
        )
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)
    
    logging.getLogger('telethon').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    
    return root_logger
