"""Тесты командной строки."""

import pytest

from polytopia_analyzer.cli import main
from polytopia_analyzer.storage import load_match

UUID = "a4d531fd-1b81-428c-d81f-08dae2baaab2"

SAMPLE_CSV = """# title: CLI тест
# mode: Domination
# player: A; Bardur
# player: B; Quetzali
turn,player,score,income,army,cities,kills,losses,captured,lost_cities,damage
1,A,100,2,1,1,0,0,0,0,0
1,B,120,3,2,1,0,0,0,0,0
2,A,250,4,3,1,2,0,0,0,10
2,B,180,3,4,1,0,2,0,0,0
3,A,400,5,2,2,1,1,1,0,4
3,B,350,6,3,1,0,0,0,1,0
"""


@pytest.fixture
def sample_csv(tmp_path):
    p = tmp_path / "match.csv"
    p.write_text(SAMPLE_CSV, encoding="utf-8")
    return p


def test_analyze_creates_report_and_prints_summary(sample_csv, tmp_path, capsys):
    out = tmp_path / "report.html"
    rc = main(["analyze", str(sample_csv), "-o", str(out)])
    assert rc == 0
    html = out.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html
    assert "CLI тест" in html
    captured = capsys.readouterr().out
    assert "A" in captured and "Победа" in captured
    assert str(out) in captured


