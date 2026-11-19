#!/usr/bin/env python3
"""Генерирует демо-матч (3 игрока, 30 ходов) в CSV и JSON.

Данные синтетические, но правдоподобные: мирная раскачка, война в середине,
захват и потеря городов. Используется для примера отчёта.
"""

import random
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from polytopia_analyzer.models import Match, MatchMeta, Player, TurnRecord
from polytopia_analyzer.storage import save_match

TURNS = 30
SEED = 42

PLAYERS = [
    Player(name="koffeny301", tribe="Vengir", color="#e6194B", is_human=True),
    Player(name="Wolf_Star", tribe="Bardur", color="#4363d8"),
    Player(name="NovaAi", tribe="Ai-Mo", color="#3cb44b"),
]

# мирный рост городов (деревни/основание): игрок -> ходы
CITY_GROWTH = {
    "koffeny301": [6, 10, 17],
    "Wolf_Star": [7, 13, 21],
    "NovaAi": [9, 15],
}

# военные захваты: ход -> (кто захватил, у кого)
CITY_EVENTS = {
    14: ("koffeny301", "Wolf_Star"),
    16: ("Wolf_Star", "NovaAi"),
    19: ("koffeny301", "Wolf_Star"),
    25: ("Wolf_Star", "koffeny301"),   # ответный удар
    27: ("koffeny301", "Wolf_Star"),
}


def cities_at(name: str, t: int) -> int:
    """Число городов игрока на конец хода t."""
    n = 1 + sum(1 for gt in CITY_GROWTH.get(name, []) if gt <= t)
    for et, (winner, loser) in CITY_EVENTS.items():
        if et <= t:
            if name == winner:
                n += 1
            elif name == loser:
                n -= 1
    return max(0, n)


def build_match() -> Match:
    rnd = random.Random(SEED)
    records: dict[str, list[TurnRecord]] = {p.name: [] for p in PLAYERS}
    scores = {p.name: 420.0 + i * 30 for i, p in enumerate(PLAYERS)}
    cities = {p.name: 1 for p in PLAYERS}  # обновляется через cities_at()
    growth = {"koffeny301": 95, "Wolf_Star": 82, "NovaAi": 74}

    for t in range(1, TURNS + 1):
        war = 12 <= t <= 27
        for p in PLAYERS:
            name = p.name
            income = round(min(15, 2 + t * 0.35 + cities[name] * 0.8) + rnd.uniform(-0.5, 0.5), 1)
            gain = growth[name] * (0.7 + 0.06 * t) * rnd.uniform(0.85, 1.15)
            kills = losses = captured = lost_cities = 0
            damage = 0.0
            if war and rnd.random() < 0.55:
                kills = rnd.randint(0, 3) if name != "NovaAi" else rnd.randint(0, 2)
                losses = rnd.randint(0, 2)
                damage = round(kills * rnd.uniform(4, 8) + rnd.uniform(0, 6), 1)
            if t in CITY_EVENTS:
                winner, loser = CITY_EVENTS[t]
                if name == winner:
                    captured, kills = 1, max(kills, 2)
                    damage = max(damage, 15.0)
                elif name == loser:
                    lost_cities, losses = 1, max(losses, 2)
            if t in CITY_GROWTH.get(name, []):
                captured += 1  # захват деревни — тоже захват города
                damage = max(damage, 6.0)
            cities[name] = cities_at(name, t)
            army_base = 1 + int(t * 0.45) + cities[name]
            army = max(0, army_base + rnd.randint(-2, 2) + (2 if war else 0))
            prev_army = records[name][-1].army if records[name] else army
            army = min(army, prev_army + max(0, int(income)))  # не больше, чем куплено
            scores[name] += gain
            records[name].append(TurnRecord(
                turn=t, player=name,
                score=round(scores[name]),
                income=income, army=army, cities=cities[name],
                kills=kills, losses=losses,
                captured=captured, lost_cities=lost_cities,
                damage=damage,
            ))

    all_records = [r for name in records for r in records[name]]
    all_records.sort(key=lambda r: (r.turn, r.player))
    return Match(
        meta=MatchMeta(
            title="Демо-матч: Vengir против Bardur и Ai-Mo",
            date=date.today().isoformat(),
            mode="Domination",
            map="Large · Continent",
            link="",  # матч синтетический — реального реплея нет
            turn_limit=30,
            notes="Синтетические данные для демонстрации отчёта.",
        ),
        players=PLAYERS,
        records=all_records,
    )


if __name__ == "__main__":
    m = build_match()
    m.validate()
    here = Path(__file__).resolve().parent
    save_match(m, here / "demo_match.csv")
    save_match(m, here / "demo_match.json")
    print(f"OK: {len(m.records)} записей, ходов: {m.max_turn()}")
    print(f"  {here / 'demo_match.csv'}")
    print(f"  {here / 'demo_match.json'}")
