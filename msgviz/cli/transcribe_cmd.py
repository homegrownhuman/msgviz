# -*- coding: utf-8 -*-
"""msgviz transcribe — audio transcription (whisper.cpp)."""
from __future__ import annotations

import typer

from ._helpers import console, die


def transcribe(
    chat: str = typer.Option(None, "--chat", "-c", help="Only audios of this chat slug."),
    limit: int = typer.Option(None, "--limit", "-n", help="Process only the first N audios."),
    progress: bool = typer.Option(True, "--progress/--no-progress", help="Rich progress tree."),
) -> None:
    """Incrementally transcribe every audio not yet transcribed."""
    from msgviz.core.progress import make_reporter
    from msgviz.workers import transcribe as worker

    reporter = make_reporter("terminal" if progress else "null")
    phase = reporter.start_phase("Transcription", "audio")
    try:
        worker.run(chat=chat, limit=limit, reporter_phase=phase)
    except SystemExit as e:
        die(f"Transcription aborted: {e}")
    except Exception as e:
        die(f"Transcription failed: {e}")
    finally:
        phase.finish()
    console.print("[green]Transcription done.[/green]")
