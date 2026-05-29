# Getting Started

This walkthrough takes you from `git clone` to a running Message Visualizer
in under five minutes — first with the bundled demo dataset, then with your
own messages.

> **Project name:** *Message Visualizer*.
> **CLI / package name:** `msgviz` (what you type in the shell).
> **License:** MIT.

---

## 0. Prerequisites

| Need                                | Why                                                      |
|-------------------------------------|----------------------------------------------------------|
| **Python ≥ 3.10**                   | Server, CLI, ingestion                                   |
| **macOS** *or* **Linux**            | Tested daily on macOS, supported on Linux                |
| ~200 MB disk                        | venv + deps + bundled demo dataset (~6 MB)               |

You do **not** need `ffmpeg`, `whisper-cli`, `swiftc`, or Tesseract just to
replay the demo. They're only needed for live ingestion, audio transcription,
and OCR — see *Optional: System Dependencies* below.

---

## 1. The 30-Second Demo

```bash
git clone https://github.com/homegrownhuman/msgviz.git
cd msgviz
bash scripts/quickstart.sh
```

Open <http://127.0.0.1:8753/>. You'll see six 1:1 chats from a fictional
"Alice Chen" across two devices (`my_mac`, `wa_archive`), spanning 2.3–5.2
years, with ~21,700 messages, photos, voice notes, and a calendar heatmap.

The demo lives entirely under `demo/`:

```
demo/
├── data/visualizer.db     # the demo DB
├── media/                 # message attachments (hashed)
├── originals/             # source-format originals (mirroring chat exports)
└── config/                # demo sources.json
```

Nothing under `data/` (your real archive) is touched.

> **Tip.** `--open` opens the browser once the server is up:
> ```bash
> bash scripts/quickstart.sh --open
> ```

---

## 2. The Three-Environment Model

Message Visualizer keeps three completely separate "homes":

| Home    | Path     | Purpose                                                  | Wrapper                       |
|---------|----------|----------------------------------------------------------|-------------------------------|
| Live    | `data/`  | Your real, indexed archive                                | `msgviz …`                    |
| Dev     | `dev/`   | Throwaway sandbox for experimenting with new imports      | `./scripts/msgviz-dev …`      |
| Demo    | `demo/`  | The bundled showcase dataset (read-only in practice)      | `./scripts/msgviz-demo …`     |

The wrappers do exactly one thing: set `MSGVIZ_HOME` to the right directory
before invoking the CLI. Every path the application touches (DB, media,
config) is rooted at `MSGVIZ_HOME`.

```bash
./scripts/msgviz-dev status         # ← reads dev/data/visualizer.db
./scripts/msgviz-demo status        # ← reads demo/data/visualizer.db
msgviz status                       # ← reads data/visualizer.db (live)
```

This is the single most important safety mechanism in the project: every
experiment, every import test, every quickstart runs in its own sandbox.

---

## 3. Starting Your Own Archive

When you're ready to ingest your own messages, switch back to the live
environment (no `MSGVIZ_HOME` set) and use the full setup:

```bash
bash scripts/setup.sh           # also checks ffmpeg / whisper-cli / OCR
source .venv/bin/activate
msgviz init                     # creates data/visualizer.db
```

Edit `config/sources.json` to declare devices and chats — see
`config/sources.example.json` for the schema.

### 3a. Add a person

A *person* is the unit of identity. One person can have many handles (phone
numbers, emails, WhatsApp IDs). Two chats with the same person on two
services collapse into one timeline.

```bash
msgviz person add "Bob Smith" \
    --handles "+491709876543,bob@example.com"
```

### 3b. Add a device

A *device* is a message source. Use `mac_live` for a Mac whose
`~/Library/Messages/chat.db` you want polled, `static` for archives like
WhatsApp exports.

```bash
msgviz device add my_mac \
    --name "My MacBook" --type mac_live --owner "Alice Chen"
msgviz device add wa_archive \
    --name "WhatsApp archive" --type static --owner "Alice Chen"
```

### 3c. Register a chat

```bash
msgviz chat add my_mac --slug bob --title "Bob Smith" --origin apple
```

### 3d. Import / sync

* **Live iMessage** (macOS only) — start the server with the file watcher
  enabled:

  ```bash
  msgviz serve            # watches ~/Library/Messages/chat.db
  ```

* **WhatsApp export** — point the importer at the unzipped export folder:

  ```bash
  msgviz import whatsapp \
      --device wa_archive \
      --folder /path/to/WhatsApp Chat - Bob \
      --slug bob \
      --me "Alice Chen"
  ```

