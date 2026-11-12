"""Модель данных матча The Battle of Polytopia.

Соглашение по метрикам TurnRecord:
  * снимок состояния на конец хода: score, income, army, cities;
  * прирост за ход: kills, losses, captured, lost_cities, damage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

SNAPSHOT_FIELDS = ("score", "income", "army", "cities")
DELTA_FIELDS = ("kills", "losses", "captured", "lost_cities", "damage")
METRIC_FIELDS = SNAPSHOT_FIELDS + DELTA_FIELDS
INT_FIELDS = ("army", "cities", "kills", "losses", "captured", "lost_cities")
FLOAT_FIELDS = ("score", "income", "damage")
META_FIELDS = ("title", "date", "mode", "map", "link", "turn_limit", "notes")
PLAYER_FIELDS = ("name", "tribe", "color", "is_human")


@dataclass
class Player:
    """Игрок (племя) в матче."""

    name: str
    tribe: str = ""
    color: Optional[str] = None
    is_human: bool = False


@dataclass
class TurnRecord:
    """Показатели игрока на конкретном ходу."""

    turn: int
    player: str
    score: float = 0.0
    income: float = 0.0
    army: int = 0
    cities: int = 0
    kills: int = 0
    losses: int = 0
    captured: int = 0
    lost_cities: int = 0
    damage: float = 0.0


@dataclass
class MatchMeta:
    """Метаданные матча."""

    title: str = ""
    date: str = ""
    mode: str = ""
    map: str = ""
    link: str = ""
    turn_limit: Optional[int] = None
    notes: str = ""


@dataclass
class Match:
    """Матч целиком: метаданные, игроки и помходовые записи."""

    meta: MatchMeta = field(default_factory=MatchMeta)
    players: list[Player] = field(default_factory=list)
    records: list[TurnRecord] = field(default_factory=list)

    def player_names(self) -> list[str]:
        return [p.name for p in self.players]

    def player(self, name: str) -> Player:
        for p in self.players:
            if p.name == name:
                return p
        raise KeyError(f"Игрок не найден: {name!r}")

    def records_for(self, name: str) -> list[TurnRecord]:
        """Записи игрока, отсортированные по ходу."""
        return sorted((r for r in self.records if r.player == name), key=lambda r: r.turn)

    def max_turn(self) -> int:
        return max((r.turn for r in self.records), default=0)

    def validate(self) -> None:
        """Проверяет целостность данных. Бросает ValueError."""
        names = self.player_names()
        if not names:
            raise ValueError("В матче нет ни одного игрока")
        if len(set(names)) != len(names):
            raise ValueError("Имена игроков должны быть уникальны")
        seen: set[tuple[int, str]] = set()
        for r in self.records:
            if r.player not in names:
                raise ValueError(f"Запись ссылается на неизвестного игрока: {r.player!r}")
            if r.turn < 0:
                raise ValueError(f"Отрицательный номер хода: {r.turn}")
            key = (r.turn, r.player)
            if key in seen:
                raise ValueError(f"Дублирующаяся запись: ход {r.turn}, игрок {r.player!r}")
            seen.add(key)
