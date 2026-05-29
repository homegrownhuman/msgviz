# -*- coding: utf-8 -*-
"""
msgviz.core.ocr.tesseract — Tesseract OCR via pytesseract.

Linux + cross-platform fallback. Requires:
    pip install 'msgviz[ocr-tesseract]'   # pytesseract + Pillow
    + system tesseract:
        macOS:  brew install tesseract tesseract-lang
        Debian: apt install tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng

Language defaults to "deu+eng" (screenshots from DE/EN apps); override
via env `MSGVIZ_OCR_LANG`.

Vision returns confidence + block boxes — Tesseract returns plain text
and a line count. That's enough for our worker logic (`is_text_dominant`),
which only needs text + lines.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


class TesseractEngine:
    name = "tesseract"

    def __init__(self, lang: str | None = None) -> None:
        self.lang = lang or os.environ.get("MSGVIZ_OCR_LANG", "deu+eng")
        self._pytesseract = None  # lazy

    def is_available(self) -> bool:
        if shutil.which("tesseract") is None:
            return False
        try:
            import pytesseract  # noqa: F401
            from PIL import Image  # noqa: F401
        except ImportError:
            # pytesseract / Pillow not installed
            return False
        return True

    def _load(self):
        if self._pytesseract is not None:
            return self._pytesseract
        import pytesseract

        self._pytesseract = pytesseract
        return pytesseract

    def recognize(self, path: Path | str) -> tuple[str, int]:
        pytesseract = self._load()
        from PIL import Image

        try:
            with Image.open(str(path)) as img:
                text = pytesseract.image_to_string(img, lang=self.lang)
        except FileNotFoundError as e:
            raise RuntimeError(f"tesseract: {e}")
        except pytesseract.TesseractError as e:
            raise RuntimeError(f"tesseract: {e}")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"tesseract: subprocess fail ({e.returncode})")
        except Exception as e:
            raise RuntimeError(f"tesseract: {type(e).__name__}: {e}")

        text = text.strip()
        lines = sum(1 for ln in text.splitlines() if ln.strip())
        return text, lines