* **iMessage backup** (read-only chat.db snapshot):

  ```bash
  msgviz import imessage --device my_mac --db /path/to/backup/chat.db
  ```

For the full command reference, see [`docs/CLI.md`](CLI.md).

---

## 4. Avatars

Avatars are stored content-hashed under `media/avatars/<prefix>/<hash>.<ext>`
and referenced from `person.avatar_src`. They surface in the API on devices
(owner), on 1:1 chats (counterpart), and on each message (sender).

```bash
msgviz person set-avatar "Bob Smith" /path/to/photo.jpg
msgviz person clear-avatar "Bob Smith"
msgviz person auto-avatars            # generate initials PNG fallbacks
```

WhatsApp exports don't include avatars, so you set them manually. iMessage
contact photos are not currently imported — same flow.

---

## 5. Serving the UI

```bash
msgviz serve                          # http://127.0.0.1:8753/
msgviz serve --host 0.0.0.0 --port 9000
```

### Reverse-proxy / sub-mount

The frontend kit supports being mounted under a prefix (e.g.
`https://example.com/messages/`). See [`docs/FRONTEND_KIT.md`](FRONTEND_KIT.md)
and [`docs/EMBEDDING.md`](EMBEDDING.md) for a Caddy / nginx / FastAPI host
recipe.

---

## 6. Optional: System Dependencies

Only required for the corresponding feature.

| Feature                          | Dependency                                       | macOS                                                | Linux                                                  |
|----------------------------------|--------------------------------------------------|------------------------------------------------------|--------------------------------------------------------|
| Audio transcription              | `ffmpeg` + `whisper-cli` + Whisper model         | `brew install ffmpeg whisper-cpp`                    | `apt install ffmpeg`, build whisper.cpp from source    |
| OCR (best quality on macOS)      | Swift Vision binary                              | `swiftc -O tools/ocr/ocr.swift -o tools/ocr/ocr`    | n/a                                                    |
| OCR (cross-platform)             | Tesseract + Python binding                       | `brew install tesseract`                             | `apt install tesseract-ocr tesseract-ocr-deu`          |
| Live iMessage sync               | macOS `~/Library/Messages/` Full-Disk-Access     | grant FDA in System Settings → Privacy & Security    | not available                                          |

`scripts/setup.sh` checks all of the above and prints any missing pieces.

---

## 7. Common Tasks

| What you want                              | Command                                                    |
|--------------------------------------------|------------------------------------------------------------|
| Show DB stats (chats, messages, media)     | `msgviz status`                                            |
| Re-index after manual DB edits             | `msgviz reindex`                                           |
| Transcribe a single voice message          | `msgviz transcribe --media <id>`                           |
| Transcribe a chat                          | `msgviz transcribe --chat my_mac/bob`                      |
| OCR a screenshot                           | `msgviz ocr --media <id>`                                  |
| Open the demo in a browser                 | `bash scripts/quickstart.sh --open`                        |
| Wipe the dev sandbox                       | `rm -rf dev/`                                              |
| Reset the demo                             | nothing — `demo/` is read-only in normal use               |

---

## 8. Troubleshooting

**`demo/data/visualizer.db not found`**
The demo dataset is committed to the repo. If it's missing, try
`git checkout -- demo/` (partial clone) or re-clone.

**`Port 8753 in use`**
Another `msgviz serve` is probably already running. Either reuse it or
start with `--port 8754`.

**`× msgviz: command not found` after install**
Activate the venv: `source .venv/bin/activate`. The wrappers
(`./scripts/msgviz-demo`, `./scripts/msgviz-dev`) work without
activating because they use `./.venv/bin/msgviz` directly.

**Live iMessage poll shows no new messages**
macOS Full-Disk-Access for the terminal / Python binary is required.
System Settings → Privacy & Security → Full Disk Access.

---

## Where to next

* [`docs/CLI.md`](CLI.md) — every CLI subcommand with examples
* [`docs/API.md`](API.md) — HTTP API for embedding the timeline in your own UI
* [`docs/SCHEMA.md`](SCHEMA.md) — SQLite tables, conventions, migration policy
* [`docs/STACK.md`](STACK.md) — full inventory of Python deps, system
  bins, frontend libraries, and models
* [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) — adapters, person resolver,
  high-level data flow
* [`docs/EMBEDDING.md`](EMBEDDING.md) — sub-mount the UI behind a reverse proxy
* [`docs/FRONTEND_KIT.md`](FRONTEND_KIT.md) — how the JS / CSS resolve paths
  under a prefix
