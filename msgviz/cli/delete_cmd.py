# -*- coding: utf-8 -*-
"""msgviz delete — delete data sets."""
from __future__ import annotations

import typer

from ._helpers import confirm_or_abort, console, die, open_db

app = typer.Typer(no_args_is_help=True, help="Delete data sets (irreversible).")


@app.command("chat")
def chat(
    slug: str = typer.Argument(..., help="Full chat slug, e.g. 'my_mac/bob'."),
    yes: bool = typer.Option(False, "--yes", "-y", help="No confirmation prompt."),
) -> None:
    """Delete a chat with its messages and media references."""
    from .chat_cmd import remove as chat_remove

    chat_remove(slug=slug, yes=yes)


@app.command("device")
def device(
    slug: str = typer.Argument(..., help="Device slug."),
    yes: bool = typer.Option(False, "--yes", "-y", help="No confirmation prompt."),
) -> None:
    """Delete a device and all its chats / messages."""
    from .device_cmd import remove as device_remove

    device_remove(slug=slug, yes=yes)


@app.command("all")
def all_(
    confirm_string: str = typer.Option(
        None,
        "--confirm",
        help='Required — type exactly "yes-wipe-everything" to confirm a full reset.',
    ),
    no_backup: bool = typer.Option(
        False, "--no-backup", help="Skip the safety copy. Only if you really mean it."
    ),
) -> None:
    """Reset the entire DB (all devices/chats/persons/messages gone).

    A DB backup is written to data/db-backups/pre-delete-all-… first
    (unless --no-backup).
    """
    expected = "yes-wipe-everything"
    if confirm_string != expected:
        die(f'Safety string missing: pass --confirm "{expected}".')
    confirm_or_abort(
        "The full DB will be wiped. Configuration in config/sources.json is kept. Continue?",
        default=False,
    )
    if not no_backup:
        from msgviz.core.backup import backup_db
        bk = backup_db("delete-all")
        if bk is not None:
            console.print(f"[dim]Backup -> {bk}[/dim]")
    tables = [
        "media",
        "source_ref",
        "message",
        "chat_participant",
        "chat_source",
        "chat",
        "device",
        "person_alias",
        "handle",
        "person",
    ]
    with open_db() as con:
        for t in tables:
            try:
                con.execute(f"DELETE FROM {t}")
            except Exception as e:
                console.print(f"[yellow]Table '{t}' skipped:[/yellow] {e}")
        try:
            con.execute("VACUUM")
        except Exception:
            pass
        con.commit()
    console.print("[green]DB fully wiped.[/green]")
