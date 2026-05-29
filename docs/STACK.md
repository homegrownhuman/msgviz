# Tech Stack

Single-page inventory of every moving part: Python packages, system
binaries, frontend libraries, and runtime services. If something can't
be found via `pip show` or `which`, it's listed here.

This file is the source of truth for "what's in the box". The README's
Dependencies table is a derived view aimed at fresh installs.

---

## Runtime overview

```
            ┌───────────────────────────────────────────┐
            │  Browser                                  │
            │  ─ vanilla JS (app/index.js, chat.js)     │
            │  ─ Font Awesome 6.5.2 (vendored)          │
            │  ─ lazysizes (vendored)                   │
            └───────────────┬───────────────────────────┘
                            │ HTTP / WebSocket
            ┌───────────────▼───────────────────────────┐
            │  FastAPI server (uvicorn)                 │
            │  + Typer CLI (msgviz status/serve/check…) │
            │  + Rich (console UI for the CLI)          │
            └───────────────┬───────────────────────────┘
                            │ sqlite3 (stdlib)
            ┌───────────────▼───────────────────────────┐
            │  SQLite WAL DB  (data/visualizer.db)      │
            └───────────────────────────────────────────┘

            ┌───────────────────────────────────────────┐
            │  Workers (called from CLI on demand)      │
            │  ─ media kit   →  Pillow + ffmpeg         │
            │  ─ transcribe  →  whisper-cli + model     │
            │  ─ ocr         →  macOS Vision OR         │
            │                   Tesseract + pytesseract │
            └───────────────────────────────────────────┘
```

---

## Python — runtime dependencies

Declared in [`pyproject.toml`](../pyproject.toml). The four core packages
are mandatory; everything else is optional and degrades gracefully
(use `msgviz check` to see what works on your machine).

### Core (required)

