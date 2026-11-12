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
    assert len(m.records) == 10
    assert all(r.score == 0 for r in m.records)


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
