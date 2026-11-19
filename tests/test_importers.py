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
    assert match.meta.turn_limit == 12  # динамически: последний фактический ход
    assert "состоянии" in match.meta.notes


def test_score_and_cities_are_snapshots(inbox):
    match = shim.match_from_inbox(inbox)
    kof = match.records_for("KOF")
    assert [r.turn for r in kof] == list(range(1, 13))  # ход 0 — база, не запись
    assert kof[-1].score == 4485
    assert kof[-1].cities == 7
    assert [r.score for r in kof[:3]] == [565, 595, 890]


def test_kills_and_losses_are_deltas(inbox):
    snaps = shim.load_snapshots(inbox)
    match = shim.match_from_inbox(inbox)
    for name, pid in (("KOF", 1), ("Goylem", 2)):
        recs = {r.turn: r for r in match.records_for(name)}
        prev_k = prev_l = 0
        for snap in snaps:
            p = next(x for x in snap["players"] if x["id"] == pid)
            turn = int(snap["turn"])
            if turn >= 1:  # ход 0 — базовый снимок, записи не имеет
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


def test_full_pipeline_inbox_to_report(inbox, tmp_path):
    """Приёмка 6.5: папка снимков → матч → проверка → отчёт."""
    from polytopia_analyzer.report import build_report
    from polytopia_analyzer.storage import load_match, save_match

    match = shim.match_from_inbox(inbox, title="Timeless Battles",
                                  mode="Might", map_name="Archipelago")
    rep = validate_match(match)
    assert not rep.has_errors and not rep.warnings

    out = tmp_path / "accept.json"
    save_match(match, out)
    back = load_match(out)
    html = build_report(back, plotly_js=None)
    assert "Timeless Battles" in html
    assert "4485" in html and "2185" in html
    assert "состояние игры (шим)" in html
    assert back.meta.source == "shim"


def test_tribe_name_fallback():
    assert shim.tribe_name(16) == "Polaris"
    assert shim.tribe_name(99) == "Tribe99"
    assert shim.tribe_name(None) == ""


# ---------- снимки v2 (0.5.x): армия, доход, захваты ----------

V2_INBOX = Path(__file__).parent / "fixtures" / "shim-v2-inbox"


def test_v2_army_and_income():
    match = shim.match_from_inbox(V2_INBOX)
    kof = {r.turn: r for r in match.records_for("KOF")}
    goy = {r.turn: r for r in match.records_for("Goylem")}
    assert kof[12].army == 15 and goy[12].army == 9
    assert kof[12].income == 19 and goy[12].income == 7
    # на первом ходе у каждого по одному воину и разведчику, доход столицы 1
    assert kof[1].army == 2 and goy[1].army == 2
    assert kof[1].income == 1 and goy[1].income == 1


def test_v2_captured_from_city_diff():
    match = shim.match_from_inbox(V2_INBOX)
    caps = {(r.turn, r.player): r.captured for r in match.records if r.captured}
    assert caps == {(3, "KOF"): 1, (5, "Goylem"): 1, (6, "KOF"): 2,
                    (10, "KOF"): 1, (10, "Goylem"): 1, (11, "KOF"): 2}
    assert all(r.lost_cities == 0 for r in match.records)
    # сверка с журналом команд: CaptureCommand ровно столько же
    cmds = json.loads((V2_INBOX / "commands.json").read_text(encoding="utf-8"))
    n_cmd = sum(1 for c in cmds["commands"] if c["t"] == "CaptureCommand")
    assert n_cmd == sum(r.captured for r in match.records) == 8


def test_v2_cities_counter_consistency():
    """Счётчик городов согласован с диффом: прирост ≥ захватов − потерь."""
    match = shim.match_from_inbox(V2_INBOX)
    for name in ("KOF", "Goylem"):
        recs = match.records_for(name)
        for prev, cur in zip(recs, recs[1:]):
            assert cur.cities - prev.cities >= cur.captured - cur.lost_cities


