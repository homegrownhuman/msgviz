# -*- coding: utf-8 -*-
"""
Lightweight schema migrations for existing DBs.

These run automatically the first time the CLI helper opens a DB after
the schema has grown a column.  Each migration is idempotent: it checks
whether the column exists before adding it.

Migration policy:
* Additive only (new columns, new tables).
* Never destructive — never DROP, never CHANGE TYPE.
* Backup is the CALLER's responsibility (the CLI's `open_db` wraps
  destructive commands; reads should not trigger migrations that touch
  data).
"""
from __future__ import annotations

import sqlite3


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def _tables(con: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def ensure_avatar_column(con: sqlite3.Connection) -> bool:
    """Add person.avatar_src if it's missing.  Returns True if added."""
    if "person" not in _tables(con):
        return False
    if "avatar_src" in _columns(con, "person"):
        return False
    con.execute("ALTER TABLE person ADD COLUMN avatar_src TEXT")
    con.commit()
    return True


def apply_all(con: sqlite3.Connection) -> list[str]:
    """Run every known additive migration.  Returns the list of names applied."""
    applied: list[str] = []
    if ensure_avatar_column(con):
        applied.append("person.avatar_src")
    return applied
