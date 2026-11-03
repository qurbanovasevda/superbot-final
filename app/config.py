"""Project settings with a small, dependency-free .env reader."""
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / '.env'


def read_setting(name: str, default: str = '') -> str:
    """Process variables override project .env values; never execute file text."""
    if name in os.environ:
        return os.environ[name]
    try:
        text = ENV_FILE.read_text(encoding='utf-8-sig')
    except FileNotFoundError:
        return default
    values = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            raise ValueError(f'Invalid .env entry on line {number}; expected NAME=value')
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip()
        if value.startswith(('"', "'")):
            if len(value)<2 or value[-1]!=value[0]:
                raise ValueError(f'Unclosed .env quote on line {number}')
            value=value[1:-1]
        else:
            value=value.split(' #',1)[0].rstrip()
        values[key]=value
    return values.get(name, default)


@dataclass(frozen=True)
class Settings:
    data_dir: Path = PROJECT_ROOT / 'data'
    database_path: Path = PROJECT_ROOT / 'var' / 'superchat.sqlite3'
    reference_date: date = date(2026, 11, 2)
    application_name: str = 'SuperChat'
    chat_mode: str = field(default_factory=lambda: read_setting('SUPERCHAT_CHAT_MODE','rules'))
    gemini_api_key: str = field(default_factory=lambda: read_setting('GEMINI_API_KEY'), repr=False)
    gemini_model: str = field(default_factory=lambda: read_setting('GEMINI_MODEL','gemini-3.5-flash-lite'))
