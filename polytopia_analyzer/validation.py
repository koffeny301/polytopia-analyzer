"""Проверка согласованности данных матча.

Три уровня:
  error   — данные противоречивы, сохранение/анализ блокируются;
  warning — подозрительное место, подсвечивается в UI, не блокирует;
  info    — справочная информация (полнота, пропуски ходов).

Правила, зависящие от заполненности, срабатывают только когда соответствующие
ячейки ДЕЙСТВИТЕЛЬНО заполнены (TurnRecord.filled): «0» и «неизвестно» —
разные вещи.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Optional

from .models import METRIC_FIELDS, Match, TurnRecord

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"


@dataclass
class Issue:
    """Одно замечание валидатора."""

    code: str
    severity: str
    message: str
    turn: Optional[int] = None
    player: Optional[str] = None
    metric: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "code": self.code, "severity": self.severity, "message": self.message,
            "turn": self.turn, "player": self.player, "metric": self.metric,
        }


@dataclass
class ValidationReport:
    """Результат проверки матча."""

    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    info: list[Issue] = field(default_factory=list)
    completeness: dict[str, float] = field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    def add(self, issue: Issue) -> None:
        bucket = {SEVERITY_ERROR: self.errors,
                  SEVERITY_WARNING: self.warnings,
                  SEVERITY_INFO: self.info}[issue.severity]
        bucket.append(issue)

    def to_dict(self) -> dict:
        return {
            "errors": [i.to_dict() for i in self.errors],
            "warnings": [i.to_dict() for i in self.warnings],
            "info": [i.to_dict() for i in self.info],
            "completeness": self.completeness,
        }


def _filled(rec: TurnRecord, f: str) -> bool:
    """True, если метрика f в записи реально заполнялась."""
    return rec.filled is None or f in rec.filled


def validate_match(match: Match) -> ValidationReport:
    """Полная проверка матча: структура, значения, согласованность."""
    rep = ValidationReport()
    add = rep.add

    names = match.player_names()
    if not names:
        add(Issue("E_NO_PLAYERS", SEVERITY_ERROR, "В матче нет ни одного игрока"))
        return rep
    if len(set(names)) != len(names):
        add(Issue("E_DUP_NAME", SEVERITY_ERROR, "Имена игроков должны быть уникальны"))

    seen: set[tuple[int, str]] = set()
    for r in match.records:
        if r.player not in names:
            add(Issue("E_UNKNOWN_PLAYER", SEVERITY_ERROR,
                      f"Неизвестный игрок: {r.player!r}", turn=r.turn, player=r.player))
            continue
        if r.turn < 0:
            add(Issue("E_NEGATIVE_TURN", SEVERITY_ERROR,
                      f"Отрицательный номер хода: {r.turn}", turn=r.turn, player=r.player))
        key = (r.turn, r.player)
        if key in seen:
            add(Issue("E_DUPLICATE", SEVERITY_ERROR,
                      f"Дубль записи: ход {r.turn}, {r.player}", turn=r.turn, player=r.player))
        seen.add(key)
        for f in METRIC_FIELDS:
            v = getattr(r, f)
            if v < 0:
                add(Issue("E_NEGATIVE", SEVERITY_ERROR,
                          f"Отрицательное значение «{f}»: {v}",
                          turn=r.turn, player=r.player, metric=f))

    for p in match.players:
        recs = match.records_for(p.name)
        if not recs:
            rep.completeness[p.name] = 0.0
            add(Issue("I_NO_RECORDS", SEVERITY_INFO,
                      f"У игрока {p.name} нет ни одной записи", player=p.name))
            continue

        filled_cells = sum(1 for r in recs for f in METRIC_FIELDS if _filled(r, f))
        ratio = round(filled_cells / (len(recs) * len(METRIC_FIELDS)), 3)
        rep.completeness[p.name] = ratio
        add(Issue("I_COMPLETENESS", SEVERITY_INFO,
                  f"Заполнено {ratio * 100:.0f}% ячеек", player=p.name))

        turns = [r.turn for r in recs]
        if len(turns) >= 2:
            missing = sorted(set(range(min(turns), max(turns) + 1)) - set(turns))
            if missing:
                shown = ", ".join(str(t) for t in missing[:10])
                if len(missing) > 10:
                    shown += "…"
                add(Issue("I_GAP", SEVERITY_INFO,
                          f"Пропущены ходы: {shown}", player=p.name))

        for prev, cur in zip(recs, recs[1:]):
            # баланс городов
            if (_filled(prev, "cities")
                    and all(_filled(cur, f) for f in ("cities", "captured", "lost_cities"))):
                expected = cur.captured - cur.lost_cities
                actual = cur.cities - prev.cities
                if actual != expected:
                    add(Issue("W_CITY_BALANCE", SEVERITY_WARNING,
                              f"Города: {prev.cities}→{cur.cities} ({actual:+d}), но "
                              f"захвачено {cur.captured}, потеряно {cur.lost_cities} "
                              f"(ожидалось {expected:+d})",
                              turn=cur.turn, player=p.name, metric="cities"))
            # убийства без урона
            if _filled(cur, "kills") and cur.kills > 0                     and _filled(cur, "damage") and cur.damage == 0:
                add(Issue("W_KILL_NO_DAMAGE", SEVERITY_WARNING,
                          f"Убито юнитов: {cur.kills}, но урон не указан",
                          turn=cur.turn, player=p.name, metric="damage"))
            # необъяснённое падение счёта
            if _filled(prev, "score") and _filled(cur, "score") and cur.score < prev.score:
                explained = (_filled(cur, "losses") and cur.losses > 0) or                             (_filled(cur, "lost_cities") and cur.lost_cities > 0)
                if not explained:
                    add(Issue("W_SCORE_DROP_UNEXPLAINED", SEVERITY_WARNING,
                              f"Счёт упал {prev.score:g}→{cur.score:g} без потерь за ход",
                              turn=cur.turn, player=p.name, metric="score"))
            # скачок дохода
            if _filled(prev, "income") and _filled(cur, "income")                     and cur.income > 2 * prev.income + 3:
                add(Issue("W_INCOME_SPIKE", SEVERITY_WARNING,
                          f"Доход вырос {prev.income:g}→{cur.income:g} за один ход",
                          turn=cur.turn, player=p.name, metric="income"))
            # армия больше, чем позволяет доход
            if _filled(prev, "army") and _filled(cur, "army") and _filled(cur, "income"):
                grown = cur.army - prev.army
                if grown > cur.income:
                    add(Issue("W_ARMY_SPIKE", SEVERITY_WARNING,
                              f"Армия +{grown} за ход при доходе {cur.income:g} ☆",
                              turn=cur.turn, player=p.name, metric="army"))

        # резкие скачки счёта
        deltas = [(cur.turn, cur.score - prev.score)
                  for prev, cur in zip(recs, recs[1:])
                  if _filled(prev, "score") and _filled(cur, "score")]
        if len(deltas) >= 3:
            median = statistics.median(d for _, d in deltas)
            if median > 0:
                for turn, d in deltas:
                    if d > 5 * median and d > 500:
                        add(Issue("I_SCORE_JUMP", SEVERITY_INFO,
                                  f"Резкий скачок счёта: +{d:g} (медиана +{median:g})",
                                  turn=turn, player=p.name, metric="score"))

    return rep
