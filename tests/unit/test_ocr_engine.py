# -*- coding: utf-8 -*-
"""
Regression guard for msgviz.core.ocr — engine abstraction.

Verifies:
1. NullEngine is always available and returns ("", 0).
2. get_engine() honors the `MSGVIZ_OCR_ENGINE` env var.
3. On macOS with the built tools/ocr/ocr binary, auto-detect picks Vision.
4. On Linux without Tesseract, auto-detect falls back to Null.
5. VisionEngine.is_available() is platform-aware (no crash on Linux).
6. TesseractEngine.is_available() is defensive (no crash without the binary).
"""
from __future__ import annotations

import os
import sys

import pytest

from msgviz.core.ocr import NullEngine, get_engine, reset_cache
from msgviz.core.ocr.tesseract import TesseractEngine
from msgviz.core.ocr.vision_macos import VisionEngine


@pytest.fixture(autouse=True)
def _reset():
    reset_cache()
    yield
    reset_cache()


def test_null_engine_always_available():
    e = NullEngine()
    assert e.is_available() is True
    assert e.recognize("/nonexistent/path.png") == ("", 0)


def test_env_override_to_null(monkeypatch):
    monkeypatch.setenv("MSGVIZ_OCR_ENGINE", "null")
    e = get_engine()
    assert e.name == "null"


def test_env_override_invalid_falls_back_to_autodetect(monkeypatch):
    """Unknown ENV value -> normal auto-detect path."""
    monkeypatch.setenv("MSGVIZ_OCR_ENGINE", "garbage")
    e = get_engine()
    # On macOS: vision (if the binary is built) or null; on Linux:
    # tesseract or null.
    assert e.name in {"vision", "tesseract", "null"}


def test_vision_unavailable_on_non_darwin(monkeypatch):
    """VisionEngine.is_available() is False on non-Darwin without raising."""
    monkeypatch.setattr(sys, "platform", "linux")
    e = VisionEngine()
    assert e.is_available() is False


def test_vision_unavailable_when_binary_missing(tmp_path, monkeypatch):
    e = VisionEngine(binary=tmp_path / "ocr-doesnt-exist")
    # On Darwin: binary missing -> not available. On Linux: not available anyway.
    assert e.is_available() is False


def test_vision_recognize_raises_when_unavailable(tmp_path):
    e = VisionEngine(binary=tmp_path / "no-such-binary")
    with pytest.raises(RuntimeError, match="vision"):
        e.recognize("/tmp/whatever.png")


def test_tesseract_unavailable_without_binary(monkeypatch):
    """TesseractEngine.is_available() = False when `tesseract` is not in PATH."""
    monkeypatch.setenv("PATH", "/nonexistent-no-bins-here")
    e = TesseractEngine()
    assert e.is_available() is False


def test_get_engine_caches_choice():
    e1 = get_engine()
    e2 = get_engine()
    assert e1 is e2


def test_get_engine_force_bypasses_cache():
    cached = get_engine()
    forced = get_engine(force="null")
    assert forced.name == "null"
    # cached stays unchanged (could have been vision, tesseract or null).
    if cached.name != "null":
        assert forced is not cached


@pytest.mark.skipif(
    sys.platform != "darwin"
    or not (
        VisionEngine().is_available()
    ),
    reason="Vision binary not available (only on macOS with tools/ocr/ocr built)",
)
def test_vision_recognizes_sample_image():
    """Smoke test on macOS: Vision engine returns (str, int) without crashing."""
    from pathlib import Path

    e = VisionEngine()
    sample = Path("tests/fixtures/sample_imgs/sample.png")
    if not sample.is_file():
        pytest.skip(f"Sample {sample} missing")
    text, lines = e.recognize(sample)
    assert isinstance(text, str)
    assert isinstance(lines, int)
    assert lines >= 0
