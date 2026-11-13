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
    assert kof.tribe == "Tribe7"        # таблица племён расширяется по мере проверки
    assert kof.is_human is True
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
