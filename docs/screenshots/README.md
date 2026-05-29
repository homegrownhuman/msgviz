# Console screenshots

Reproducible SVG screenshots of the `msgviz` CLI, rendered with
[Rich](https://github.com/Textualize/rich)'s `Console.save_svg()`.
These render inline in GitHub Markdown — no PNG capture session,
no platform-dependent fonts.

## Regenerate

```bash
MSGVIZ_HOME=demo .venv/bin/python scripts/render_console_screenshots.py
```

Run against the demo dataset (`MSGVIZ_HOME=demo`) so the output
reflects the bundled showcase data instead of your live archive.

| File | What it shows |
|---|---|
| `check.svg`      | `msgviz check` — feature matrix + "How to fix" panel |
| `status.svg`     | `msgviz status` — DB stats, media breakdown, top chats |
| `import.svg`     | `msgviz import whatsapp` — progress bars during ingest |
| `transcribe.svg` | `msgviz transcribe` — whisper.cpp progress |

The import and transcribe screenshots are **simulated** (a single
frozen frame of the progress bars) because Rich's animated progress
output doesn't render meaningfully to a static SVG. The DB-content
and check screenshots are 100% live — they're literally what the
command prints.
