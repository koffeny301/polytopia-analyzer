"""Пути данных: работают и из репозитория, и из собранного onefile-бинарника.

В onefile PyInstaller распаковывает код во временную папку, поэтому пути,
вычисленные от `__file__`, указывать на данные пользователя не могут.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "polytopia-analyzer"


def _home() -> Path:
    return Path.home()


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def user_state_dir() -> Path:
    """Корень данных приложения (~/.local/share/polytopia-analyzer)."""
    env = os.environ.get("POLYTOPIA_STATE_DIR")
    if env:
        return Path(env).expanduser()
    return _home() / ".local/share" / APP_DIR_NAME


def matches_dir() -> Path:
    """Каталог сохранённых матчей."""
    env = os.environ.get("POLYTOPIA_DATA_DIR")
    if env:
        return Path(env).expanduser()
    if not is_frozen():
        repo_data = Path(__file__).resolve().parents[1] / "data"
        if repo_data.is_dir():
            return repo_data
    return user_state_dir() / "matches"


def inbox_dir() -> Path:
    """Каталог, куда шим пишет снимки состояния."""
    env = os.environ.get("POLYREAD_DUMP_DIR")
    if env:
        return Path(env).expanduser()
    return user_state_dir() / "inbox"


def probe_dir() -> Path:
    """Каталог выгрузки пробника (веха 6.1)."""
    return user_state_dir() / "probe"


def shim_bin_dir() -> Path:
    """Куда ставится libpolyread_dump.so."""
    return user_state_dir() / "bin"