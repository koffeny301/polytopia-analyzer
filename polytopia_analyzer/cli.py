"""Командная строка polytopia-analyzer."""

from __future__ import annotations

import argparse
import datetime as _dt
import platform
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

from . import __version__
from .analysis import standings
from .links import extract_game_id, mobile_link, share_link, steam_link
from .models import Match, MatchMeta, Player
from .report import build_report
from .storage import CSV_COLUMNS, load_match, match_to_csv, save_match


# ---------- analyze ----------

def _print_summary(match: Match, report_path: Path | None = None) -> None:
    stats = standings(match)
    if not stats:
        print("В матче нет данных.")
        return
    w = stats[0]
    tribe = f" ({w.tribe})" if w.tribe else ""
    gap = ""
    if len(stats) > 1:
        gap = f" (отрыв +{w.final_score - stats[1].final_score:g})"
    print(f"Победа: {w.name}{tribe} — {w.final_score:g} очков{gap}")
    print()
    print(f"{'#':<3}{'Игрок':<18}{'Счёт':>8}{'Прирост':>9}{'☆/ход':>7}"
          f"{'Убито':>7}{'Потер.':>8}{'K/D':>7}{'Гор.захв':>9}")
    for i, s in enumerate(stats, start=1):
        name = s.name if len(s.name) <= 16 else s.name[:15] + "…"
        print(f"{i:<3}{name:<18}{s.final_score:>8g}{s.score_growth:>+9g}"
              f"{s.avg_income:>7.1f}{s.total_kills:>7}{s.total_losses:>8}"
              f"{s.kd:>7.2f}{s.total_captured:>9}")
    if report_path:
        print()
        print(f"Отчёт: {report_path}")


