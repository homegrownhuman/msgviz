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
    no_transcribe: bool = typer.Option(
        False, "--no-transcribe",
        help="Skip Whisper transcription of voice notes after the import."
    ),
    no_ocr: bool = typer.Option(
        False, "--no-ocr",
        help="Skip OCR of images after the import."
    ),
    progress: bool = typer.Option(True, "--progress/--no-progress", help="Show the Rich progress tree."),
) -> None:
    """Import a WhatsApp export.

    By default the command runs three phases — parse + DB write + media,
    then transcribes any voice notes via whisper-cli, then OCRs any
    images. Pass --no-transcribe / --no-ocr to skip the heavier post
    passes (useful for fast test imports with --limit).
    """
    # tools/ isn't installed by pip — locate it relative to the msgviz
    # package (msgviz/cli/import_cmd.py -> ../../tools/) so it's
    # importable regardless of CWD and regardless of MSGVIZ_HOME.
    import sys as _sys
    from pathlib import Path as _Path
    _repo_root = _Path(__file__).resolve().parent.parent.parent
    if (_repo_root / "tools").is_dir() and str(_repo_root) not in _sys.path:
        _sys.path.insert(0, str(_repo_root))
    from tools.import_whatsapp_export import (
        import_export, transcribe_chat, ocr_chat,
    )

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
        # Post-passes: only meaningful when media was actually imported.
        # We honour the same opt-out flags the legacy __main__ block has.
        if not no_media:
            if not no_transcribe:
                transcribe_chat(result_slug, reporter=reporter)
            if not no_ocr:
                ocr_chat(result_slug, reporter=reporter)
    except SystemExit as e:
        die(f"Import aborted: {e}")
    except Exception as e:
        die(f"Import failed: {e}")
    finally:
        reporter.close()
    console.print(f"[green]Import OK:[/green] {result_slug}")


@app.command("whatsapp-live")
def whatsapp_live(
    device: str = typer.Option(..., "--device", "-d", help="Device slug the WhatsApp install is attached to."),
    chat: str = typer.Option(
        None, "--chat", "-c",
        help="Only chats whose title or JID contains this text (case-insensitive). Default: all chats.",
    ),
    me_name: str = typer.Option(
        None, "--me", help="Your display name (default: 'Me')."
    ),
    db: Path = typer.Option(
        None, "--db",
        help="Override the ChatStorage.sqlite path (default: macOS WhatsApp Desktop container).",
    ),
    no_media: bool = typer.Option(False, "--no-media", help="Skip attachments."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Count new messages; write nothing."
    ),
    progress: bool = typer.Option(True, "--progress/--no-progress", help="Show the Rich progress tree."),
) -> None:
    """Incrementally import WhatsApp Desktop's live ChatStorage.sqlite (macOS).

    Reads the plaintext SQLite the WhatsApp Desktop app keeps on disk —
    no network, no companion-device pairing, no account-ban risk. Re-runs
    only insert genuinely-new messages (dedup via source_ref). Schema
    drift shipped by Meta is recorded to the drift_event table and a
    fatal change aborts the import with nothing written — see
    `msgviz drift`.
    """
    import sys as _sys
    from pathlib import Path as _Path
    _repo_root = _Path(__file__).resolve().parent.parent.parent
    if (_repo_root / "tools").is_dir() and str(_repo_root) not in _sys.path:
        _sys.path.insert(0, str(_repo_root))
    from tools.import_whatsapp_live import import_live

    from msgviz.core.progress import make_reporter

    if _sys.platform != "darwin" and db is None:
        console.print(
            "[yellow]Note:[/yellow] WhatsApp Desktop live import targets the "
            "macOS container by default. On other platforms pass --db with an "
            "explicit ChatStorage.sqlite path."
        )

    reporter = make_reporter("terminal" if progress else "null")
    try:
        stats = import_live(
            device_slug=device,
            db_path=str(db) if db else None,
            me_name=me_name,
            chat_filter=chat,
            with_media=not no_media,
            report_only=dry_run,
            reporter=reporter,
        )
    except SystemExit as e:
        die(f"{e}")
    except Exception as e:
        die(f"Import failed: {e}")
    finally:
        reporter.close()

    verb = "Would import" if dry_run else "Imported"
    console.print(
        f"[green]{verb}:[/green] {stats['new']} new message(s) across "
        f"{stats['chats']} chat(s) · {stats['media']} media · "
        f"{stats['skipped_existing']} already present"
    )
    if stats["drift_warn"]:
        console.print(
            f"[yellow]⚠ {stats['drift_warn']} schema-drift warning(s)[/yellow] "
            f"— see [bold]msgviz drift[/bold]."
        )


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
    console.print("[green]iMessage sync done[/green]"
                  + (f" (chat={chat})" if chat else ""))
