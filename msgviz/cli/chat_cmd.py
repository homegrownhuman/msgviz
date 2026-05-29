# -*- coding: utf-8 -*-
"""msgviz chat — manage chats."""
from __future__ import annotations

import typer

from ._helpers import confirm_or_abort, console, die, open_db, render_table

app = typer.Typer(no_args_is_help=True, help="Manage chats.")

VALID_ORIGINS = {"apple", "whatsapp", "signal", "telegram", "sms"}


@app.command("add")
def add(
    device: str = typer.Argument(..., help="Slug of the device the chat belongs to."),
    slug: str = typer.Option(..., "--slug", "-s", help="Chat slug, unique per device."),
    title: str = typer.Option(..., "--title", "-t", help="Display title."),
    subtitle: str = typer.Option("", "--subtitle", help="Subtitle (optional)."),
    origin: str = typer.Option(
        "apple",
        "--origin",
        "-o",
        help=f"Source ({', '.join(sorted(VALID_ORIGINS))}).",
    ),
    is_group: bool = typer.Option(False, "--group", help="Group chat."),
) -> None:
    """Add a chat under an existing device."""
    if origin not in VALID_ORIGINS:
        die(f"Unknown origin '{origin}'. Allowed: {sorted(VALID_ORIGINS)}")
    with open_db() as con:
        dev = con.execute("SELECT id FROM device WHERE slug = ?", (device,)).fetchone()
        if dev is None:
            die(f"Device '{device}' not found. Run `msgviz device add` first.")
        combined_slug = f"{device}/{slug}"
        try:
            con.execute(
                """INSERT INTO chat(slug, device_id, title, subtitle, is_group, origin)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (combined_slug, dev[0], title, subtitle, int(is_group), origin),
            )
            con.commit()
        except Exception as e:
            die(f"Could not create chat: {e}")
    console.print(
        f"[green]Chat created:[/green] {combined_slug} ({title}, origin={origin})"
    )


@app.command("list")
def list_(
    device: str = typer.Option(None, "--device", "-d", help="Only chats of this device."),
) -> None:
    """List every chat (optionally filtered by device)."""
    with open_db(readonly=True) as con:
        if device:
            rows = con.execute(
                """SELECT c.slug, c.title, c.origin, d.slug AS device,
                          (SELECT COUNT(*) FROM message m WHERE m.chat_id = c.id) AS messages
                   FROM chat c JOIN device d ON d.id = c.device_id
                   WHERE d.slug = ?
                   ORDER BY messages DESC""",
                (device,),
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT c.slug, c.title, c.origin, d.slug AS device,
                          (SELECT COUNT(*) FROM message m WHERE m.chat_id = c.id) AS messages
                   FROM chat c JOIN device d ON d.id = c.device_id
                   ORDER BY messages DESC"""
            ).fetchall()
    render_table("Chats", [dict(r) for r in rows])


@app.command("remove")
def remove(
    slug: str = typer.Argument(..., help="Full chat slug (e.g. 'my_mac/bob')."),
    yes: bool = typer.Option(False, "--yes", "-y", help="No confirmation prompt."),
    no_backup: bool = typer.Option(False, "--no-backup", help="Skip the safety copy."),
    keep_files: bool = typer.Option(
        False, "--keep-files",
        help="Delete DB rows only; leave media files on disk (legacy behaviour).",
    ),
) -> None:
    """Remove a chat with all its messages — and its media files on disk.

    Media is content-addressed (shared between chats by hash), so a file
    is only deleted from disk when no other chat still references it.
    Files shared with other chats are kept. Use --keep-files to remove
    only the DB rows.
    """
    from msgviz.core import purge as purge_mod

    with open_db() as con:
        row = con.execute(
            """SELECT c.id,
                      (SELECT COUNT(*) FROM message WHERE chat_id = c.id) AS n_msgs
               FROM chat c WHERE c.slug = ?""",
            (slug,),
        ).fetchone()
        if row is None:
            die(f"Chat '{slug}' not found.")
        cid, n_msgs = row[0], row[1]

        # Preview the disk impact before asking for confirmation.
        preview = purge_mod.purge_chats(con, [cid], dry_run=True)
        if not yes:
            confirm_or_abort(
                f"Delete chat '{slug}': {n_msgs} messages, "
                f"{preview.files_deleted} media file(s) from disk "
                f"({preview.bytes_freed // 1024} KB), "
                f"{preview.files_kept_shared} shared file(s) kept. Continue?"
            )

        if not no_backup:
            from msgviz.core.backup import backup_db
            bk = backup_db(f"remove-chat-{slug.replace('/', '_')}")
            if bk is not None:
                console.print(f"[dim]Backup -> {bk}[/dim]")

        if keep_files:
            con.execute(
                "DELETE FROM media WHERE message_id IN "
                "(SELECT id FROM message WHERE chat_id = ?)", (cid,),
            )
            con.execute("DELETE FROM source_ref WHERE message_id IN "
                        "(SELECT id FROM message WHERE chat_id = ?)", (cid,))
            con.execute("DELETE FROM message WHERE chat_id = ?", (cid,))
            con.execute("DELETE FROM chat_participant WHERE chat_id = ?", (cid,))
            con.execute("DELETE FROM chat WHERE id = ?", (cid,))
            con.commit()
            console.print(
                f"[green]Chat '{slug}' and {n_msgs} messages deleted "
                f"(media files kept).[/green]"
            )
            return

        stats = purge_mod.purge_chats(con, [cid])
    console.print(
        f"[green]Chat '{slug}' deleted:[/green] {stats.messages} messages, "
        f"{stats.files_deleted} media file(s) removed from disk "
        f"({stats.bytes_freed // 1024} KB freed), "
        f"{stats.files_kept_shared} shared file(s) kept."
    )
    if stats.errors:
        console.print(f"[yellow]{len(stats.errors)} file error(s).[/yellow]")
