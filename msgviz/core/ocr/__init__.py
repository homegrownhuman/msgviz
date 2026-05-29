# -*- coding: utf-8 -*-
"""
msgviz.core.ocr — OCR engine abstraction.

So msgviz runs on Linux too, the macOS-specific Vision binary is no
longer hard-wired into the worker. Instead:

    Engine = get_engine()  # auto-detect
    text, lines = Engine.recognize(path)

Detection logic:
    1. ENV `MSGVIZ_OCR_ENGINE` (vision|tesseract|none) — explicit
    2. macOS + tools/ocr/ocr present -> Vision
    3. Otherwise: Tesseract (if `tesseract` binary and pytesseract are present)
    4. Otherwise: NullEngine (returns "", no crash — the worker logs a skip)

`recognize(path)` always returns (text: str, lines: int). On error: a
RuntimeError with the engine name in the message. The caller (worker)
decides whether a single failure aborts the whole run (default: no).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Protocol


class OCREngine(Protocol):
    """Minimal OCR protocol."""

    name: str

    def recognize(self, path: Path | str) -> tuple[str, int]:
        """(text, line_count). Raises RuntimeError on engine error."""
        ...

    def is_available(self) -> bool:
        """True if this engine is operational on the current system."""
        ...


class NullEngine:
    """Fallback when neither Vision nor Tesseract is available — no crash."""

    name = "null"

    def recognize(self, path):  # type: ignore[override]
        return "", 0

    def is_available(self) -> bool:
        return True


_ENGINE_CACHE: OCREngine | None = None


def get_engine(force: str | None = None) -> OCREngine:
    """Return the OCR engine, per auto-detect or env override.

    force=None: cached; force="vision"/"tesseract"/"null": pick fresh.
    """
    global _ENGINE_CACHE
    if force is None and _ENGINE_CACHE is not None:
        return _ENGINE_CACHE

    explicit = (force or os.environ.get("MSGVIZ_OCR_ENGINE") or "").strip().lower()
    engine: OCREngine

    if explicit == "vision":
        from .vision_macos import VisionEngine

        engine = VisionEngine()
    elif explicit == "tesseract":
        from .tesseract import TesseractEngine

        engine = TesseractEngine()
    elif explicit == "null":
        engine = NullEngine()
    else:
        # Auto-detect.
        if sys.platform == "darwin":
            from .vision_macos import VisionEngine

            v = VisionEngine()
            if v.is_available():
                engine = v
            else:
                engine = _try_tesseract_or_null()
        else:
            engine = _try_tesseract_or_null()

    if force is None:
        _ENGINE_CACHE = engine
    return engine


def _try_tesseract_or_null() -> OCREngine:
    from .tesseract import TesseractEngine

    t = TesseractEngine()
    if t.is_available():
        return t
    return NullEngine()


def reset_cache() -> None:
    """Test helper — forget the cached engine choice."""
    global _ENGINE_CACHE
    _ENGINE_CACHE = None
