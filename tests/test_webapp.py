"""Тесты локального веб-интерфейса (HTTP API webapp)."""

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

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


def _make_inbox(tmp_path):
    """Раскладывает эталонные снимки шима по файлам."""
    root = tmp_path / "inbox"
    root.mkdir()
    src = tmp_path.parent / "fixtures" / "replay-timeless-battles-shim.json"
    if not src.exists():
        src = Path(__file__).parent / "fixtures" / "replay-timeless-battles-shim.json"
    data = json.loads(src.read_text(encoding="utf-8"))
    for snap in data["turns"]:
        (root / f"snapshot-{int(snap['turn']):03d}.json").write_text(
            json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    return root


def test_steam_shim_status_and_toggle(base, tmp_path, monkeypatch):
    from polytopia_analyzer import steam

    cfg = tmp_path / "localconfig.vdf"
    cfg.write_text('"X"\n{\n\t"apps"\n\t{\n\t\t"874390"\n\t\t{\n\t\t}\n\t}\n}\n"'
                   .replace('\\t', '\t'), encoding="utf-8")
    shim = tmp_path / "libpolyread_dump.so"
    shim.write_bytes(b"\x7fELF fake")
    monkeypatch.setattr(steam, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(steam, "is_steam_running", lambda: False)
    monkeypatch.setattr(steam, "start_steam", lambda: None)
    monkeypatch.setattr(steam, "shim_install_path", lambda: shim)
    monkeypatch.setattr(steam, "default_config_path", lambda: cfg)
    monkeypatch.setattr(steam, "config_paths", lambda: [cfg])
    monkeypatch.setattr(steam, "steam_roots", lambda: [])
    monkeypatch.setattr(steam, "game_dirs", lambda: [])

    url, _ = base
    status, body = req(url + "/api/steam-shim")
    assert status == 200
    data = jload(body)
    assert data["ok"] and not data["installed"] and data["shim_present"]
    assert "%command%" in data["manual"]
    assert data["steam_found"] is False and data["config_found"] is True
    assert data["game_found"] is False and data["game_native"] is False

    status, body = req(url + "/api/steam-shim", method="POST",
                       data={"action": "enable", "force": True})
    assert status == 200
    assert jload(body)["ok"]
    assert '"LaunchOptions"' in cfg.read_text(encoding="utf-8")

    status, body = req(url + "/api/steam-shim", method="POST",
                       data={"action": "disable", "force": True})
    assert status == 200
    assert '"LaunchOptions"' not in cfg.read_text(encoding="utf-8")


def test_steam_shim_rejects_unknown_action(base, tmp_path, monkeypatch):
    from polytopia_analyzer import steam

    cfg = tmp_path / "localconfig.vdf"
    cfg.write_text('"X"\n{\n\t"apps"\n\t{\n\t\t"874390"\n\t\t{\n\t\t}\n\t}\n}\n',
                   encoding="utf-8")
    monkeypatch.setattr(steam, "default_config_path", lambda: cfg)
    monkeypatch.setattr(steam, "is_steam_running", lambda: False)
    url, _ = base
    status, _ = req(url + "/api/steam-shim", method="POST", data={"action": "hack"})
    assert status == 400


def test_inbox_status(base, tmp_path):
    url, _ = base
    inbox = _make_inbox(tmp_path)
    status, body = req(url + "/api/inbox?dir=" + urllib.parse.quote(str(inbox)))
    assert status == 200
    data = jload(body)
    assert data["ok"] and data["files"] == 13
    assert data["turns"][0] == 0 and data["turns"][-1] == 12


def test_inbox_import_creates_match(base, tmp_path):
    url, data_dir = base
    inbox = _make_inbox(tmp_path)
    status, body = req(url + "/api/inbox/import", method="POST",
                       data={"dir": str(inbox), "title": "Timeless Battles", "out": "shim-test"})
    assert status == 200
    data = jload(body)
    assert data["ok"] and data["name"] == "shim-test.json"
    assert (data_dir / "shim-test.json").exists()
    assert data["match"]["meta"]["source"] == "shim"
    assert [p["name"] for p in data["match"]["players"]] == ["KOF", "Goylem"]

    status, body = req(url + "/api/matches")
    names = [m["name"] for m in jload(body)["matches"]]
    assert "shim-test.json" in names


def test_inbox_import_empty_dir_404(base, tmp_path):
    url, _ = base
    empty = tmp_path / "empty-inbox"
    empty.mkdir()
    status, body = req(url + "/api/inbox/import", method="POST", data={"dir": str(empty)})
    assert status == 404


# ---------- .state-сейвы (M7) ----------

def _state_dir(tmp_path):
    """Папка с одним сейвом, скопированным из фикстур."""
    d = tmp_path / "saves"
    d.mkdir()
    src = Path(__file__).parent / "fixtures" / "singleplayer-v124.state"
    (d / "test.state").write_bytes(src.read_bytes())
    return d


def test_state_status(base, tmp_path):
    url, _ = base
    d = _state_dir(tmp_path)
    status, body = req(url + "/api/state?dir=" + urllib.parse.quote(str(d)))
    assert status == 200
    data = jload(body)
    assert data["ok"] and data["path"].endswith("test.state")
    assert data["size"] > 0 and data["mtime"] > 0


def test_state_status_empty_dir(base, tmp_path):
    url, _ = base
    d = tmp_path / "no-saves"
    d.mkdir()
    status, body = req(url + "/api/state?dir=" + urllib.parse.quote(str(d)))
    assert status == 200
    assert jload(body)["path"] is None


def test_state_import_creates_match(base, tmp_path):
    url, data_dir = base
    d = _state_dir(tmp_path)
    status, body = req(url + "/api/state/import", method="POST",
                       data={"dir": str(d), "title": "Из сейва", "out": "state-test"})
    assert status == 200
    data = jload(body)
    assert data["ok"] and data["name"] == "state-test.json"
    assert (data_dir / "state-test.json").exists()
    assert data["match"]["meta"]["source"] == "state"
    assert [p["name"] for p in data["match"]["players"]] == ["KOF", "Shayoszu"]
    assert data["validation"]["errors"] == []


def test_state_import_by_explicit_path(base, tmp_path):
    url, data_dir = base
    src = Path(__file__).parent / "fixtures" / "singleplayer-v124.state"
    status, body = req(url + "/api/state/import", method="POST",
                       data={"path": str(src), "out": "state-explicit"})
    assert status == 200
    data = jload(body)
    assert data["ok"] and data["name"] == "state-explicit.json"
    assert (data_dir / "state-explicit.json").exists()


def test_state_import_empty_dir_404(base, tmp_path):
    url, _ = base
    d = tmp_path / "no-saves2"
    d.mkdir()
    status, _ = req(url + "/api/state/import", method="POST", data={"dir": str(d)})
    assert status == 404


def test_state_import_broken_file_400(base, tmp_path):
    url, _ = base
    bad = tmp_path / "bad.state"
    bad.write_bytes(b"not a save at all, not even close to being one")
    status, body = req(url + "/api/state/import", method="POST", data={"path": str(bad)})
    assert status == 400
    assert not jload(body)["ok"]


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


def test_index_has_wizard_mode(base):
    """M2: в интерфейсе есть переключатель Таблица/Мастер и карточка хода."""
    url, _ = base
    status, body = req(url + "/")
    assert status == 200
    assert 'id="view-toggle"' in body
    assert 'id="wizard"' in body
    assert "Ctrl+Enter" in body


# ---------- M4: OCR финального табло ----------


def _ocr_png():
    import base64
    import io

    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (640, 220), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", 72)
    except OSError:
        font = ImageFont.load_default(size=72)
    d.text((40, 15), "3480", fill="black", font=font)
    d.text((40, 125), "3068", fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_ocr_endpoint(base):
    import shutil

    pytest.importorskip("pytesseract")
    if shutil.which("tesseract") is None:
        pytest.skip("tesseract не установлен")
    url, _ = base
    status, body = req(url + "/api/ocr", "POST", {"image_b64": _ocr_png()})
    assert status == 200
    data = jload(body)
    assert data["ok"] is True
    assert 3480 in [n["value"] for n in data["numbers"]]
    assert data["width"] == 640 and data["height"] == 220


def test_ocr_bad_base64(base):
    url, _ = base
    status, body = req(url + "/api/ocr", "POST", {"image_b64": "!!!не base64!!!"})
    assert status == 400
    assert jload(body)["ok"] is False


def test_ocr_unavailable_returns_503(base, monkeypatch):
    import base64

    from polytopia_analyzer.importers import ocr as ocr_mod

    def boom(*a, **k):
        raise ocr_mod.OcrUnavailableError("tesseract не найден — установите пакет")

    monkeypatch.setattr(ocr_mod, "recognize_numbers", boom)
    url, _ = base
    status, body = req(url + "/api/ocr", "POST",
                       {"image_b64": base64.b64encode(b"x").decode()})
    assert status == 503
    assert "tesseract" in jload(body)["error"]


def test_index_has_ocr_ui(base):
    """M4: в интерфейсе есть загрузка скриншота табло и привязка чисел."""
    url, _ = base
    status, body = req(url + "/")
    assert status == 200
    assert 'id="ocr-file"' in body
    assert 'id="ocr-dialog"' in body


def test_web_reuses_running_instance(base, monkeypatch, capsys):
    """Порт занят нашим сервером → не ошибка, а открытие браузера (exit 0)."""
    from polytopia_analyzer import cli

    url, _ = base
    port = int(url.rsplit(":", 1)[1])
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda u, *a, **k: opened.append(u) or True)
    rc = cli.main(["web", "--port", str(port)])
    assert rc == 0
    assert opened == [f"http://127.0.0.1:{port}/"]
    assert "уже запущен" in capsys.readouterr().out


def test_web_port_busy_by_foreign_server(monkeypatch, capsys):
    """Порт занят чужим сервисом → понятная ошибка, exit 1."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from polytopia_analyzer import cli

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        port = srv.server_address[1]
        opened = []
        monkeypatch.setattr("webbrowser.open", lambda u, *a, **k: opened.append(u) or True)
        rc = cli.main(["web", "--port", str(port)])
        assert rc == 1
        assert not opened
        assert "Ошибка" in capsys.readouterr().out
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)
