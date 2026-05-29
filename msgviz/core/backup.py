# -*- coding: utf-8 -*-
"""
msgviz.core.backup — DB backups.

Before every structural change to data/visualizer.db (migrate run,
schema update, manual person merge), `backup_db()` copies the live DB
to data/db-backups/pre-<tag>-<timestamp>.db.

* Only spends filesystem time; no DB lock — we copy the file.
* Skipped if the DB does not exist (bootstrap case).
* Skipped if the DB is empty (no person/device/chat) — nothing to save.
* Keeps the latest N backups (default 20); older ones are deleted.

Usage:
    from msgviz.core.backup import backup_db
    backup_path = backup_db("migrate-people-out")
    if backup_path:
        print(f"Backup -> {backup_path}")
"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from msgviz.paths import data_dir, db_file

MAX_BACKUPS = 20


def _backup_dir() -> Path:
    p = data_dir() / "db-backups"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _db_has_content(path: Path) -> bool:
    """True if the DB has at least one person/device/chat row."""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except Exception:
        return False
    try:
        for tbl in ("person", "device", "chat", "message"):
            try:
                n = con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            except sqlite3.OperationalError:
                # table doesn't exist — ignore
                continue
            if n > 0:
                return True
        return False
    finally:
        con.close()


def _prune_old_backups(keep: int = MAX_BACKUPS) -> int:
    """Keep only the `keep` newest backups, remove older. Return: number deleted."""
    files = sorted(
        _backup_dir().glob("pre-*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for old in files[keep:]:
        try:
            old.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def backup_db(
    tag: str,
    *,
    skip_if_empty: bool = True,
    keep: int = MAX_BACKUPS,
) -> Path | None:
    """Copy the live DB to data/db-backups/pre-<tag>-<ts>.db.

    Args:
        tag: short label for the occasion, e.g. "migrate", "people-out".
             Included in the filename.
        skip_if_empty: when True and the DB has no content -> no backup.
        keep: maximum number of retained backups (FIFO).

    Returns:
        Path to the new backup, or None if skipped.
    """
    src = db_file()
    if not src.is_file():
        return None
    if skip_if_empty and not _db_has_content(src):
        return None

    safe_tag = "".join(c if c.isalnum() or c in "-_" else "_" for c in tag) or "manual"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = _backup_dir() / f"pre-{safe_tag}-{stamp}.db"

    shutil.copy2(src, dst)
    _prune_old_backups(keep=keep)
    return dst


def list_backups() -> list[Path]:
    """All existing backups, newest first."""
    return sorted(
        _backup_dir().glob("pre-*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
