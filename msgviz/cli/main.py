# -*- coding: utf-8 -*-
"""
msgviz.cli.main — top-level Typer app, wires up every subcommand group.
"""
from __future__ import annotations

import typer

from . import (
    chat_cmd,
    check_cmd,
    delete_cmd,
    device_cmd,
    import_cmd,
    init_cmd,
    ocr_cmd,
    person_cmd,
    serve_cmd,
    status_cmd,
    transcribe_cmd,
)

app = typer.Typer(
    name="msgviz",
    help="msgviz — local, source-agnostic chat archive visualizer.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# Single-step commands.
app.command(name="init", help="Initialize configuration and an empty DB.")(init_cmd.init)
app.command(name="status", help="Show DB stats and health info.")(status_cmd.status)
app.command(name="serve", help="Start the local FastAPI server.")(serve_cmd.serve)
app.command(name="transcribe", help="Transcribe audio messages (whisper.cpp).")(
    transcribe_cmd.transcribe
)
app.command(name="ocr", help="OCR on images (macOS Vision / Tesseract).")(ocr_cmd.ocr)
app.command(name="check", help="Selftest — which features work on this machine.")(
    check_cmd.check
)

# Subcommand groups.
app.add_typer(device_cmd.app, name="device", help="Manage devices.")
app.add_typer(chat_cmd.app, name="chat", help="Manage chats.")
app.add_typer(person_cmd.app, name="person", help="Manage persons.")
app.add_typer(import_cmd.app, name="import", help="Import data.")
app.add_typer(delete_cmd.app, name="delete", help="Delete data sets.")


def main() -> None:
    """Console entry point (via pyproject.toml: `msgviz = msgviz.cli:app`)."""
    app()


if __name__ == "__main__":
    main()
