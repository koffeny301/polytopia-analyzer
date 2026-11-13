"""Установка шима через строку запуска Steam (веха 6.4).

Зачем: deep-link `share.polytopia.io` игра получает только при запуске из Steam,
а шим (`LD_PRELOAD`) должен оказаться в том же процессе. Значит строка запуска
Steam — единственный штатный способ совместить их, без подмены файлов игры.

Особенность Steam: конфиг (`userdata/<id>/config/localconfig.vdf`) он держит
в памяти и перезаписывает при выходе. Поэтому менять файл при работающем Steam
бесполезно — правку затрут. Правильный порядок:

    1. остановить Steam;
    2. записать LaunchOptions;
    3. запустить Steam.

Модуль умеет это целиком (`enable(..., restart_steam=True)`) или только
писать файл, если Steam уже остановлен.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

APPID = "874390"
STATE_FILE = Path.home() / ".local/share/polytopia-analyzer/steam-launch.json"
SHIM_NAME = "libpolyread_dump.so"
GAME_DIR_NAME = "The Battle of Polytopia"
NATIVE_MARKER = "Polytopia.x86_64"


class SteamRunningError(RuntimeError):
    """Steam запущен — правка конфига не переживёт его выхода."""


class SteamConfigError(RuntimeError):
    """Конфиг Steam не найден или имеет неожидаемый вид."""


# ---------- поиск конфига ----------

def steam_roots() -> list[Path]:
    home = Path.home()
    return [p for p in (home / ".steam/steam", home / ".local/share/Steam") if p.is_dir()]


def config_paths() -> list[Path]:
    """Все localconfig.vdf во всех профилях Steam."""
    out: list[Path] = []
    for root in steam_roots():
        userdata = root / "userdata"
        if not userdata.is_dir():
            continue
        for acc in sorted(userdata.iterdir()):
            cfg = acc / "config" / "localconfig.vdf"
            if cfg.is_file():
                out.append(cfg)
    return out


def default_config_path() -> Path:
    """Конфиг активного профиля (первый найденный), с подсказкой при отсутствии."""
    env = os.environ.get("POLYREAD_STEAM_CONFIG")
    if env:
        p = Path(env).expanduser()
        if not p.is_file():
            raise SteamConfigError(f"Конфиг Steam не найден: {p}")
        return p
    paths = config_paths()
    if not paths:
        raise SteamConfigError("Не найден localconfig.vdf — Steam не установлен?")
    return paths[0]


def game_dir_default() -> Path:
    env = os.environ.get("POLYTOPIA_DIR")
    if env:
        return Path(env)
    return Path.home() / ".steam/steam/steamapps/common" / GAME_DIR_NAME


def steam_library_roots() -> list[Path]:
    """Все папки библиотек Steam (libraryfolders.vdf) + сами корни."""
    roots: list[Path] = []
    seen: set[str] = set()

    def _add(p: Path) -> None:
        s = str(p)
        if s not in seen and p.is_dir():
            seen.add(s)
            roots.append(p)

    for root in steam_roots():
        _add(root)
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if not vdf.is_file():
            continue
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in re.finditer(r'"path"\s*"([^"]+)"', text):
            _add(Path(m.group(1).replace("\\\\", "\\")))
    return roots


def game_dirs() -> list[Path]:
    """Кандидаты каталогов установки игры, по приоритету."""
    out: list[Path] = []
    env = os.environ.get("POLYTOPIA_DIR")
    if env:
        out.append(Path(env).expanduser())
    for root in steam_library_roots():
        out.append(root / "steamapps" / "common" / GAME_DIR_NAME)
    return out


def shim_install_path() -> Path:
    from . import paths

    return paths.shim_bin_dir() / SHIM_NAME


def launch_line(shim: Optional[Path] = None, inbox: Optional[Path] = None) -> str:
    """Строка запуска Steam: переменные + %command%."""
    from . import paths

    shim = shim or shim_install_path()
    inbox = inbox or paths.inbox_dir()
    return f"POLYREAD_DUMP={inbox} LD_PRELOAD={shim} %command%"


# ---------- минимальный разбор VDF ----------

def _find_app_block(lines: list[str], appid: str) -> tuple[int, int] | None:
    """Возвращает (индекс строки с appid, индекс закрывающей скобки) или None."""
    key = f'"{appid}"'
    for i, line in enumerate(lines):
        if line.strip() != key:
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines) or lines[j].strip() != "{":
            continue
        depth = 1
        k = j + 1
        while k < len(lines) and depth > 0:
            s = lines[k].strip()
            if s == "{":
                depth += 1
            elif s == "}":
                depth -= 1
                if depth == 0:
                    return i, k
            k += 1
    return None


def read_launch_options(config: Path, appid: str = APPID) -> Optional[str]:
    """Текущее значение LaunchOptions для приложения (None — ключа нет)."""
    lines = config.read_text(encoding="utf-8", errors="surrogateescape").split("\n")
    block = _find_app_block(lines, appid)
    if not block:
        raise SteamConfigError(f"В конфиге нет блока приложения {appid}: {config}")
    start, end = block
    for line in lines[start:end]:
        s = line.strip()
        if s.startswith('"LaunchOptions"'):
            parts = s.split('"')
            # "LaunchOptions"		"значение"
            if len(parts) >= 4:
                return parts[3]
            return ""
    return None


def _block_indent(lines: list[str], start: int, end: int) -> str:
    """Отступ строк внутри блока (на один уровень глубже ключа)."""
    for line in lines[start + 1:end]:
        if line.strip() and line.strip() != "{":
            return line[:len(line) - len(line.lstrip())]
    return "\t\t\t\t\t\t"


def write_launch_options(config: Path, appid: str, value: Optional[str]) -> None:
    """Записывает (или удаляет при value=None) LaunchOptions в конфиг."""
    text = config.read_text(encoding="utf-8", errors="surrogateescape")
    lines = text.split("\n")
    block = _find_app_block(lines, appid)
    if not block:
        raise SteamConfigError(f"В конфиге нет блока приложения {appid}: {config}")
    start, end = block

    idx = None
    for i in range(start, end):
        if lines[i].strip().startswith('"LaunchOptions"'):
            idx = i
            break

    if value is None:
        if idx is not None:
            lines.pop(idx)
    else:
        new_line = f'{_block_indent(lines, start, end)}"LaunchOptions"\t\t"{value}"'
        if idx is not None:
            lines[idx] = new_line
        else:
            lines.insert(end, new_line)

    config.write_text("\n".join(lines), encoding="utf-8", errors="surrogateescape")


# ---------- состояние установки ----------

def _read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(data: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- Steam ----------

def steam_processes() -> list[int]:
    """PID'ы процессов Steam (по cmdline), без внешних зависимостей."""
    out: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if not cmd:
            continue
        name = cmd.split(" ")[0]
        if os.path.basename(name) in ("steam", "steam.sh") or "steamwebhelper" in cmd:
            out.append(int(entry.name))
    return out


