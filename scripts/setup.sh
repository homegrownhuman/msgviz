#!/usr/bin/env bash
#
# msgviz — one-shot setup for macOS and Linux.
#
# What it does:
#   1. Create venv (.venv/)
#   2. Install msgviz as editable (pip install -e .)
#   3. Check system dependencies — ffmpeg, whisper-cli, OCR engine.
#   4. Copy the example config (config/sources.example.json -> config/sources.json)
#      if no config exists yet.
#   5. Initialize the DB (msgviz init).
#   6. On macOS: build the Swift Vision OCR binary if swiftc is present.
#
# What it does NOT do (manual):
#   * Download the Whisper model (~3 GB, takes a while) — printed as a hint.
#   * Live iMessage sync is macOS-only and uses ~/Library/Messages/chat.db
#     directly; no setup action needed.
#
# Usage:
#   bash scripts/setup.sh                # with prompts when unsure
#   bash scripts/setup.sh --yes          # fully automatic, no prompts
#   bash scripts/setup.sh --dev          # also install dev extras (pytest, ruff)
#   bash scripts/setup.sh --yes --dev    # both

set -e

YES=0
DEV=0
for arg in "$@"; do
    case "$arg" in
        --yes|-y) YES=1 ;;
        --dev)    DEV=1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Detect platform
# ---------------------------------------------------------------------------
OS="$(uname -s)"
case "$OS" in
    Darwin) PLATFORM="macos" ;;
    Linux)  PLATFORM="linux" ;;
    *)      echo "Unknown OS: $OS"; exit 1 ;;
esac
echo "Platform: $PLATFORM"

# ---------------------------------------------------------------------------
# Check repo root
# ---------------------------------------------------------------------------
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
if [ ! -f pyproject.toml ] || [ ! -d msgviz ]; then
    echo "Script must be run from the msgviz repo root or scripts/."
    exit 1
fi
echo "Repo root: $ROOT"

# ---------------------------------------------------------------------------
# Check Python
# ---------------------------------------------------------------------------
if ! command -v python3 >/dev/null; then
    echo "Python 3 missing. Install:"
    [ "$PLATFORM" = "macos" ] && echo "  brew install python@3.12"
    [ "$PLATFORM" = "linux" ] && echo "  apt install python3.12 python3.12-venv  (or equivalent)"
    exit 2
fi
PY_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "Python: $PY_VERSION"

# ---------------------------------------------------------------------------
# Venv + install
# ---------------------------------------------------------------------------
if [ ! -d .venv ]; then
    echo "→ creating .venv …"
    python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
echo "→ pip install (editable) …"
pip install --quiet --upgrade pip
if [ "$DEV" -eq 1 ]; then
    echo "  (including dev extras: pytest, ruff)"
    pip install --quiet -e '.[dev]'
else
    pip install --quiet -e .
fi

# ---------------------------------------------------------------------------
# Check system deps
# ---------------------------------------------------------------------------
echo ""
echo "→ checking system dependencies …"

check_bin() {
    local name="$1"
    if command -v "$name" >/dev/null; then
        echo "  ✓ $name: $(command -v "$name")"
        return 0
    else
        echo "  ✗ $name: not installed"
        return 1
    fi
}

MISSING=()
check_bin ffmpeg     || MISSING+=("ffmpeg")
check_bin whisper-cli || MISSING+=("whisper-cli")
if [ "$PLATFORM" = "macos" ]; then
    check_bin swiftc      || true                   # only for the OCR build
else
    check_bin tesseract  || MISSING+=("tesseract")
fi

if [ ${#MISSING[@]} -gt 0 ]; then
    echo ""
    echo "Missing system tools:"
    if [ "$PLATFORM" = "macos" ]; then
        echo "  brew install ${MISSING[*]/whisper-cli/whisper-cpp}"
    else
        APT=("${MISSING[@]}")
        # whisper-cli is not in apt on Linux — note it separately.
        for i in "${!APT[@]}"; do
            [ "${APT[$i]}" = "whisper-cli" ] && unset 'APT[i]'
        done
        if [ ${#APT[@]} -gt 0 ]; then
            echo "  sudo apt install ${APT[*]}"
        fi
        if [[ " ${MISSING[*]} " == *" whisper-cli "* ]]; then
            echo "  # build whisper-cli from source:"
            echo "  git clone https://github.com/ggerganov/whisper.cpp && cd whisper.cpp"
            echo "  make -j && sudo cp build/bin/whisper-cli /usr/local/bin/"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Config + DB
# ---------------------------------------------------------------------------
echo ""
if [ ! -f config/sources.json ]; then
    if [ -f config/sources.example.json ]; then
        echo "→ copying example config to config/sources.json"
        cp config/sources.example.json config/sources.json
    else
        echo "× config/sources.example.json missing — manual setup required."
    fi
else
    echo "✓ config/sources.json exists"
fi

if [ ! -f data/visualizer.db ]; then
    echo "→ initializing DB (msgviz init) …"
    msgviz init || true
else
    echo "✓ data/visualizer.db exists"
fi

# ---------------------------------------------------------------------------
# macOS: build Vision OCR binary
# ---------------------------------------------------------------------------
if [ "$PLATFORM" = "macos" ] && command -v swiftc >/dev/null; then
    if [ ! -x tools/ocr/ocr ] && [ -f tools/ocr/ocr.swift ]; then
        echo "→ building macOS Vision OCR binary …"
        swiftc -O tools/ocr/ocr.swift -o tools/ocr/ocr
        echo "  ✓ tools/ocr/ocr built"
    elif [ -x tools/ocr/ocr ]; then
        echo "✓ tools/ocr/ocr exists"
    fi
fi

# ---------------------------------------------------------------------------
# Whisper model — hint
# ---------------------------------------------------------------------------
if [ "$PLATFORM" = "macos" ]; then
    MODEL_DIR="$HOME/.whisper-models"
else
    MODEL_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/whisper-models"
fi
if [ -f "$MODEL_DIR/ggml-large-v3.bin" ]; then
    echo "✓ Whisper model present: $MODEL_DIR/ggml-large-v3.bin"
else
    echo ""
    echo "Whisper model missing (~3 GB download):"
    echo "  mkdir -p '$MODEL_DIR'"
    echo "  curl -L -o '$MODEL_DIR/ggml-large-v3.bin' \\"
    echo "    https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin"
    echo "(without the model everything works except 'msgviz transcribe'.)"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "─────────────────────────────────────────────────"
echo "msgviz setup done."
echo "Next steps:"
echo "  source .venv/bin/activate     # if the shell isn't already active"
echo "  msgviz status                 # check DB stats"
echo "  msgviz device add ...         # add the first device"
echo "  msgviz serve                  # start the UI (http://127.0.0.1:8753/)"
echo ""
echo "Full docs: docs/CLI.md, docs/API.md, docs/EMBEDDING.md"
