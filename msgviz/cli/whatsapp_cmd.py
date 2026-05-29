# -*- coding: utf-8 -*-
"""msgviz whatsapp — discover what's in WhatsApp Desktop before importing."""
from __future__ import annotations

from pathlib import Path

import typer

from ._helpers import console, die

app = typer.Typer(no_args_is_help=True, help="Inspect WhatsApp Desktop (macOS).")


@app.command("chats")
def chats(
    db: Path = typer.Option(
        None, "--db",
        help="Override the ChatStorage.sqlite path (default: macOS WhatsApp Desktop container).",
    ),
    chat: str = typer.Option(
        None, "--chat", "-c",
        help="Only show chats whose title or JID contains this text.",
    ),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """List the chats in your WhatsApp Desktop database.

    Pure discovery — needs no device, no setup, no msgviz archive. Reads
    only the on-disk ChatStorage.sqlite so you can see what's there
    before deciding what to import with `msgviz import whatsapp-live`.
    macOS only by default; pass --db elsewhere.
    """
    import sys as _sys
    _repo_root = Path(__file__).resolve().parent.parent.parent
    if (_repo_root / "tools").is_dir() and str(_repo_root) not in _sys.path:
        _sys.path.insert(0, str(_repo_root))
    from tools.import_whatsapp_live import list_whatsapp_chats

    if _sys.platform != "darwin" and db is None:
        console.print(
            "[yellow]Note:[/yellow] WhatsApp Desktop is read from the macOS "
            "container by default. On other platforms pass --db."
        )

    try:
        result = list_whatsapp_chats(
            db_path=str(db) if db else None, chat_filter=chat
        )
    except SystemExit as e:
        die(f"{e}")
    except Exception as e:
        die(f"Could not read WhatsApp DB: {e}")

    rows = sorted(result["chats"], key=lambda c: c["total"], reverse=True)

    if json_out:
        console.print_json(data={"chats": rows})
        return

    if not rows:
        console.print("[dim]No chats found.[/dim]")
        return

    console.print(f"[bold]{len(rows)} WhatsApp chat(s):[/bold]\n")
    for c in rows:
        kind = "group" if c["is_group"] else "1:1"
        console.print(
            f"  [cyan]{c['title']}[/cyan] [dim]({kind})[/dim] — "
            f"{c['total']} messages"
        )
    console.print(
        "\nImport with [bold]msgviz import whatsapp-live --device <slug> "
        "--chat \"<name>\"[/bold] (or [bold]--all-chats[/bold])."
    )
    if result["drift_warn"]:
        console.print(
            f"[yellow]⚠ {result['drift_warn']} schema-drift warning(s)[/yellow] "
            f"— see [bold]msgviz drift[/bold]."
        )
