"""Тесты локального веб-интерфейса (HTTP API webapp)."""

import json
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from polytopia_analyzer import webapp

MATCH = {
    "meta": {"title": "Веб-тест", "mode": "Domination"},
    "players": [{"name": "A", "tribe": "Bardur"}, {"name": "B"}],
    "turns": [
        {"turn": 1, "player": "A", "score": 100},
        {"turn": 1, "player": "B", "score": 120},
        {"turn": 2, "player": "A", "score": 250, "kills": 2},
        {"turn": 2, "player": "B", "score": 180, "losses": 2},
    ],
}

UUID = "a4d531fd-1b81-428c-d81f-08dae2baaab2"


@pytest.fixture()
def base(tmp_path):
    httpd = webapp.create_server(host="127.0.0.1", port=0, data_dir=tmp_path)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", tmp_path
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=5)


def req(url, method="GET", data=None, ctype="application/json"):
    body = None
    if data is not None:
        body = (
            data.encode("utf-8")
            if isinstance(data, str)
            else json.dumps(data, ensure_ascii=False).encode("utf-8")
        )
    r = urllib.request.Request(
        url, data=body, method=method,
        headers={"Content-Type": ctype} if body is not None else {},
    )
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def jload(text):
    return json.loads(text)


def test_index_served(base):
    url, _ = base
    status, body = req(url + "/")
    assert status == 200
    assert "<!DOCTYPE html>" in body
    assert "Polytopia Analyzer" in body


def test_health(base):
    url, _ = base
    status, body = req(url + "/api/health")
    assert status == 200
    assert jload(body)["ok"] is True


def test_match_crud(base):
    url, data_dir = base
    s, b = req(url + "/api/matches/demo", "POST", {"match": MATCH})
    assert s == 200 and jload(b)["name"] == "demo.json"
    assert (data_dir / "demo.json").exists()

    s, b = req(url + "/api/matches")
    items = jload(b)["matches"]
    assert len(items) == 1
    assert items[0]["title"] == "Веб-тест"
    assert items[0]["players"] == ["A", "B"]
    assert items[0]["turns"] == 2

    s, b = req(url + "/api/matches/demo")
    assert len(jload(b)["match"]["turns"]) == 4

    s, b = req(url + "/api/matches/demo?format=csv")
    assert s == 200
    assert "turn,player,score" in b and "# title: Веб-тест" in b

    s, b = req(url + "/api/matches/demo", "DELETE")
    assert s == 200
    s, b = req(url + "/api/matches")
    assert jload(b)["matches"] == []
    s, _ = req(url + "/api/matches/demo", "DELETE")
    assert s == 404


def test_save_match_with_cyrillic_name(base):
    url, data_dir = base
    name = urllib.parse.quote("матч-1")
    s, b = req(f"{url}/api/matches/{name}", "POST", {"match": MATCH})
    assert s == 200
    assert (data_dir / "матч-1.json").exists()


def test_save_invalid_match_returns_400(base):
    url, _ = base
    bad = json.loads(json.dumps(MATCH))
    bad["turns"][0]["player"] = "Неизвестный"
    s, b = req(url + "/api/matches/bad", "POST", {"match": bad})
    assert s == 400
    assert "неизвестного" in jload(b)["error"]


def test_path_traversal_rejected(base):
    url, _ = base
    s, _ = req(url + "/api/matches/..%2F..%2Fsecret")
    assert s == 400


def test_analyze_returns_report(base):
    url, _ = base
    s, b = req(url + "/api/analyze", "POST", {"match": MATCH})
    assert s == 200
    html = jload(b)["html"]
    assert "Победа: A" in html
    assert "Plotly.newPlot" in html


def test_upload_csv_autodetect(base):
    url, _ = base
    csv_text = (
        "# title: Загрузка\n# player: A\n# player: B\n"
        "turn,player,score\n1,A,10\n1,B,20\n"
    )
    s, b = req(url + "/api/upload", "POST", csv_text, ctype="text/csv")
    assert s == 200
    m = jload(b)["match"]
    assert m["meta"]["title"] == "Загрузка"
    assert len(m["turns"]) == 2


def test_upload_json_autodetect(base):
    url, _ = base
    s, b = req(url + "/api/upload", "POST", json.dumps(MATCH, ensure_ascii=False))
    assert s == 200
    assert jload(b)["match"]["players"][0]["name"] == "A"


def test_upload_broken_returns_400(base):
    url, _ = base
    s, b = req(url + "/api/upload", "POST", "это не таблица", ctype="text/csv")
    assert s == 400


def test_export_csv_and_json(base):
    url, _ = base
    s, b = req(url + "/api/export", "POST", {"match": MATCH, "format": "csv"})
    assert "turn,player,score" in jload(b)["text"]
    s, b = req(url + "/api/export", "POST", {"match": MATCH, "format": "json"})
    assert jload(jload(b)["text"])["meta"]["title"] == "Веб-тест"


def test_link_endpoint(base):
    url, _ = base
    s, b = req(url + "/api/link", "POST", {"link": f"https://share.polytopia.io/g/{UUID}"})
    d = jload(b)
    assert d["game_id"] == UUID
    assert "steam://run/874390" in d["steam"]
    s, b = req(url + "/api/link", "POST", {"link": "https://example.com/x"})
    assert s == 400


def test_safe_match_path_rejects_bad_names(tmp_path):
    for bad in ["", "..", "a/b", "a\\b", "../x"]:
        with pytest.raises(ValueError):
            webapp.safe_match_path(tmp_path, bad)
    p = webapp.safe_match_path(tmp_path, "ok")
    assert p.parent == tmp_path.resolve()
    assert p.name == "ok.json"


CITY_IMBALANCE = {
    "meta": {"title": "Дисбаланс"},
    "players": [{"name": "A"}],
    "turns": [
        {"turn": 1, "player": "A", "score": 100, "cities": 1},
        {"turn": 2, "player": "A", "score": 200, "cities": 1, "captured": 1, "lost_cities": 0},
    ],
}


def test_validate_endpoint_returns_warnings(base):
    url, _ = base
    s, b = req(url + "/api/validate", "POST", {"match": CITY_IMBALANCE})
    assert s == 200
    d = jload(b)
    assert d["ok"] is True
    assert any(i["code"] == "W_CITY_BALANCE" for i in d["warnings"])
    assert "A" in d["completeness"]


def test_save_rejects_errors_with_issues(base):
    url, data_dir = base
    bad = json.loads(json.dumps(MATCH))
    bad["turns"][0]["score"] = -5
    s, b = req(url + "/api/matches/neg", "POST", {"match": bad})
    assert s == 400
    d = jload(b)
    assert d["ok"] is False
    assert any(i["code"] == "E_NEGATIVE" for i in d["issues"])
    assert not (data_dir / "neg.json").exists()


def test_save_allows_warnings(base):
    url, data_dir = base
    s, b = req(url + "/api/matches/warn", "POST", {"match": CITY_IMBALANCE})
    assert s == 200
    assert (data_dir / "warn.json").exists()
