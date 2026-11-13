"""Тесты импортёра одиночных .state-сейвов (веха M7).

Эталонные значения сняты с реального сейва игры 2.18 (формат v124):
tests/fixtures/singleplayer-v124.state — свежая одиночная игра (ход 0),
KOF (человек) против Shayoszu (ИИ) + Nature.
"""

from pathlib import Path

import pytest

from polytopia_analyzer.importers import state
from polytopia_analyzer.storage import load_match, save_match
from polytopia_analyzer.validation import validate_match

FIXTURE = Path(__file__).parent / "fixtures" / "singleplayer-v124.state"


@pytest.fixture(scope="module")
def raw() -> bytes:
    return FIXTURE.read_bytes()


@pytest.fixture(scope="module")
def save(raw):
    return state.parse_state_data(state.decompress_state(raw))


# ---------- LZ4-конверт ----------

def test_decompress_exact_size(raw):
    data = state.decompress_state(raw)
    assert len(data) == 16502


def test_decompress_is_deterministic(raw):
    assert state.decompress_state(raw) == state.decompress_state(raw)


def test_decompress_rejects_unknown_flag(raw):
    bad = b"\x00" + raw[1:]
    with pytest.raises(ValueError, match="[Cc]onvert|флаг|flag|0x00"):
        state.decompress_state(bad)


