"""
Settings for CFIA Redmine
"""
import os
from pathlib import Path


def _load_env_file(env_file_path=None):
    env_file = env_file_path or Path(__file__).resolve().parents[1] / 'env'
    env_file = Path(env_file)
    if not env_file.is_file():
        return

    for line in env_file.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip()


def _bool_env(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


def _int_env(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _str_env(name, default=None):
    return os.environ.get(name, default)


_load_env_file()

API_KEY = _str_env('API_KEY', '')
DEV_API_KEY = _str_env('DEV_API_KEY', '')
