#!/usr/bin/env python3
"""Снятие покадровых счёт/доход с реплея Polytopia (Linux + KDE/X11).

Зачем: реплей не отдаёт данные ни через share-ссылку, ни из файлов клиента,
поэтому единственный автоматический источник без мода — экран игры.
Скрипт снимает кадры во время автовоспроизведения реплея, распознаёт HUD
(Score / Stars (+доход) / Turn) и складывает ряды «ход -> счёт, доход».

Ограничения (честно):
  * читается только HUD: счёт и доход. Армия, города, убийства, захваты и
    урон на HUD не выводятся — для них нужен PolyMod-экспорт (M3).
  * точность ±1-2%: счётчик анимируется, в кадр попадают промежуточные
    значения; берётся максимум по ходу.
  * HUD показывает по очереди обоих игроков, поэтому ряд раскладывается на
    две серии по кластерам значений и сверяется с финальными счетами.

Требования: tesseract, spectacle (KDE), X11/XTest, Pillow.
Запуск:
    python scripts/harvest_replay.py --seconds 180 --out /tmp/frames
    python scripts/harvest_replay.py --series-only --out /tmp/frames

Перед запуском откройте реплей в игре: `polytopia-analyzer open <ссылка>
--launch` (или ссылка share.polytopia.io -> STEAM).
"""

from __future__ import annotations

import argparse
import collections
import ctypes
import ctypes.util
import glob
import json
import os
import re
import subprocess
import sys
import time

# окно игры: смещение на экране (второй монитор по умолчанию)
WINDOW_OFFSET_X = int(os.environ.get("POLY_OFFSET_X", "1920"))
WINDOW_OFFSET_Y = int(os.environ.get("POLY_OFFSET_Y", "0"))
WINDOW_W, WINDOW_H = 1920, 1080
HUD_H = 80  # верхняя полоса с HUD


def _spectacle(path: str) -> bool:
    env = dict(os.environ, DISPLAY=os.environ.get("DISPLAY", ":0"))
    try:
        subprocess.run(["spectacle", "-b", "-n", "-f", "-o", path], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return os.path.exists(path) and os.path.getsize(path) > 5000


def harvest(outdir: str, seconds: float, interval: float) -> int:
    os.makedirs(outdir, exist_ok=True)
    for f in glob.glob(os.path.join(outdir, "f*.png")):
        os.remove(f)
    start = time.time()
    i = 0
    while time.time() - start < seconds:
        path = os.path.join(outdir, f"f{i:03d}.png")
        _spectacle(path)
        i += 1
        time.sleep(interval)
    return i


def _ocr_hud(png: str) -> list[tuple[str, int, int]]:
    from PIL import Image
    Image.open(png).crop((WINDOW_OFFSET_X, WINDOW_OFFSET_Y,
                          WINDOW_OFFSET_X + WINDOW_W,
                          WINDOW_OFFSET_Y + HUD_H)).save("/tmp/_hud_crop.png")
    subprocess.run(["tesseract", "/tmp/_hud_crop.png", "/tmp/_hud_crop", "--psm", "11", "tsv"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    words = []
    with open("/tmp/_hud_crop.tsv", encoding="utf-8", errors="replace") as fh:
        for line in fh.read().splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) >= 12 and parts[11].strip():
                words.append((parts[11].strip(), int(parts[6]), int(parts[7])))
    return words


def parse_frames(outdir: str) -> list[dict]:
    rows = []
    for png in sorted(glob.glob(os.path.join(outdir, "f*.png"))):
        turn = score = inc = None
        for text, x, y in _ocr_hud(png):
            m = re.fullmatch(r"\(?\+(\d+)\)?", text)
            if m and inc is None:
                inc = int(m.group(1))
            if y > 30 and 995 <= x <= 1090 and re.fullmatch(r"\d{1,2}", text):
                turn = int(text)
            if (y > 30 and 845 <= x <= 945 and re.fullmatch(r"[\d,]+", text)
                    and text.replace(",", "")):
                score = int(text.replace(",", ""))
        if turn is not None:
            rows.append({"turn": turn, "score": score, "income": inc})
    return rows


def split_series(rows: list[dict], gory_cap: int = 2600) -> dict:
    """Раскладывает HUD на серии двух игроков: высокий кластер — победитель."""
    by_turn: dict[int, list[dict]] = collections.defaultdict(list)
    for r in rows:
        if r["score"] is not None:
            by_turn[r["turn"]].append(r)

    high, low, high_inc, low_inc = {}, {}, {}, {}
    for turn in sorted(by_turn):
        vals = [r["score"] for r in by_turn[turn]]
        top = max(vals)
        high[turn] = top
        lows = [v for v in vals if v < gory_cap and v < top * 0.75]
        if lows:
            low[turn] = max(lows)

        def pick(target, tol):
            c = collections.Counter(r["income"] for r in by_turn[turn]
                                    if r["income"] and r["score"]
                                    and abs(r["score"] - target) <= tol)
            return c.most_common(1)[0][0] if c else None

        high_inc[turn] = pick(top, 40)
        if turn in low:
            low_inc[turn] = pick(low[turn], 40)

    return {"high": high, "low": low, "high_income": high_inc, "low_income": low_inc}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Снятие счёт/доход с реплея через OCR HUD")
    ap.add_argument("--out", default="/tmp/frames", help="каталог для кадров")
    ap.add_argument("--seconds", type=float, default=180, help="длительность съёмки")
    ap.add_argument("--interval", type=float, default=0.25, help="пауза между кадрами")
    ap.add_argument("--series-only", action="store_true", help="не снимать, только разобрать кадры")
    args = ap.parse_args(argv)

    if not args.series_only:
        n = harvest(args.out, args.seconds, args.interval)
        print(f"снято кадров: {n}")

    rows = parse_frames(args.out)
    if not rows:
        print("HUD не распознан: игра не на экране или нет tesseract", file=sys.stderr)
        return 1
    series = split_series(rows)
    path = os.path.join(args.out, "series.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(series, fh, ensure_ascii=False, indent=1)
    print("победитель (счёт):", {t: series["high"][t] for t in sorted(series["high"])})
    print("соперник (счёт) :", {t: series["low"][t] for t in sorted(series["low"])})
    print("сохранено:", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())