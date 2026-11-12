"""Загрузка и сохранение матчей в форматах JSON и CSV."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict
from pathlib import Path
from typing import Union

from .models import (
    DELTA_FIELDS,
    FLOAT_FIELDS,
    INT_FIELDS,
    META_FIELDS,
    METRIC_FIELDS,
    PLAYER_FIELDS,
    SNAPSHOT_FIELDS,
    Match,
    MatchMeta,
    Player,
    TurnRecord,
)

PathLike = Union[str, Path]

CSV_COLUMNS = ["turn", "player", *METRIC_FIELDS]
_HUMAN_MARKERS = ("human", "1", "true", "yes", "да")


# ---------- приведение типов ----------

def _to_number(value, field_name: str) -> float:
    """Приводит значение к числу; пустое значение -> 0."""
    if value is None:
        return 0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        s = value.strip().replace(",", ".")
        if not s:
            return 0
        try:
            return float(s)
        except ValueError:
            pass
    raise ValueError(f"Поле {field_name!r}: ожидалось число, получено {value!r}")


def _coerce_record_fields(raw: dict) -> dict:
    unknown = set(raw) - {"turn", "player"} - set(METRIC_FIELDS)
    if unknown:
        raise ValueError(f"Неизвестные поля записи: {sorted(unknown)}")
    for required in ("turn", "player"):
        if required not in raw:
            raise ValueError(f"В записи нет обязательного поля {required!r}")
    out: dict = {"player": str(raw["player"]).strip()}
    if not out["player"]:
        raise ValueError("Пустое имя игрока в записи")
    turn = _to_number(raw["turn"], "turn")
    out["turn"] = int(turn)
    if out["turn"] != turn:
        raise ValueError(f"Номер хода должен быть целым: {raw['turn']!r}")
    for f in METRIC_FIELDS:
        v = _to_number(raw.get(f), f)
        out[f] = int(v) if f in INT_FIELDS else float(v)
    return out


def _player_from_mapping(raw: dict) -> Player:
    unknown = set(raw) - set(PLAYER_FIELDS)
    if unknown:
        raise ValueError(f"Неизвестные поля игрока: {sorted(unknown)}")
    if not raw.get("name"):
        raise ValueError("У игрока нет имени")
    return Player(
        name=str(raw["name"]),
        tribe=str(raw.get("tribe") or ""),
        color=raw.get("color") or None,
        is_human=bool(raw.get("is_human", False)),
    )


def _meta_from_mapping(raw: dict) -> MatchMeta:
    unknown = set(raw) - set(META_FIELDS)
    if unknown:
        raise ValueError(f"Неизвестные поля в meta: {sorted(unknown)}")
    kwargs = {k: v for k, v in raw.items() if k != "turn_limit"}
    if raw.get("turn_limit") is not None:
        kwargs["turn_limit"] = int(_to_number(raw["turn_limit"], "turn_limit"))
    return MatchMeta(**kwargs)


# ---------- JSON ----------

def match_to_dict(match: Match) -> dict:
    meta = {k: v for k, v in asdict(match.meta).items() if v not in ("", None)}
    players = [asdict(p) for p in match.players]
    turns = [asdict(r) for r in sorted(match.records, key=lambda r: (r.turn, r.player))]
    return {"meta": meta, "players": players, "turns": turns}


def match_from_dict(data: dict) -> Match:
    match = Match(
        meta=_meta_from_mapping(data.get("meta") or {}),
        players=[_player_from_mapping(p) for p in data.get("players") or []],
        records=[TurnRecord(**_coerce_record_fields(t)) for t in data.get("turns") or []],
    )
    match.validate()
    return match


def match_to_json(match: Match) -> str:
    return json.dumps(match_to_dict(match), ensure_ascii=False, indent=2)


def match_from_json(text: str) -> Match:
    return match_from_dict(json.loads(text))


# ---------- CSV ----------

def match_to_csv(match: Match) -> str:
    buf = io.StringIO()
    for f in META_FIELDS:
        v = getattr(match.meta, f)
        if v not in ("", None):
            buf.write(f"# {f}: {v}\n")
    for p in match.players:
        parts = [p.name]
        if p.tribe or p.color or p.is_human:
            parts.append(p.tribe)
        if p.color or p.is_human:
            parts.append(p.color or "")
        if p.is_human:
            parts.append("human")
        buf.write("# player: " + "; ".join(parts) + "\n")
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    for r in sorted(match.records, key=lambda r: (r.turn, r.player)):
        row = [getattr(r, c) for c in CSV_COLUMNS]
        writer.writerow(row)
    return buf.getvalue()


def match_from_csv(text: str) -> Match:
    meta_kwargs: dict = {}
    players: list[Player] = []
    data_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            _parse_csv_comment(stripped[1:].strip(), meta_kwargs, players)
        elif stripped:
            data_lines.append(line)
    if not data_lines:
        raise ValueError("В CSV нет табличных данных")
    records = _parse_csv_table(data_lines)
    match = Match(meta=_meta_from_mapping(meta_kwargs), players=players, records=records)
    match.validate()
    return match


def _parse_csv_comment(content: str, meta_kwargs: dict, players: list[Player]) -> None:
    low = content.lower()
    if low.startswith("player:"):
        parts = [p.strip() for p in content[len("player:"):].split(";")]
        if not parts or not parts[0]:
            raise ValueError(f"Пустое имя игрока в комментарии: {content!r}")
        tribe = parts[1] if len(parts) > 1 else ""
        color = parts[2] if len(parts) > 2 and parts[2] else None
        is_human = len(parts) > 3 and parts[3].lower() in _HUMAN_MARKERS
        players.append(Player(name=parts[0], tribe=tribe, color=color, is_human=is_human))
        return
    if ":" not in content:
        return  # обычный комментарий
    key, _, value = content.partition(":")
    key = key.strip().lower()
    if key not in META_FIELDS:
        raise ValueError(f"Неизвестное поле в комментарии CSV: {key!r}")
    value = value.strip()
    if key == "turn_limit":
        meta_kwargs[key] = int(_to_number(value, key))
    else:
        meta_kwargs[key] = value


def _parse_csv_table(data_lines: list[str]) -> list[TurnRecord]:
    reader = csv.DictReader(io.StringIO("\n".join(data_lines)))
    if reader.fieldnames is None:
        raise ValueError("В CSV нет заголовка")
    header = [h.strip().lower() for h in reader.fieldnames]
    unknown = set(header) - {"turn", "player"} - set(METRIC_FIELDS)
    if unknown:
        raise ValueError(f"Неизвестные колонки в CSV: {sorted(unknown)}")
    for required in ("turn", "player"):
        if required not in header:
            raise ValueError(f"В CSV нет обязательной колонки {required!r}")
    records = []
    for i, row in enumerate(reader, start=2):
        clean = {(k or "").strip().lower(): v for k, v in row.items() if k}
        try:
            records.append(TurnRecord(**_coerce_record_fields(clean)))
        except ValueError as e:
            raise ValueError(f"CSV, строка {i}: {e}") from e
    return records


# ---------- файлы ----------

def load_match(path: PathLike) -> Match:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    suffix = p.suffix.lower()
    if suffix == ".json":
        return match_from_json(text)
    if suffix in (".csv", ".txt"):
        return match_from_csv(text)
    # неизвестное расширение: пробуем JSON, затем CSV
    try:
        return match_from_json(text)
    except json.JSONDecodeError:
        return match_from_csv(text)


def save_match(match: Match, path: PathLike) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    suffix = p.suffix.lower()
    if suffix == ".csv":
        p.write_text(match_to_csv(match), encoding="utf-8")
    else:
        p.write_text(match_to_json(match), encoding="utf-8")
    return p
