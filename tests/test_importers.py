"""Тесты импортёра снимков состояния игры (шим LD_PRELOAD)."""

import json
from pathlib import Path

import pytest

from polytopia_analyzer.importers import shim
from polytopia_analyzer.validation import validate_match

FIXTURE = Path(__file__).parent / "fixtures" / "replay-timeless-battles-shim.json"


@pytest.fixture()
def inbox(tmp_path):
    """Раскладывает эталонный набор снимков по файлам, как это делает шим."""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    d = tmp_path / "inbox"
    d.mkdir()
    for snap in data["turns"]:
        (d / f"snapshot-{int(snap['turn']):03d}.json").write_text(
            json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    return d


def test_load_snapshots_sorted(inbox):
    snaps = shim.load_snapshots(inbox)
    assert [int(s["turn"]) for s in snaps] == list(range(13))


def test_players_without_barbarians(inbox):
    match = shim.match_from_inbox(inbox)
    assert [p.name for p in match.players] == ["KOF", "Goylem"]
    assert match.player("KOF").tribe == "Polaris"
    assert match.player("Goylem").tribe == "Zebasi"


def test_meta_source_is_shim(inbox):
    match = shim.match_from_inbox(inbox, title="Timeless Battles", link="https://example/1")
    assert match.meta.source == "shim"
    assert match.meta.title == "Timeless Battles"
    assert match.meta.link == "https://example/1"
    assert match.meta.turn_limit == 13
    assert "состоянии" in match.meta.notes


def test_score_and_cities_are_snapshots(inbox):
    match = shim.match_from_inbox(inbox)
    kof = match.records_for("KOF")
    assert [r.turn for r in kof] == list(range(13))
    assert kof[-1].score == 4485
    assert kof[-1].cities == 7
    assert [r.score for r in kof[:3]] == [565, 565, 595]


def test_kills_and_losses_are_deltas(inbox):
    snaps = shim.load_snapshots(inbox)
    match = shim.match_from_inbox(inbox)
    for name, pid in (("KOF", 1), ("Goylem", 2)):
        recs = {r.turn: r for r in match.records_for(name)}
        prev_k = prev_l = 0
        for snap in snaps:
            p = next(x for x in snap["players"] if x["id"] == pid)
            turn = int(snap["turn"])
            assert recs[turn].kills == p["kills"] - prev_k
            assert recs[turn].losses == p["casualties"] - prev_l
            prev_k, prev_l = p["kills"], p["casualties"]


def test_unavailable_metrics_are_not_filled(inbox):
    match = shim.match_from_inbox(inbox)
    for r in match.records:
        assert r.filled == {"score", "cities", "kills", "losses"}
        assert "income" not in r.filled and "damage" not in r.filled


def test_match_passes_validation(inbox):
    match = shim.match_from_inbox(inbox)
    rep = validate_match(match)
    assert not rep.has_errors
    assert 0.4 < rep.completeness["KOF"] < 0.6  # 4 из 9 метрик


def test_roundtrip_json(inbox, tmp_path):
    from polytopia_analyzer.storage import load_match, save_match

    match = shim.match_from_inbox(inbox)
    out = tmp_path / "match.json"
    save_match(match, out)
    back = load_match(out)
    assert back.meta.source == "shim"
    assert back.max_turn() == 12
    assert back.records_for("Goylem")[-1].score == 2185


def test_scan_inbox_reports_turns(inbox):
    session = shim.scan_inbox(inbox)
    assert not session.is_empty
    assert session.turns == list(range(13))
    assert session.newest_mtime > 0


def test_empty_dir_raises(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert shim.scan_inbox(empty).is_empty
    with pytest.raises(ValueError):
        shim.load_snapshots(empty)


def test_missing_dir_raises(tmp_path):
    with pytest.raises(ValueError):
        shim.load_snapshots(tmp_path / "nope")


def test_broken_snapshot_raises(inbox):
    (inbox / "snapshot-999.json").write_text("{ не json", encoding="utf-8")
    with pytest.raises(ValueError):
        shim.load_snapshots(inbox)


def test_tribe_name_fallback():
    assert shim.tribe_name(16) == "Polaris"
    assert shim.tribe_name(99) == "Tribe99"
    assert shim.tribe_name(None) == ""