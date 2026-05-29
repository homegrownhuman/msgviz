# -*- coding: utf-8 -*-
"""msgviz status — DB health and statistics."""
from __future__ import annotations

from msgviz.paths import data_dir, db_file, media_root, originals_root

from ._helpers import console, open_db, render_table


def status() -> None:
    """Show paths, DB stats and media overview."""
    console.print(f"[bold]DB:[/bold]        {db_file()}")
    console.print(f"[bold]Data dir:[/bold]  {data_dir()}")
    console.print(f"[bold]Media:[/bold]     {media_root()}")
    console.print(f"[bold]Originals:[/bold] {originals_root()}")
    console.print()

    if not db_file().is_file():
        console.print(
            "[yellow]DB does not exist yet.[/yellow]\n"
            "Initialize with: [bold]msgviz init[/bold]"
        )
        return

    with open_db(readonly=True) as con:
        rows = []
        for tbl in ("person", "handle", "device", "chat", "message", "media"):
            try:
                n = con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            except Exception as e:
                n = f"err: {e}"
            rows.append({"table": tbl, "rows": n})
        render_table("DB content", rows)

        try:
            kinds = con.execute(
                "SELECT kind, COUNT(*) AS n FROM media GROUP BY kind ORDER BY n DESC"
            ).fetchall()
            render_table("Media by kind", [dict(r) for r in kinds])
        except Exception as e:
            console.print(f"[dim]media-kind breakdown skipped: {e}[/dim]")

        try:
            chats = con.execute(
                """SELECT c.slug,
                          c.title,
                          (SELECT COUNT(*) FROM message m WHERE m.chat_id = c.id) AS messages
                   FROM chat c
                   ORDER BY messages DESC"""
            ).fetchall()
            render_table("Chats", [dict(r) for r in chats])
        except Exception as e:
            console.print(f"[dim]chat overview skipped: {e}[/dim]")