def test_v2_filled_and_validation():
    match = shim.match_from_inbox(V2_INBOX)
    assert sorted({r.turn for r in match.records}) == list(range(1, 13))  # без хода 0
    expected = {"score", "cities", "kills", "losses",
                "army", "income", "captured", "lost_cities"}
    for r in match.records:
        assert r.filled == expected
    rep = validate_match(match)
    assert not rep.has_errors
    assert rep.completeness["KOF"] > 0.8  # 8 из 9 метрик


def test_v2_income_explains_star_dynamics():
    """Доход городов согласован с динамикой звёзд: Δstars ≤ income (трата+награда).

    Точное равенство не гарантировано: звёзды ещё дают награды руин/городов,
    а траты уводят баланс в минус. Проверяем мягкую границу сверху.
    """
    snaps = {int(s["turn"]): s for s in shim.load_snapshots(V2_INBOX)}
    match = shim.match_from_inbox(V2_INBOX)
    for name, pid in (("KOF", 1), ("Goylem", 2)):
        for r in match.records_for(name):
            if r.turn == 0:
                continue
            prev_p = next(p for p in snaps[r.turn - 1]["players"] if p["id"] == pid)
            cur_p = next(p for p in snaps[r.turn]["players"] if p["id"] == pid)
            delta = cur_p["stars"] - prev_p["stars"]
            assert delta <= r.income + 10  # награды не безграничны


def test_v2_full_pipeline_to_report(tmp_path):
    from polytopia_analyzer.report import build_report
    from polytopia_analyzer.storage import load_match, save_match

    match = shim.match_from_inbox(V2_INBOX, title="Timeless Battles v2")
    rep = validate_match(match)
    assert not rep.has_errors
    out = tmp_path / "v2.json"
    save_match(match, out)
    html = build_report(load_match(out), plotly_js=None)
    assert "Timeless Battles v2" in html
    assert "4485" in html and "2185" in html


# ---------- v3 (0.8.0): урон по журналу команд и HP-дельтам ----------

V3_INBOX = Path(__file__).parent / "fixtures" / "shim-v3-inbox"


def test_v3_damage_per_turn():
    match = shim.match_from_inbox(V3_INBOX)
    dmg = {(r.turn, r.player): r.damage for r in match.records if r.damage}
    assert dmg == {(6, "Goylem"): 6.0, (11, "KOF"): 5.0, (11, "Goylem"): 10.0,
                   (12, "KOF"): 20.0, (12, "Goylem"): 27.0}


def test_v3_damage_totals_filled_and_validation():
    match = shim.match_from_inbox(V3_INBOX)
    tot = {p: sum(r.damage for r in match.records_for(p)) for p in ("KOF", "Goylem")}
    assert tot == {"KOF": 25.0, "Goylem": 43.0}
    expected = {"score", "cities", "kills", "losses", "army", "income",
                "captured", "lost_cities", "damage"}
    for r in match.records:
        assert r.filled == expected
    rep = validate_match(match)
    assert not rep.has_errors
    assert rep.completeness["KOF"] == 1.0  # 9 из 9 метрик


def test_v3_turn_zero_is_baseline_only():
    match = shim.match_from_inbox(V3_INBOX)
    assert min(r.turn for r in match.records) == 1
    assert match.meta.turn_limit == 12


def test_v3_command_turns_mapping():
    cmds = shim.load_commands(V3_INBOX)
    assert cmds and cmds["v"] == 2
    turns = shim._command_turns(cmds["commands"])
    assert len(turns) == cmds["count"]
    assert turns[0] == 1
    assert max(turns) == 13  # EndMatch после последнего круга EndTurn


def test_v2_inbox_without_commands_v2_has_no_damage():
    """Обратная совместимость: commands.json v1 → урон не заполняется."""
    match = shim.match_from_inbox(V2_INBOX)
    assert all("damage" not in (r.filled or set()) for r in match.records)