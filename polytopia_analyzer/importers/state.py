"""Импорт одиночных `.state`-сейвов игры (веха M7).

Сейв одиночной игры лежит в
`~/.config/unity3d/Midjiwan/Polytopia/<uuid>/Data/Singleplayer/<uuid>.state`
(Linux) или `%USERPROFILE%/AppData/LocalLow/Midjiwan/Polytopia/<uuid>/Data/
Singleplayer/` (Windows). Это полный снимок партии: LZ4-блок, внутри
начальное состояние (ход 0), текущее состояние и история действий.

Формат (v124, игра 2.18) reverse-engineered Samuel Yuan (MIT):
PolytopiaSaveEditor / polytopiamapmodelgo / PolytopiaWorld / PolyVision;
расхождения v124 против опубликованной v122 найдены эмпирически на живых
сейвах и зафиксированы тестами (docs/state-format.md):

  * файл: [u32 версия][начальное состояние][разделитель 11Б с save_id]
          [текущее состояние][2Б][u16 numActions][действия][трейлер];
  * версия состояния (u32) открывается КАЖДАЯ секция, в v122-библиотеке
    вторая копия принималась за «Version2» первого заголовка;
  * после каждой записи игрока стоит контрольный байт == tribe;
  * трейлер: [00 00 00][save_id u32][TA u16][flag u8][сессия с UUID сейва…];
    save_id совпадает с разделителем секций — оракул границы действий.

Проверено на двух живых сейвах: 14×14/3 игрока (ход 0) и 16×16/17 игроков
(70 действий, 31 юнит, ход 1).

Целостность гарантируют оракулы (любое рассогласование — ошибка, молчаливых
пропусков нет): координаты всех тайлов (x,y)==(col,row), контрольный байт
игрока == tribe, numActions == TotalActions заголовка, точный размер LZ4.

В модель Match переносятся только проверенные метрики: score, cities, army
(число юнитов на карте), kills/losses (накопительные счётчики → прирост
разностью с начальным состоянием). Доход, захваты городов и урон сейв
напрямую не даёт — поля остаются незаполненными. Варвары (id 255, племя
Nature) в матч не попадают.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..models import Match, MatchMeta, Player, TurnRecord
from .shim import NPC_PLAYER_ID, NPC_TRIBE_ID, tribe_name

# Проверенные версии формата. 124 = игра 2.18 (живой сейв + тесты).
# Расширять только после проверки на реальном файле новой версии.
SUPPORTED_FILE_VERSIONS = {124}

# Размеры полезных нагрузок действий (байты, без u16-типа).
# Таблица из polytopiamapmodelgo (replay.go), v122; для v124 сверена
# на образце (TotalActions == numActions == прочитано).
ACTION_SIZES = {
    1: 11, 2: 21, 3: 9, 4: 9, 5: 11, 6: 21, 7: 13, 8: 3, 9: 9,
    11: 11, 13: 9, 14: 9, 15: 1, 16: 11, 17: 9, 18: 9, 20: 1,
    21: 9, 24: 9, 25: 9, 27: 10, 28: 3, 29: 10, 30: 10,
}
ACTION_END_TURN = 15
ACTION_CAPTURE_CITY = 7
NATURE_END_TURN_PLAYER = 255

_UNIT_FIXED_SIZE = 42  # UnitData: см. tiledata.go


# ---------- LZ4 ----------

def lz4_block_decompress(src: bytes, expected_size: int) -> bytes:
    """Декомпрессия сырого LZ4-блока (без фрейма). Чистый stdlib."""
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        token = src[i]
        i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                if i >= n:
                    raise ValueError("LZ4: оборванный блок (литералы)")
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        if i + lit > n:
            raise ValueError("LZ4: литералы выходят за границу блока")
        out += src[i:i + lit]
        i += lit
        if i >= n:
            break  # последняя последовательность — литералы без совпадения
        if i + 2 > n:
            raise ValueError("LZ4: оборванный блок (смещение)")
        offset = src[i] | (src[i + 1] << 8)
        i += 2
        if offset <= 0 or offset > len(out):
            raise ValueError(f"LZ4: недопустимое смещение {offset}")
        ml = token & 0xF
        if ml == 15:
            while True:
                if i >= n:
                    raise ValueError("LZ4: оборванный блок (длина совпадения)")
                b = src[i]
                i += 1
                ml += b
                if b != 255:
                    break
        ml += 4
        start = len(out) - offset
        for k in range(ml):
            out.append(out[start + k])  # пословное копирование: перекрывающиеся совпадения
    if len(out) != expected_size:
        raise ValueError(
            f"LZ4: размер не сошёлся (получено {len(out)}, ожидалось {expected_size})")
    return bytes(out)


def decompress_state(raw: bytes) -> bytes:
    """Снимает конверт .state: флаг-байт, разница размеров, LZ4-блок."""
    if len(raw) < 8:
        raise ValueError("Файл .state слишком короткий")
    size_of_diff = (raw[0] >> 6) & 3
    if size_of_diff == 3:
        size_of_diff = 4
    if size_of_diff != 2 and size_of_diff != 4:
        raise ValueError(
            f"Неизвестный флаг конверта .state: 0x{raw[0]:02x} "
            f"(ожидались 0x80/0xC0)")
    fmt = "<H" if size_of_diff == 2 else "<I"
    (diff,) = struct.unpack_from(fmt, raw, 1)
    data_offset = 1 + size_of_diff
    body = raw[data_offset:]
    result_length = len(body) + diff
    if result_length < len(body):
        raise ValueError("Конверт .state: отрицательная разница размеров")
    return lz4_block_decompress(body, result_length)


# ---------- побайтовый ридер ----------

class _Reader:
    def __init__(self, buf: bytes):
        self.b = buf
        self.i = 0

    def take(self, n: int) -> bytes:
        if self.i + n > len(self.b):
            raise EOFError(f"нужно {n} байт по смещению {self.i}, всего {len(self.b)}")
        v = self.b[self.i:self.i + n]
        self.i += n
        return v

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def i16(self) -> int:
        return struct.unpack("<h", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def i32(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def f32(self) -> float:
        return struct.unpack("<f", self.take(4))[0]

    def varstr(self, max_len: int = 64) -> str:
        n = self.u8()
        if n > max_len:
            raise ValueError(f"Подозрительная длина строки: {n} > {max_len}")
        return self.take(n).decode("utf-8", "replace")

    def peek_u8(self) -> Optional[int]:
        return self.b[self.i] if self.i < len(self.b) else None


# ---------- секции ----------

def _read_state_header(r: _Reader, file_version: int) -> dict:
    h = {}
    h["version"] = r.u32()
    if h["version"] != file_version:
        raise ValueError(
            f"Версия состояния {h['version']} != версия файла {file_version}")
    h["total_actions"] = r.u16()
    h["current_turn"] = r.u32()
    h["current_player_index"] = r.u8()
    h["max_unit_id"] = r.u32()
    h["game_state"] = r.u8()
    h["seed"] = r.i32()
    h["turn_limit"] = r.u32()
    h["score_limit"] = r.u32()
    h["win_by_capital"] = r.u8()
    r.take(6)  # unknown settings
    h["game_mode_base"] = r.u8()
    h["game_mode_rules"] = r.u8()
    h["map_name"] = r.varstr()
    h["square_size"] = r.u32()
    n = r.u16()
    if n > 64:
        raise ValueError(f"Подозрительное число отключённых племён: {n}")
    h["disabled_tribes"] = [r.u16() for _ in range(n)]
    n = r.u16()
    if n > 64:
        raise ValueError(f"Подозрительное число разблокированных племён: {n}")
    h["unlocked_tribes"] = [r.u16() for _ in range(n)]
    h["difficulty"] = r.u16()
    h["num_opponents"] = r.u32()
    h["game_type"] = r.u16()
    h["map_preset"] = r.u8()
    h["turn_time_limit_minutes"] = r.i32()
    r.f32(); r.f32(); r.f32()
    r.take(4)  # time settings
    n = r.u32()
    if n > 64:
        raise ValueError(f"Подозрительное число скинов племён: {n}")
    h["tribe_skins"] = [(r.u16(), r.u16()) for _ in range(n)]
    if h["version"] >= 122:
        r.u8()  # дополнительный байт заголовка (v122+)
    h["map_width"] = r.u16()
    h["map_height"] = r.u16()
    if not (4 <= h["map_width"] <= 64 and 4 <= h["map_height"] <= 64):
        raise ValueError(
            f"Недопустимый размер карты: {h['map_width']}x{h['map_height']}")
    return h


def _read_tiles(r: _Reader, w: int, hgt: int, version: int) -> dict:
    """Читает тайлы (оракул: координаты каждого тайла), собирает статистику."""
    unit_counts: dict[int, int] = {}
    city_counts: dict[int, int] = {}
    city_tiles: dict[tuple[int, int], int] = {}
    for row in range(hgt):
        for col in range(w):
            x = r.u32()
            y = r.u32()
            if (x, y) != (col, row):
                raise ValueError(
                    f"Рассинхрон формата: координаты тайла ({x},{y}) "
                    f"не совпали с ожидаемыми ({col},{row})")
            r.u16(); r.u16(); r.i16()      # terrain, climate, altitude
            tile_owner = r.u8()
            r.u8()                          # capital flag
            r.i32(); r.i32()               # capital coordinates
            if r.u8() == 1:
                r.u16()                     # resource type
            if r.u8() == 1:
                imp_type = r.u16()
                # ImprovementData
                r.u16(); r.u16(); r.i16(); r.u16(); r.i16(); r.i16(); r.i16(); r.i16()
                r.u8()                      # connected to capital
                if r.u8() == 1:
                    r.varstr()              # city name
                r.u8()                      # founded tribe
                k = r.u16()
                if k > 32:
                    raise ValueError(f"Подозрительное число наград города: {k}")
                r.take(2 * k)
                if r.u16() != 0:
                    r.take(2)               # rebellion buffer
                if imp_type == 1:           # город (или нейтральная деревня)
                    city_counts[tile_owner] = city_counts.get(tile_owner, 0) + 1
                    city_tiles[(col, row)] = tile_owner
            if r.u8() == 1:                 # юнит
                r.take(_UNIT_FIXED_SIZE)
                owner = r.b[r.i - _UNIT_FIXED_SIZE + 4]
                unit_counts[owner] = unit_counts.get(owner, 0) + 1
                if r.u8() == 1:             # пассажир (погрузка)
                    r.take(_UNIT_FIXED_SIZE)
                    p_owner = r.b[r.i - _UNIT_FIXED_SIZE + 4]
                    unit_counts[p_owner] = unit_counts.get(p_owner, 0) + 1
                    if r.u8() != 0:
                        raise ValueError("Флаг «пассажир пассажира» != 0")
                    k = r.u16()
                    if k > 32:
                        raise ValueError(f"Подозрительное число эффектов: {k}")
                    r.take(2 * k)
                    r.take(5)
                k = r.u16()
                if k > 32:
                    raise ValueError(f"Подозрительное число эффектов: {k}")
                r.take(2 * k)
                r.take(5)
            k = r.u8()                      # видимость по игрокам
            if k > 32:
                raise ValueError(f"Подозрительный список видимости: {k}")
            r.take(k)
            r.u8(); r.u8()                  # road, water route
            r.u16(); r.take(2)              # tile skin, unknown
            if version >= 105:
                if r.u8() == 1:
                    r.u32()                 # flooded value
    return {"units": unit_counts, "cities": city_counts, "city_tiles": city_tiles}


def _read_players(r: _Reader, version: int) -> list[dict]:
    n = r.u16()
    if n == 0 or n > 32:
        raise ValueError(f"Подозрительное число игроков: {n}")
    players = []
    for _ in range(n):
        p: dict = {}
        p["id"] = r.u8()
        if version >= 122 and r.peek_u8() == 0xFF:
            r.u8()  # маркер 0xff перед именем (v122+), семантика неизвестна
        p["name"] = r.varstr()
        p["account_id"] = r.varstr(max_len=64)
        p["auto_play"] = r.u8()
        p["start_x"] = r.i32()
        p["start_y"] = r.i32()
        p["tribe"] = r.u16()
        r.u8()  # unknown byte
        p["handicap"] = r.u32()
        if version < 114:
            k = r.u16()
            r.take(5 * k)  # aggressions
        p["currency"] = r.u32()
        p["score"] = r.u32()
        r.u32()  # unknown int 2
        p["num_cities"] = r.u16()
        k = r.u16()
        if k > 64:
            raise ValueError(f"Подозрительное число технологий: {k}")
        p["tech"] = [r.u16() for _ in range(k)]
        k = r.u16()
        if k > 32:
            raise ValueError(f"Подозрительное число встреченных игроков: {k}")
        p["encountered"] = [r.u8() for _ in range(k)]
        k = r.i16()
        if k < 0 or k > 32:
            raise ValueError(f"Подозрительное число задач игрока: {k}")
        p["tasks"] = []
        for _ in range(k):
            tt = r.i16()
            if tt in (1, 5):
                p["tasks"].append((tt, r.take(6).hex()))
            elif 1 <= tt <= 8:
                p["tasks"].append((tt, r.take(2).hex()))
            else:
                raise ValueError(f"Неизвестный тип задачи игрока: {tt}")
        p["kills"] = r.i32()
        p["losses"] = r.i32()
        p["tribes_destroyed"] = r.i32()
        p["override_color"] = r.take(4).hex()
        r.u8()  # override tribe
        k = r.u16()
        if k > 64:
            raise ValueError(f"Подозрительное число уникальных построек: {k}")
        r.take(2 * k)
        k = r.u16()
        if k > 32:
            raise ValueError(f"Подозрительное число дипломатических записей: {k}")
        r.take(23 * k)
        k = r.u16()
        if k > 64:
            raise ValueError(f"Подозрительное число дипломатических сообщений: {k}")
        r.take(2 * k)
        p["destroyed_by_tribe"] = r.u8()
        p["destroyed_turn"] = r.u32()
        r.take(4)  # unknown buffer 2
        p["end_score"] = r.i32()
        r.u16()    # player skin
        r.take(4)  # unknown buffer 3
        # v124: контрольный байт записи игрока — дубль tribe (эмпирически,
        # 3/3 совпадения на живом сейве). Несовпадение = рассинхрон формата.
        trailing = r.u8()
        if trailing != (p["tribe"] & 0xFF):
            raise ValueError(
                f"Контрольный байт племени не совпал: {trailing} != {p['tribe']} "
                f"(игрок {p['name']!r}) — формат, вероятно, изменился")
        p["_end"] = r.i - 1
        players.append(p)
    return players


@dataclass
class StateSection:
    """Одно состояние (начальное или текущее): заголовок, тайлы, игроки."""

    header: dict
    players: list[dict]
    unit_counts: dict[int, int] = field(default_factory=dict)
    city_counts: dict[int, int] = field(default_factory=dict)
    city_tiles: dict[tuple[int, int], int] = field(default_factory=dict)
    tiles_offset: int = 0


@dataclass
class StateSave:
    """Разобранный сейв целиком."""

    file_version: int
    initial: StateSection
    current: StateSection
    gap_bytes: bytes = b""
    actions: Optional[list[dict]] = None
    num_actions: Optional[int] = None
    trailer: bytes = b""
    save_id: Optional[int] = None
    decompressed_size: int = 0


def _read_section(r: _Reader, file_version: int) -> StateSection:
    header = _read_state_header(r, file_version)
    tiles_offset = r.i
    stats = _read_tiles(r, header["map_width"], header["map_height"], header["version"])
    players = _read_players(r, header["version"])
    return StateSection(header=header, players=players,
                        unit_counts=stats["units"], city_counts=stats["cities"],
                        city_tiles=stats["city_tiles"], tiles_offset=tiles_offset)


def _read_actions(r: _Reader, total_actions: int) -> tuple[list[dict], bool]:
    """Читает поток действий. Возвращает (действия, все_типы_известны).

    Нумерация ходов 0-based, синхронно current_turn заголовка: действия
    первого круга происходят на ходу 0, круг закрывает EndTurn природы
    (player 255), после счётчик растёт.
    """
    actions: list[dict] = []
    turn = 0
    for _ in range(total_actions):
        at = r.u16()
        size = ACTION_SIZES.get(at)
        if size is None:
            return actions, False
        payload = r.take(size)
        player = payload[0] if payload else None
        actions.append({"type": at, "turn": turn, "player": player,
                        "payload": payload.hex()})
        if at == ACTION_END_TURN and player == NATURE_END_TURN_PLAYER:
            turn += 1
    return actions, True


def parse_state_data(data: bytes) -> StateSave:
    """Разбирает декомпрессированный сейв. Любое рассогласование — ошибка."""
    if len(data) < 64:
        raise ValueError("Сейв слишком короткий — это не .state Polytopia")
    r = _Reader(data)
    file_version = r.u32()
    if file_version not in SUPPORTED_FILE_VERSIONS:
        raise ValueError(
            f"Неподдерживаемая версия формата .state: {file_version} "
            f"(поддерживаются: {sorted(SUPPORTED_FILE_VERSIONS)}). "
            f"Формат новой версии игры сначала нужно проверить на живом сейве.")

    initial = _read_section(r, file_version)
    players_end = r.i

    # Текущая секция: ищем начало сканом (разделитель между секциями
    # в v124 длиннее, чем в v122, и содержит непрозрачные байты).
    current: Optional[StateSection] = None
    gap_bytes = b""
    limit = min(len(data) - 8, players_end + 64)
    for cand in range(players_end, limit):
        if struct.unpack_from("<I", data, cand)[0] != file_version:
            continue
        probe = _Reader(data)
        probe.i = cand
        try:
            section = _read_section(probe, file_version)
        except (ValueError, EOFError, struct.error):
            continue
        if (section.header["map_width"] != initial.header["map_width"]
                or section.header["map_height"] != initial.header["map_height"]):
            continue
        current = section
        gap_bytes = data[players_end:cand]
        r.i = probe.i
        break
    if current is None:
        raise ValueError(
            "Не удалось найти текущее состояние в сейве (рассинхрон формата)")

    # Действия: numActions обязан совпасть с TotalActions заголовка,
    # а трейлер за действиями — с оракулом: [00 00 00][save_id u32][TA u16].
    # save_id — константа сейва, лежит и в разделителе секций (gap[7:11]).
    save_id: Optional[int] = None
    if len(gap_bytes) >= 11:
        save_id = struct.unpack_from("<I", gap_bytes, 7)[0]
    total_actions = current.header["total_actions"]
    actions: Optional[list[dict]] = None
    num_actions: Optional[int] = None
    trailer = data[r.i:]
    for cand in range(r.i, min(len(data) - 2, r.i + 8)):
        if struct.unpack_from("<H", data, cand)[0] != total_actions:
            continue
        probe = _Reader(data)
        probe.i = cand + 2
        try:
            acts, complete = _read_actions(probe, total_actions)
        except (EOFError, struct.error):
            continue
        rest = data[probe.i:]
        if not complete:
            continue
        if (len(rest) < 9 or rest[0:3] != b"\x00\x00\x00"
                or struct.unpack_from("<H", rest, 7)[0] != total_actions):
            continue
        if save_id is not None and struct.unpack_from("<I", rest, 3)[0] != save_id:
            continue
        actions, num_actions, trailer = acts, total_actions, rest
        break
    if save_id is None and len(trailer) >= 7:
        save_id = struct.unpack_from("<I", trailer, 3)[0]

    return StateSave(file_version=file_version, initial=initial, current=current,
                     gap_bytes=gap_bytes, actions=actions, num_actions=num_actions,
                     trailer=trailer, save_id=save_id, decompressed_size=len(data))


def parse_state_file(path: Path | str) -> StateSave:
    p = Path(path)
    raw = p.read_bytes()
    return parse_state_data(decompress_state(raw))


# ---------- импорт в модель матча ----------

def _real_players(players: list[dict]) -> list[dict]:
    out = []
    for p in players:
        if int(p.get("id", -1)) == NPC_PLAYER_ID:
            continue
        if int(p.get("tribe", -1)) == NPC_TRIBE_ID:
            continue
        if (p.get("name") or "").strip().lower() == "nature":
            continue
        out.append(p)
    return out


def _snapshot_players(section: StateSection) -> list[dict]:
    """Реальные игроки секции в виде единого формата словарей для снимка."""
    out = []
    for p in _real_players(section.players):
        pid = int(p["id"])
        out.append({
            "id": pid,
            "name": str(p["name"]),
            "tribe": int(p["tribe"]),
            "auto_play": int(p.get("auto_play", 1)),
            "score": int(p["score"]),
            "stars": int(p.get("currency", 0)),
            "cities": int(p["num_cities"]),
            "army": int(section.unit_counts.get(pid, 0)),
            "kills": int(p["kills"]),
            "losses": int(p["losses"]),
        })
    out.sort(key=lambda x: x["id"])
    return out


def snapshot_from_save(save: StateSave) -> dict:
    """Снимок текущего состояния сейва (для разового импорта и наблюдения)."""
    cur = save.current
    return {
        "turn": int(cur.header["current_turn"]),
        "turn_limit": int(cur.header["turn_limit"]),
        "map_name": str(cur.header["map_name"]),
        "save_id": save.save_id,
        "players": _snapshot_players(cur),
    }


# ---------- история действий: захваты городов ----------

def capture_events(actions: Optional[list[dict]],
                   city_owners: dict[tuple[int, int], int]) -> dict:
    """Захваты по ходам из истории действий.

    Возвращает {ход: {player_id: {"captured": n, "lost": n}}}.
    `city_owners` — владение городами на генерации {(x,y): owner}; обход
    действий обновляет владельцев. Нейтральные деревни (owner 0) дают захват,
    но никому не засчитываются в потери; города, построенные уже в партии,
    в начальной карте отсутствуют — у них нет предыдущего владельца.
    """
    owners = dict(city_owners)
    events: dict[int, dict[int, dict]] = {}
    for a in actions or []:
        if int(a.get("type", -1)) != ACTION_CAPTURE_CITY:
            continue
        try:
            payload = bytes.fromhex(a.get("payload", ""))
        except ValueError:
            continue
        if len(payload) < 13:
            continue
        pid = int(payload[0])
        x, y = struct.unpack_from("<II", payload, 5)
        prev = owners.get((x, y))
        owners[(x, y)] = pid
        slot = events.setdefault(int(a.get("turn", 0)), {})
        me = slot.setdefault(pid, {"captured": 0, "lost": 0})
        me["captured"] += 1
        if prev is not None and prev not in (0, NPC_PLAYER_ID) and prev != pid:
            other = slot.setdefault(prev, {"captured": 0, "lost": 0})
            other["lost"] += 1
    return events


def events_from_save(save: StateSave) -> dict:
    """Захваты по ходам для всего сейва (история действий полная)."""
    return capture_events(save.actions, save.initial.city_tiles)


# ---------- сборка Match из снимков ----------

@dataclass
class WatchSession:
    """Сеанс наблюдения за партией: снимки по ходам одной игры.

    Идентификатор игры — save_id из трейлера сейва; при смене игры
    (новая партия поверх старой) история сбрасывается.
    """

    save_id: Optional[int] = None
    snapshots: dict = field(default_factory=dict)   # ход -> снимок
    latest: Optional[StateSave] = None
    source_path: Optional[Path] = None

    def update(self, save: StateSave, path: Path | str | None = None) -> None:
        sid = save.save_id
        if sid is not None and self.save_id is not None and sid != self.save_id:
            self.snapshots.clear()  # началась другая партия
        if sid is not None:
            self.save_id = sid
        snap = snapshot_from_save(save)
        self.snapshots[snap["turn"]] = snap   # последний снимок хода побеждает
        self.latest = save
        if path is not None:
            self.source_path = Path(path)

    def turns(self) -> list[int]:
        return sorted(self.snapshots)

    @property
    def is_empty(self) -> bool:
        return not self.snapshots


def match_from_snapshots(snapshots, events: Optional[dict] = None, *,
                         title: str = "", date: str = "", mode: str = "",
                         map_name: str = "", notes: str = "",
                         turn_limit: Optional[int] = None,
                         default_title: str = "Матч из сейва игры") -> Match:
    """Собирает Match из снимков состояния (ход → игроки) и захватов по ходам.

    Снимки — состояние НА момент записи (score/cities/army); kills/losses в
    сейве накопительные — в запись попадает прирост относительно предыдущего
    наблюдавшегося снимка (для первого снимка сеанса — вся сумма; это оговорено
    в примечаниях матча). Захваты городов — точные, из истории действий.
    """
    snaps = sorted(snapshots, key=lambda s: int(s["turn"]))
    if not snaps:
        raise ValueError("Нет снимков для импорта")
    last = snaps[-1]
    players_snap = sorted(last["players"], key=lambda p: int(p["id"]))
    if not players_snap:
        raise ValueError("В сейве нет игроков (кроме варваров)")
    players = [
        Player(name=p["name"], tribe=tribe_name(p.get("tribe")),
               is_human=not int(p.get("auto_play", 1)))
        for p in players_snap
    ]
    name_by_id = {int(p["id"]): p["name"] for p in players_snap}

    records: list[TurnRecord] = []
    prev: dict[int, dict] = {}
    # Ход 0 — начальное состояние партии, а не сыгранный ход: служит лишь
    # базой для приростов. В матч попадает только если других данных нет
    # (сейв свежей партии).
    emit_zero = not any(int(s["turn"]) >= 1 for s in snaps)
    for s in snaps:
        turn = int(s["turn"])
        emit = turn >= 1 or emit_zero
        for p in s["players"]:
            pid = int(p["id"])
            if pid not in name_by_id:
                continue  # варвары (id 0) и неизвестные id
            base = prev.get(pid)
            kills = max(0, int(p["kills"]) - (int(base["kills"]) if base else 0))
            losses = max(0, int(p["losses"]) - (int(base["losses"]) if base else 0))
            if emit:
                records.append(TurnRecord(
                    turn=turn, player=name_by_id[pid],
                    score=float(p["score"]), cities=int(p["cities"]),
                    army=int(p.get("army", 0)),
                    kills=kills, losses=losses,
                    filled={"score", "cities", "army", "kills", "losses"},
                ))
            prev[pid] = p

    # захваты из истории действий: сливаем в снимки своего хода,
    # для ходов без снимков создаём частичную запись
    for turn, per_player in sorted((events or {}).items()):
        for pid, ev in per_player.items():
            name = name_by_id.get(int(pid))
            if name is None:
                continue  # варвары и неизвестные — не в матче
            target = next((r for r in records
                           if r.turn == turn and r.player == name), None)
            if target is None:
                target = TurnRecord(turn=int(turn), player=name, filled=set())
                records.append(target)
            target.captured = int(ev.get("captured", 0))
            target.lost_cities = int(ev.get("lost", 0))
            target.filled = (target.filled or set()) | {"captured", "lost_cities"}

    if turn_limit is None:
        # динамически: последний фактический ход с данными; для пустого сейва
        # (ход 0) — игровой лимит из заголовка
        turn_limit = max((r.turn for r in records), default=0) \
            or int(last.get("turn_limit") or 0) or None
    meta = MatchMeta(
        title=title or default_title,
        date=date or datetime.now().strftime("%Y-%m-%d"),
        mode=mode,
        map=map_name or str(last.get("map_name") or ""),
        turn_limit=turn_limit,
        notes=notes,
        source="state",
    )
    match = Match(meta=meta, players=players, records=records)
    match.validate()
    return match


def match_from_save(save: StateSave, *, title: str = "", date: str = "",
                    mode: str = "", map_name: str = "", notes: str = "") -> Match:
    """Разовый импорт одного сейва: ход 0 (если партия шла) + текущий ход."""
    cur = save.current
    turn = int(cur.header["current_turn"])
    snaps = []
    if turn > 0:
        snaps.append({
            "turn": 0,
            "turn_limit": cur.header["turn_limit"],
            "map_name": cur.header["map_name"],
            "players": _snapshot_players(save.initial),
        })
    snaps.append(snapshot_from_save(save))
    events = events_from_save(save)
    default_notes = (
        f"Импортировано из сейва одиночной игры (.state), формат v{save.file_version}. "
        f"Точные снимки: начало партии и ход {turn}. Убийства и потери — "
        "накопительные счётчики, прирост за партию отнесён к последнему ходу. "
        "Захваты городов — по ходам из истории действий игры. "
        "Доход и урон сейв не содержит — этих данных нет."
    )
    return match_from_snapshots(
        snaps, events, title=title, date=date, mode=mode, map_name=map_name,
        notes=notes or default_notes,
    )


def match_from_state(path: Path | str, *, title: str = "", date: str = "",
                     mode: str = "", map_name: str = "", notes: str = "") -> Match:
    """Читает сейв одиночной игры и собирает Match."""
    p = Path(path)
    save = parse_state_file(p)
    if not date:
        try:
            date = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
        except OSError:
            date = ""
    return match_from_save(save, title=title, date=date, mode=mode,
                           map_name=map_name, notes=notes)


def match_from_watch(session: WatchSession, *, title: str = "", date: str = "",
                     mode: str = "", map_name: str = "", notes: str = "") -> Match:
    """Матч из снимков сеанса наблюдения (история по ходам)."""
    if session.is_empty:
        raise ValueError("Сеанс наблюдения пуст")
    events = events_from_save(session.latest) if session.latest else {}
    default_notes = (
        "Записано наблюдением за сейвом одиночной игры (.state): снимок на "
        "каждом ходу, захваты городов — из истории действий игры. Убийства и "
        "потери — прирост между снимками (за первый наблюдавшийся ход — вся "
        "накопленная сумма). Доход и урон сейв не содержит — этих данных нет."
    )
    if not date and session.source_path is not None:
        try:
            date = datetime.fromtimestamp(
                session.source_path.stat().st_mtime).strftime("%Y-%m-%d")
        except OSError:
            pass
    return match_from_snapshots(
        list(session.snapshots.values()), events,
        title=title, date=date, mode=mode, map_name=map_name,
        notes=notes or default_notes,
        default_title="Партия из сейва (наблюдение)",
    )


# ---------- поиск сейвов ----------

def game_save_dirs() -> list[Path]:
    """Кандидаты на папку одиночных сейвов игры (Linux, Windows, override)."""
    import os

    env = os.environ.get("POLYTOPIA_SAVES_DIR")
    if env:
        return [Path(env).expanduser()]
    home = Path.home()
    bases = [
        home / ".config/unity3d/Midjiwan/Polytopia",           # Linux
        home / "AppData/LocalLow/Midjiwan/Polytopia",          # Windows
    ]
    dirs: list[Path] = []
    for base in bases:
        if base.is_dir():
            for sub in sorted(base.glob("*/Data/Singleplayer")):
                if sub.is_dir():
                    dirs.append(sub)
    return dirs


def find_latest_save(search_dirs: Optional[list[Path]] = None) -> Optional[Path]:
    """Самый свежий одиночный сейв (или None)."""
    dirs = search_dirs if search_dirs is not None else game_save_dirs()
    best: Optional[Path] = None
    best_mtime = -1.0
    for d in dirs:
        for f in d.glob("*.state"):
            try:
                m = f.stat().st_mtime
            except OSError:
                continue
            if m > best_mtime:
                best, best_mtime = f, m
    return best
