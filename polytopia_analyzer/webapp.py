"""Локальный веб-интерфейс анализатора: HTTP API + одностраничный редактор.

Только стандартная библиотека: ThreadingHTTPServer отдаёт встроенную страницу
(assets/webui.html) и JSON API для матчей, анализа и ссылок. Сервер слушает
127.0.0.1 — интерфейс локальный и не предназначен для публикации в сеть.
"""

from __future__ import annotations

import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .links import extract_game_id, mobile_link, share_link, steam_link
from .report import build_report
from .storage import (
    load_match,
    match_from_csv,
    match_from_dict,
    match_from_json,
    match_to_csv,
    match_to_dict,
    match_to_json,
    save_match,
)

ASSETS_DIR = Path(__file__).parent / "assets"
INDEX_HTML = ASSETS_DIR / "webui.html"
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
MAX_BODY_BYTES = 15 * 1024 * 1024
ALLOWED_SUFFIXES = (".json", ".csv")


def safe_match_path(data_dir: Path, name: str) -> Path:
    """Преобразует имя матча из URL в безопасный путь внутри data_dir.

    Отклоняет разделители пути, '..' и пустые имена; результат проверяется
    на выход за пределы data_dir. Без расширения добавляет '.json'.
    """
    name = unquote(name).strip()
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\x00" in name:
        raise ValueError(f"Некорректное имя матча: {name!r}")
    if not name.lower().endswith(ALLOWED_SUFFIXES):
        name += ".json"
    path = (data_dir / name).resolve()
    if not path.is_relative_to(data_dir.resolve()):
        raise ValueError(f"Имя выходит за пределы каталога данных: {name!r}")
    return path


