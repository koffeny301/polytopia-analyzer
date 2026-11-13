"""Импорт снимков состояния игры (шим LD_PRELOAD, веха 6.2/6.3).

Шим пишет в папку inbox файлы `snapshot-<ход>.json`:

    {"turn": 12,
     "players": [{"id": 1, "name": "KOF", "tribe": 16, "score": 4485,
                  "stars": 8, "cities": 7, "kills": 1, "casualties": 2,
                  "resigned_turn": -1, "wiped": false}, ...]}

Здесь они превращаются в обычную модель Match:

  * score и cities — снимок на ход, переносятся как есть;
  * kills и casualties в состоянии накопительные, в модели убийства/потери
    считаются за ход → берём разницу с предыдущим ходом;
  * income, army, captured, lost_cities, damage состояние не содержит —
    эти поля помечаются незаполненными (не нулями), чтобы валидация и отчёт
    честно показывали «нет данных».

Варвары (id 255, племя Nature) в матч не попадают.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from ..models import Match, MatchMeta, Player, TurnRecord

# TribeType — runtime-enum игры, одинаков в снимках шима и в .state-сейвах.
# Полная таблица собрана по живому сейву 16×16 с 16 ИИ: имя каждого бота
# принадлежит своему племени (gist «Polytopia Tribe Names from Bots»), каждое
# соответствие подтверждено стартовым счётом/звёздами из независимой таблицы
# (Agentcounter/tribes.md). Контрольные якоря: 1/15/16 — шим на реальном
# матче (KOF — Polaris, Goylem — Zebasi); 4 — конвертер PolyVision (5517
# сейвов Bardur). 9 = Luxidoor — исключением (остальные id заняты) + контроль
# (уникальные 2 стартовые звезды). Непроверенных id не осталось; для битых
# данных запасной вариант Tribe<n> сохраняется.
TRIBE_NAMES = {
    1: "Nature",
    2: "Ai-Mo",
    3: "Aquarion",
    4: "Bardur",
    5: "Elyrion",
    6: "Hoodrick",
    7: "Imperius",
    8: "Kickoo",
    9: "Luxidoor",
    10: "Oumaji",
    11: "Quetzali",
    12: "Vengir",
    13: "Xin-xi",
    14: "Yadakk",
    15: "Zebasi",
    16: "Polaris",
    17: "Cymanti",
}
NPC_TRIBE_ID = 1          # варвары
NPC_PLAYER_ID = 255

# что реально есть в состоянии игры
AVAILABLE_METRICS = ("score", "cities", "kills", "losses")


def tribe_name(tribe_id: Optional[int]) -> str:
    """Название племени по числовому TribeType (с запасным вариантом)."""
    if tribe_id is None:
        return ""
    return TRIBE_NAMES.get(int(tribe_id), f"Tribe{int(tribe_id)}")


@dataclass
class InboxSession:
    """Набор снимков одного проигрывания (папка inbox целиком)."""

    directory: Path
    files: list[Path]
    turns: list[int]
    newest_mtime: float

    @property
    def is_empty(self) -> bool:
        return not self.files


def load_snapshots(directory: Path | str) -> list[dict]:
    """Читает все snapshot-*.json из папки, сортируя по номеру хода."""
    d = Path(directory)
    if not d.is_dir():
        raise ValueError(f"Папка снимков не найдена: {d}")
    snaps: list[dict] = []
    for p in sorted(d.glob("snapshot-*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ValueError(f"Некорректный снимок {p.name}: {e}") from e
        if not isinstance(data, dict) or "turn" not in data:
            raise ValueError(f"Снимок без номера хода: {p.name}")
        snaps.append(data)
    if not snaps:
        raise ValueError(f"В папке нет снимков: {d}")
    snaps.sort(key=lambda s: int(s["turn"]))
    return snaps


def scan_inbox(directory: Path | str) -> InboxSession:
    """Описывает содержимое папки inbox, ничего не импортируя."""
    d = Path(directory)
    files = sorted(d.glob("snapshot-*.json")) if d.is_dir() else []
    turns: list[int] = []
    newest = 0.0
    for p in files:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            turns.append(int(data["turn"]))
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
        newest = max(newest, p.stat().st_mtime)
    return InboxSession(directory=d, files=files, turns=sorted(turns), newest_mtime=newest)


def _real_players(snapshot: dict) -> list[dict]:
    out = []
    for p in snapshot.get("players") or []:
        if int(p.get("id", -1)) == NPC_PLAYER_ID:
            continue
        if int(p.get("tribe", -1)) == NPC_TRIBE_ID:
            continue
        if (p.get("name") or "").strip().lower() == "nature":
            continue
        out.append(p)
    return out


def match_from_snapshots(snapshots: Iterable[dict], *, title: str = "",
                         date: str = "", link: str = "", mode: str = "", map_name: str = "",
                         notes: str = "") -> Match:
    """Собирает Match из снимков состояния игры."""
    snaps = sorted(snapshots, key=lambda s: int(s["turn"]))
    if not snaps:
        raise ValueError("Нет снимков для импорта")

    # игроки — по последнему снимку (там точно все)
    last = snaps[-1]
    players_raw = _real_players(last)
    if not players_raw:
        raise ValueError("В снимках нет игроков (кроме варваров)")

    players = [
        Player(name=str(p.get("name") or f"Игрок{p['id']}"),
               tribe=tribe_name(p.get("tribe")),
               is_human=bool(p.get("is_human", False)))
        for p in sorted(players_raw, key=lambda p: int(p.get("id", 0)))
    ]
    names_by_id = {int(p["id"]): str(p.get("name")) for p in players_raw}

    records: list[TurnRecord] = []
    prev: dict[int, dict] = {}
    for snap in snaps:
        turn = int(snap["turn"])
        for p in _real_players(snap):
            pid = int(p.get("id", -1))
            name = names_by_id.get(pid) or str(p.get("name") or f"Игрок{pid}")
            base = prev.get(pid, {})
            kills_total = int(p.get("kills", 0) or 0)
            losses_total = int(p.get("casualties", 0) or 0)
            prev_kills = int(base.get("kills", 0) or 0) if base else 0
            prev_losses = int(base.get("casualties", 0) or 0) if base else 0
            records.append(TurnRecord(
                turn=turn,
                player=name,
                score=float(p.get("score", 0) or 0),
                cities=int(p.get("cities", 0) or 0),
                kills=max(0, kills_total - prev_kills),
                losses=max(0, losses_total - prev_losses),
                filled={"score", "cities", "kills", "losses"},
            ))
            prev[pid] = p

    default_notes = ("Снято шимом из состояния игры. Доход, армия, захваты городов и урон "
                     "в состоянии отсутствуют — этих данных в матче нет.")
    meta = MatchMeta(
        title=title or "Матч из игры",
        date=date or datetime.fromtimestamp(
            datetime.now().timestamp()).strftime("%Y-%m-%d"),
        mode=mode,
        map=map_name,
        link=link,
        turn_limit=max(int(s["turn"]) for s in snaps) + 1,
        notes=(notes or default_notes),
        source="shim",
    )
    match = Match(meta=meta, players=players, records=records)
    match.validate()
    return match


def match_from_inbox(directory: Path | str, *, title: str = "", date: str = "",
                     link: str = "", mode: str = "", map_name: str = "") -> Match:
    """Читает папку inbox и собирает из неё матч."""
    return match_from_snapshots(load_snapshots(directory), title=title, date=date,
                                link=link, mode=mode, map_name=map_name)


def default_inbox_dir() -> Path:
    """Папка, в которую пишет шим (см. shim/run-dump.sh)."""
    from .. import paths

    return paths.inbox_dir()