@pytest.fixture
def shim_inbox(tmp_path):
    """Папка со снимками шима, как её оставляет игра."""
    import json
    from pathlib import Path

    data = json.loads((Path(__file__).parent / "fixtures" /
                       "replay-timeless-battles-shim.json").read_text(encoding="utf-8"))
    d = tmp_path / "inbox"
    d.mkdir()
    for snap in data["turns"]:
        (d / f"snapshot-{int(snap['turn']):03d}.json").write_text(
            json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    return d


def test_import_inbox_creates_match(shim_inbox, tmp_path, capsys):
    out = tmp_path / "shim.json"
    rc = main(["import-inbox", "--dir", str(shim_inbox), "-o", str(out),
               "--title", "Timeless Battles", "--mode", "Might"])
    assert rc == 0
    match = load_match(out)
    assert match.meta.source == "shim"
    assert match.meta.title == "Timeless Battles"
    assert match.player_names() == ["KOF", "Goylem"]
    assert match.max_turn() == 12
    assert match.records_for("KOF")[-1].score == 4485
    printed = capsys.readouterr().out
    assert "Победа: KOF" in printed


VDF = """"UserLocalConfigStore"
{
\t"Software"
\t{
\t\t"Valve"
\t\t{
\t\t\t"Steam"
\t\t\t{
\t\t\t\t"apps"
\t\t\t\t{
\t\t\t\t\t"874390"
\t\t\t\t\t{
\t\t\t\t\t\t"LastPlayed"\t\t"1794524800"
\t\t\t\t\t}
\t\t\t\t\t"881100"
\t\t\t\t\t{
\t\t\t\t\t\t"Playtime"\t\t"177"
\t\t\t\t\t}
\t\t\t\t}
\t\t\t}
\t\t}
\t}
}
"""


@pytest.fixture
def steam_env(tmp_path, monkeypatch):
    from polytopia_analyzer import steam

    cfg = tmp_path / "localconfig.vdf"
    cfg.write_text(VDF, encoding="utf-8")
    shim = tmp_path / "libpolyread_dump.so"
    shim.write_bytes(b"\x7fELF fake")
    monkeypatch.setattr(steam, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(steam, "is_steam_running", lambda: False)
    monkeypatch.setattr(steam, "start_steam", lambda: None)
    return cfg, shim


def test_install_shim_enable_status_disable(steam_env, capsys):
    cfg, shim = steam_env
    rc = main(["install-shim", "enable", "--config", str(cfg), "--shim", str(shim)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Включено чтение" in out
    assert '"LaunchOptions"' in cfg.read_text(encoding="utf-8")

    rc = main(["install-shim", "status", "--config", str(cfg), "--shim", str(shim)])
    assert rc == 0
    assert "включено" in capsys.readouterr().out

    rc = main(["install-shim", "disable", "--config", str(cfg)])
    assert rc == 0
    assert "Откат выполнен" in capsys.readouterr().out
    assert '"LaunchOptions"' not in cfg.read_text(encoding="utf-8")


def test_install_shim_refuses_when_steam_running(steam_env, monkeypatch, capsys):
    from polytopia_analyzer import steam

    cfg, shim = steam_env
    monkeypatch.setattr(steam, "is_steam_running", lambda: True)
    rc = main(["install-shim", "enable", "--config", str(cfg), "--shim", str(shim)])
    assert rc == 1
    assert "Steam запущен" in capsys.readouterr().out


def test_import_inbox_empty_dir_fails(tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    rc = main(["import-inbox", "--dir", str(empty), "-o", str(tmp_path / "x.json")])
    assert rc == 1
    assert "нет снимков" in capsys.readouterr().out


# ---------- import-state (.state-сейвы, веха M7) ----------

def _state_fixture():
    from pathlib import Path
    return Path(__file__).parent / "fixtures" / "singleplayer-v124.state"


def test_import_state_creates_match(tmp_path, capsys):
    out = tmp_path / "state.json"
    rc = main(["import-state", str(_state_fixture()), "-o", str(out),
               "--title", "Первая партия"])
    assert rc == 0
    match = load_match(out)
    assert match.meta.source == "state"
    assert match.meta.title == "Первая партия"
    assert match.meta.turn_limit == 30
    assert match.player_names() == ["KOF", "Shayoszu"]
    assert match.records_for("KOF")[0].score == 505
    printed = capsys.readouterr().out
    assert "Сейв:" in printed
    assert "ошибок 0" in printed


def test_import_state_missing_file(tmp_path, capsys):
    rc = main(["import-state", str(tmp_path / "nope.state"),
               "-o", str(tmp_path / "x.json")])
    assert rc == 1
    assert "не найден" in capsys.readouterr().out.lower()


def test_import_state_bad_file(tmp_path, capsys):
    bad = tmp_path / "bad.state"
    bad.write_bytes(b"this is definitely not a polytopia save file")
    rc = main(["import-state", str(bad), "-o", str(tmp_path / "x.json")])
    assert rc == 1
    assert "Ошибка разбора сейва" in capsys.readouterr().out


def test_import_state_midgame(tmp_path, capsys):
    from pathlib import Path
    mid = Path(__file__).parent / "fixtures" / "singleplayer-v124-midgame.state"
    out = tmp_path / "mid.json"
    rc = main(["import-state", str(mid), "-o", str(out)])
    assert rc == 0
    match = load_match(out)
    assert len(match.player_names()) == 16
    assert match.max_turn() == 1
    assert match.player("KOF").tribe == "Luxidoor"
    assert [r.turn for r in match.records_for("KOF")] == [0, 1]


def test_analyze_default_output_path(sample_csv, capsys):
    rc = main(["analyze", str(sample_csv)])
    assert rc == 0
    default = sample_csv.with_name("match_report.html")
    assert default.exists()


def test_analyze_missing_file_returns_error(tmp_path, capsys):
    rc = main(["analyze", str(tmp_path / "nope.csv")])
    assert rc == 1
    assert "не найден" in capsys.readouterr().out.lower() or "ошибка" in capsys.readouterr().out.lower()


def test_template_creates_fillable_csv(tmp_path):
    out = tmp_path / "t.csv"
    rc = main(["template", "--players", "X,Y", "--turns", "5", "-o", str(out),
               "--title", "Новый матч"])
    assert rc == 0
    m = load_match(out)
    assert m.player_names() == ["X", "Y"]
    assert m.meta.title == "Новый матч"
    # пустой шаблон: незаполненные строки игнорируются при загрузке
    assert len(m.records) == 0


def test_template_records_turn_limit_and_link(tmp_path):
    out = tmp_path / "t.csv"
    url = "https://share.polytopia.io/g/35b44c27-a0a2-4f52-3290-08df4019957a"
    rc = main(["template", "--players", "X,Y", "--turns", "7", "-o", str(out),
               "--link", url])
    assert rc == 0
    assert "# turn_limit: 7" in out.read_text(encoding="utf-8")
    m = load_match(out)
    assert m.meta.turn_limit == 7
    assert m.meta.link == url


def test_convert_csv_to_json_roundtrip(sample_csv, tmp_path):
    j = tmp_path / "match.json"
    back = tmp_path / "back.csv"
    assert main(["convert", str(sample_csv), str(j)]) == 0
    assert main(["convert", str(j), str(back)]) == 0
    m1 = load_match(sample_csv)
    m2 = load_match(back)
    assert m1.players == m2.players
    assert len(m1.records) == len(m2.records)


def test_open_link_prints_deeplinks(capsys):
    rc = main(["open", f"https://share.polytopia.io/g/{UUID}"])
    assert rc == 0
    out = capsys.readouterr().out
    assert f"steam://run/874390//opengame?id={UUID}" in out
    assert f"polytopia://opengame?id={UUID}" in out


def test_open_invalid_link_returns_error(capsys):
    rc = main(["open", "https://example.com/whatever"])
    assert rc == 1


def test_template_partial_fill_analyze_ignores_empty_turns(tmp_path, capsys):
    out = tmp_path / "t.csv"
    assert main(["template", "--players", "P,Q", "--turns", "3", "-o", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    text = text.replace("1,P,,,,,,,,,", "1,P,100,2,1,1,0,0,0,0,0")
    text = text.replace("1,Q,,,,,,,,,", "1,Q,90,2,1,1,0,0,0,0,0")
    text = text.replace("2,P,,,,,,,,,", "2,P,250,4,2,1,1,0,0,0,5")
    text = text.replace("2,Q,,,,,,,,,", "2,Q,180,3,2,1,0,1,0,0,0")
    out.write_text(text, encoding="utf-8")
    report = tmp_path / "r.html"
    assert main(["analyze", str(out), "-o", str(report)]) == 0
    summary = capsys.readouterr().out
    assert "Победа: P" in summary
    assert "250" in summary
    assert report.exists()


def test_cmd_validate_reports_counts(sample_csv, capsys):
    rc = main(["validate", str(sample_csv)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Ошибок: 0" in out
    assert "Предупреждений: 1" in out  # у Бота cities не сходится с lost_cities


def test_cmd_validate_errors_exit_1(tmp_path, capsys):
    p = tmp_path / "bad.csv"
    p.write_text("# player: A\nturn,player,score\n1,A,-5\n", encoding="utf-8")
    rc = main(["validate", str(p)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "E_NEGATIVE" in out


def test_analyze_prints_validation_summary(sample_csv, tmp_path, capsys):
    rc = main(["analyze", str(sample_csv), "-o", str(tmp_path / "r.html")])
    assert rc == 0
    assert "Проверка данных" in capsys.readouterr().out
