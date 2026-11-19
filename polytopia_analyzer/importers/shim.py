"""Импорт снимков состояния игры (шим LD_PRELOAD, вехи 6.2/6.3/0.5.x).

Шим пишет в папку inbox файлы `snapshot-<ход>.json`. Формат v2 (0.5.x):

    {"v": 2, "turn": 12, "actions_total": 9,
     "players": [{"id": 1, "name": "KOF", "tribe": 16, "score": 4485,
                  "stars": 8, "cities": 7, "kills": 1, "casualties": 2,
                  "resigned_turn": -1, "wiped": false,
                  "army": 15, "production": 19}, ...],
     "cities": [[x, y, tile_owner, imp_owner, type, level, production], ...],
     "units": [[id, owner, health, type, x, y], ...],
     "events": [...]}

Рядом лежит `commands.json` (v2) — полный журнал команд матча с координатами
(AttackCommand: uid/origin/target; Move/Recover/Disband/Explode: координаты)
и `actions.json` — диагностический перехват очереди анимаций (неполный:
опрос ловит ~30% боевых действий, для данных НЕ используется).

Превращение в модель Match:

  * score и cities — снимок на ход, переносятся как есть;
  * kills и casualties в состоянии накопительные, в модели убийства/потери
    считаются за ход → берём разницу с предыдущим ходом;
  * army — число юнитов игрока на карте (v2);
  * income — сумма production городов игрока (v2); сверялась с HUD и
    динамикой звёзд; награды руин/городов в income не входят;
  * captured / lost_cities — дифф владельца городов (type==1) между
    снимками (v2): сменился владелец — захват; сверено с CaptureCommand
    из журнала команд (commands.json);
  * events в снимке — диагностические: ActionStack игры является живой
    очередью анимаций, события дублируются/пропускаются, импортёр их
    игнорирует;
  * damage — реконструкция по журналу команд v2 и HP-дельтам юнитов:
    атакованные юниты → урон атакующим, контратака → урон защитнику;
    добивания учитываются по остатку HP (overkill не виден), небоевые
    исчезновения (посадка на корабль и т.п.) исключаются по командам.

Снимки v1 (без "v"/cities) импортируются по-старому: score/cities/kills/losses.
Варвары (id 255, племя Nature) в матч не попадают.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from ..models import Match, MatchMeta, Player, TurnRecord

# TribeType — runtime-enum игры, одинаков в снимках шима и в .state-сейвах.
# Полная таблица собрана по живому сейву 16×16 с 16 ИИ: имя каждого бота
# принадлежит своему племени (gist «Polytopia Tribe Names from Bots»), каждое
# соответствие подтверждено стартовым счётом/звёздами из независимой таблицы
# (Agentcounter/tribes.md). Контрольные якоря: 1/15/16 — шим на реальном
# матче (KOF — Polaris, Goylem — Zebasi); 4 — конвертер PolyVision (5517
# сейвов Bardur). 9 = Luxidoor — исключением (остальные id заняты) + контроль
# (уникальные 2 стартовые звезды). Непроверенных id не осталось; для битых
# данных запасной вариант Tribe<n> сохраняется.
TRIBE_NAMES = {
    1: "Nature",
    2: "Ai-Mo",
    3: "Aquarion",
    4: "Bardur",
    5: "Elyrion",
    6: "Hoodrick",
    7: "Imperius",
    8: "Kickoo",
    9: "Luxidoor",
    10: "Oumaji",
    11: "Quetzali",
    12: "Vengir",
    13: "Xin-xi",
    14: "Yadakk",
    15: "Zebasi",
    16: "Polaris",
    17: "Cymanti",
}
NPC_TRIBE_ID = 1          # варвары
NPC_PLAYER_ID = 255

# что реально есть в состоянии игры (v2-снимки шима); v1 — только первые четыре
AVAILABLE_METRICS = ("score", "cities", "kills", "losses",
                     "army", "income", "captured", "lost_cities")
AVAILABLE_METRICS_V1 = ("score", "cities", "kills", "losses")

CITY_IMPROVEMENT_TYPE = 1  # ImprovementData.Type: город/деревня (как в .state)


def _is_v2(snap: dict) -> bool:
    return int(snap.get("v") or 1) >= 2 or "cities" in snap


def _city_owners(snap: dict) -> dict[tuple[int, int], int]:
    """Города снимка: (x, y) → владелец тайла.

    Владелец города = владелец тайла (TileData.owner): поле owner самого
    ImprovementState в реплее не заполнено. Записи: [x, y, tile_owner,
    imp_owner, type, level, production].
    """
    out: dict[tuple[int, int], int] = {}
    for c in snap.get("cities") or []:
        try:
            if len(c) >= 7 and int(c[4]) == CITY_IMPROVEMENT_TYPE:
                out[(int(c[0]), int(c[1]))] = int(c[2])
        except (TypeError, ValueError):
            continue
    return out


def _city_deltas(prev_owners: dict[tuple[int, int], int],
                 cur_owners: dict[tuple[int, int], int], pid: int) -> tuple[int, int]:
    """(захвачено, потеряно) городов игрока pid между двумя снимками."""
    captured = lost = 0
    for coord, own in cur_owners.items():
        old = prev_owners.get(coord)
        if old is None or old == own:
            continue
        if own == pid:
            captured += 1
        if old == pid:
            lost += 1
    return captured, lost


def tribe_name(tribe_id: Optional[int]) -> str:
    """Название племени по числовому TribeType (с запасным вариантом)."""
    if tribe_id is None:
        return ""
    return TRIBE_NAMES.get(int(tribe_id), f"Tribe{int(tribe_id)}")


@dataclass
class InboxSession:
    """Набор снимков одного проигрывания (папка inbox целиком)."""

    directory: Path
    files: list[Path]
    turns: list[int]
    newest_mtime: float

    @property
    def is_empty(self) -> bool:
        return not self.files


def load_snapshots(directory: Path | str) -> list[dict]:
    """Читает все snapshot-*.json из папки, сортируя по номеру хода."""
    d = Path(directory)
    if not d.is_dir():
        raise ValueError(f"Папка снимков не найдена: {d}")
    snaps: list[dict] = []
    for p in sorted(d.glob("snapshot-*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ValueError(f"Некорректный снимок {p.name}: {e}") from e
        if not isinstance(data, dict) or "turn" not in data:
            raise ValueError(f"Снимок без номера хода: {p.name}")
        snaps.append(data)
    if not snaps:
        raise ValueError(f"В папке нет снимков: {d}")
    snaps.sort(key=lambda s: int(s["turn"]))
    return snaps


def scan_inbox(directory: Path | str) -> InboxSession:
    """Описывает содержимое папки inbox, ничего не импортируя."""
    d = Path(directory)
    files = sorted(d.glob("snapshot-*.json")) if d.is_dir() else []
    turns: list[int] = []
    newest = 0.0
    for p in files:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            turns.append(int(data["turn"]))
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
        newest = max(newest, p.stat().st_mtime)
    return InboxSession(directory=d, files=files, turns=sorted(turns), newest_mtime=newest)


def load_commands(directory: Path | str) -> Optional[dict]:
    """Читает commands.json (журнал команд матча) из папки снимков, если есть."""
    p = Path(directory) / "commands.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


# ---------- урон по журналу команд и HP-дельтам (M14) ----------

HP_SCALE = 10.0  # внутренняя шкала HP игры: 100 = 10 HP в UI


def _command_turns(commands: list[dict]) -> list[int]:
    """Номер хода для каждой команды: раунд = полный круг EndTurnCommand."""
    end_ps = {int(c.get("p", 0)) for c in commands if c.get("t") == "EndTurnCommand"}
    turns: list[int] = []
    cur, ended = 1, set()
    for c in commands:
        turns.append(cur)
        if c.get("t") == "EndTurnCommand":
            ended.add(int(c.get("p", 0)))
            if end_ps and len(ended) == len(end_ps):
                cur += 1
                ended = set()
    return turns


def _unit_map(snap: dict) -> dict[int, dict]:
    return {int(u[0]): {"own": int(u[1]), "hp": int(u[2]),
                        "x": int(u[4]), "y": int(u[5])}
            for u in (snap.get("units") or []) if len(u) >= 6}


def _compute_damage(snaps: list[dict], commands_doc: dict) -> tuple[dict, list[str]]:
    """Детерминированная реконструкция урона (проверена на эталонных реплеях).

    Источник истины — снимки (HP юнитов со сквозными id) и commands.json v2
    (AttackCommand: кто/откуда/куда; Move/Recover/Disband/Explode: координаты).
    ActionStack игры для этого непригоден: перехват опросом ловит лишь ~30%
    боевых действий (очередь анимаций опустошается быстрее опроса).

    Правила: потеря HP юнита, по которому в раунде были AttackCommand, —
    урон атакующих (поровну при нескольких); потеря HP атакующего, которого
    не били, — контратака (урон записывается защитнику); исчезновение юнита
    без атаки/Disband/Explode — небоевое (посадка на корабль и т.п.);
    смерть от атаки учитывается по остатку HP (overkill не виден);
    лечение в том же раунде занижает урон (флагуется).

    Возвращает ({(ход, id_игрока): урон в единицах UI}, [оговорки]).
    """
    commands = commands_doc.get("commands") or []
    if not commands:
        return {}, []
    turn_of = _command_turns(commands)
    by_turn = {int(s["turn"]): s for s in snaps}
    dmg: dict[tuple[int, int], float] = {}
    flags: list[str] = []
    n_overkill = n_heal = 0
    for t in range(1, max(by_turn) + 1):
        if t not in by_turn or (t - 1) not in by_turn:
            continue
        prev_u = _unit_map(by_turn[t - 1])
        cur_u = _unit_map(by_turn[t])
        pos = {uid: (u["x"], u["y"]) for uid, u in prev_u.items()}
        attacked_by: dict[int, list[int]] = {}
        attacker_victims: dict[int, list[int]] = {}
        recover_xy: set = set()
        noncombat_xy: set = set()
        for i, c in enumerate(commands):
            if turn_of[i] != t:
                continue
            ct = c.get("t")
            if ct == "MoveCommand" and c.get("uid") is not None:
                pos[int(c["uid"])] = (c.get("tx"), c.get("ty"))
            elif ct == "AttackCommand":
                tgt = (c.get("tx"), c.get("ty"))
                ap = int(c.get("p", -1))
                victim = next((uid for uid, pxy in pos.items()
                               if pxy == tgt and uid in prev_u
                               and prev_u[uid]["own"] != ap), None)
                if victim is not None:
                    attacked_by.setdefault(victim, []).append(ap)
                    if c.get("uid") is not None:
                        attacker_victims.setdefault(int(c["uid"]), []).append(
                            prev_u[victim]["own"])
                auid = c.get("uid")
                if auid is not None and int(auid) in cur_u:
                    au = cur_u[int(auid)]
                    pos[int(auid)] = (au["x"], au["y"])
            elif ct == "RecoverCommand":
                recover_xy.add((c.get("x"), c.get("y")))
            elif ct in ("DisbandCommand", "ExplodeCommand"):
                noncombat_xy.add((c.get("x"), c.get("y")))
        for uid, pu in prev_u.items():
            alive = uid in cur_u
            loss = pu["hp"] - (cur_u[uid]["hp"] if alive else 0)
            if loss <= 0:
                continue
            if not alive and (pu["x"], pu["y"]) in noncombat_xy:
                continue  # расформирован/взорван — не боевая потеря
            atkers = attacked_by.get(uid)
            if atkers:
                if alive and (pu["x"], pu["y"]) in recover_xy:
                    n_heal += 1
                if not alive:
                    n_overkill += 1
                for ap in atkers:
                    key = (t, ap)
                    dmg[key] = dmg.get(key, 0.0) + loss / len(atkers) / HP_SCALE
            elif uid in attacker_victims:
                victims = attacker_victims[uid]
                for vo in victims:
                    key = (t, vo)
                    dmg[key] = dmg.get(key, 0.0) + loss / len(victims) / HP_SCALE
            elif alive:
                flags.append(f"ход {t}: юнит {uid} потерял {loss} HP без команды атаки")
    if n_overkill:
        flags.append(f"добиваний с невидимым overkill: {n_overkill}")
    if n_heal:
        flags.append(f"лечение в ход атаки: {n_heal}")
    return dmg, flags


def _real_players(snapshot: dict) -> list[dict]:
    out = []
    for p in snapshot.get("players") or []:
        if int(p.get("id", -1)) == NPC_PLAYER_ID:
            continue
        if int(p.get("tribe", -1)) == NPC_TRIBE_ID:
            continue
        if (p.get("name") or "").strip().lower() == "nature":
            continue
        out.append(p)
    return out


def match_from_snapshots(snapshots: Iterable[dict], *, commands: Optional[dict] = None,
                         title: str = "",
                         date: str = "", link: str = "", mode: str = "", map_name: str = "",
                         notes: str = "") -> Match:
    """Собирает Match из снимков состояния игры.

    commands — журнал команд матча (commands.json v2, см. load_commands):
    с ним заполняется последняя метрика — урон (реконструкция по HP-дельтам).
    """
    snaps = sorted(snapshots, key=lambda s: int(s["turn"]))
    if not snaps:
        raise ValueError("Нет снимков для импорта")

    # игроки — по последнему снимку (там точно все)
    last = snaps[-1]
    players_raw = _real_players(last)
    if not players_raw:
        raise ValueError("В снимках нет игроков (кроме варваров)")

    players = [
        Player(name=str(p.get("name") or f"Игрок{p['id']}"),
               tribe=tribe_name(p.get("tribe")),
               is_human=bool(p.get("is_human", False)))
        for p in sorted(players_raw, key=lambda p: int(p.get("id", 0)))
    ]
    names_by_id = {int(p["id"]): str(p.get("name")) for p in players_raw}

    records: list[TurnRecord] = []
    prev: dict[int, dict] = {}
    v2 = any(_is_v2(s) for s in snaps)
    filled = set(AVAILABLE_METRICS) if v2 else set(AVAILABLE_METRICS_V1)
    dmg_by: dict = {}
    dmg_flags: list = []
    dmg_on = bool(v2 and commands and int(commands.get("v", 1)) >= 2)
    if dmg_on:
        dmg_by, dmg_flags = _compute_damage(snaps, commands or {})
        filled = filled | {"damage"}
    prev_owners: dict[tuple[int, int], int] = {}
    # Ход 0 — начальное состояние ДО проигрывания реплея, а не сыгранный ход:
    # служит лишь базой для приростов и диффа городов. В матч попадает только
    # если других данных нет (реплей так и не начали проигрывать).
    emit_zero = not any(int(s["turn"]) >= 1 for s in snaps)
    for snap in snaps:
        turn = int(snap["turn"])
        emit = turn >= 1 or emit_zero
        cur_owners = _city_owners(snap) if v2 else {}
        for p in _real_players(snap):
            pid = int(p.get("id", -1))
            name = names_by_id.get(pid) or str(p.get("name") or f"Игрок{pid}")
            base = prev.get(pid, {})
            kills_total = int(p.get("kills", 0) or 0)
            losses_total = int(p.get("casualties", 0) or 0)
            prev_kills = int(base.get("kills", 0) or 0) if base else 0
            prev_losses = int(base.get("casualties", 0) or 0) if base else 0
            captured, lost = _city_deltas(prev_owners, cur_owners, pid) if v2 else (0, 0)
            if emit:
                records.append(TurnRecord(
                    turn=turn,
                    player=name,
                    score=float(p.get("score", 0) or 0),
                    cities=int(p.get("cities", 0) or 0),
                    kills=max(0, kills_total - prev_kills),
                    losses=max(0, losses_total - prev_losses),
                    army=int(p.get("army", 0) or 0) if v2 else 0,
                    income=float(p.get("production", 0) or 0) if v2 else 0.0,
                    captured=captured,
                    lost_cities=lost,
                    damage=float(dmg_by.get((turn, pid), 0.0)) if dmg_on else 0.0,
                    filled=set(filled),
                ))
            prev[pid] = p
        prev_owners = cur_owners

    if v2 and dmg_on:
        default_notes = ("Снято шимом из состояния игры (формат v2): счёт, города, армия, "
                         "доход городов, убийства, потери, захваты и урон (реконструкция "
                         "по журналу команд и HP юнитов).")
        if dmg_flags:
            default_notes += " Оговорки урона: " + "; ".join(dmg_flags) + "."
    elif v2:
        default_notes = ("Снято шимом из состояния игры (формат v2): счёт, города, армия, "
                         "доход городов, убийства, потери и захваты. Урон состояние "
                         "не содержит — колонка не заполнена.")
    else:
        default_notes = ("Снято шимом из состояния игры. Доход, армия, захваты городов и урон "
                         "в состоянии отсутствуют — этих данных в матче нет.")
    meta = MatchMeta(
        title=title or "Матч из игры",
        date=date or datetime.fromtimestamp(
            datetime.now().timestamp()).strftime("%Y-%m-%d"),
        mode=mode,
        map=map_name,
        link=link,
        turn_limit=max((r.turn for r in records), default=0),
        notes=(notes or default_notes),
        source="shim",
    )
    match = Match(meta=meta, players=players, records=records)
    match.validate()
    return match


def match_from_inbox(directory: Path | str, *, title: str = "", date: str = "",
                     link: str = "", mode: str = "", map_name: str = "") -> Match:
    """Читает папку inbox и собирает из неё матч."""
    return match_from_snapshots(load_snapshots(directory),
                                commands=load_commands(directory),
                                title=title, date=date,
                                link=link, mode=mode, map_name=map_name)


def default_inbox_dir() -> Path:
    """Папка, в которую пишет шим (см. shim/run-dump.sh)."""
    from .. import paths

    return paths.inbox_dir()