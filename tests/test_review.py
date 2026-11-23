"""Тесты разбора матча в стиле chess.com (review.py) и его секции в отчёте."""

from pathlib import Path

from polytopia_analyzer.importers import shim
from polytopia_analyzer.models import Match, MatchMeta, Player, TurnRecord
from polytopia_analyzer.report import build_report
from polytopia_analyzer.review import (BLUNDER, GOOD, GREAT, MISTAKE,
                                       classify_turn, review_match)

V3_INBOX = Path(__file__).parent / "fixtures" / "shim-v3-inbox"


def make_review_match() -> Match:
    meta = MatchMeta(title="Тест разбора", turn_limit=4)
    players = [Player(name="A"), Player(name="B")]
    records = [
        # ход 1: тихо, B чуть впереди по счёту
        TurnRecord(turn=1, player="A", score=100, income=2, army=1, cities=1),
        TurnRecord(turn=1, player="B", score=110, income=2, army=1, cities=1),
        # ход 2: A захватил город, B потерял
        TurnRecord(turn=2, player="A", score=250, income=4, army=3, cities=2,
                   kills=1, captured=1, damage=8),
        TurnRecord(turn=2, player="B", score=150, income=2, army=1, cities=0,
                   losses=1, lost_cities=1),
        # ход 3: A размен в минус
        TurnRecord(turn=3, player="A", score=280, income=4, army=2, cities=2,
                   kills=1, losses=2),
        TurnRecord(turn=3, player="B", score=200, income=3, army=2, cities=1),
        # ход 4: A добивает без потерь, B теряет двух юнитов без ответа
        TurnRecord(turn=4, player="A", score=400, income=5, army=3, cities=2,
                   kills=2, damage=24),
        TurnRecord(turn=4, player="B", score=260, income=3, army=1, cities=1,
                   losses=2, damage=4),
    ]
    return Match(meta=meta, players=players, records=records)


def test_classify_rules():
    cases = [
        (TurnRecord(turn=1, player="X", lost_cities=1, captured=1), BLUNDER),
        (TurnRecord(turn=1, player="X", losses=2, kills=0), BLUNDER),
        (TurnRecord(turn=1, player="X", captured=1), GREAT),
        (TurnRecord(turn=1, player="X", kills=2), GREAT),
        (TurnRecord(turn=1, player="X", damage=15), GREAT),
        (TurnRecord(turn=1, player="X", kills=1, losses=2), MISTAKE),
        (TurnRecord(turn=1, player="X", kills=1, losses=1), GOOD),
        (TurnRecord(turn=1, player="X", income=5), GOOD),
    ]
    for rec, expected in cases:
        assert classify_turn(rec)[0] == expected


def test_accuracy_ordering():
    rv = review_match(make_review_match())
    a, b = rv.players["A"], rv.players["B"]
    assert a.counts == {GREAT: 2, GOOD: 1, MISTAKE: 1, BLUNDER: 0}
    assert b.counts[BLUNDER] == 2
    assert a.accuracy == 91.2   # (95+100+70+100)/4
    assert b.accuracy == 62.5   # (95+30+95+30)/4
    assert a.accuracy > b.accuracy


def test_eval_follows_lead():
    rv = review_match(make_review_match())
    ea = dict(rv.eval_series["A"])
    eb = dict(rv.eval_series["B"])
    assert ea[1] < 0            # на ходу 1 ведёт B
    assert ea[4] > 0 > eb[4]    # к концу ведёт A
    assert all(-100 <= v <= 100 for v in list(ea.values()) + list(eb.values()))


def test_key_moments():
    rv = review_match(make_review_match())
    assert any(t == 2 and n == "A" and "захват" in x for t, n, x in rv.key_moments)
    assert any(t == 2 and n == "B" and "потеря города" in x for t, n, x in rv.key_moments)
    assert any(t == 4 and n == "A" and "урон" in x for t, n, x in rv.key_moments)


def test_single_player_and_empty_match():
    m = Match(meta=MatchMeta(), players=[Player(name="solo")],
              records=[TurnRecord(turn=1, player="solo", score=10)])
    rv = review_match(m)
    assert rv.eval_series["solo"] == [(1, 0.0)]   # нет соперников → 0
    assert rv.players["solo"].accuracy == 95.0
    empty = Match(meta=MatchMeta(), players=[Player(name="x")], records=[])
    rv2 = review_match(empty)
    assert rv2.players["x"].accuracy == 0.0
    assert rv2.key_moments == []


def test_report_review_section_and_gating():
    html = build_report(make_review_match(), plotly_js=None)
    assert 'id="chart-eval"' in html
    assert "Разбор матча" in html and "Ключевые моменты" in html
    assert "91.2%" in html and "62.5%" in html
    assert "Зевк: потерян город" in html
    # матч без боевых данных — секции разбора нет
    quiet = Match(meta=MatchMeta(title="q"),
                  players=[Player(name="A"), Player(name="B")],
                  records=[TurnRecord(turn=1, player="A", score=10, income=1, army=1, cities=1),
                           TurnRecord(turn=1, player="B", score=11, income=1, army=1, cities=1)])
    h2 = build_report(quiet, plotly_js=None)
    assert 'id="chart-eval"' not in h2 and "Разбор матча" not in h2


def test_review_on_real_v3_match():
    match = shim.match_from_inbox(V3_INBOX)
    rv = review_match(match)
    assert set(rv.players) == {"KOF", "Goylem"}
    for pr in rv.players.values():
        assert 0.0 <= pr.accuracy <= 100.0
        assert len(pr.verdicts) == 12
    assert rv.key_moments  # захваты и урон в эталонном реплее есть
