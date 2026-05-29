# -*- coding: utf-8 -*-
"""
Regression guard for msgviz.core.whisper — path/model resolver.

Verifies:
1. find_whisper_cli() honors ENV WHISPER_CLI (absolute path).
2. find_ffmpeg() honors ENV FFMPEG.
3. find_model() honors ENV WHISPER_MODEL (absolute path).
4. model_search_paths() is platform-aware (XDG on Linux, App Support on
   Darwin).
5. WhisperPaths.is_complete() / missing() work as expected.
6. resolve() does not crash when binaries are missing — returns None.
7. setup_hint() returns OS-appropriate text.
"""
from __future__ import annotations

import os
import sys

import pytest

from msgviz.core import whisper


def test_default_model_name_constant():
    assert whisper.DEFAULT_MODEL_NAME == "ggml-large-v3.bin"


def test_default_model_name_env_override(monkeypatch):
    monkeypatch.setenv("WHISPER_MODEL_NAME", "ggml-small.bin")
    assert whisper.default_model_name() == "ggml-small.bin"


def test_find_whisper_cli_env_override_to_existing(tmp_path, monkeypatch):
    fake = tmp_path / "fake-whisper-cli"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setenv("WHISPER_CLI", str(fake))
    assert whisper.find_whisper_cli() == fake


def test_find_whisper_cli_env_override_to_nonexisting_returns_none(monkeypatch):
    monkeypatch.setenv("WHISPER_CLI", "/this/path/does/not/exist")
    assert whisper.find_whisper_cli() is None


def test_find_ffmpeg_env_override(tmp_path, monkeypatch):
    fake = tmp_path / "fake-ffmpeg"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setenv("FFMPEG", str(fake))
    assert whisper.find_ffmpeg() == fake


def test_find_model_env_override_absolute(tmp_path, monkeypatch):
    fake_model = tmp_path / "my-model.bin"
    fake_model.write_bytes(b"\x00")
    monkeypatch.setenv("WHISPER_MODEL", str(fake_model))
    assert whisper.find_model() == fake_model


def test_find_model_env_override_nonexisting(monkeypatch):
    monkeypatch.setenv("WHISPER_MODEL", "/no/such/model.bin")
    assert whisper.find_model() is None


def test_find_model_searches_custom_dir(tmp_path, monkeypatch):
    """ENV WHISPER_MODEL_DIR adds a search directory at the front."""
    monkeypatch.delenv("WHISPER_MODEL", raising=False)
    monkeypatch.setenv("WHISPER_MODEL_DIR", str(tmp_path))
    fake_model = tmp_path / whisper.DEFAULT_MODEL_NAME
    fake_model.write_bytes(b"\x00")
    assert whisper.find_model() == fake_model


def test_model_search_paths_includes_darwin_lib(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    paths = whisper.model_search_paths()
    paths_str = [str(p) for p in paths]
    assert any("Library/Application Support" in p for p in paths_str)


def test_model_search_paths_includes_xdg_on_linux(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    paths = whisper.model_search_paths()
    assert (tmp_path / "whisper-models") in paths


def test_model_search_paths_default_linux_fallback(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    paths = whisper.model_search_paths()
    paths_str = [str(p) for p in paths]
    assert any(".local/share/whisper-models" in p for p in paths_str)


def test_whisper_paths_is_complete():
    from pathlib import Path

    paths = whisper.WhisperPaths(
        whisper_cli=Path("/bin/sh"),
        model=Path("/bin/sh"),
        ffmpeg=Path("/bin/sh"),
    )
    assert paths.is_complete()
    assert paths.missing() == []


def test_whisper_paths_missing_lists_problems():
    paths = whisper.WhisperPaths(whisper_cli=None, model=None, ffmpeg=None)
    assert paths.is_complete() is False
    missing = paths.missing()
    assert "whisper-cli" in missing
    assert "ffmpeg" in missing
    # The model label is built in msgviz/core/whisper.py — match the
    # current text. If the source string changes, update here too.
    assert any("Whisper model" in m or "Whisper-Modell" in m for m in missing)


def test_resolve_does_not_crash_when_nothing_found(monkeypatch):
    monkeypatch.setenv("WHISPER_CLI", "/no/such/binary")
    monkeypatch.setenv("FFMPEG", "/no/such/ffmpeg")
    monkeypatch.setenv("WHISPER_MODEL", "/no/such/model.bin")
    paths = whisper.resolve()
    assert paths.whisper_cli is None
    assert paths.ffmpeg is None
    assert paths.model is None
    assert paths.is_complete() is False


def test_setup_hint_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    hint = whisper.setup_hint()
    assert "brew install" in hint
    assert ".whisper-models" in hint


def test_setup_hint_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    hint = whisper.setup_hint()
    assert "apt install" in hint
    assert "whisper.cpp" in hint
    assert ".local/share" in hint
