# -*- coding: utf-8 -*-
"""msgviz import — import data from iMessage / WhatsApp."""
from __future__ import annotations

from pathlib import Path

import typer

from ._helpers import confirm_or_abort, console, die

app = typer.Typer(no_args_is_help=True, help="Import data.")


def _ensure_device(slug: str) -> None:
    """Make sure a device with this slug exists; offer to create it.

    Cuts the setup friction: instead of failing with "device not found"
    and forcing a separate `msgviz device add`, we ask. Declined → exit.
    """
    from ._helpers import existing_device_slugs, open_db
    with open_db(readonly=True) as con:
        row = con.execute(
            "SELECT 1 FROM device WHERE slug = ?", (slug,)
        ).fetchone()
    if row is not None:
        return

    console.print(f"[yellow]Device '{slug}' does not exist yet.[/yellow]")
    others = [s for s in existing_device_slugs() if s != slug]
    if others:
        # Surface existing devices so a typo is obvious before the user
        # creates a near-duplicate.
        console.print(f"[dim]Existing devices: {', '.join(others)}[/dim]")
    confirm_or_abort(f"Create device '{slug}' now?", default=True)
    name = typer.prompt("Display name for this device", default=slug)
    owner = typer.prompt("Your name (the 'me' in these chats)", default="Me")
    with open_db() as con:
        pid = con.execute(
            "SELECT id FROM person WHERE display_name = ?", (owner,)
        ).fetchone()
        pid = pid[0] if pid else con.execute(
            "INSERT INTO person(display_name) VALUES(?)", (owner,)
        ).lastrowid
        con.execute(
            "INSERT INTO device(slug, name, type, owner_person_id) "
            "VALUES(?,?,?,?)",
            (slug, name, "mac_live", pid),
        )
        con.commit()
    console.print(f"[green]Device created:[/green] {slug} (owner={owner})")


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
    chat: list[str] = typer.Option(
        None, "--chat", "-c",
        help="Import only chats whose title or JID contains this text "
             "(case-insensitive). Repeatable. Required unless --all-chats.",
    ),
    all_chats: bool = typer.Option(
        False, "--all-chats",
        help="Import EVERY chat. Deliberate opt-in — without it (and without "
             "--chat) the command just lists your chats and writes nothing.",
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
        False, "--dry-run", help="Preview only; count new messages, write nothing."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the pre-import confirmation prompt."
    ),
    progress: bool = typer.Option(True, "--progress/--no-progress", help="Show the Rich progress tree."),
) -> None:
    """Incrementally import WhatsApp Desktop's live ChatStorage.sqlite (macOS).

    Reads the plaintext SQLite the WhatsApp Desktop app keeps on disk —
    no network, no companion-device pairing, no account-ban risk. Re-runs
    only insert genuinely-new messages (dedup via source_ref). Schema
    drift shipped by Meta is recorded and a fatal change aborts with
    nothing written (see `msgviz drift`).

    Selection is deliberate: with neither --chat nor --all-chats the
    command lists your chats and exits without writing. Before any write
    it previews the chats, message counts, and which NEW people would be
    added to your archive, and asks for confirmation.

    Anything imported can be fully removed later — DB rows and media
    files on disk — with `msgviz delete chat <slug>`.
    """
    import sys as _sys
    from pathlib import Path as _Path
    _repo_root = _Path(__file__).resolve().parent.parent.parent
    if (_repo_root / "tools").is_dir() and str(_repo_root) not in _sys.path:
        _sys.path.insert(0, str(_repo_root))
    from tools.import_whatsapp_live import import_live, preview_live

    from msgviz.core.progress import make_reporter

    if _sys.platform != "darwin" and db is None:
        console.print(
            "[yellow]Note:[/yellow] WhatsApp Desktop live import targets the "
            "macOS container by default. On other platforms pass --db with an "
            "explicit ChatStorage.sqlite path."
        )

    chat_filters = list(chat) if chat else []
    db_path = str(db) if db else None

    # --- Guardrail: require an explicit selection -------------------------
    # Discovery ("what's in my WhatsApp?") lives in `msgviz whatsapp chats`,
    # which needs no device. Import is for writing — so it needs a real
    # selection. No silent "import that imports nothing".
    if not chat_filters and not all_chats:
        die(
            "No chat selected. To see what's available:\n"
            "    msgviz whatsapp chats\n"
            "Then import with --chat \"<name>\" (repeatable) or --all-chats."
        )

    # --- Ensure the target device exists (offer to create it) -------------
    _ensure_device(device)

    # --- Preview + confirm before writing ---------------------------------
    # A single combined filter (substring OR across the given --chat values
    # is approximated by previewing per filter); for the preview we show the
    # union via the importer's single-filter preview per term.
    def _run_preview(flt):
        return preview_live(device_slug=device, db_path=db_path,
                            me_name=me_name, chat_filter=flt)

    try:
        if all_chats:
            plan = _run_preview(None)
        else:
            # Union of the per-filter previews (dedup chats by slug).
            seen = {}
            new_persons: set[str] = set()
            warn = 0
            for flt in chat_filters:
                p = _run_preview(flt)
                warn = max(warn, p["drift_warn"])
                for c in p["chats"]:
                    seen[c["slug"]] = c
                new_persons.update(p["new_persons"])
            plan = {
                "chats": list(seen.values()),
                "new_persons": sorted(new_persons),
                "matched_persons": 0,
                "drift_warn": warn,
            }
    except SystemExit as e:
        die(f"{e}")

    if not plan["chats"]:
        die("No chats matched your selection. Nothing to import.")

    total_new = sum(c["new"] for c in plan["chats"])
    console.print(
        f"[bold]About to import[/bold] {total_new} new message(s) across "
        f"{len(plan['chats'])} chat(s):"
    )
    for c in sorted(plan["chats"], key=lambda c: c["new"], reverse=True)[:30]:
        console.print(f"  [cyan]{c['title']}[/cyan] — {c['new']} new")
    if plan["new_persons"]:
        console.print(
            f"\n[yellow]{len(plan['new_persons'])} new person(s)[/yellow] "
            f"will be created in your archive:"
        )
        for name in plan["new_persons"][:20]:
            console.print(f"  + {name}")
        if len(plan["new_persons"]) > 20:
            console.print(f"  … and {len(plan['new_persons']) - 20} more")

    if dry_run:
        console.print("\n[dim]Dry run — nothing written.[/dim]")
        raise typer.Exit(code=0)

    if not yes:
        confirm_or_abort("\nProceed with the import?", default=False)

    # --- Actual import (one pass; chat_filter=None when --all-chats) -------
    reporter = make_reporter("terminal" if progress else "null")
    agg = {"new": 0, "chats": 0, "media": 0, "skipped_existing": 0, "drift_warn": 0}
    try:
        filters = [None] if all_chats else chat_filters
        for flt in filters:
            stats = import_live(
                device_slug=device, db_path=db_path, me_name=me_name,
                chat_filter=flt, with_media=not no_media,
                report_only=False, reporter=reporter,
            )
            agg["new"] += stats["new"]
            agg["media"] += stats["media"]
            agg["skipped_existing"] += stats["skipped_existing"]
            agg["drift_warn"] = max(agg["drift_warn"], stats["drift_warn"])
    except SystemExit as e:
        die(f"{e}")
    except Exception as e:
        die(f"Import failed: {e}")
    finally:
        reporter.close()

    console.print(
        f"[green]Imported:[/green] {agg['new']} new message(s) · "
        f"{agg['media']} media · {agg['skipped_existing']} already present"
    )
    if agg["drift_warn"]:
        console.print(
            f"[yellow]⚠ {agg['drift_warn']} schema-drift warning(s)[/yellow] "
            f"— see [bold]msgviz drift[/bold]."
        )
    console.print(
        "[dim]Remove later with [bold]msgviz delete chat <slug>[/bold] "
        "(DB + media files).[/dim]"
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
