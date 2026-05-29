# Contributing to msgviz

Thanks for considering a contribution. This document describes the
fastest path from idea to merged PR.

## TL;DR

```bash
git clone https://github.com/<user>/msgviz.git
cd msgviz
bash scripts/setup.sh --dev   # venv + deps (incl. pytest)
.venv/bin/pytest -q           # 119/119 green?
# change code …
.venv/bin/pytest -q           # still green?
git commit -m "<area>: what and why"
# open PR
```

CI runs automatically on macOS and Linux with Python 3.10–3.12.

## Areas and ownership

```
msgviz/
├── core/         data models, schema, PersonResolver, sync, OCR engines
├── adapters/     one module per source (iMessage, WhatsApp, …)
├── workers/      transcription, OCR, media processing
├── server/       FastAPI app factory + routes
├── cli/          Typer subcommands
└── mediakit/     image/audio/video conversion
app/              vanilla JS frontend (heatmap, chat, media)
tests/            unit + integration (pytest)
docs/             CLI / API / EMBEDDING / FRONTEND_KIT / ARCHITECTURE
```

If you plan deeper changes in an area, skim the local sub-docs (or the
files under `docs/`) first.

## Most-wanted contributions

1. **New source adapters**: Signal Desktop DB, Telegram export JSON,
   Android SMS backup. Skeleton in `msgviz/adapters/imessage_db.py` or
   `whatsapp_export.py`; the protocol is `msgviz/core/source_adapter.py`.
2. **Linux hardening**: tests pass, but live experience is limited. Open
   an issue if anything misbehaves — even without a fix.
3. **UI improvements**: everything under `app/` is vanilla JS, no build
   step.
4. **Tests** for under-covered areas (coverage reports welcome).
5. **Docs** — if anything was missing in the quick-start for you, it
   probably is for the next person too.

## Code style

* **Python**: ruff-compliant (see `pyproject.toml` `[tool.ruff]`).
* **JavaScript**: no build tools, no framework — stick to the existing
  vanilla style. Functions over classes where possible.
* **Commit messages**: short and descriptive.
  `core: …`, `cli: …`, `tests: …`, `docs: …`, `adapters/whatsapp: …`.

## Tests

```bash
.venv/bin/pytest -q                  # all
.venv/bin/pytest tests/unit/ -q      # unit only
.venv/bin/pytest -q -k "person"     # targeted
```

New code paths need tests. Bug fixes need a regression test
(red → green in the same commit).

Characterization tests in `tests/integration/` pin existing behavior —
please don't change them silently; if you change the behavior on
purpose, update the test in the same commit and explain it in the
commit message.

## Privacy in contributions

The repository was originally a personal refactor and was PII-cleaned
before going public. Please:

* **No real names, phone numbers or email addresses** in tests, docs
  or example configs.
* Use pseudonyms (Alice, Bob, Carol, …) and test phone numbers
  (`+491701234567`).
* `config/sources.json` and `data/visualizer.db` are in `.gitignore` —
  keep them that way.

## DB schema changes

Schema changes need:
1. **Migration** in `msgviz/core/migrate.py` or as a CLI subcommand.
2. **Backup hook** — see `msgviz/core/backup.py`. Always snapshot to
   `data/db-backups/` before destructive runs.
3. **Characterization test** in `tests/integration/` that documents
   old and new behavior.
4. **CHANGELOG.md** entry.

## Issues and PRs

* Issues: please include repro steps and environment (see issue template).
* PRs: read your own diff once before pushing (`git diff main...`) and
  check for PII, leftover debug `print`s, commented-out code.
* If unsure whether a PR makes sense: open an issue first and discuss —
  often saves a lot of implementation time.

## Code of Conduct

See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Short version:
respectful, factual, direct.

## License

Contributions are covered by the project's [MIT license](LICENSE).
