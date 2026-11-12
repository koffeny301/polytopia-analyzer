"""Тесты производной статистики матча."""

from polytopia_analyzer.analysis import (
    awards,
    cumulative_series,
    leader_changes,
    player_stats,
    rank_history,
    standings,
)
from polytopia_analyzer.models import Match, MatchMeta, Player, TurnRecord


def make_match() -> Match:
    players = [Player(name="A", tribe="Bardur"), Player(name="B", tribe="Quetzali")]
    records = [
        TurnRecord(turn=1, player="A", score=100, income=2, army=1, cities=1),
        TurnRecord(turn=1, player="B", score=120, income=3, army=2, cities=1),
        TurnRecord(turn=2, player="A", score=250, income=4, army=3, cities=1, kills=2, damage=10),
        TurnRecord(turn=2, player="B", score=180, income=3, army=4, cities=1, losses=2),
        TurnRecord(turn=3, player="A", score=400, income=5, army=2, cities=2, kills=1, losses=1, captured=1, damage=4),
        TurnRecord(turn=3, player="B", score=350, income=6, army=3, cities=1, lost_cities=1),
    ]
    return Match(meta=MatchMeta(title="t"), players=players, records=records)


def test_standings_sorted_by_final_score():
    s = standings(make_match())
    assert [p.name for p in s] == ["A", "B"]
    assert s[0].final_score == 400


def test_player_stats_totals_and_derived():
    st = player_stats(make_match())["A"]
    assert st.total_kills == 3
    assert st.total_losses == 1
    assert st.kd == 3.0
    assert st.peak_army == 3
    assert st.total_damage == 14
    assert st.final_cities == 2
    assert st.max_cities == 2
    assert st.best_turn == 2
    assert st.best_turn_gain == 150
    assert abs(st.avg_income - 11 / 3) < 1e-9


def test_kd_without_losses_equals_kills():
    m = make_match()
    st = player_stats(m)["B"]
    assert st.total_kills == 0 and st.kd == 0.0


def test_rank_history_per_turn():
    rh = rank_history(make_match())
    assert dict(rh["B"])[1] == 1   # B лидировал на ходу 1
    assert dict(rh["A"])[1] == 2
    assert dict(rh["A"])[2] == 1   # A вышел вперёд на ходу 2
    assert dict(rh["A"])[3] == 1


def test_leader_changes():
    assert leader_changes(make_match()) == [(1, "B"), (2, "A")]


def test_cumulative_series():
    cs = cumulative_series(make_match(), "kills")
    assert cs["A"] == [(1, 0), (2, 2), (3, 3)]
    assert cs["B"] == [(1, 0), (2, 0), (3, 0)]


def test_awards_winners():
    aw = {a["title"].split()[-1]: a for a in awards(make_match())}
    assert aw["Воин"]["player"] == "A"
    assert aw["Экономист"]["player"] == "B"
    assert aw["Завоеватель"]["player"] == "A"
    assert aw["Снайпер"]["player"] == "A"
    assert aw["Стратег"]["player"] == "A"


def test_player_color_fallback():
    st = player_stats(make_match())
    assert st["A"].color.startswith("#")
    assert st["A"].color != st["B"].color