def test_decompress_rejects_truncated(raw):
    with pytest.raises((ValueError, EOFError)):
        state.decompress_state(raw[: len(raw) // 2])


def test_parse_rejects_short_file():
    with pytest.raises(ValueError):
        state.parse_state_data(b"12345")


# ---------- структура сейва ----------

def test_versions(save):
    assert save.file_version == 124
    assert save.initial.header["version"] == 124
    assert save.current.header["version"] == 124


def test_header_fields(save):
    h = save.current.header
    assert h["map_width"] == 14 and h["map_height"] == 14
    assert h["map_name"] == "No name"
    assert h["turn_limit"] == 30
    assert h["current_turn"] == 0
    assert h["total_actions"] == 1
    assert 1 in h["unlocked_tribes"] and len(h["unlocked_tribes"]) == 18


def test_actions_section_consistent(save):
    # numActions в секции действий совпал с TotalActions заголовка
    assert save.num_actions == save.current.header["total_actions"] == 1
    assert save.actions is not None
    assert save.actions[0]["type"] == 20


def test_initial_players(save):
    by_name = {p["name"]: p for p in save.initial.players}
    assert set(by_name) == {"KOF", "Shayoszu", "Nature"}
    assert by_name["KOF"]["score"] == 405
    assert by_name["KOF"]["num_cities"] == 1
    assert by_name["Nature"]["score"] == 0
    assert by_name["Nature"]["num_cities"] == 0


def test_current_players(save):
    by_name = {p["name"]: p for p in save.current.players}
    kof = by_name["KOF"]
    assert kof["id"] == 1 and kof["tribe"] == 7
    assert kof["score"] == 505          # 405 + 100 за технологию
    assert kof["currency"] == 6
    assert kof["num_cities"] == 1
    assert kof["auto_play"] == 0        # человек
    assert kof["kills"] == 0 and kof["losses"] == 0
    ai = by_name["Shayoszu"]
    assert ai["tribe"] == 13 and ai["auto_play"] == 1 and ai["score"] == 505
    nature = by_name["Nature"]
    assert nature["id"] == 255 and nature["tribe"] == 1


def test_tile_stats(save):
    # ход 0: у каждого игрока одна столица (+ 11 нейтральных деревень owner=0)
    assert save.current.city_counts.get(1) == 1
    assert save.current.city_counts.get(2) == 1
    assert save.current.city_counts.get(0) == 11
    assert save.initial.city_counts.get(1) == 1
    # Юнитов в этом сейве нет вообще: мир сгенерирован, но партия не начата
    # (стартовые юниты ещё не заспавнены). Косвенное подтверждение: счёт 505 =
    # 405 (карта) + 100 (технология), без +5 за стартовый юнит. Скан всего
    # файла по паттерну UnitData тоже даёт ноль совпадений.
    # Парсинг юнитов проверяется на сейве с сыгранной партией (docs/state-format.md).
    assert save.current.unit_counts == {}
    assert save.initial.unit_counts == {}


# ---------- защита от рассинхрона формата (fail-closed) ----------

def test_rejects_unsupported_version(save, raw):
    data = bytearray(state.decompress_state(raw))
    assert save.file_version == 124
    data[0:4] = (999).to_bytes(4, "little")
    with pytest.raises(ValueError, match="[Вв]ерси|version"):
        state.parse_state_data(bytes(data))


def test_rejects_corrupted_tile_coordinates(save, raw):
    data = bytearray(state.decompress_state(raw))
    off = save.initial.tiles_offset
    data[off] ^= 0xFF  # ломаем x нулевого тайла
    with pytest.raises(ValueError, match="координат|coordinate|тайл"):
        state.parse_state_data(bytes(data))


def test_rejects_corrupted_player_trailing_byte(save, raw):
    data = bytearray(state.decompress_state(raw))
    end = save.initial.players[0]["_end"]  # последний байт записи = tribe
    data[end] ^= 0xFF
    with pytest.raises(ValueError, match="Контрольный байт"):
        state.parse_state_data(bytes(data))


# ---------- импорт в модель Match ----------

def test_match_from_state(save, tmp_path):
    match = state.match_from_state(FIXTURE)
    assert [p.name for p in match.players] == ["KOF", "Shayoszu"]
    kof = match.player("KOF")
    assert kof.tribe == "Imperius"        # tribe 7 — полная таблица в shim.TRIBE_NAMES
    assert kof.is_human is True
    assert match.player("Shayoszu").tribe == "Xin-xi"
    assert match.player("Shayoszu").is_human is False


def test_match_records_turn_zero(save):
    # сейв на ходе 0 → одна запись на игрока, без дублей
    match = state.match_from_state(FIXTURE)
    recs = match.records_for("KOF")
    assert len(recs) == 1 and recs[0].turn == 0
    r = recs[0]
    assert r.score == 505
    assert r.cities == 1
    assert r.army == 0   # в этом сейве юнитов нет (см. test_tile_stats)
    assert r.kills == 0 and r.losses == 0
    assert r.filled == {"score", "cities", "army", "kills", "losses"}


def test_match_meta(save):
    match = state.match_from_state(FIXTURE)
    assert match.meta.source == "state"
    assert match.meta.turn_limit == 30
    assert "сейв" in match.meta.notes.lower() or ".state" in match.meta.notes


def test_match_validates_and_roundtrips(tmp_path):
    match = state.match_from_state(FIXTURE)
    rep = validate_match(match)
    assert rep.errors == []
    out = tmp_path / "m.json"
    save_match(match, out)
    loaded = load_match(out)
    assert loaded.records_for("KOF")[0].score == 505
    assert loaded.meta.source == "state"


def test_nature_never_in_match(save):
    match = state.match_from_state(FIXTURE)
    assert "Nature" not in match.player_names()
    assert all(r.player != "Nature" for r in match.records)


# ---------- сейв средней партии (16×16, 17 игроков, 70 действий) ----------

MIDGAME = Path(__file__).parent / "fixtures" / "singleplayer-v124-midgame.state"


@pytest.fixture(scope="module")
def mid():
    return state.parse_state_file(MIDGAME)


def test_midgame_header(mid):
    h = mid.current.header
    assert h["map_width"] == 16 and h["map_height"] == 16
    assert h["current_turn"] == 1
    assert h["total_actions"] == 70
    assert h["difficulty"] == 3
    assert h["seed"] == 1715778920


def test_midgame_players(mid):
    assert len(mid.current.players) == 17
    by_id = {p["id"]: p for p in mid.current.players}
    kof = by_id[1]
    assert kof["tribe"] == 9 and kof["score"] == 550
    assert kof["currency"] == 8 and kof["num_cities"] == 1
    lifidee = by_id[12]
    assert lifidee["name"] == "Lïfïdee" and lifidee["score"] == 940
    assert lifidee["tech"] == [0, 22, 1, 10]


def test_midgame_initial_section(mid):
    # начальное состояние = генерация мира ДО выдачи стартовых технологий
    by_id = {p["id"]: p for p in mid.initial.players}
    assert all(p["tech"] == [] for p in by_id.values())
    assert by_id[1]["score"] == 515 and by_id[1]["currency"] == 2  # Luxidoor: 2 звезды, без теха
    assert by_id[2]["score"] == 430           # Polaris: 405 карта + 25 Mooni (5★), без Frostwork
    assert by_id[3]["score"] == 430           # Vengir: +25 Swordsman, позже 730 после Smithery t3
    assert by_id[16]["score"] == 410          # Aquarion 415 − 5 (угловой спавн)
    assert mid.initial.unit_counts[1] == 1    # один стартовый юнит


def test_midgame_units(mid):
    assert mid.current.unit_counts == {
        1: 3, 2: 2, 3: 2, 4: 2, 5: 2, 6: 1, 7: 2, 8: 1,
        9: 3, 10: 2, 11: 2, 12: 2, 13: 2, 14: 2, 15: 1, 16: 2,
    }
    assert mid.current.city_counts == {i: 1 for i in range(1, 17)}


def test_midgame_actions(mid):
    assert mid.actions is not None and mid.num_actions == 70
    assert len(mid.actions) == 70
    kinds = {}
    for a in mid.actions:
        kinds[a["type"]] = kinds.get(a["type"], 0) + 1
    assert kinds == {1: 7, 2: 3, 5: 13, 6: 17, 8: 9, 11: 2, 15: 17, 18: 1, 20: 1}
    assert kinds[15] == 17  # EndTurn: 16 игроков + Nature — ровно один круг
    # ходы 0-based, синхронно с current_turn заголовка
    assert max(a["turn"] for a in mid.actions) == 1 == mid.current.header["current_turn"]


def test_midgame_save_id_and_trailer(mid):
    assert mid.save_id is not None and mid.save_id != 0
    # save_id одинаков в разделителе секций и в трейлере
    assert mid.gap_bytes[7:11] == mid.trailer[3:7]
    # трейлер: [00 00 00][save_id][TA u16]
    assert mid.trailer[0:3] == b"\x00\x00\x00"
    import struct
    assert struct.unpack_from("<H", mid.trailer, 7)[0] == 70


def test_midgame_match(mid):
    match = state.match_from_state(MIDGAME)
    assert len(match.players) == 16
    assert "Nature" not in match.player_names()
    kof = match.player("KOF")
    assert kof.tribe == "Luxidoor" and kof.is_human
    recs = match.records_for("KOF")
    assert [r.turn for r in recs] == [0, 1]
    assert recs[0].score == 515 and recs[0].army == 1
    assert recs[1].score == 550 and recs[1].army == 3
    assert recs[1].kills == 0 and recs[1].losses == 0
    rep = validate_match(match)
    assert rep.errors == []


def test_tribe_table_full():
    from polytopia_analyzer.importers.shim import TRIBE_NAMES, tribe_name
    assert len(TRIBE_NAMES) == 17
    assert tribe_name(9) == "Luxidoor"
    assert tribe_name(13) == "Xin-xi"
    assert tribe_name(17) == "Cymanti"
    assert tribe_name(1234) == "Tribe1234"  # запасной вариант для битых данных