| Package | Version | What for | Imported from |
|---|---|---|---|
| [**fastapi**](https://fastapi.tiangolo.com/) | `>=0.110` | HTTP + WebSocket server, automatic OpenAPI | `msgviz/server/factory.py` |
| [**uvicorn**](https://www.uvicorn.org/) `[standard]` | `>=0.27` | ASGI server runtime, file watcher for `--reload` | `msgviz/cli/serve_cmd.py` |
| [**typer**](https://typer.tiangolo.com/) | `>=0.12` | CLI framework (`msgviz status`, `msgviz import …`, etc.) | every `msgviz/cli/*_cmd.py` |
| [**rich**](https://github.com/Textualize/rich) | `>=13` | Pretty CLI tables, progress bars, SVG export for screenshots | `msgviz/cli/_helpers.py`, `check_cmd.py` |

### Optional — image / OCR

| Package | Extras flag | What it unlocks |
|---|---|---|
| [**Pillow**](https://python-pillow.org/) | `[dev]` | Image thumbnail generation; demo asset generation. Without it the media kit serves originals as-is. |
| [**pytesseract**](https://github.com/madmaze/pytesseract) | `[ocr-tesseract]` | Tesseract bridge for cross-platform OCR (Linux primary path). |

### Optional — development

| Package | Extras flag | What for |
|---|---|---|
| [**pytest**](https://docs.pytest.org/) | `[dev]` | The test suite (134 tests at last count) |
| [**httpx**](https://www.python-httpx.org/) | `[dev]` | Backs `fastapi.testclient.TestClient` |
| [**ruff**](https://docs.astral.sh/ruff/) | `[dev]` | Linting + formatting |
| [**grip**](https://github.com/joeyespo/grip) | not pinned | Optional: preview Markdown locally as GitHub renders it |

Install commands:

```bash
pip install -e .                       # core only
pip install -e '.[dev]'                # core + Pillow + tests + ruff
pip install -e '.[ocr-tesseract]'      # core + pytesseract + Pillow
```

---

## Python — standard library

Listed here because reviewers asked "what does it actually depend on
besides the listed packages." Everything below ships with Python
itself, but knowing the set helps when porting or auditing.

| Module | Where it shows up |
|---|---|
| `sqlite3` | The entire database layer — there is no ORM |
| `asyncio` | WebSocket fanout, file watcher event loop |
| `subprocess` | Calls to `whisper-cli`, `ffmpeg`, Vision binary |
| `hashlib` | SHA-256 for the hash-based media layout |
| `pathlib` | All path handling |
| `dataclasses` | Config and small value types |
| `datetime`, `time` | Timestamp normalization |
| `plistlib` | Reading Apple's binary plists in iMessage data |
| `unicodedata` | Normalizing handles and aliases |
| `re`, `json`, `os`, `shutil`, `tempfile`, `argparse`, `importlib`, `typing`, `contextlib`, `glob`, `io`, `platform`, `sys` | Used throughout |

No `aiohttp`, no `requests`, no `pydantic` (FastAPI vendors what it
needs), no `numpy`, no ORM, no template engine — the dependency surface
is intentionally tiny.

---

## System binaries

Detected at runtime via `shutil.which()` plus Homebrew-fallback paths
in [`msgviz/core/whisper.py`](../msgviz/core/whisper.py). All optional;
`msgviz check` reports which are present.

| Binary | What it does | macOS install | Linux install |
|---|---|---|---|
| **ffmpeg** | Convert voice notes (m4a/ogg → wav) before transcription | `brew install ffmpeg` | `apt install ffmpeg` |
| **whisper-cli** ([whisper.cpp](https://github.com/ggerganov/whisper.cpp)) | On-device speech-to-text, Metal-accelerated on Apple Silicon | `brew install whisper-cpp` | build from source |
| **tesseract** | Cross-platform OCR (Linux primary, macOS fallback) | `brew install tesseract` | `apt install tesseract-ocr tesseract-ocr-eng tesseract-ocr-deu` |
| **swiftc** | Build the macOS Vision OCR binary (one-time, `swiftc -O tools/ocr/ocr.swift -o tools/ocr/ocr`) | ships with Xcode CLT | n/a |

---

## Models / data files

| File | Size | Purpose | Where to put it |
|---|---|---|---|
| **Whisper model** (`ggml-large-v3.bin`) | ~3 GB | Speech-to-text neural net used by whisper-cli | `~/.whisper-models/` (macOS) or `${XDG_DATA_HOME:-$HOME/.local/share}/whisper-models/` (Linux). Override with `WHISPER_MODEL=…` |

Download:

```bash
mkdir -p ~/.whisper-models
curl -L -o ~/.whisper-models/ggml-large-v3.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin
```

Smaller models (`ggml-base.bin`, `ggml-small.bin`) work too — set
`WHISPER_MODEL_NAME=ggml-base.bin` to use them. Tradeoff: speed vs.
transcription quality.

---

## Frontend — vendored assets

The frontend is plain JS — no build step, no node_modules, no bundler.
All third-party assets are committed to the repo so the demo works
without a network connection.

| Asset | Version | What for |
|---|---|---|
| [**Font Awesome Free**](https://fontawesome.com/) | 6.5.2 | All UI icons. Lives under `app/fontawesome/`. Mixed-license (Icons CC BY 4.0, Fonts SIL OFL 1.1, Code MIT). |
| [**lazysizes**](https://github.com/aFarkas/lazysizes) | `app/lazysizes.min.js` (bundled) | Lazy-loading for chat media |
| **Custom JS** | — | `app/index.js` (overview), `app/chat.js` (chat page), `app/msgviz-base.js` (path-prefix helpers) |
| **Custom CSS** | — | `app/chat.css` |

No external CDN is fetched at runtime — the `<base href="…">` rewriter
in `app/msgviz-base.js` resolves all paths against `MSGVIZ.base`, never
against a third-party origin.

---

## OS-level features

Things that aren't packages but matter for what works:

| Feature | OS | Requirement |
|---|---|---|
| **Live iMessage sync** | macOS only | Full-Disk-Access for the terminal / Python binary (System Settings → Privacy & Security). Reads `~/Library/Messages/chat.db`. |
| **macOS Vision OCR** | macOS only | Swift toolchain (`swiftc`) to build the binary, then native Vision framework. Highest OCR quality available on macOS. |
| **WAL mode SQLite** | both | Enabled via `PRAGMA journal_mode = WAL` at schema creation. Lets readers (the FastAPI server) coexist with writers (the live sync) without blocking. |

---

## What's deliberately not used

Listed so reviewers don't have to guess "why not X":

- **No ORM.** Raw `sqlite3` with named parameters. The schema is small,
  queries are explicit, no migration surprises.
- **No async DB driver.** SQLite + threadpool is fast enough for tens
  of thousands of messages on a laptop. FastAPI endpoints awaiting a
  DB query is purely a convenience pattern, not a perf requirement.
- **No frontend framework.** Vanilla JS keeps the binary surface small
  and the load time negligible.
- **No bundler.** Assets are committed in their final form.
- **No cloud SDKs.** Message Visualizer never talks to a remote
  service at runtime. The two outbound calls in the codebase
  (`scripts/fetch_demo_assets.py` to randomuser.me + Lorem Picsum)
  are dev-only and not shipped to end users.
- **No telemetry, analytics, or crash reporters.**

---

## Where each dependency declaration lives

If you need to change something:

| For | File |
|---|---|
| Python deps (core / `[dev]` / `[ocr-tesseract]`) | [`pyproject.toml`](../pyproject.toml) |
| System bin detection logic | [`msgviz/core/whisper.py`](../msgviz/core/whisper.py) for ffmpeg/whisper-cli, [`msgviz/core/ocr/__init__.py`](../msgviz/core/ocr/__init__.py) for OCR engines |
| Setup-script probes (interactive install) | [`scripts/setup.sh`](../scripts/setup.sh) |
| Selftest probes (`msgviz check`) | [`msgviz/cli/check_cmd.py`](../msgviz/cli/check_cmd.py) |
| Whisper model paths | [`msgviz/core/whisper.py`](../msgviz/core/whisper.py) |

`msgviz check` is the canonical runtime audit — if it doesn't probe
for something, neither does the application.
