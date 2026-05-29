# Changelog

All notable changes to msgviz are recorded here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [SemVer](https://semver.org/).

## [Unreleased]

### Added
- CI pipeline (`.github/workflows/test.yml`): pytest on macOS-latest and
  ubuntu-latest with Python 3.10, 3.11, 3.12.
- Issue templates (bug report, feature request) and PR template.
- `CONTRIBUTING.md` with area overview, code-style rules, test policy,
  PII notes, DB-schema workflow.
- `CODE_OF_CONDUCT.md` (short variant inspired by Contributor Covenant 2.1).
- `CHANGELOG.md` (this file).

## [0.1.0] – not yet released

First public alpha.

### Added
- Source-agnostic SQLite DB (Person/Handle/Device/Chat/Message/Media/
  SourceRef) — no Apple- or WhatsApp-specific schema.
- Adapters for **iMessage live** (macOS), **iMessage backup** (iOS backup
  in MobileSync folder) and **WhatsApp export** (German/English/Italian/
  Spanish/Dutch).
- Incremental sync with per-source-instance dedup via `source_ref`.
- `PersonResolver` with `person_alias` table (case-insensitive
  multi-spelling).
- Content-hash media layout (`media/<kind>/<prefix>/<hash>.<ext>`).
- Audio transcription via whisper.cpp, race-safe incremental writes to
  `data/transcripts.json`.
- Image OCR with an adapter pattern: macOS Vision on Darwin,
  Tesseract on Linux (auto-detect + `MSGVIZ_OCR_ENGINE` override).
- Cross-platform Whisper path resolver (XDG-compliant on Linux,
  classic paths on macOS).
- FastAPI app with `create_app(MVConfig)` — embeddable as a sub-app in
  any host application under any URL prefix.
- Frontend bootstrap (`app/msgviz-base.js`) makes the vanilla JS UI
  sub-mount-aware (HTML template rendering + `mvUrl()` helper).
- Typer-based CLI `msgviz` with 18 subcommands (init/status/serve/
  transcribe/ocr + device/chat/person/import/delete groups).
- Backup hook: automatic DB copy before every mutating CLI command,
  written to `data/db-backups/pre-<tag>-YYYYMMDD-HHMMSS.db`. FIFO
  rotation (max 20 backups).
- Four doc files: `docs/CLI.md`, `docs/API.md`, `docs/EMBEDDING.md`,
  `docs/FRONTEND_KIT.md`.
- `scripts/setup.sh` — platform auto-detect, system-deps check,
  venv creation, OCR build on macOS.
- 119 automated tests (unit + integration).

### Changed
- Repository layout is now a pip-installable package (`msgviz/`).
- `sources.json` schema still accepts the legacy `people` map for
  backward compatibility, but it isn't required anymore — persons
  live in the DB.
- `OWNER_ALIAS` in `msgviz/core/migrate.py` is empty by default.
  Override via environment variable `MSGVIZ_OWNER_ALIASES="Short1:Full1,…"`.

### Removed
- Six old migration and import scripts (`tools/migrate_*.py`,
  `tools/import_pureblade_natalie.py`) — one-shot scripts that did
  their job and have no value in a public repository. Moved to local
  `_legacy/`.

### Security
- All personally identifiable data (real names, phone numbers, emails)
  removed from the repository (source code, tests, docs, example
  config). Pseudonyms: Alice, Bob, Carol, Owner, plus test phone
  numbers in the `+491701234567` range.
- `config/sources.json` and `data/visualizer.db` are in `.gitignore`.

[Unreleased]: https://github.com/<user>/msgviz/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/<user>/msgviz/releases/tag/v0.1.0
