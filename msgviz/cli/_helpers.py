# -*- coding: utf-8 -*-
"""
msgviz.cli._helpers — helpers shared by every subcommand.

What lives here, to avoid duplicating it in every cmd module:
* opening the DB (with row_factory),
* nice Rich-table output,
* uniform error exits.

Not a public API — internal to msgviz.cli.
"""
from __future__ import annotations

import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import typer
from rich.console import Console
from rich.table import Table

from msgviz.paths import db_file

console = Console()


def get_db_path() -> Path:
    return db_file()


@contextmanager
def open_db(readonly: bool = False) -> Iterator[sqlite3.Connection]:
    """Open the visualizer.db. Abort if it's missing.

    For writable connections, also runs additive schema migrations
    (new columns, new tables) so old DBs work with current code.
    """
    path = get_db_path()
    if not path.is_file():
        console.print(
            f"[red]DB missing:[/red] {path}\n"
            f"Initialize with: [bold]msgviz init[/bold]"
        )
        raise typer.Exit(code=1)
    if readonly:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        con = sqlite3.connect(str(path))
        # Apply additive schema migrations once per connection.
        try:
            from msgviz.core.schema_migrate import apply_all
            applied = apply_all(con)
            if applied:
                console.print(
                    f"[dim]Schema upgraded: {', '.join(applied)}[/dim]"
                )
        except Exception as e:
            # Migration failures should not silently corrupt operations.
            console.print(f"[red]Schema migration error:[/red] {e}")
            raise
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
    finally:
        con.close()


def render_table(title: str, rows: list[dict]) -> None:
    """Render a Rich table from dict rows. Columns come from the first row."""
    if not rows:
        console.print(f"[dim]{title}: empty[/dim]")
        return
    table = Table(title=title, show_header=True, header_style="bold cyan")
    cols = list(rows[0].keys())
    for c in cols:
        table.add_column(c)
    for r in rows:
        table.add_row(*[str(r.get(c, "")) for c in cols])
    console.print(table)


_err_console = Console(stderr=True)


def die(msg: str, code: int = 1) -> None:
    _err_console.print(f"[red]Error:[/red] {msg}")
    raise typer.Exit(code=code)


def confirm_or_abort(prompt: str, default: bool = False) -> None:
    if not typer.confirm(prompt, default=default):
        console.print("[yellow]Aborted.[/yellow]")
        raise typer.Exit(code=1)
