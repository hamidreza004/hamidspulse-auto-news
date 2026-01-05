import yaml
import os
from typing import Dict, Any, List
from pathlib import Path


class Config:
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = config_path
        self.data: Dict[str, Any] = {}
        self.load()
    
    def load(self):
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.data = yaml.safe_load(f)
        else:
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
    
    def save(self):
        with open(self.config_path, 'w', encoding='utf-8') as f:
            yaml.dump(self.data, f, allow_unicode=True, default_flow_style=False)
    
    def get(self, key_path: str, default=None):
        keys = key_path.split('.')
        value = self.data
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
                if value is None:
                    return default
            else:
                return default
        return value
    
    def set(self, key_path: str, value):
        keys = key_path.split('.')
        data = self.data
        for key in keys[:-1]:
            if key not in data:
                data[key] = {}
            data = data[key]
        data[keys[-1]] = value
        self.save()
    
    @property
    def target_channel(self) -> str:
        return self.get('target_channel.username', 'hamidspulse')
    
    @property
    def source_channels(self) -> List[str]:
        return self.get('source_channels', [])
    
    def add_source_channel(self, username: str):
        channels = self.source_channels
        if username not in channels:
            channels.append(username)
            self.set('source_channels', channels)
    
    def remove_source_channel(self, username: str):
        channels = self.source_channels
        if username in channels:
            channels.remove(username)
            self.set('source_channels', channels)
    
    @property
    def high_threshold(self) -> int:
        return self.get('thresholds.high_threshold', 85)
    
    @property
    def medium_threshold(self) -> int:
        return self.get('thresholds.medium_threshold', 55)
    
    @property
    def max_posts_per_hour(self) -> int:
        return self.get('rate_limits.max_posts_per_hour', 5)
    
    @property
    def triage_model(self) -> str:
        return self.get('gpt_models.triage_model', 'gpt-4o-mini')
    
    @property
    def content_model(self) -> str:
        return self.get('gpt_models.content_model', 'gpt-4o')
    
    @property
    def timezone(self) -> str:
        return self.get('timezone', 'Asia/Tehran')
    
    def get_content_style_prompt(self) -> str:
        characteristics = self.get('content_style.core_characteristics', [])
        
        prompt = "ویژگی‌های محتوا:\n"
        for char in characteristics:
            prompt += f"- {char}\n"
        
        return prompt
