# -*- coding: utf-8 -*-
"""msgviz init — initialize configuration and an empty DB."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import typer

from msgviz.paths import config_dir, data_dir, db_file, schema_sql

from ._helpers import confirm_or_abort, console


def init(
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite existing DB/config (with confirmation)."
    ),
) -> None:
    """Create `data/visualizer.db` with the current schema and a minimal
    `config/sources.json` if missing.
    """
    data_dir().mkdir(parents=True, exist_ok=True)
    config_dir().mkdir(parents=True, exist_ok=True)

    dbp = db_file()
    if dbp.is_file() and not force:
        console.print(f"[yellow]DB already exists:[/yellow] {dbp}")
        console.print("Overwrite with [bold]--force[/bold].")
        raise typer.Exit(code=1)
    if dbp.is_file() and force:
        confirm_or_abort(
            f"DB {dbp} will be deleted and recreated. Continue?",
            default=False,
        )
        dbp.unlink()

    schema = schema_sql()
    if not schema.is_file():
        console.print(f"[red]Schema file missing:[/red] {schema}")
        raise typer.Exit(code=2)

    con = sqlite3.connect(str(dbp))
    con.executescript(schema.read_text(encoding="utf-8"))
    con.commit()
    con.close()
    console.print(f"[green]DB created:[/green] {dbp}")

    sources = config_dir() / "sources.json"
    if not sources.is_file():
        minimal = {
            "_comment": "msgviz configuration. devices = devices/sources, people = handle->name mapping.",
            "devices": [],
            "people": {},
        }
        sources.write_text(json.dumps(minimal, indent=2, ensure_ascii=False), encoding="utf-8")
        console.print(f"[green]Config created:[/green] {sources}")
    else:
        console.print(f"[dim]Config exists:[/dim] {sources}")

    console.print(
        "\nNext steps:\n"
        "  [bold]msgviz device add[/bold]   – add a device\n"
        "  [bold]msgviz chat add[/bold]     – attach a chat to the device\n"
        "  [bold]msgviz import …[/bold]     – import data\n"
        "  [bold]msgviz serve[/bold]        – start the UI"
    )
