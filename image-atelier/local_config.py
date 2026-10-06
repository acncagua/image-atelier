"""Backend-only credentials. Never include this configuration in API responses."""
import json
import os
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / 'config.local.json'


def _key(field,environment,path=None):
    config_path = Path(path) if path is not None else CONFIG_PATH
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding='utf-8-sig'))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ValueError('config.local.jsonを読み込めません。UTF-8のJSON形式を確認してください。') from None
        if not isinstance(config, dict) or not isinstance(config.get(field, ''), str):
            raise ValueError('config.local.jsonの'+field+'は文字列で指定してください。')
        key = config.get(field, '').strip()
        if key:
            return key
    return os.environ.get(environment, '').strip()

def api_key(path=None):return _key('openai_api_key','OPENAI_API_KEY',path)

def strata_api_key(path=None):return _key('strata_api_key','STRATA_API_KEY',path)
