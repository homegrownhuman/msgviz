# -*- coding: utf-8 -*-
"""msgviz import — import data from iMessage / WhatsApp."""
from __future__ import annotations

from pathlib import Path

import typer

from ._helpers import console, die

app = typer.Typer(no_args_is_help=True, help="Import data.")


@app.command("whatsapp")
def whatsapp(
    device: str = typer.Option(..., "--device", "-d", help="Device slug the chat is attached to."),
    folder: Path = typer.Option(
        ...,
        "--folder",
        "-f",
        help="WhatsApp export folder containing `_chat.txt` and attachments.",
        exists=True,
        file_okay=False,
        dir_okay=True,
    ),
    slug: str = typer.Option(..., "--slug", "-s", help="Target chat slug."),
    me_name: str = typer.Option(
        None, "--me", help="Your display name in this chat (overrides the device owner)."
    ),
    limit: int = typer.Option(None, "--limit", help="Import only the first N messages."),
    no_media: bool = typer.Option(False, "--no-media", help="Skip media (images/audio)."),
    progress: bool = typer.Option(True, "--progress/--no-progress", help="Show the Rich progress tree."),
) -> None:
    """Import a WhatsApp export."""
    # tools/ isn't installed by pip — locate it relative to the msgviz
    # package (msgviz/cli/import_cmd.py -> ../../tools/) so it's
    # importable regardless of CWD and regardless of MSGVIZ_HOME.
    import sys as _sys
    from pathlib import Path as _Path
    _repo_root = _Path(__file__).resolve().parent.parent.parent
    if (_repo_root / "tools").is_dir() and str(_repo_root) not in _sys.path:
        _sys.path.insert(0, str(_repo_root))
    from tools.import_whatsapp_export import import_export  # existing worker

    from msgviz.core.progress import make_reporter

    reporter = make_reporter("terminal" if progress else "null")
    try:
        result_slug = import_export(
            str(folder),
            device_slug=device,
            chat_slug=slug,
            me_name=me_name,
            limit=limit,
            with_media=not no_media,
            reporter=reporter,
        )
    except SystemExit as e:
        die(f"Import aborted: {e}")
    except Exception as e:
        die(f"Import failed: {e}")
    console.print(f"[green]Import OK:[/green] {result_slug}")


@app.command("imessage")
def imessage(
    device: str = typer.Option(..., "--device", "-d", help="Device slug."),
    chat: str = typer.Option(
        None,
        "--chat",
        "-c",
        help="Sync only this chat (slug). Without: all chats of the device.",
    ),
    report_only: bool = typer.Option(
        False, "--dry-run", help="Only print what would be new; do not write."
    ),
) -> None:
    """Sync against Apple's chat.db (live or backup). Live mode only makes
    sense on macOS; backups work cross-platform as long as the path in the
    config is absolute.
    """
    import sys

    from msgviz.core import sync as sync_mod

    if not hasattr(sync_mod, "sync"):
        die("No sync.sync() available — live sync is not set up.")
    if sys.platform != "darwin":
        console.print(
            "[yellow]Note:[/yellow] live iMessage sync only works on macOS. "
            "Backup imports work cross-platform as long as the backup path in the config is absolute."
        )
    try:
        sync_mod.sync(report_only=report_only)
    except Exception as e:
        die(f"Sync failed: {e}")
    console.print(f"[green]iMessage sync done[/green]"
                  + (f" (chat={chat})" if chat else ""))
