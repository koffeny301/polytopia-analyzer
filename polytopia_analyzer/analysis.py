"""Производная статистика и итоги матча."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .models import DELTA_FIELDS, Match

DEFAULT_COLORS = [
    "#e6194B", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
    "#42d4f4", "#f032e6", "#bfef45", "#fabed4", "#9A6324",
]


@dataclass
class PlayerStats:
    """Итоговая статистика игрока за матч."""

    name: str
    tribe: str
    color: str
    final_score: float = 0.0
    peak_score: float = 0.0
    avg_income: float = 0.0
    peak_army: int = 0
    final_cities: int = 0
    max_cities: int = 0
    total_kills: int = 0
    total_losses: int = 0
    total_captured: int = 0
    total_lost_cities: int = 0
    total_damage: float = 0.0
    kd: float = 0.0
    best_turn: Optional[int] = None
    best_turn_gain: float = 0.0
    score_growth: float = 0.0
    turns_recorded: int = 0


def player_color(match: Match, name: str) -> str:
    p = match.player(name)
    if p.color:
        return p.color
    idx = match.player_names().index(name)
    return DEFAULT_COLORS[idx % len(DEFAULT_COLORS)]


def player_stats(match: Match) -> dict[str, PlayerStats]:
    """Считает итоговую статистику каждого игрока."""
    out: dict[str, PlayerStats] = {}
    for p in match.players:
        recs = match.records_for(p.name)
        st = PlayerStats(name=p.name, tribe=p.tribe, color=player_color(match, p.name))
        if recs:
            st.turns_recorded = len(recs)
            st.final_score = recs[-1].score
            st.peak_score = max(r.score for r in recs)
            st.avg_income = sum(r.income for r in recs) / len(recs)
            st.peak_army = max(r.army for r in recs)
            st.final_cities = recs[-1].cities
            st.max_cities = max(r.cities for r in recs)
            st.total_kills = sum(r.kills for r in recs)
            st.total_losses = sum(r.losses for r in recs)
            st.total_captured = sum(r.captured for r in recs)
            st.total_lost_cities = sum(r.lost_cities for r in recs)
            st.total_damage = sum(r.damage for r in recs)
            st.score_growth = recs[-1].score - recs[0].score
            best_gain, best_turn = 0.0, None
            for prev, cur in zip(recs, recs[1:]):
                gain = cur.score - prev.score
                if gain > best_gain:
                    best_gain, best_turn = gain, cur.turn
            st.best_turn_gain = best_gain
            st.best_turn = best_turn if best_turn is not None else recs[0].turn
            if st.total_losses:
                st.kd = st.total_kills / st.total_losses
            else:
                st.kd = float(st.total_kills)
        out[p.name] = st
    return out


def standings(match: Match) -> list[PlayerStats]:
    """Итоговая таблица: по убыванию финального счёта."""
    stats = player_stats(match)
    return sorted(stats.values(), key=lambda s: (-s.final_score, s.name))


def score_series(match: Match) -> dict[str, list[tuple[int, float]]]:
    """(ход, счёт) по каждому игроку — для линейных графиков."""
    return {p.name: [(r.turn, r.score) for r in match.records_for(p.name)] for p in match.players}


def metric_series(match: Match, field_name: str) -> dict[str, list[tuple[int, float]]]:
    """(ход, значение снимочной метрики) по каждому игроку."""
    if field_name not in ("score", "income", "army", "cities"):
        raise ValueError(f"Не снимочная метрика: {field_name!r}")
    return {
        p.name: [(r.turn, getattr(r, field_name)) for r in match.records_for(p.name)]
        for p in match.players
    }


def cumulative_series(match: Match, field_name: str) -> dict[str, list[tuple[int, float]]]:
    """(ход, накопительный итог дельта-метрики) по каждому игроку."""
    if field_name not in DELTA_FIELDS:
        raise ValueError(f"Не дельта-метрика: {field_name!r}")
    out: dict[str, list[tuple[int, float]]] = {}
    for p in match.players:
        cum = 0
        points = []
        for r in match.records_for(p.name):
            cum += getattr(r, field_name)
            points.append((r.turn, cum))
        out[p.name] = points
    return out


def rank_history(match: Match) -> dict[str, list[tuple[int, int]]]:
    """Место игрока (1 = лидер) на каждом зафиксированном ходу.

    Между записями используется последний известный счёт.
    """
    turns = sorted({r.turn for r in match.records})
    last_score: dict[str, float] = {}
    history: dict[str, list[tuple[int, int]]] = {p.name: [] for p in match.players}
    for t in turns:
        for r in match.records:
            if r.turn == t:
                last_score[r.player] = r.score
        seen = [n for n in last_score]
        unseen = [p.name for p in match.players if p.name not in last_score]
        ordered = sorted(seen, key=lambda n: (-last_score[n], n)) + sorted(unseen)
        for rank, name in enumerate(ordered, start=1):
            history[name].append((t, rank))
    return history


def leader_changes(match: Match) -> list[tuple[int, str]]:
    """Смены лидера: (ход, новый лидер). Первая запись — первый лидер."""
    rh = rank_history(match)
    timeline: list[tuple[int, str]] = []
    for name, points in rh.items():
        for turn, rank in points:
            if rank == 1:
                timeline.append((turn, name))
    timeline.sort()
    changes: list[tuple[int, str]] = []
    for turn, name in timeline:
        if not changes or changes[-1][1] != name:
            changes.append((turn, name))
    return changes


def awards(match: Match) -> list[dict]:
    """Номинации по итогам матча (только с ненулевыми достижениями)."""
    stats = list(player_stats(match).values())
    if not stats:
        return []
    result: list[dict] = []

    def best(key, min_value=0):
        candidate = max(stats, key=lambda s: getattr(s, key))
        return candidate if getattr(candidate, key) > min_value else None

    w = best("total_kills")
    if w:
        result.append({"title": "⚔️ Воин", "player": w.name,
                       "comment": f"{w.total_kills} уничтоженных юнитов противника"})
    e = best("avg_income")
    if e:
        result.append({"title": "💰 Экономист", "player": e.name,
                       "comment": f"средний доход {e.avg_income:.1f} ☆ за ход"})
    c = best("total_captured")
    if c:
        result.append({"title": "🏙️ Завоеватель", "player": c.name,
                       "comment": f"захвачено городов: {c.total_captured}"})
    snipers = [s for s in stats if s.total_kills > 0]
    if snipers:
        s = max(snipers, key=lambda s: s.kd)
        result.append({"title": "🎯 Снайпер", "player": s.name,
                       "comment": f"лучшее соотношение K/D: {s.kd:.2f}"})
    g = best("score_growth")
    if g:
        result.append({"title": "📈 Стратег", "player": g.name,
                       "comment": f"прирост счёта за матч: +{g.score_growth:g}"})
    return result
