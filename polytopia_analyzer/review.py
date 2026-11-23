"""Разбор матча в стиле chess.com Game Review — эвристический.

Работает на записях матча (TurnRecord) и не требует данных шима: подходит
и для снятых реплеев, и для матчей, введённых вручную.

Что считается:
  * классификация каждого хода: «Отличный ход» / «Хорошо» / «Ошибка» / «Зевк»
    по боевым исходам хода (правила ниже, все пороги — эвристика);
  * «точность» игрока — средний балл ходов (как accuracy в chess.com);
  * кривая оценки — преимущество игрока относительно среднего соперника
    в условных единицах [-100, +100] (счёт 50%, армия 20%, города 20%,
    доход 10%);
  * ключевые моменты — захваты/потери городов, массовые убийства, всплески
    урона.

Чего здесь НЕТ по сравнению с chess.com: движка и «лучшего хода» (симулятор
игры не строим), калиброванного win rate (нужен большой датасет матчей).
Оценка — эвристика «на глаз», а не вероятность победы.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Match, TurnRecord

# ---------- классы ходов ----------

GREAT = "great"
GOOD = "good"
MISTAKE = "mistake"
BLUNDER = "blunder"

CLASS_LABELS = {
    GREAT: "Отличный ход",
    GOOD: "Хорошо",
    MISTAKE: "Ошибка",
    BLUNDER: "Зевк",
}
# вклад класса в «точность» игрока (схема как в chess.com: средний балл ходов)
CLASS_ACCURACY = {GREAT: 100.0, GOOD: 95.0, MISTAKE: 70.0, BLUNDER: 30.0}

# веса компонентов кривой оценки
EVAL_WEIGHTS = {"score": 0.5, "army": 0.2, "cities": 0.2, "income": 0.1}
# пороги ключевых моментов и «отличных» ходов
GREAT_DAMAGE = 15.0
MOMENT_KILLS = 3
MOMENT_DAMAGE = 20.0
MAX_MOMENTS = 40


@dataclass
class TurnVerdict:
    """Вердикт по одному ходу игрока."""
    turn: int
    player: str
    cls: str
    reason: str = ""

    @property
    def label(self) -> str:
        return CLASS_LABELS[self.cls]


@dataclass
class PlayerReview:
    """Итог разбора по одному игроку."""
    name: str
    accuracy: float = 0.0
    counts: dict = field(default_factory=dict)
    verdicts: list = field(default_factory=list)

    @property
    def notable(self) -> list:
        """Вердикты, которые стоит показывать (все кроме «Хорошо»)."""
        return [v for v in self.verdicts if v.cls != GOOD]


@dataclass
class MatchReview:
    """Разбор матча целиком."""
    players: dict
    eval_series: dict
    key_moments: list


def classify_turn(r: TurnRecord) -> tuple[str, str]:
    """Классифицирует ход: возвращает (класс, причина).

    Правила (эвристика, порядок важен):
      зевк     — потерян город; либо потеряно >=2 юнитов без единого убийства;
      отличный — захвачен город; либо >=2 убийств без потерь; либо >=15 урона;
      ошибка   — размен в минус (потери больше убийств, losses > 0);
      хорошо   — остальное (включая равные размены).
    """
    if r.lost_cities > 0:
        return BLUNDER, f"потерян город ({r.lost_cities})"
    if r.losses >= 2 and r.kills == 0:
        return BLUNDER, f"потеряно {r.losses} юнитов без ответа"
    if r.captured > 0:
        return GREAT, f"захвачено городов: {r.captured}"
    if r.kills >= 2 and r.losses == 0:
        return GREAT, f"убито {r.kills} юнитов без потерь"
    if r.damage >= GREAT_DAMAGE:
        return GREAT, f"нанесено {r.damage:g} урона"
    if r.losses > 0 and r.losses > r.kills:
        return MISTAKE, f"размен в минус: −{r.losses} / +{r.kills}"
    return GOOD, ""


def _composite(r: TurnRecord) -> dict:
    return {"score": float(r.score), "army": float(r.army),
            "cities": float(r.cities), "income": float(r.income)}


def _eval_for(rec: TurnRecord, opponents: list[TurnRecord]) -> float:
    """Преимущество игрока относительно среднего соперника, [-100, +100]."""
    if not opponents:
        return 0.0
    own = _composite(rec)
    adv = 0.0
    for metric, weight in EVAL_WEIGHTS.items():
        mean_opp = sum(_composite(o)[metric] for o in opponents) / len(opponents)
        denom = max(1.0, abs(mean_opp))
        adv += weight * (own[metric] - mean_opp) / denom
    return max(-100.0, min(100.0, round(100.0 * adv, 1)))


def review_match(match: Match) -> MatchReview:
    """Полный разбор матча: вердикты, точность, кривая оценки, моменты."""
    names = match.player_names()
    by_turn: dict[int, dict[str, TurnRecord]] = {}
    for r in match.records:
        by_turn.setdefault(r.turn, {})[r.player] = r

    reviews = {
        name: PlayerReview(name=name,
                           counts={GREAT: 0, GOOD: 0, MISTAKE: 0, BLUNDER: 0})
        for name in names
    }
    for turn in sorted(by_turn):
        for name in names:
            r = by_turn[turn].get(name)
            if r is None:
                continue
            cls, reason = classify_turn(r)
            reviews[name].verdicts.append(
                TurnVerdict(turn=turn, player=name, cls=cls, reason=reason))
            reviews[name].counts[cls] += 1
    for pr in reviews.values():
        n = len(pr.verdicts)
        if n:
            pr.accuracy = round(
                sum(CLASS_ACCURACY[v.cls] for v in pr.verdicts) / n, 1)

    eval_series: dict[str, list[tuple[int, float]]] = {name: [] for name in names}
    for turn in sorted(by_turn):
        recs = by_turn[turn]
        for name, rec in recs.items():
            if name not in eval_series:
                continue
            opp = [o for pname, o in recs.items() if pname != name]
            eval_series[name].append((turn, _eval_for(rec, opp)))

    key_moments: list[tuple[int, str, str]] = []
    for turn in sorted(by_turn):
        for name, r in by_turn[turn].items():
            if r.captured > 0:
                key_moments.append((turn, name, f"захват города (+{r.captured})"))
            if r.lost_cities > 0:
                key_moments.append((turn, name, f"потеря города (−{r.lost_cities})"))
            if r.kills >= MOMENT_KILLS:
                key_moments.append((turn, name, f"−{r.kills} юнитов врага"))
            if r.damage >= MOMENT_DAMAGE:
                key_moments.append((turn, name, f"урон {r.damage:g}"))
    key_moments.sort(key=lambda m: (m[0], m[1]))
    return MatchReview(players=reviews, eval_series=eval_series,
                       key_moments=key_moments[:MAX_MOMENTS])