def is_steam_running() -> bool:
    return bool(steam_processes())


def stop_steam(timeout: float = 40.0) -> bool:
    """Просит Steam выйти и ждёт завершения. True — вышел."""
    exe = shutil.which("steam") or str(Path.home() / ".steam/steam/steam.sh")
    try:
        subprocess.run([exe, "-shutdown"], timeout=20,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.SubprocessError):
        pass
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_steam_running():
            return True
        time.sleep(0.5)
    return not is_steam_running()


def start_steam() -> None:
    """Запускает Steam отдельной сессией, не привязывая к нашему процессу."""
    exe = shutil.which("steam") or str(Path.home() / ".steam/steam/steam.sh")
    subprocess.Popen([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, start_new_session=True)


# ---------- операции ----------

@dataclass
class Status:
    config: Path
    appid: str
    current: Optional[str]           # что сейчас в LaunchOptions
    expected: str                    # наша строка
    installed: bool                  # совпадает с нашей
    steam_running: bool
    shim_present: bool

    def to_dict(self) -> dict:
        return {
            "config": str(self.config),
            "appid": self.appid,
            "current": self.current,
            "expected": self.expected,
            "installed": self.installed,
            "steam_running": self.steam_running,
            "shim_present": self.shim_present,
        }


def is_our_line(value: Optional[str]) -> bool:
    """Наша ли это строка запуска (не зависит от конкретного пути к шиму)."""
    return bool(value) and SHIM_NAME in value and "POLYREAD_DUMP=" in value


def status(config: Optional[Path] = None, appid: str = APPID,
           shim: Optional[Path] = None) -> Status:
    cfg = config or default_config_path()
    shim = shim or shim_install_path()
    cur = read_launch_options(cfg, appid)
    installed = is_our_line(cur)
    if not installed and cur is not None:
        installed = cur.strip() == launch_line(shim)
    return Status(config=cfg, appid=appid, current=cur, expected=launch_line(shim),
                  installed=installed,
                  steam_running=is_steam_running(), shim_present=shim.is_file())


def enable(config: Optional[Path] = None, appid: str = APPID, shim: Optional[Path] = None,
           inbox: Optional[Path] = None, *, restart_steam: bool = False,
           force: bool = False) -> dict:
    """Включает чтение: пишет LaunchOptions. Возвращает отчёт о действии."""
    cfg = config or default_config_path()
    shim = shim or shim_install_path()
    from . import paths

    inbox = inbox or paths.inbox_dir()
    if not shim.is_file():
        raise FileNotFoundError(
            f"Шим не найден: {shim}. Соберите: make -C shim и скопируйте {SHIM_NAME} "
            f"в {shim.parent}")

    was_running = is_steam_running()
    stopped = False
    if was_running:
        if restart_steam:
            stopped = stop_steam()
            if not stopped:
                raise SteamRunningError("Не удалось остановить Steam — закройте его и повторите")
        elif not force:
            raise SteamRunningError(
                "Steam запущен: он держит конфиг в памяти и перезапишет правку при выходе. "
                "Остановите Steam (steam -shutdown) или запустите установку с --restart-steam.")

    backup = cfg.with_suffix(cfg.suffix + ".polyread.bak")
    shutil.copy2(cfg, backup)
    previous = read_launch_options(cfg, appid)
    line = launch_line(shim, inbox)
    write_launch_options(cfg, appid, line)

    _write_state({
        "config": str(cfg),
        "appid": appid,
        "previous": previous,
        "installed": line,
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "backup": str(backup),
    })

    if stopped:
        start_steam()
        time.sleep(2)

    return {"ok": True, "config": str(cfg), "appid": appid, "installed": line,
            "previous": previous, "steam_was_running": was_running,
            "steam_stopped": stopped, "backup": str(backup)}


def disable(config: Optional[Path] = None, appid: str = APPID, *,
            restart_steam: bool = False, force: bool = False) -> dict:
    """Откат: возвращает прежнее значение LaunchOptions."""
    cfg = config or default_config_path()
    state = _read_state()
    previous = state.get("previous") if state.get("config") == str(cfg) else None
    installed = state.get("installed")

    current = read_launch_options(cfg, appid)
    if current is not None and installed and current.strip() != installed.strip() and not force:
        raise SteamConfigError(
            "В LaunchOptions уже другое значение (правил пользователь?). "
            "Повторите с --force, чтобы заменить на прежнее.")

    was_running = is_steam_running()
    stopped = False
    if was_running:
        if restart_steam:
            stopped = stop_steam()
            if not stopped:
                raise SteamRunningError("Не удалось остановить Steam — закройте его и повторите")
        elif not force:
            raise SteamRunningError(
                "Steam запущен: правка не переживёт его выхода. "
                "Остановите Steam или используйте --restart-steam.")

    write_launch_options(cfg, appid, previous)
    if state.get("config") == str(cfg):
        _write_state({})
    if stopped:
        start_steam()
        time.sleep(2)
    return {"ok": True, "config": str(cfg), "appid": appid, "restored": previous,
            "steam_was_running": was_running, "steam_stopped": stopped}


def manual_instructions(shim: Optional[Path] = None, inbox: Optional[Path] = None) -> str:
    """Текст для ручной настройки в интерфейсе Steam."""
    line = launch_line(shim, inbox)
    return (
        "Вручную: Steam → Библиотека → The Battle of Polytopia → правой кнопкой →\n"
        "Свойства → Общие → Параметры запуска, вставить строку:\n\n"
        f"    {line}\n\n"
        "Откат: убрать эту строку из параметров запуска.")


def detect() -> dict:
    """Предпроверка без исключений — для GUI-установщика шима.

    Возвращает всё, что нужно интерфейсу: найден ли Steam, его конфиг,
    установлена ли игра и является ли сборка нативной (Linux), на месте ли
    шим, включена ли уже наша строка запуска.
    """
    gdirs = game_dirs()
    gdir = next((d for d in gdirs if d.is_dir()), None)
    shim = shim_install_path()
    cfgs = config_paths()
    cfg = cfgs[0] if cfgs else None
    data: dict = {
        "steam_found": bool(steam_roots()),
        "config_found": cfg is not None,
        "config": str(cfg) if cfg else None,
        "appid": APPID,
        "game_found": gdir is not None,
        "game_dir": str(gdir) if gdir else None,
        "game_native": bool(gdir and (gdir / NATIVE_MARKER).is_file()),
        "shim_present": shim.is_file(),
        "shim_path": str(shim),
        "steam_running": is_steam_running(),
        "installed": False,
        "current": None,
        "expected": launch_line(),
    }
    if cfg is not None:
        try:
            st = status(config=cfg)
            data["installed"] = st.installed
            data["current"] = st.current
            data["expected"] = st.expected
        except (SteamConfigError, SteamRunningError, OSError):
            pass
    data["manual"] = manual_instructions()
    return data