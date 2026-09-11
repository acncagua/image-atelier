"""Backend-only credentials. Never include this configuration in API responses."""
import json
import os
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / 'config.local.json'


def api_key(path=None):
    config_path = Path(path) if path is not None else CONFIG_PATH
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding='utf-8-sig'))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ValueError('config.local.jsonを読み込めません。UTF-8のJSON形式を確認してください。') from None
        if not isinstance(config, dict) or not isinstance(config.get('openai_api_key', ''), str):
            raise ValueError('config.local.jsonのopenai_api_keyは文字列で指定してください。')
        key = config.get('openai_api_key', '').strip()
        if key:
            return key
    return os.environ.get('OPENAI_API_KEY', '').strip()