def cmd_analyze(args: argparse.Namespace) -> int:
    inp = Path(args.input)
    if not inp.exists():
        print(f"Ошибка: файл не найден: {inp}")
        return 1
    match = load_match(inp)
    html = build_report(match, plotly_js=None) if args.cdn else build_report(match)
    out = Path(args.output) if args.output else inp.with_name(inp.stem + "_report.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    _print_summary(match, out.resolve())
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


# ---------- template ----------

def cmd_template(args: argparse.Namespace) -> int:
    names = [n.strip() for n in args.players.split(",") if n.strip()]
    if not names:
        print("Ошибка: укажите имена игроков через запятую (--players)")
        return 1
    out = Path(args.output)
    lines = []
    if args.title:
        lines.append(f"# title: {args.title}")
    lines.append(f"# date: {_dt.date.today().isoformat()}")
    if args.mode:
        lines.append(f"# mode: {args.mode}")
    if args.link:
        lines.append(f"# link: {args.link}")
    for n in names:
        lines.append(f"# player: {n}")
    lines.append(",".join(CSV_COLUMNS))
    for turn in range(1, args.turns + 1):
        for n in names:
            lines.append(f"{turn},{n}," + ",".join([""] * (len(CSV_COLUMNS) - 2)))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Шаблон создан: {out.resolve()}")
    print(f"Игроков: {len(names)}, ходов: {args.turns}.")
    print("Заполняйте колонки по ходу просмотра реплея:")
    print("  score — общая стоимость/счёт, income — доход ☆ за ход,")
    print("  army — размер армии, cities — число городов (снимок на конец хода);")
    print("  kills/losses — убитые/потерянные юниты за ход,")
    print("  captured/lost_cities — захваченные/потерянные города за ход,")
    print("  damage — нанесённый урон за ход.")
    return 0


# ---------- open ----------

def _launch_url(url: str) -> bool:
    system = platform.system()
    try:
        if system == "Linux":
            if not shutil.which("xdg-open"):
                return False
            subprocess.Popen(["xdg-open", url])
        elif system == "Darwin":
            subprocess.Popen(["open", url])
        elif system == "Windows":
            subprocess.Popen(["cmd", "/c", "start", "", url])
        else:
            return False
        return True
    except OSError:
        return False


def cmd_open(args: argparse.Namespace) -> int:
    try:
        gid = extract_game_id(args.link)
    except ValueError as e:
        print(f"Ошибка: {e}")
        return 1
    print(f"Матч: {gid}")
    print(f"  Веб-ссылка: {share_link(gid)}")
    print(f"  Мобильная версия: {mobile_link(gid)}")
    print(f"  Steam: {steam_link(gid)}")
    if args.launch:
        if _launch_url(steam_link(gid)):
            print("Запускаю реплей в Steam…")
        else:
            print("Не удалось запустить автоматически — откройте ссылку Steam вручную.")
    else:
        print()
        print("Совет: добавьте --launch, чтобы сразу открыть реплей в Steam.")
    return 0


# ---------- convert ----------

def cmd_convert(args: argparse.Namespace) -> int:
    inp, out = Path(args.input), Path(args.output)
    if not inp.exists():
        print(f"Ошибка: файл не найден: {inp}")
        return 1
    if inp.suffix.lower() == out.suffix.lower():
        print("Ошибка: входной и выходной форматы совпадают (нужны .csv и .json)")
        return 1
    match = load_match(inp)
    save_match(match, out)
    print(f"Сохранено: {out.resolve()}")
    return 0


# ---------- web ----------

def cmd_web(args: argparse.Namespace) -> int:
    from .webapp import serve

    try:
        serve(host=args.host, port=args.port, data_dir=args.data_dir,
              open_browser=not args.no_browser)
    except OSError as e:
        print(f"Ошибка: не удалось запустить сервер на {args.host}:{args.port} ({e})")
        return 1
    return 0


# ---------- parser ----------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polytopia-analyzer",
        description="Анализ матчей The Battle of Polytopia: HTML-отчёты в стиле Civilization.",
    )
    parser.add_argument("--version", action="version", version=f"polytopia-match-analyzer {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("analyze", help="построить HTML-отчёт по данным матча (.csv/.json)")
    p.add_argument("input", help="файл матча (.csv или .json)")
    p.add_argument("-o", "--output", help="путь к HTML-отчёту (по умолчанию <вход>_report.html)")
    p.add_argument("--cdn", action="store_true", help="подключить plotly.js через CDN вместо встраивания")
    p.add_argument("--open", action="store_true", help="открыть отчёт в браузере")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("template", help="создать CSV-шаблон для записи матча")
    p.add_argument("--players", default="Игрок1,Игрок2", help="имена игроков через запятую")
    p.add_argument("--turns", type=int, default=30, help="число ходов (по умолчанию 30)")
    p.add_argument("--title", default="", help="название матча")
    p.add_argument("--mode", default="", help="режим (Perfection/Domination/Glory/Might)")
    p.add_argument("--link", default="", help="ссылка на реплей (share.polytopia.io/...)")
    p.add_argument("-o", "--output", default="match.csv", help="выходной CSV-файл")
    p.set_defaults(func=cmd_template)

    p = sub.add_parser("open", help="открыть реплей матча по ссылке share.polytopia.io")
    p.add_argument("link", help="ссылка на матч или UUID")
    p.add_argument("--launch", action="store_true", help="сразу запустить реплей через Steam")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("convert", help="конвертировать данные матча между CSV и JSON")
    p.add_argument("input")
    p.add_argument("output")
    p.set_defaults(func=cmd_convert)

    p = sub.add_parser("web", help="запустить локальный веб-интерфейс (localhost)")
    p.add_argument("--port", type=int, default=8765, help="порт (по умолчанию 8765)")
    p.add_argument("--host", default="127.0.0.1",
                   help="адрес (по умолчанию только локальный)")
    p.add_argument("--data-dir", default=None,
                   help="папка с матчами (по умолчанию data/ в проекте)")
    p.add_argument("--no-browser", action="store_true",
                   help="не открывать браузер автоматически")
    p.set_defaults(func=cmd_web)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ValueError as e:
        print(f"Ошибка данных: {e}")
        return 1
    except FileNotFoundError as e:
        print(f"Ошибка: файл не найден: {e.filename}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
