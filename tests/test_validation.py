"""Тесты валидации данных матча."""

from polytopia_analyzer.models import Match, MatchMeta, Player, TurnRecord
from polytopia_analyzer.storage import match_from_csv
from polytopia_analyzer.validation import validate_match

HEADER = "turn,player,score,income,army,cities,kills,losses,captured,lost_cities,damage"


def parse(rows, players=("A",)):
    text = "\n".join([f"# player: {p}" for p in players] + [HEADER] + rows)
    return match_from_csv(text)


def codes(report):
    return {i.code for i in report.errors + report.warnings + report.info}


FULL1 = "1,A,100,2,1,1,0,0,0,0,0"
FULL2 = "2,A,200,3,2,1,0,0,0,0,0"


def test_clean_match_has_no_errors_or_warnings():
    rep = validate_match(parse([FULL1, FULL2]))
    assert rep.errors == []
    assert rep.warnings == []
    assert not rep.has_errors


def test_no_players_is_error():
    rep = validate_match(Match())
    assert "E_NO_PLAYERS" in codes(rep)
    assert rep.has_errors


def test_duplicate_player_names_is_error():
    m = Match(players=[Player(name="A"), Player(name="A")])
    assert "E_DUP_NAME" in codes(validate_match(m))


def test_unknown_player_is_error():
    m = parse([FULL1])
    m.records.append(TurnRecord(turn=2, player="Z", score=1))
    rep = validate_match(m)
    assert "E_UNKNOWN_PLAYER" in codes(rep)


def test_duplicate_record_is_error():
    m = parse([FULL1])
    m.records.append(TurnRecord(turn=1, player="A", score=2))
    assert "E_DUPLICATE" in codes(validate_match(m))


def test_negative_metric_is_error_with_metric_and_turn():
    rep = validate_match(parse(["1,A,100,2,1,1,-1,0,0,0,0"]))
    hits = [i for i in rep.errors if i.code == "E_NEGATIVE"]
    assert hits and hits[0].metric == "kills" and hits[0].turn == 1


def test_city_balance_warning_fires():
    m = parse(["1,A,,,,1,,,,,", "2,A,,,,1,,,1,0,"])
    hits = [i for i in validate_match(m).warnings if i.code == "W_CITY_BALANCE"]
    assert hits and hits[0].turn == 2 and hits[0].player == "A"


def test_city_balance_ok_when_consistent():
    m = parse(["1,A,,,,1,,,,,", "2,A,,,,2,,,1,0,"])
    assert "W_CITY_BALANCE" not in codes(validate_match(m))


def test_city_balance_skipped_when_fields_not_filled():
    m = parse(["1,A,,,,1,,,,,", "2,A,200,,,,,,,,"])
    assert "W_CITY_BALANCE" not in codes(validate_match(m))


def test_kill_without_damage_warning():
    m = parse([FULL1, "2,A,200,2,1,1,2,,,,0"])
    assert "W_KILL_NO_DAMAGE" in codes(validate_match(m))


def test_kill_without_damage_skipped_when_damage_not_filled():
    m = parse([FULL1, "2,A,200,2,1,1,2,,,,"])
    assert "W_KILL_NO_DAMAGE" not in codes(validate_match(m))


def test_unexplained_score_drop_warning():
    m = parse(["1,A,500,2,1,1,,,,,,", "2,A,400,2,1,1,,,,,,"])
    assert "W_SCORE_DROP_UNEXPLAINED" in codes(validate_match(m))


def test_score_drop_with_losses_is_not_warned():
    m = parse(["1,A,500,2,1,1,,,,,,", "2,A,400,2,1,1,,1,,,,"])
    assert "W_SCORE_DROP_UNEXPLAINED" not in codes(validate_match(m))


def test_income_spike_warning():
    m = parse([FULL1, "2,A,200,10,1,1,,,,,,"])
    assert "W_INCOME_SPIKE" in codes(validate_match(m))


def test_army_spike_warning():
    m = parse([FULL1, "2,A,200,2,6,1,,,,,,"])
    assert "W_ARMY_SPIKE" in codes(validate_match(m))


def test_gap_info_lists_missing_turns():
    m = parse([FULL1, FULL2, "5,A,500,3,2,2,,,,,,"])
    hits = [i for i in validate_match(m).info if i.code == "I_GAP"]
    assert hits and "3" in hits[0].message and "4" in hits[0].message


def test_completeness_info_and_ratio():
    rep = validate_match(parse([FULL1, FULL2]))
    assert rep.completeness["A"] == 1.0
    assert "I_COMPLETENESS" in codes(rep)


def test_score_jump_info():
    rows = ["1,A,100,2,1,1,,,,,,", "2,A,200,2,1,1,,,,,,",
            "3,A,300,2,1,1,,,,,,", "4,A,400,2,1,1,,,,,,",
            "5,A,2400,2,1,1,,,,,,"]
    hits = [i for i in validate_match(parse(rows)).info if i.code == "I_SCORE_JUMP"]
    assert hits and hits[0].turn == 5


def test_report_to_dict_shape():
    d = validate_match(parse(["1,A,100,2,1,1,-1,0,0,0,0"])).to_dict()
    assert set(d) == {"errors", "warnings", "info", "completeness"}
    e = d["errors"][0]
    assert set(e) == {"code", "severity", "message", "turn", "player", "metric"}


def test_no_records_player_gets_info():
    m = parse([FULL1], players=("A", "B"))
    assert "I_NO_RECORDS" in codes(validate_match(m))
