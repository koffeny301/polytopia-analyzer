"""OCR-1 (M4): поиск чисел на скриншоте финального табло.

OCR только НАХОДИТ числа (значение + координаты + уверенность).
Привязку к игрокам, метрикам и ходам делает человек в веб-интерфейсе —
автоматическое угадывание запрещено принципом плана v0.2.

Зависимости опциональные: pip install polytopia-match-analyzer[ocr]
плюс системный tesseract (Windows: установщик UB-Mannheim,
Linux: пакет tesseract-ocr / tesseract).
"""

from __future__ import annotations

import io
import shutil
from dataclasses import dataclass, field


class OcrUnavailableError(RuntimeError):
    """OCR-стек не установлен (системный tesseract или python-обвязка)."""


_INSTALL_HINT = (
    "OCR недоступен: {reason}. "
    "Установите системный tesseract (Linux: pacman -S tesseract / "
    "apt install tesseract-ocr; Windows: установщик UB-Mannheim) "
    "и pip install polytopia-match-analyzer[ocr]."
)


@dataclass(frozen=True)
class OcrNumber:
    """Одно распознанное число: значение, рамка на изображении, уверенность."""

    value: int
    x: int
    y: int
    w: int
    h: int
    conf: float

    def to_dict(self) -> dict:
        return {"value": self.value, "x": self.x, "y": self.y,
                "w": self.w, "h": self.h, "conf": self.conf}


@dataclass
class OcrResult:
    numbers: list[OcrNumber] = field(default_factory=list)
    width: int = 0
    height: int = 0

    def to_dict(self) -> dict:
        return {"numbers": [n.to_dict() for n in self.numbers],
                "width": self.width, "height": self.height}


def recognize_numbers(image_bytes: bytes, *, psm: int = 11,
                      min_conf: float = 40.0) -> OcrResult:
    """Находит числа на изображении (PNG/JPEG/...).

    psm=11 — «разреженный текст»: подходит для табло, где числа разбросаны
    по картинке. min_conf отсекает мусор (шкала tesseract 0..100, -1 = нет).
    Буквы отбрасываются whitelist'ом цифр.
    """
    if shutil.which("tesseract") is None:
        raise OcrUnavailableError(
            _INSTALL_HINT.format(reason="tesseract не найден в PATH"))
    try:
        import pytesseract
        from PIL import Image
    except ImportError as e:
        raise OcrUnavailableError(
            _INSTALL_HINT.format(reason=f"не установлен {e.name}")) from e

    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
    except Exception as e:
        raise ValueError(f"Не удалось прочитать изображение: {e}") from e

    width, height = img.size
    data = pytesseract.image_to_data(
        img.convert("L"),
        config=f"--psm {psm} -c tessedit_char_whitelist=0123456789",
        output_type=pytesseract.Output.DICT,
    )
    numbers: list[OcrNumber] = []
    for i, text in enumerate(data["text"]):
        token = str(text).strip()
        if not token or not token.isdigit():
            continue
        conf = float(data["conf"][i])
        if conf < min_conf:
            continue
        numbers.append(OcrNumber(
            value=int(token),
            x=int(data["left"][i]), y=int(data["top"][i]),
            w=int(data["width"][i]), h=int(data["height"][i]),
            conf=round(conf, 1),
        ))
    return OcrResult(numbers=numbers, width=width, height=height)