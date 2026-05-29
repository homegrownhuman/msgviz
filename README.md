# Message Visualizer

> Local, source-agnostic chat archive viewer for iMessage, WhatsApp & more.
> Heatmap, search, audio transcription and OCR — fully offline, your data
> never leaves the machine.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Status: Alpha](https://img.shields.io/badge/status-alpha-orange)
![Python: 3.10+](https://img.shields.io/badge/python-3.10+-blue)
![Tests: 134](https://img.shields.io/badge/tests-134%20passing-brightgreen)

*The CLI / package name is `msgviz` — that's what you type in the shell.
"Message Visualizer" is the project name.*

---

## Install

Requires **Python ≥ 3.10** on **macOS** or **Linux** (~200 MB disk for
the venv + dependencies + bundled demo). Everything else — `ffmpeg`,
`whisper-cli`, OCR — is *optional* and only needed for the
corresponding feature. After install, [`msgviz check`](#verify-your-install)
tells you exactly what works on your machine.

### Option A — Try the demo (30 seconds, no real data needed)

```bash
git clone https://github.com/homegrownhuman/msgviz.git
cd msgviz
bash scripts/quickstart.sh --open
```

The script creates a venv, installs `msgviz`, points `MSGVIZ_HOME` at
the bundled `demo/` directory, and starts the server at
<http://127.0.0.1:8753/>. The demo dataset has 6 chats across two
devices, ~21,700 messages spanning 2.3–5.2 years, real photos, voice
notes, and a calendar heatmap. Your live `data/` directory stays empty.

### Option B — Install for your own archive

```bash
git clone https://github.com/homegrownhuman/msgviz.git
cd msgviz

bash scripts/setup.sh             # venv + pip install -e . + system-dep check + msgviz init
source .venv/bin/activate

# Declare your first person, device, and chat
msgviz person add "Alice" --handles "alice@example.com,+491701234567"
msgviz device add wa_archive --name "iPhone 14 (WhatsApp backup)" \
                             --type static --owner "Alice"
msgviz chat add wa_archive --slug bob --title "Bob" --origin whatsapp

# Import the data — point at a WhatsApp chat export folder
msgviz import whatsapp \
    --device wa_archive \
    --folder ~/Downloads/WhatsApp\ Chat\ -\ Bob \
    --slug bob \
    --me "Alice"

msgviz status
msgviz serve                      # → http://127.0.0.1:8753/
```

`setup.sh` also checks for the optional system tools (`ffmpeg`,
`whisper-cli`, OCR) and prints install hints for whatever's missing.

The import command emits a live progress tree: each phase shows a
spinner while running, then a ✓ with item counts, duration, and the
most recent status note. A finished 1,200-message import looks like
this:

![msgviz import whatsapp — live progress tree](docs/screenshots/import.svg)

For iMessage backup imports, live iMessage sync (macOS), avatars,
group chats, and reverse-proxy setup, see
[**docs/GETTING_STARTED.md**](docs/GETTING_STARTED.md) and
[**docs/CLI.md**](docs/CLI.md).

---

## What it looks like

![Message Visualizer index page with six demo chats](docs/screenshots/page-index.png)

Click into any chat for the timeline, the heatmap, the media overview,
or the voice-notes browser (click a thumbnail for the full view):

<table>
  <tr>
    <td width="33%">
      <a href="docs/screenshots/page-chat.png">
        <img src="docs/screenshots/page-chat.png" alt="Chat page with messages, voice notes, and side heatmap">
      </a>
      <p align="center"><sub>Chat + heatmap</sub></p>
    </td>
    <td width="33%">
      <a href="docs/screenshots/page-media.png">
        <img src="docs/screenshots/page-media.png" alt="Media overview grid with photo thumbnails">
      </a>
      <p align="center"><sub>Media overview</sub></p>
    </td>
    <td width="33%">
      <a href="docs/screenshots/page-voice.png">
        <img src="docs/screenshots/page-voice.png" alt="Voice notes browser with inline playback">
      </a>
      <p align="center"><sub>Voice notes</sub></p>
    </td>
  </tr>
</table>

Your real archive under `data/` is not touched — see
[*The Three-Environment Model*](docs/GETTING_STARTED.md#2-the-three-environment-model).

For a step-by-step walkthrough including your own archive, avatars,
imports and reverse-proxy setup, see
[**docs/GETTING_STARTED.md**](docs/GETTING_STARTED.md).

---

## Verify your install

```bash
msgviz check        # selftest — see exactly what works on your machine
```

`msgviz check` probes every dependency (Python, FastAPI/uvicorn,
Pillow, ffmpeg, whisper-cli, the Whisper model, OCR engines, live
iMessage access) and reports — per feature — whether it's *ready*,
*degraded* (works without that piece), or *not available*. For
anything missing it prints the consequence and the exact fix.

![msgviz check screenshot](docs/screenshots/check.svg)

> Exit code 0 means the server can run. Exit code 1 means baseline
> is broken (Python < 3.10 or core packages missing). Pass `--json`
> for machine-readable output, `--verbose` for the per-probe table.

---

## Dependencies

Message Visualizer is **modular** — it works with whatever subset of
optional tooling you have. `msgviz check` tells you which features
each missing piece would unlock. Full inventory (Python packages,
system bins, frontend libraries, models) in
[**docs/STACK.md**](docs/STACK.md).

| Component | Required for | Install (macOS) | Install (Linux) |
|---|---|---|---|
| Python ≥ 3.10                       | Everything                       | `brew install python@3.12`                 | `apt install python3.12 python3.12-venv` |
| fastapi, uvicorn, typer, rich       | Server + CLI                     | `pip install -e .` (auto)                  | `pip install -e .` (auto)                |
| Pillow                              | Image thumbnails, demo asset gen | `pip install -e '.[dev]'`                  | `pip install -e '.[dev]'`                |
| ffmpeg                              | Voice note conversion            | `brew install ffmpeg`                      | `apt install ffmpeg`                     |
| [whisper.cpp](https://github.com/ggerganov/whisper.cpp) `whisper-cli` | Audio transcription              | `brew install whisper-cpp`                 | build from source                        |
| Whisper model (`ggml-large-v3.bin`) | Audio transcription              | curl from huggingface (~3 GB)              | same                                     |
| macOS Vision binary                 | Screenshot OCR (best quality)    | `swiftc -O tools/ocr/ocr.swift -o tools/ocr/ocr` | n/a                                |
| Tesseract                           | Screenshot OCR (cross-platform)  | `brew install tesseract` + `pip install 'msgviz[ocr-tesseract]'` | `apt install tesseract-ocr tesseract-ocr-eng tesseract-ocr-deu` + `pip install 'msgviz[ocr-tesseract]'` |
| Full-Disk-Access for the terminal   | Live iMessage sync (macOS only)  | System Settings → Privacy & Security       | n/a                                      |

Anything not installed *degrades* the corresponding feature; the rest
of msgviz still runs. The server, both importers (WhatsApp, iMessage
backup), search, the heatmap and the avatar system work with **only**
the four core packages.

---

## Why this tool

Archiving messages across several sources (live iMessage, iMessage backups,
WhatsApp exports) usually means juggling a patchwork of single-purpose tools.
**Message Visualizer unifies them** in one database, one web UI, one
person-centric view:

* **All chats with one person across services** — Bob on iMessage and WhatsApp
  is the same person, not two.
* **100% offline** — no cloud upload, no trackers, no external API.
* **Local audio transcription** with [whisper.cpp](https://github.com/ggerganov/whisper.cpp)
  (Metal-accelerated on Apple Silicon).
* **OCR for screenshots** — macOS Vision (best quality) or Tesseract as a
  cross-platform fallback.
* **Web UI with calendar heatmap, search, media overview, live push** for
  incoming iMessages.
* **Avatars** — content-hashed, surfaced on devices, 1:1 chats and per
  message; manually assignable from any image file.
* **Adapter pattern** — new sources (Signal, Telegram, SMS backups) are a
  module, not a fork.

## Status

🟡 **Alpha.** Runs in production on macOS (Apple Silicon) with tens of
thousands of messages and thousands of media items. Linux support is
implemented (Tesseract OCR, no live iMessage sync) but less tested.
Schema and API are not yet guaranteed to be stable.

## Three Environments

`MSGVIZ_HOME` decides which directory holds the DB, media, and config.
Three wrappers preset it for you:

| Wrapper                       | `MSGVIZ_HOME` | Purpose                                  |
|-------------------------------|---------------|------------------------------------------|
| `msgviz …`                    | `data/`       | Your live archive                        |
| `./scripts/msgviz-dev …`      | `dev/`        | Throwaway sandbox                        |
| `./scripts/msgviz-demo …`     | `demo/`       | Bundled showcase dataset                 |

The demo lives entirely in `demo/`. Experiments live entirely in `dev/`.
Nothing leaks into your live `data/` without your explicit say-so.

## Supported sources today

| Source | Status |
|---|---|
| **iMessage live** (macOS, `~/Library/Messages/chat.db`) | ✅ incremental sync + live push |
| **iMessage backup** (iOS backup in MobileSync folder) | ✅ |
| **WhatsApp export** (`_chat.txt` + attachments, iOS and Android format) | ✅ German/English/Italian/Spanish/Dutch |
| Signal | 🔜 adapter open for contributions |
| Telegram | 🔜 ditto |

## Architecture in 5 sentences

* **`msgviz/core/`** — data models, DB schema, person resolver, sync.
* **`msgviz/adapters/`** — one module per source, yielding
  `CanonicalMessage`s (`iter_messages()` as Protocol).
* **`msgviz/workers/`** — transcription, OCR, media processing — incremental,
  race-safe, with progress reporter.
* **`msgviz/server/`** — `create_app(MVConfig)` returns a FastAPI app that
  runs standalone or as a sub-mount in any host server.
* **`app/`** — vanilla JS frontend (heatmap, chat, media) — no build step,
  usable on its own if you want.

Deep architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Docs

| | |
|---|---|
| [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) | Linear walkthrough — clone → demo → own archive |
| [docs/CLI.md](docs/CLI.md) | All `msgviz` subcommands with examples |
| [docs/API.md](docs/API.md) | HTTP API reference (REST + WebSocket) |
| [docs/SCHEMA.md](docs/SCHEMA.md) | SQLite tables, conventions, migration policy |
| [docs/STACK.md](docs/STACK.md) | Full inventory of Python deps, system bins, frontend assets |
| [docs/EMBEDDING.md](docs/EMBEDDING.md) | Mount Message Visualizer inside your own FastAPI app |
| [docs/FRONTEND_KIT.md](docs/FRONTEND_KIT.md) | Drop the frontend into a different host |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Deeper architecture |

## Related tools

| Tool | Focus | Stars | What Message Visualizer does differently |
|---|---|---|---|
| [ReagentX/imessage-exporter](https://github.com/ReagentX/imessage-exporter) | iMessage → text/HTML | 5.2k | viewer + multi-source + local transcription, not just export |
| [KnugiHK/WhatsApp-Chat-Exporter](https://github.com/KnugiHK/WhatsApp-Chat-Exporter) | WhatsApp backups → HTML | 1.1k | merges WhatsApp and iMessage, dedupes by person |
| [Pustur/whatsapp-chat-parser-website](https://github.com/Pustur/whatsapp-chat-parser-website) | WhatsApp export, browser-only | 264 | handles images + audio + transcription, multi-source |

## Privacy

* The DB (`data/visualizer.db`), media (`media/`, `originals/`) and
  generated JSON caches (`data/transcripts.json`, `data/ocr.json`,
  `data/chats/`) live only on your machine. They are excluded via
  `.gitignore`.
* No telemetry, no cloud sync, no external API calls at runtime
  (except local `whisper-cli` and `tesseract`/`vision`).
* The HTTP server binds to `127.0.0.1` by default. If you expose it
  publicly, add an auth layer yourself (see
  [docs/API.md](docs/API.md#cors-auth-https)).

## Roadmap

- [ ] Signal adapter (local Signal Desktop DB)
- [ ] Telegram adapter (Telegram export JSON)
- [ ] SMS backup reader (Android XML)
- [ ] UI language switcher (interface localization, currently EN-only)
- [ ] First-install script for Linux
- [ ] CI pipeline with test badge

## Contributing

Issues, PRs and bug reports welcome. See
[CONTRIBUTING.md](.github/CONTRIBUTING.md) for conventions.

Before a larger refactor, skim
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — there are intentional
design choices (adapter pattern, source-agnostic schema, local JSON
caches for transcripts/OCR) that aren't obvious.

## License

[MIT](LICENSE) — use freely, modify, redistribute.
