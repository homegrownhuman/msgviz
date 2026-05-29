#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Render console screenshots to SVG for embedding in README / docs.

Why SVG: the Rich library renders the same terminal output to scalable
vector graphics that GitHub displays inline in Markdown. No PNG capture
session, no platform-dependent fonts — the screenshots regenerate
reproducibly from this script.

Output:  docs/screenshots/*.svg

Usage:
    .venv/bin/python scripts/render_console_screenshots.py
        # runs every supported screenshot

    .venv/bin/python scripts/render_console_screenshots.py check status
        # render only the named screenshots

What gets rendered:
    check     msgviz check  (feature matrix, fix panel)
    status    msgviz status (DB stats tables)
    import    simulated msgviz import progress bar
    transcribe  simulated msgviz transcribe progress bar

Run inside the configured MSGVIZ_HOME (default: live data/).
For the public demo screenshots, run with MSGVIZ_HOME=demo so the
output reflects the bundled showcase data.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "screenshots"
WIDTH = 100  # terminal columns


def _new_console() -> Console:
    """Console configured for SVG export — `record=True` captures output."""
    return Console(
        record=True,
        width=WIDTH,
        # Force a terminal-like rendering even when piped to a file.
        force_terminal=True,
    )


def _save(console: Console, name: str, title: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.svg"
    console.save_svg(str(path), title=title)
    print(f"  → {path.relative_to(ROOT)}")


# ---------------------------------------------------------------------------
# msgviz check
# ---------------------------------------------------------------------------
def render_check() -> None:
    """Re-run the check command's render logic into a recording console."""
    from msgviz.cli.check_cmd import (
        PROBES, Probe, Report,
        _feature_matrix, _fix_panel,
    )

    console = _new_console()
    report = Report()
    for fn in PROBES:
        try:
            report.add(fn())
        except Exception as e:
            report.add(Probe(
                feature=fn.__name__.replace("_probe_", ""),
                status="degraded",
                detail=f"probe crashed: {e}",
            ))

    import platform
    console.print(f"[bold]Platform:[/bold] {platform.platform()}")
    console.print(f"[bold]Python:[/bold]   {sys.version.split()[0]}")
    console.print()
    console.print(_feature_matrix(report))
    panel = _fix_panel(report)
    if panel is not None:
        console.print()
        console.print(panel)
    console.print()
    if not report.baseline_ok():
        console.print("[bold red]✗ baseline broken — msgviz can't run.[/bold red]")
    elif report.has_degraded() or report.has_missing():
        console.print(
            "[bold yellow]~ baseline OK — some optional features "
            "unavailable (see above).[/bold yellow]"
        )
    else:
        console.print("[bold green]✓ all checks passed.[/bold green]")

    _save(console, "check", "msgviz check")


# ---------------------------------------------------------------------------
# msgviz status (DB stats)
# ---------------------------------------------------------------------------
def render_status() -> None:
    from msgviz.paths import data_dir, db_file, media_root, originals_root
    from msgviz.cli._helpers import open_db

    console = _new_console()
    console.print(f"[bold]DB:[/bold]        {db_file()}")
    console.print(f"[bold]Data dir:[/bold]  {data_dir()}")
    console.print(f"[bold]Media:[/bold]     {media_root()}")
    console.print(f"[bold]Originals:[/bold] {originals_root()}")
    console.print()

    if not db_file().is_file():
        console.print("[yellow]DB does not exist yet.[/yellow]")
        _save(console, "status", "msgviz status")
        return

    with open_db(readonly=True) as con:
        tbl = Table(title="DB content", show_lines=False, header_style="bold")
        tbl.add_column("table", style="bold")
        tbl.add_column("rows", justify="right")
        for name in ("person", "handle", "device", "chat", "message", "media"):
            try:
                n = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                tbl.add_row(name, f"{n:,}")
            except Exception:
                tbl.add_row(name, "—")
        console.print(tbl)
        console.print()

        try:
            kinds = con.execute(
                "SELECT kind, COUNT(*) AS n FROM media GROUP BY kind ORDER BY n DESC"
            ).fetchall()
            mtbl = Table(title="Media by kind", show_lines=False, header_style="bold")
            mtbl.add_column("kind", style="bold")
            mtbl.add_column("count", justify="right")
            for row in kinds:
                mtbl.add_row(row[0], f"{row[1]:,}")
            console.print(mtbl)
            console.print()
        except Exception:
            pass

        try:
            chats = con.execute(
                """SELECT c.slug, c.title,
                          (SELECT COUNT(*) FROM message m WHERE m.chat_id=c.id) AS n
                   FROM chat c ORDER BY n DESC LIMIT 8"""
            ).fetchall()
            ctbl = Table(title="Top chats", show_lines=False, header_style="bold")
            ctbl.add_column("slug", style="bold")
            ctbl.add_column("title")
            ctbl.add_column("messages", justify="right")
            for row in chats:
                ctbl.add_row(row[0], row[1], f"{row[2]:,}")
            console.print(ctbl)
        except Exception:
            pass

    _save(console, "status", "msgviz status")


# ---------------------------------------------------------------------------
# Simulated import progress bar
# ---------------------------------------------------------------------------
def render_import() -> None:
    """Capture a single frame mid-import — Rich progress bars don't render
    well to SVG when animated. We freeze the progress at ~62%."""
    console = _new_console()
    console.print(
        "[bold]→ msgviz import whatsapp[/bold] "
        "--device wa_archive --folder ~/exports/Chat\\ -\\ Bob \\\n"
        "                          --slug bob --me 'Alice Chen'"
    )
    console.print()
    console.print("[dim]Reading _chat.txt …[/dim]")
    console.print("[dim]4,873 lines parsed; resolving handles[/dim]")
    console.print()

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}[/bold]"),
        BarColumn(bar_width=40),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )
    # We need to render without actually running — start, update to a value,
    # render once, then stop.
    with progress:
        t1 = progress.add_task("Importing messages", total=4837)
        progress.update(t1, completed=2998)
        t2 = progress.add_task("Hashing media (images)", total=405)
        progress.update(t2, completed=287)
        t3 = progress.add_task("Hashing media (voice notes)", total=124)
        progress.update(t3, completed=51)
        # One refresh so Rich renders the current state into the recording.
        progress.refresh()
        time.sleep(0.01)

    console.print()
    console.print("[dim]Live DB stays at data/visualizer.db (untouched). "
                  "Import writes happen in a single transaction.[/dim]")
    _save(console, "import", "msgviz import whatsapp")


# ---------------------------------------------------------------------------
# Simulated transcribe progress
# ---------------------------------------------------------------------------
def render_transcribe() -> None:
    console = _new_console()
    console.print(
        "[bold]→ msgviz transcribe[/bold] --chat my_mac/bob"
    )
    console.print()
    console.print("[dim]whisper-cli:[/dim] /opt/homebrew/bin/whisper-cli")
    console.print("[dim]model:      [/dim] ~/.whisper-models/ggml-large-v3.bin (2,951 MB)")
    console.print("[dim]ffmpeg:     [/dim] /opt/homebrew/bin/ffmpeg")
    console.print()

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}[/bold]"),
        BarColumn(bar_width=40),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    )
    with progress:
        t = progress.add_task("Transcribing voice notes", total=124)
        progress.update(t, completed=78)
        progress.refresh()
        time.sleep(0.01)

    console.print()
    console.print("[green]✓[/green] 78 / 124 transcripts written to "
                  "data/transcripts.json")
    console.print("[dim]Skipping 3 voice notes with no audio data.[/dim]")
    _save(console, "transcribe", "msgviz transcribe")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
RENDERERS = {
    "check": render_check,
    "status": render_status,
    "import": render_import,
    "transcribe": render_transcribe,
}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "names",
        nargs="*",
        choices=list(RENDERERS) + [[]],
        help="Which screenshots to render (default: all).",
    )
    args = p.parse_args()
    names = args.names or list(RENDERERS)
    print(f"Rendering {len(names)} screenshot(s) to {OUT_DIR.relative_to(ROOT)}/")
    for name in names:
        RENDERERS[name]()


if __name__ == "__main__":
    main()
