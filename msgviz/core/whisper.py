# -*- coding: utf-8 -*-
"""
msgviz.core.whisper — cross-platform Whisper path/model resolution.

WHISPER_CLI/MODEL/FFMPEG used to be module globals in
workers/transcribe.py with macOS-specific defaults (~/.whisper-models).
This module encapsulates:

* `find_whisper_cli()`   -> Path | None    (search PATH + Brew/standard paths)
* `find_ffmpeg()`        -> Path | None
* `model_search_paths()` -> list[Path]     (XDG + macOS convention)
* `find_model(name)`     -> Path | None    (searches all of the above)
* `default_model_name()` -> str            (default: ggml-large-v3.bin)

All with env overrides:
    WHISPER_CLI=/abs/path/whisper-cli
    WHISPER_MODEL=/abs/path/model.bin   # absolute path
    WHISPER_MODEL_DIR=/dir              # add one search directory
    WHISPER_MODEL_NAME=ggml-large-v3.bin
    FFMPEG=/abs/path/ffmpeg

Setup notes (for README / CLI help):
* macOS:  brew install whisper-cpp ffmpeg
          mkdir -p ~/.whisper-models &&
          curl -L -o ~/.whisper-models/ggml-large-v3.bin \\
            https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin
* Linux:  apt install ffmpeg && build whisper.cpp from source
          (https://github.com/ggerganov/whisper.cpp), put the model in
          ~/.local/share/whisper-models/.
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL_NAME = "ggml-large-v3.bin"


# ---------------------------------------------------------------------------
#  Find binaries
# ---------------------------------------------------------------------------
def _candidate_paths_for(name: str) -> list[Path]:
    """Platform-specific standard locations for a CLI binary."""
    out: list[Path] = []
    # Homebrew (macOS Apple Silicon and Intel).
    out.append(Path("/opt/homebrew/bin") / name)
    out.append(Path("/usr/local/bin") / name)
    # Standard Linux.
    out.append(Path("/usr/bin") / name)
    out.append(Path("/usr/local/bin") / name)
    return out


def find_whisper_cli() -> Path | None:
    """Find the whisper.cpp 'whisper-cli' binary.

    Priority:
      1. env WHISPER_CLI (absolute path)
      2. shutil.which('whisper-cli') — walks PATH
      3. standard paths (Homebrew, /usr/local/bin)
    """
    explicit = os.environ.get("WHISPER_CLI")
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    found = shutil.which("whisper-cli")
    if found:
        return Path(found)
    for candidate in _candidate_paths_for("whisper-cli"):
        if candidate.is_file():
            return candidate
    return None


def find_ffmpeg() -> Path | None:
    explicit = os.environ.get("FFMPEG")
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    found = shutil.which("ffmpeg")
    if found:
        return Path(found)
    for candidate in _candidate_paths_for("ffmpeg"):
        if candidate.is_file():
            return candidate
    return None


# ---------------------------------------------------------------------------
#  Find model
# ---------------------------------------------------------------------------
def model_search_paths() -> list[Path]:
    """Platform-aware search directories for ggml Whisper models.

    Order:
      1. env WHISPER_MODEL_DIR (if set)
      2. ~/.whisper-models/   (legacy msgviz convention, all platforms)
      3. macOS: ~/Library/Application Support/whisper-models/
      4. Linux/POSIX: $XDG_DATA_HOME/whisper-models or
                      ~/.local/share/whisper-models/
      5. /usr/local/share/whisper-models/    (system-wide)
      6. /opt/whisper-models/                (Brew-cellar style)
    """
    home = Path.home()
    paths: list[Path] = []

    env_dir = os.environ.get("WHISPER_MODEL_DIR")
    if env_dir:
        paths.append(Path(env_dir).expanduser())

    paths.append(home / ".whisper-models")

    if sys.platform == "darwin":
        paths.append(home / "Library" / "Application Support" / "whisper-models")
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            paths.append(Path(xdg) / "whisper-models")
        else:
            paths.append(home / ".local" / "share" / "whisper-models")

    paths.append(Path("/usr/local/share/whisper-models"))
    paths.append(Path("/opt/whisper-models"))

    # Drop duplicates (e.g. /usr/local/share appearing twice).
    seen: set[Path] = set()
    out: list[Path] = []
    for p in paths:
        rp = p.resolve() if p.exists() else p
        if rp in seen:
            continue
        seen.add(rp)
        out.append(p)
    return out


def default_model_name() -> str:
    return os.environ.get("WHISPER_MODEL_NAME") or DEFAULT_MODEL_NAME


def find_model(name: str | None = None) -> Path | None:
    """Find a model file. ENV WHISPER_MODEL (absolute) wins.

    Otherwise `name` (or default_model_name()) is searched in the search
    paths.
    """
    abs_override = os.environ.get("WHISPER_MODEL")
    if abs_override:
        p = Path(abs_override).expanduser()
        return p if p.is_file() else None
    target = name or default_model_name()
    for d in model_search_paths():
        candidate = d / target
        if candidate.is_file():
            return candidate
    return None


# ---------------------------------------------------------------------------
#  High-level resolver for the worker
# ---------------------------------------------------------------------------
@dataclass
class WhisperPaths:
    """Resolved paths — None if not found, the worker decides what to do."""

    whisper_cli: Path | None
    model: Path | None
    ffmpeg: Path | None

    def is_complete(self) -> bool:
        return all([self.whisper_cli, self.model, self.ffmpeg])

    def missing(self) -> list[str]:
        m = []
        if self.whisper_cli is None:
            m.append("whisper-cli")
        if self.model is None:
            m.append(f"Whisper model ({default_model_name()})")
        if self.ffmpeg is None:
            m.append("ffmpeg")
        return m


def resolve() -> WhisperPaths:
    return WhisperPaths(
        whisper_cli=find_whisper_cli(),
        model=find_model(),
        ffmpeg=find_ffmpeg(),
    )


def setup_hint() -> str:
    """Multi-line setup hint for CLI/README, platform-aware."""
    is_mac = sys.platform == "darwin"
    if is_mac:
        return (
            "macOS setup:\n"
            "  brew install whisper-cpp ffmpeg\n"
            "  mkdir -p ~/.whisper-models\n"
            "  curl -L -o ~/.whisper-models/ggml-large-v3.bin \\\n"
            "    https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin\n"
        )
    return (
        "Linux setup:\n"
        "  apt install ffmpeg                  # or pacman/dnf/... equivalent\n"
        "  # build whisper.cpp from source:\n"
        "  git clone https://github.com/ggerganov/whisper.cpp && cd whisper.cpp\n"
        "  make -j && sudo cp build/bin/whisper-cli /usr/local/bin/\n"
        "  mkdir -p ~/.local/share/whisper-models\n"
        "  curl -L -o ~/.local/share/whisper-models/ggml-large-v3.bin \\\n"
        "    https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin\n"
    )
