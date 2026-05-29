# -*- coding: utf-8 -*-
"""msgviz device — manage devices."""
from __future__ import annotations

import typer

from ._helpers import confirm_or_abort, console, die, open_db, render_table

app = typer.Typer(no_args_is_help=True, help="Manage devices (sources).")

VALID_TYPES = {"mac_live", "ios_backup", "iphone_backup", "static"}


@app.command("add")
def add(
    slug: str = typer.Argument(..., help="Unique device slug, e.g. 'mac_alice'."),
    name: str = typer.Option(..., "--name", "-n", help="Display name."),
    type_: str = typer.Option(
        "static",
        "--type",
        "-t",
        help=f"Device type ({', '.join(sorted(VALID_TYPES))}).",
    ),
    owner: str = typer.Option(..., "--owner", "-o", help="Owner person (display_name)."),
) -> None:
    """Add a new device. The owner is created if not already present."""
    if type_ not in VALID_TYPES:
        die(f"Unknown device type '{type_}'. Allowed: {sorted(VALID_TYPES)}")
    with open_db() as con:
        pid = con.execute(
            "SELECT id FROM person WHERE display_name = ?", (owner,)
        ).fetchone()
        if pid is None:
            pid = con.execute(
                "INSERT INTO person(display_name) VALUES(?)", (owner,)
            ).lastrowid
            console.print(f"[dim]Person created:[/dim] {owner} (id={pid})")
        else:
            pid = pid[0]
        try:
            con.execute(
                "INSERT INTO device(slug, name, type, owner_person_id) VALUES(?,?,?,?)",
                (slug, name, type_, pid),
            )
            con.commit()
        except Exception as e:
            die(f"Could not create device: {e}")
    console.print(f"[green]Device created:[/green] {slug} ({name}, type={type_}, owner={owner})")


@app.command("list")
def list_() -> None:
    """List every device."""
    with open_db(readonly=True) as con:
        rows = con.execute(
            """SELECT d.slug, d.name, d.type, p.display_name AS owner,
                      (SELECT COUNT(*) FROM chat c WHERE c.device_id = d.id) AS chats
               FROM device d
               LEFT JOIN person p ON p.id = d.owner_person_id
               ORDER BY d.slug"""
        ).fetchall()
    render_table("Devices", [dict(r) for r in rows])


@app.command("remove")
def remove(
    slug: str = typer.Argument(..., help="Device slug."),
    yes: bool = typer.Option(False, "--yes", "-y", help="No confirmation prompt."),
    no_backup: bool = typer.Option(False, "--no-backup", help="Skip the safety copy."),
) -> None:
    """Remove a device WITH all its chats and messages."""
    if not no_backup:
        from msgviz.core.backup import backup_db
        bk = backup_db(f"remove-device-{slug}")
        if bk is not None:
            console.print(f"[dim]Backup -> {bk}[/dim]")
    with open_db() as con:
        row = con.execute(
            """SELECT d.id,
                      (SELECT COUNT(*) FROM chat WHERE device_id = d.id) AS n_chats,
                      (SELECT COUNT(*) FROM message m
                         JOIN chat c ON c.id = m.chat_id
                         WHERE c.device_id = d.id) AS n_msgs
               FROM device d WHERE d.slug = ?""",
            (slug,),
        ).fetchone()
        if row is None:
            die(f"Device '{slug}' not found.")
        did, n_chats, n_msgs = row[0], row[1], row[2]
        if not yes:
            confirm_or_abort(
                f"Delete device '{slug}' including {n_chats} chats and {n_msgs} messages. Continue?"
            )
        con.execute(
            """DELETE FROM message
               WHERE chat_id IN (SELECT id FROM chat WHERE device_id = ?)""",
            (did,),
        )
        con.execute("DELETE FROM chat WHERE device_id = ?", (did,))
        con.execute("DELETE FROM device WHERE id = ?", (did,))
        con.commit()
    console.print(f"[green]Device '{slug}' and all its data deleted.[/green]")