class RequestHandler(BaseHTTPRequestHandler):
    """Маршрутизация: страница, список/чтение/запись матчей, анализ, ссылки."""

    server_version = f"PolytopiaAnalyzer/{__version__}"
    protocol_version = "HTTP/1.1"

    # ---------- инфраструктура ----------

    @property
    def data_dir(self) -> Path:
        return self.server.data_dir

    def log_message(self, fmt, *args):  # noqa: A003 — сигнатура базового класса
        if getattr(self.server, "verbose", False):
            sys.stderr.write(f"[web] {self.address_string()} {fmt % args}\n")

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _text(self, text: str, ctype: str, status: int = 200) -> None:
        self._send(status, text.encode("utf-8"), ctype)

    def _fail(self, status: int, message: str) -> None:
        self._json({"ok": False, "error": message}, status)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            raise ValueError("Слишком большое тело запроса")
        return self.rfile.read(length) if length > 0 else b""

    def _json_body(self) -> dict:
        raw = self._read_body()
        if not raw:
            raise ValueError("Пустое тело запроса")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ValueError(f"Некорректный JSON: {e}") from e
        if not isinstance(data, dict):
            raise ValueError("Ожидался JSON-объект")
        return data

    # ---------- маршрутизация ----------

    def do_GET(self):  # noqa: N802
        self._route(self._handle_get)

    def do_POST(self):  # noqa: N802
        self._route(self._handle_post)

    def do_DELETE(self):  # noqa: N802
        self._route(self._handle_delete)

    def _route(self, handler) -> None:
        try:
            handler(urlparse(self.path))
        except ValueError as e:
            self._fail(400, str(e))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self._fail(500, f"Внутренняя ошибка: {e}")
            except OSError:
                pass

    # ---------- GET ----------

    def _handle_get(self, parsed) -> None:
        path = parsed.path
        qs = parse_qs(parsed.query)
        if path in ("/", "/index.html"):
            if not INDEX_HTML.exists():
                raise ValueError(f"Файл интерфейса не найден: {INDEX_HTML}")
            return self._text(INDEX_HTML.read_text(encoding="utf-8"),
                              "text/html; charset=utf-8")
        if path == "/api/health":
            return self._json({"ok": True, "version": __version__})
        if path == "/api/matches":
            return self._json({"ok": True, "matches": self._list_matches()})
        if path.startswith("/api/matches/"):
            return self._get_match(path[len("/api/matches/"):], qs)
        self._fail(404, f"Неизвестный маршрут: {path}")

    def _get_match(self, raw_name: str, qs: dict) -> None:
        p = safe_match_path(self.data_dir, raw_name)
        if not p.exists():
            return self._fail(404, f"Матч не найден: {p.name}")
        match = load_match(p)
        fmt = (qs.get("format") or ["json"])[0].lower()
        if fmt == "csv":
            return self._text(match_to_csv(match), "text/csv; charset=utf-8")
        return self._json({"ok": True, "name": p.name, "match": match_to_dict(match)})

    def _list_matches(self) -> list[dict]:
        items = []
        for p in self.data_dir.iterdir():
            if not p.is_file() or p.suffix.lower() not in ALLOWED_SUFFIXES:
                continue
            entry: dict = {
                "name": p.name,
                "format": p.suffix.lower().lstrip("."),
                "mtime": p.stat().st_mtime,
                "title": "", "mode": "", "players": [], "turns": 0, "error": None,
            }
            try:
                m = load_match(p)
                entry.update(title=m.meta.title, mode=m.meta.mode,
                             players=m.player_names(), turns=m.max_turn())
            except Exception as e:
                entry["error"] = str(e)
            items.append(entry)
        items.sort(key=lambda it: -it["mtime"])
        return items

    # ---------- POST ----------

    def _handle_post(self, parsed) -> None:
        path = parsed.path
        qs = parse_qs(parsed.query)
        if path == "/api/analyze":
            payload = self._json_body()
            match = match_from_dict(payload.get("match") or {})
            html = (build_report(match, plotly_js=None) if payload.get("cdn")
                    else build_report(match))
            return self._json({"ok": True, "html": html})
        if path == "/api/upload":
            text = self._read_body().decode("utf-8", errors="replace")
            fmt = (qs.get("format") or ["auto"])[0].lower()
            if fmt == "auto":
                fmt = "json" if text.lstrip().startswith("{") else "csv"
            match = match_from_json(text) if fmt == "json" else match_from_csv(text)
            return self._json({"ok": True, "match": match_to_dict(match)})
        if path == "/api/export":
            payload = self._json_body()
            match = match_from_dict(payload.get("match") or {})
            fmt = str(payload.get("format", "csv")).lower()
            text = match_to_json(match) if fmt == "json" else match_to_csv(match)
            return self._json({"ok": True, "format": fmt, "text": text})
        if path == "/api/link":
            payload = self._json_body()
            gid = extract_game_id(str(payload.get("link", "")))
            return self._json({"ok": True, "game_id": gid, "share": share_link(gid),
                               "mobile": mobile_link(gid), "steam": steam_link(gid)})
        if path.startswith("/api/matches/"):
            p = safe_match_path(self.data_dir, path[len("/api/matches/"):])
            payload = self._json_body()
            match = match_from_dict(payload.get("match") or payload)
            save_match(match, p)
            return self._json({"ok": True, "name": p.name})
        self._fail(404, f"Неизвестный маршрут: {path}")

    # ---------- DELETE ----------

    def _handle_delete(self, parsed) -> None:
        path = parsed.path
        if path.startswith("/api/matches/"):
            p = safe_match_path(self.data_dir, path[len("/api/matches/"):])
            if not p.exists():
                return self._fail(404, f"Матч не найден: {p.name}")
            p.unlink()
            return self._json({"ok": True, "name": p.name})
        self._fail(404, f"Неизвестный маршрут: {path}")


# ---------- запуск ----------

def create_server(host: str = "127.0.0.1", port: int = 8765,
                  data_dir: Path | str | None = None,
                  verbose: bool = False) -> ThreadingHTTPServer:
    """Создаёт HTTP-сервер (не запускает). port=0 — свободный порт от ОС."""
    data = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    data.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), RequestHandler)
    httpd.daemon_threads = True
    httpd.data_dir = data  # type: ignore[attr-defined]
    httpd.verbose = verbose  # type: ignore[attr-defined]
    return httpd


def serve(host: str = "127.0.0.1", port: int = 8765,
          data_dir: Path | str | None = None,
          open_browser: bool = True, verbose: bool = False) -> None:
    """Запускает сервер блокирующе; Ctrl+C — остановка."""
    httpd = create_server(host, port, data_dir, verbose)
    actual_host, actual_port = httpd.server_address[:2]
    url = f"http://{actual_host}:{actual_port}/"
    print(f"Веб-интерфейс Polytopia Analyzer: {url}")
    print(f"Каталог матчей: {httpd.data_dir}")
    print("Остановка: Ctrl+C")
    if open_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        httpd.server_close()
