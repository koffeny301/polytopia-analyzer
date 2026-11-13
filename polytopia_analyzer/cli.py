"""Командная строка polytopia-analyzer."""

from __future__ import annotations

import argparse
import datetime as _dt
import platform
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from . import __version__
from .analysis import standings
from .links import extract_game_id, mobile_link, share_link, steam_link
from .models import Match, MatchMeta, Player
from .report import build_report
from .storage import CSV_COLUMNS, load_match, match_to_csv, save_match
from .validation import validate_match


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
    _print_validation(match)
    html = build_report(match, plotly_js=None) if args.cdn else build_report(match)
    out = Path(args.output) if args.output else inp.with_name(inp.stem + "_report.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    _print_summary(match, out.resolve())
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


# ---------- validate ----------

def _fmt_issue(i) -> str:
    loc = []
    if i.turn is not None:
        loc.append(f"ход {i.turn}")
    if i.player:
        loc.append(str(i.player))
    where = ", ".join(loc)
    return f"[{i.code}] {where + ': ' if where else ''}{i.message}"


def _print_validation(match: Match) -> None:
    rep = validate_match(match)
    print(f"Проверка данных: ошибок {len(rep.errors)}, "
          f"предупреждений {len(rep.warnings)}, инфо {len(rep.info)}")
    for i in rep.errors[:5]:
        print("  " + _fmt_issue(i))
    for i in rep.warnings[:5]:
        print("  " + _fmt_issue(i))
    if len(rep.warnings) > 5:
        print(f"  …и ещё {len(rep.warnings) - 5} предупреждений")
    print()


def cmd_validate(args: argparse.Namespace) -> int:
    inp = Path(args.input)
    if not inp.exists():
        print(f"Ошибка: файл не найден: {inp}")
        return 1
    match = load_match(inp)
    rep = validate_match(match)
    print(f"Проверка: {inp}")
    print(f"Ошибок: {len(rep.errors)}")
    for i in rep.errors:
        print("  " + _fmt_issue(i))
    print(f"Предупреждений: {len(rep.warnings)}")
    for i in rep.warnings:
        print("  " + _fmt_issue(i))
    print(f"Инфо: {len(rep.info)}")
    for i in rep.info:
        print("  " + _fmt_issue(i))
    if rep.completeness:
        comp = ", ".join(f"{n} {v * 100:.0f}%" for n, v in rep.completeness.items())
        print(f"Полнота данных: {comp}")
    return 1 if rep.has_errors else 0


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
    lines.append(f"# turn_limit: {args.turns}")
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


# ---------- import-inbox ----------

def _default_shim_out() -> Path:
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M")
    return Path("data") / f"shim-{stamp}.json"


def _import_inbox_once(directory: Path, out: Path, args) -> tuple[int, Match]:
    from .importers import shim

    match = shim.match_from_inbox(
        directory, title=args.title, date=args.date, link=args.link,
        mode=args.mode, map_name=args.map,
    )
    rep = validate_match(match)
    save_match(match, out)
    return len(rep.errors), match


def cmd_import_inbox(args: argparse.Namespace) -> int:
    from .importers import shim

    directory = Path(args.dir).expanduser() if args.dir else shim.default_inbox_dir()
    session = shim.scan_inbox(directory)
    if session.is_empty and not args.watch:
        print(f"Ошибка: в папке нет снимков состояния: {directory}")
        print("Подсказка: снимки пишет шим, см. docs/shim-dump.md")
        return 1
    out = Path(args.output).expanduser() if args.output else _default_shim_out()

    if not args.watch:
        n_err, match = _import_inbox_once(directory, out, args)
        _print_validation(match)
        _print_summary(match, out.resolve())
        return 1 if n_err else 0

    print(f"Слежу за папкой: {directory}")
    print(f"Матч пишется в: {out.resolve()}")
    if session.is_empty:
        print("Снимков пока нет — жду, пока игра начнёт писать состояние.")
    last_mtime = 0.0
    try:
        while True:
            session = shim.scan_inbox(directory)
            if not session.is_empty and session.newest_mtime != last_mtime:
                last_mtime = session.newest_mtime
                try:
                    n_err, match = _import_inbox_once(directory, out, args)
                except ValueError as e:
                    print(f"  пропуск: {e}")
                else:
                    print(f"  ходы {session.turns[0]}–{session.turns[-1]}: "
                          f"{', '.join(match.player_names())} → {out} "
                          f"(ошибок {n_err})")
            time.sleep(max(1, args.interval))
    except KeyboardInterrupt:
        print("\nОстановлено.")
        return 0


# ---------- install-shim ----------

def _ensure_shim_installed(explicit: str | None) -> Path:
    """Копирует собранный шим в стабильный путь, если его там нет."""
    from . import steam as steam_mod

    target = Path(explicit).expanduser() if explicit else steam_mod.shim_install_path()
    if target.is_file():
        return target
    built = Path(__file__).resolve().parents[1] / "shim" / steam_mod.SHIM_NAME
    if built.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(built, target)
        print(f"Шим установлен: {target}")
        return target
    raise FileNotFoundError(
        f"Шим не собран: нет {target} и {built}. Соберите: make -C shim")


def cmd_install_shim(args: argparse.Namespace) -> int:
    from . import steam as steam_mod

    action = args.action or "status"
    cfg = Path(args.config).expanduser() if args.config else None
    try:
        if action == "status":
            st = steam_mod.status(cfg, shim=Path(args.shim).expanduser() if args.shim else None)
            print(f"Конфиг Steam: {st.config}")
            print(f"Приложение:   {st.appid}")
            print(f"Шим:          {'найден' if st.shim_present else 'не найден'} "
                  f"({steam_mod.shim_install_path()})")
            print(f"Steam:        {'запущен' if st.steam_running else 'остановлен'}")
            print(f"LaunchOptions: {st.current!r}")
            print(f"Состояние:    {'включено' if st.installed else 'выключено'}")
            if st.installed:
                print()
                print("Откат: polytopia-analyzer install-shim --disable")
            else:
                print()
                print(steam_mod.manual_instructions())
            return 0

        if action == "enable":
            shim = _ensure_shim_installed(args.shim)
            res = steam_mod.enable(cfg, shim=shim, restart_steam=args.restart_steam,
                                   force=args.force)
            print(f"Включено чтение из игры: {res['installed']}")
            print(f"Конфиг: {res['config']}")
            print(f"Резервная копия: {res['backup']}")
            if res["steam_was_running"] and not res["steam_stopped"]:
                print()
                print("ВАЖНО: Steam был запущен и держит конфиг в памяти — правка "
                      "применится после перезапуска Steam (или используйте "
                      "--restart-steam).")
            elif res["steam_stopped"]:
                print("Steam перезапущен — настройка уже действует.")
            print()
            print("Дальше: откройте реплей по ссылке из Steam, затем "
                  "polytopia-analyzer import-inbox --watch")
            return 0

        if action == "disable":
            res = steam_mod.disable(cfg, restart_steam=args.restart_steam, force=args.force)
            prev = res["restored"]
            print("Откат выполнен: " + (f"возвращено {prev!r}" if prev is not None
                                        else "параметры запуска очищены"))
            if res["steam_was_running"] and not res["steam_stopped"]:
                print("Изменение применится после перезапуска Steam.")
            return 0
    except (steam_mod.SteamRunningError, steam_mod.SteamConfigError, FileNotFoundError) as e:
        print(f"Ошибка: {e}")
        return 1
    return 2


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

    p = sub.add_parser("validate", help="проверить данные матча без построения отчёта")
    p.add_argument("input", help="файл матча (.csv или .json)")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("open", help="открыть реплей матча по ссылке share.polytopia.io")
    p.add_argument("link", help="ссылка на матч или UUID")
    p.add_argument("--launch", action="store_true", help="сразу запустить реплей через Steam")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("convert", help="конвертировать данные матча между CSV и JSON")
    p.add_argument("input")
    p.add_argument("output")
    p.set_defaults(func=cmd_convert)

    p = sub.add_parser("install-shim",
                       help="включить/выключить чтение состояния через строку запуска Steam")
    p.add_argument("action", nargs="?", choices=["enable", "disable", "status"],
                   default="status", help="что сделать (по умолчанию status)")
    p.add_argument("--config", default=None, help="путь к localconfig.vdf (по умолчанию авто)")
    p.add_argument("--shim", default=None, help="путь к libpolyread_dump.so")
    p.add_argument("--restart-steam", action="store_true",
                   help="остановить Steam, записать настройку и запустить снова")
    p.add_argument("--force", action="store_true",
                   help="писать при работающем Steam / поверх чужого значения")
    p.set_defaults(func=cmd_install_shim)

    p = sub.add_parser("import-inbox", help="импортировать снимки состояния игры (шим)")
    p.add_argument("--dir", default=None,
                   help="папка со снимками (по умолчанию ~/.local/share/polytopia-analyzer/inbox)")
    p.add_argument("-o", "--output", default=None,
                   help="файл матча (по умолчанию data/shim-<дата>.json)")
    p.add_argument("--title", default="", help="название матча")
    p.add_argument("--link", default="", help="ссылка на реплей")
    p.add_argument("--mode", default="", help="режим (Might/Perfection/…)")
    p.add_argument("--map", default="", help="карта")
    p.add_argument("--date", default="", help="дата матча (YYYY-MM-DD)")
    p.add_argument("--watch", action="store_true",
                   help="следить за папкой и обновлять матч по ходу реплея")
    p.add_argument("--interval", type=float, default=3.0,
                   help="период опроса папки в режиме --watch, секунды")
    p.set_defaults(func=cmd_import_inbox)

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
