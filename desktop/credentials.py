"""Private provider keys in the original dotenv format; never return key values."""
import os
from pathlib import Path

from dotenv import dotenv_values, set_key, unset_key

ENV_NAME = {'deepseek': 'DEEPSEEK_API_KEY'}
ENV_FILE = Path(__file__).resolve().parents[1] / '.env'


def status(provider='deepseek'):
    name = ENV_NAME.get(provider)
    if not name:
        raise ValueError('Provider không được hỗ trợ để lưu khóa.')
    saved = dotenv_values(ENV_FILE).get(name) if ENV_FILE.exists() else None
    return {'provider': provider, 'configured': bool(saved or os.environ.get(name))}


def save(provider, key):
    name = ENV_NAME.get(provider)
    if not name or not isinstance(key, str):
        raise ValueError('Cấu hình khóa không hợp lệ.')
    key = key.strip()
    if not key or len(key) > 4096 or any(ord(c) < 32 for c in key):
        raise ValueError('Nhập khóa API hợp lệ.')
    if ENV_FILE.is_symlink():
        raise ValueError('Không lưu khóa vào file liên kết.')
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not ENV_FILE.exists():
        fd = os.open(ENV_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    ENV_FILE.chmod(0o600)
    set_key(str(ENV_FILE), name, key, quote_mode='always')
    ENV_FILE.chmod(0o600)
    return {'provider': provider, 'configured': True}


def remove(provider):
    name = ENV_NAME.get(provider)
    if not name:
        raise ValueError('Provider không được hỗ trợ.')
    if ENV_FILE.is_symlink():
        raise ValueError('Không sửa file liên kết.')
    if ENV_FILE.exists():
        unset_key(str(ENV_FILE), name)
        ENV_FILE.chmod(0o600)
    return {'provider': provider, 'configured': bool(os.environ.get(name))}
