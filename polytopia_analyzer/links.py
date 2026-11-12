"""Работа со ссылками на матчи Polytopia.

Ссылка share.polytopia.io — это только deep-link в приложение: сами данные
матча хранятся на серверах Midjiwan и загружаются игровым клиентом.
Поэтому модуль умеет извлекать id матча и строить ссылки для открытия
реплея на разных платформах.
"""

from __future__ import annotations

import re

STEAM_APP_ID = "874390"

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_ID_IN_URL_RE = re.compile(
    r"(?:share\.polytopia\.io/g/|opengame\?id=)"
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


def extract_game_id(link: str) -> str:
    """Извлекает UUID матча из ссылки любого формата или из голого UUID."""
    s = (link or "").strip()
    if _UUID_RE.match(s):
        return s.lower()
    m = _ID_IN_URL_RE.search(s)
    if m:
        return m.group(1).lower()
    raise ValueError(f"Не удалось извлечь id матча из ссылки: {link!r}")


def share_link(game_id: str) -> str:
    return f"https://share.polytopia.io/g/{extract_game_id(game_id)}"


def mobile_link(game_id: str) -> str:
    return f"polytopia://opengame?id={extract_game_id(game_id)}"


def steam_link(game_id: str) -> str:
    return f"steam://run/{STEAM_APP_ID}//opengame?id={extract_game_id(game_id)}"
