# -*- coding: utf-8 -*-
"""msgviz ocr — OCR on images."""
from __future__ import annotations

import typer

from ._helpers import console, die


def ocr(
    chat: str = typer.Option(None, "--chat", "-c", help="Only images of this chat slug."),
    limit: int = typer.Option(None, "--limit", "-n", help="Process only the first N images."),
    progress: bool = typer.Option(True, "--progress/--no-progress", help="Rich progress tree."),
) -> None:
    """OCR every image that has no OCR text yet (incrementally)."""
    from msgviz.core.progress import make_reporter
    from msgviz.workers import ocr_images as worker

    reporter = make_reporter("terminal" if progress else "null")
    phase = reporter.start_phase("OCR", "image")
    try:
        worker.run(chat=chat, limit=limit, reporter_phase=phase)
    except SystemExit as e:
        die(f"OCR aborted: {e}")
    except Exception as e:
        die(f"OCR failed: {e}")
    finally:
        phase.finish()
    console.print("[green]OCR done.[/green]")
