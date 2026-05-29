#!/usr/bin/env bash
#
# Message Visualizer — quickstart.
#
# Minimum path from a fresh clone to a running demo server:
#
#   1. Create .venv (if missing) and pip install -e .
#   2. Verify the bundled demo/ dataset is present.
#   3. Launch the server pointed at the demo dataset.
#
# This script does NOT:
#   * install system dependencies (ffmpeg, whisper-cli, swiftc) — those are
#     only needed for ingest / transcription, not for replaying the demo
#   * touch the live data/ directory or any dev/ sandbox
#   * download anything from the network
#
# For the full developer setup (system deps, OCR build, whisper model hint)
# use:   bash scripts/setup.sh
#
# Usage:
#   bash scripts/quickstart.sh                # serve on 127.0.0.1:8753
#   bash scripts/quickstart.sh --port 9000    # custom port
#   bash scripts/quickstart.sh --no-serve     # install + verify only
#   bash scripts/quickstart.sh --open         # also open the browser

set -e

PORT=8753
HOST=127.0.0.1
SERVE=1
OPEN=0
while [ $# -gt 0 ]; do
    case "$1" in
        --port)     PORT="$2"; shift 2 ;;
        --host)     HOST="$2"; shift 2 ;;
        --no-serve) SERVE=0; shift ;;
        --open)     OPEN=1; shift ;;
        -h|--help)
            sed -n '3,25p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *)
            echo "unknown arg: $1" >&2
            exit 2 ;;
    esac
done

# ---------------------------------------------------------------------------
# Locate repo root
# ---------------------------------------------------------------------------
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
if [ ! -f pyproject.toml ] || [ ! -d msgviz ]; then
    echo "× quickstart.sh must run from the Message Visualizer repo root."
    exit 1
fi

# ---------------------------------------------------------------------------
# Python + venv
# ---------------------------------------------------------------------------
# Pick a Python ≥ 3.10. Try, in order, $MSGVIZ_PYTHON, python3.13, python3.12,
# python3.11, python3.10, then plain python3 — and verify the version.
pick_python() {
    local candidates=()
    [ -n "$MSGVIZ_PYTHON" ] && candidates+=("$MSGVIZ_PYTHON")
    candidates+=(python3.13 python3.12 python3.11 python3.10 python3)
    for py in "${candidates[@]}"; do
        if command -v "$py" >/dev/null 2>&1; then
            local v
            v="$("$py" -c 'import sys; print(f"{sys.version_info.major}{sys.version_info.minor:02d}")' 2>/dev/null || echo 0)"
            if [ "$v" -ge 310 ]; then
                echo "$py"
                return 0
            fi
        fi
    done
    return 1
}

PY="$(pick_python)" || {
    echo "× No Python ≥ 3.10 found. Install one of:"
    echo "    macOS:  brew install python@3.12"
    echo "    Linux:  sudo apt install python3.12 python3.12-venv"
    echo "    or set MSGVIZ_PYTHON=/path/to/python3.12"
    exit 2
}
echo "✓ using $PY ($($PY --version 2>&1))"

if [ ! -d .venv ]; then
    echo "→ creating virtualenv (.venv) …"
    "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

if ! .venv/bin/pip show msgviz >/dev/null 2>&1; then
    echo "→ installing msgviz (editable) …"
    .venv/bin/pip install --quiet --upgrade pip
    .venv/bin/pip install --quiet -e .
else
    echo "✓ msgviz already installed in .venv"
fi

# ---------------------------------------------------------------------------
# Demo dataset
# ---------------------------------------------------------------------------
if [ ! -f demo/data/visualizer.db ]; then
    cat <<'EOM'
× demo/data/visualizer.db not found.

The demo dataset (~6 MB) ships with the repository. If you cloned and
don't see it, one of the following applies:
  • partial clone — run:  git checkout -- demo/
  • LFS-tracked repo  — run:  git lfs pull
  • intentionally removed — re-clone the repo

Without demo/data/visualizer.db there's nothing for the server to show.
EOM
    exit 3
fi

# Quick sanity check on the demo DB.
N_CHATS="$(.venv/bin/python -c "
import sqlite3, sys
con = sqlite3.connect('demo/data/visualizer.db')
print(con.execute('SELECT COUNT(*) FROM chat').fetchone()[0])
" 2>/dev/null)" || N_CHATS=0
N_MSGS="$(.venv/bin/python -c "
import sqlite3
con = sqlite3.connect('demo/data/visualizer.db')
print(con.execute('SELECT COUNT(*) FROM message').fetchone()[0])
" 2>/dev/null)" || N_MSGS=0

echo "✓ demo dataset: ${N_CHATS} chats, ${N_MSGS} messages"

# ---------------------------------------------------------------------------
# Serve
# ---------------------------------------------------------------------------
if [ "$SERVE" -eq 0 ]; then
    echo ""
    echo "Setup complete. Start the demo server with:"
    echo "    ./scripts/msgviz-demo serve --host ${HOST} --port ${PORT}"
    exit 0
fi

URL="http://${HOST}:${PORT}/"
echo ""
echo "─────────────────────────────────────────────────"
echo "Starting demo server on ${URL}"
echo "Demo dataset:   ${ROOT}/demo/"
echo "Live data/ and dev/ directories are NOT touched."
echo "Stop with Ctrl-C."
echo "─────────────────────────────────────────────────"

if [ "$OPEN" -eq 1 ]; then
    # Open the browser in the background once the server is up.
    (
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            sleep 1
            if command -v curl >/dev/null && curl -sf "${URL}" >/dev/null; then
                if command -v open >/dev/null; then
                    open "${URL}"
                elif command -v xdg-open >/dev/null; then
                    xdg-open "${URL}"
                fi
                exit 0
            fi
        done
    ) &
fi

export MSGVIZ_HOME="${ROOT}/demo"
exec .venv/bin/msgviz serve --host "${HOST}" --port "${PORT}"
