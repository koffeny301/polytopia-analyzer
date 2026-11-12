"""Тесты модели данных и загрузки/сохранения JSON/CSV."""

import pytest

from polytopia_analyzer.models import Match, MatchMeta, Player, TurnRecord
from polytopia_analyzer.storage import (
    load_match,
    match_from_csv,
    match_from_json,
    match_to_csv,
    match_to_json,
    save_match,
)


def make_match() -> Match:
    meta = MatchMeta(
        title="Тестовый матч",
        mode="Domination",
        link="https://share.polytopia.io/g/a4d531fd-1b81-428c-d81f-08dae2baaab2",
        turn_limit=30,
    )
    players = [
        Player(name="Вася", tribe="Bardur", color="#e6194B", is_human=True),
        Player(name="Петя", tribe="Quetzali"),
    ]
    records = [
        TurnRecord(turn=1, player="Вася", score=520, income=3, army=2, cities=1),
        TurnRecord(turn=1, player="Петя", score=480, income=2, army=1, cities=1),
        TurnRecord(turn=2, player="Вася", score=610, income=4, army=3, cities=1, kills=1, damage=5),
        TurnRecord(turn=2, player="Петя", score=590, income=3, army=2, cities=2, captured=1),
    ]
    return Match(meta=meta, players=players, records=records)


def test_json_roundtrip_preserves_data():
    m = make_match()
    restored = match_from_json(match_to_json(m))
    assert restored.meta.title == "Тестовый матч"
    assert restored.meta.turn_limit == 30
    assert restored.players == m.players
    assert len(restored.records) == 4
    by_key = {(r.turn, r.player): r for r in restored.records}
    assert by_key[(2, "Вася")].kills == 1
    assert by_key[(2, "Вася")].damage == 5.0
    assert by_key[(2, "Петя")].captured == 1


def test_csv_roundtrip_preserves_data():
    m = make_match()
    restored = match_from_csv(match_to_csv(m))
    assert restored.meta.title == "Тестовый матч"
    assert restored.meta.link == m.meta.link
    assert restored.players == m.players
    by_key = {(r.turn, r.player): r for r in restored.records}
    assert by_key[(1, "Вася")].score == 520.0
    assert by_key[(2, "Петя")].cities == 2


def test_csv_parses_meta_and_players_from_comments():
    csv_text = """# title: Битва титанов
# mode: Perfection
# player: A; Bardur; #ff0000; human
# player: B; Aquarion
turn,player,score,income,army,cities,kills,losses,captured,lost_cities,damage
1,A,100,2,1,1,0,0,0,0,0
1,B,90,1,1,1,0,0,0,0,0
"""
    m = match_from_csv(csv_text)
    assert m.meta.title == "Битва титанов"
    assert m.meta.mode == "Perfection"
    assert m.players[0] == Player(name="A", tribe="Bardur", color="#ff0000", is_human=True)
    assert m.players[1] == Player(name="B", tribe="Aquarion")
    assert len(m.records) == 2


def test_csv_allows_missing_metric_columns():
    csv_text = """# player: A
# player: B
turn,player,score
1,A,100
1,B,90
"""
    m = match_from_csv(csv_text)
    assert m.records[0].kills == 0
    assert m.records[0].army == 0


def test_unknown_player_in_records_raises():
    m = make_match()
    m.records.append(TurnRecord(turn=3, player="Федя", score=1))
    with pytest.raises(ValueError, match="неизвестного игрока"):
        m.validate()


def test_duplicate_turn_record_raises():
    m = make_match()
    m.records.append(TurnRecord(turn=1, player="Вася", score=999))
    with pytest.raises(ValueError, match="Дублирующаяся"):
        m.validate()


def test_records_for_returns_sorted_by_turn():
    m = make_match()
    m.records.append(TurnRecord(turn=10, player="Вася", score=900))
    turns = [r.turn for r in m.records_for("Вася")]
    assert turns == [1, 2, 10]


def test_max_turn():
    assert make_match().max_turn() == 2


def test_load_save_files_json_and_csv(tmp_path):
    m = make_match()
    pj = tmp_path / "match.json"
    pc = tmp_path / "match.csv"
    save_match(m, pj)
    save_match(m, pc)
    assert load_match(pj).players == m.players
    assert len(load_match(pc).records) == 4


def test_json_rejects_unknown_meta_fields():
    bad = '{"meta": {"unknown_field": 1}, "players": [{"name": "A"}], "turns": []}'
    with pytest.raises(ValueError, match="Неизвестные поля"):
        match_from_json(bad)


def test_json_rejects_non_numeric_metric():
    bad = '{"players": [{"name": "A"}], "turns": [{"turn": 1, "player": "A", "score": "много"}]}'
    with pytest.raises(ValueError, match="число"):
        match_from_json(bad)
