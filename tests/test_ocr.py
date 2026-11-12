"""M4 (OCR-1): распознавание чисел на скриншоте финального табло.

Принцип: OCR только НАХОДИТ числа с координатами; привязку чисел к
игрокам/метрикам/ходам делает человек в интерфейсе.
"""

import io
import shutil

import pytest

from polytopia_analyzer.importers.ocr import OcrUnavailableError, recognize_numbers


def _font(size):
    """TrueType-шрифт, если есть в системе; иначе встроенный масштабированный."""
    from PIL import ImageFont

    for path in (
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def scoreboard_png(lines=("3480", "3068", "2740"), extra=()):
    """Синтетическое табло: крупные чёрные числа на белом фоне."""
    from PIL import Image, ImageDraw

    rows = list(lines) + list(extra)
    img = Image.new("RGB", (640, 110 * max(len(rows), 1)), "white")
    d = ImageDraw.Draw(img)
    font = _font(72)
    for i, text in enumerate(rows):
        d.text((40, 15 + i * 110), text, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), img.size


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract не установлен")
def test_finds_numbers_with_boxes():
    png, (w, h) = scoreboard_png()
    res = recognize_numbers(png)
    assert (res.width, res.height) == (w, h)
    values = {n.value for n in res.numbers}
    assert {3480, 3068, 2740} <= values
    for n in res.numbers:
        assert isinstance(n.value, int)
        assert 0 <= n.x < w and 0 <= n.y < h
        assert n.w > 0 and n.h > 0
        assert n.x + n.w <= w and n.y + n.h <= h
        assert 0 < n.conf <= 100


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract не установлен")
def test_ignores_words():
    """Буквы не должны превращаться в числа (whitelist цифр)."""
    png, _ = scoreboard_png(lines=("3480",), extra=("Player One",))
    res = recognize_numbers(png)
    assert 3480 in {n.value for n in res.numbers}
    # «Player One» не даёт числовых токенов
    assert all(n.value == 3480 for n in res.numbers)


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract не установлен")
def test_blank_image_has_no_numbers():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (300, 200), "white").save(buf, format="PNG")
    res = recognize_numbers(buf.getvalue())
    assert res.numbers == []
    assert (res.width, res.height) == (300, 200)


def test_missing_tesseract_raises_with_install_hint(monkeypatch):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(buf, format="PNG")
    monkeypatch.setattr(shutil, "which", lambda *_: None)
    with pytest.raises(OcrUnavailableError, match="tesseract"):
        recognize_numbers(buf.getvalue())


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract не установлен")
def test_bad_image_bytes_raise_value_error():
    with pytest.raises(ValueError):
        recognize_numbers(b"this is not an image")


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract не установлен")
def test_to_dict_is_json_ready():
    import json

    png, _ = scoreboard_png(lines=("1234",))
    d = recognize_numbers(png).to_dict()
    json.dumps(d)  # не падает
    assert d["width"] > 0 and d["height"] > 0
    assert {"value", "x", "y", "w", "h", "conf"} <= set(d["numbers"][0])