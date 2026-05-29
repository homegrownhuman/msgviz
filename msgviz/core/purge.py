# -*- coding: utf-8 -*-
"""
Complete chat / device removal — DB rows **and** files on disk.

``msgviz delete chat`` historically removed only the database rows,
leaving every media file orphaned under ``media/`` (and any
``originals/``). For an archive that ingests live sources — where you
might import the wrong chat and need it *gone* — that's not good enough:
a removal has to be total, or the safety net isn't real.

The tricky part is that media is **content-addressed**: the same file
(``media/<kind>/<prefix>/<hash>.<ext>``) is shared by every message
that sent the identical bytes, across chats. So a file may only be
deleted from disk once **no remaining message** references it. This
module does that reference-counting: it computes the set of
``src`` / ``content_hash`` values used *only* by the chat being removed,
deletes those files, and leaves shared files in place.

Everything is driven by an open writable connection the caller
supplies (so it composes with the CLI's backup-then-delete flow) and
honours ``MSGVIZ_HOME`` via :mod:`msgviz.paths`.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from msgviz.paths import originals_root, project_root


@dataclass
class PurgeStats:
    """Outcome of a purge (or dry-run preview)."""
    chats: int = 0
    messages: int = 0
    media_rows: int = 0
    files_deleted: int = 0          # media files removed from disk
    files_kept_shared: int = 0      # not removed — still used by other chats
    originals_deleted: int = 0
    bytes_freed: int = 0
    errors: list[str] = field(default_factory=list)
    deleted_paths: list[str] = field(default_factory=list)  # for dry-run preview


def _chat_ids_for_device(con: sqlite3.Connection, device_id: int) -> list[int]:
    return [
        r[0] for r in con.execute(
            "SELECT id FROM chat WHERE device_id = ?", (device_id,)
        )
    ]


def _srcs_used_only_by(
    con: sqlite3.Connection, chat_ids: list[int]
) -> tuple[set[str], set[str]]:
    """Return (src_paths, content_hashes) that are referenced by the
    given chats and by NO message outside them.

    These are the files safe to delete from disk. Files shared with
    other chats are excluded — content-addressed dedup means deleting
    them would break those other chats.
    """
    if not chat_ids:
        return set(), set()
    placeholders = ",".join("?" * len(chat_ids))

    # Candidate srcs/hashes that this chat set uses.
    in_set_src = {
        r[0] for r in con.execute(
            f"""SELECT DISTINCT m.src FROM (
                    SELECT src, content_hash FROM media
                    WHERE message_id IN (
                        SELECT id FROM message WHERE chat_id IN ({placeholders})
                    )
                ) m WHERE m.src IS NOT NULL""",
            chat_ids,
        )
    }
    in_set_hash = {
        r[0] for r in con.execute(
            f"""SELECT DISTINCT content_hash FROM media
                WHERE content_hash IS NOT NULL
                  AND message_id IN (
                    SELECT id FROM message WHERE chat_id IN ({placeholders})
                  )""",
            chat_ids,
        )
    }

    # Of those, which are ALSO used by messages outside the chat set?
    # Those must be kept.
    shared_src: set[str] = set()
    if in_set_src:
        sp = ",".join("?" * len(in_set_src))
        shared_src = {
            r[0] for r in con.execute(
                f"""SELECT DISTINCT src FROM media
                    WHERE src IN ({sp})
                      AND message_id NOT IN (
                        SELECT id FROM message WHERE chat_id IN ({placeholders})
                      )""",
                (*in_set_src, *chat_ids),
            )
        }
    shared_hash: set[str] = set()
    if in_set_hash:
        hp = ",".join("?" * len(in_set_hash))
        shared_hash = {
            r[0] for r in con.execute(
                f"""SELECT DISTINCT content_hash FROM media
                    WHERE content_hash IN ({hp})
                      AND message_id NOT IN (
                        SELECT id FROM message WHERE chat_id IN ({placeholders})
                      )""",
                (*in_set_hash, *chat_ids),
            )
        }

    return (in_set_src - shared_src), (in_set_hash - shared_hash)


def _delete_file(rel_or_path: str, stats: PurgeStats, *, dry_run: bool) -> None:
    """Delete one media file given its web-relative src (or absolute path)."""
    root = project_root()
    p = Path(rel_or_path)
    if not p.is_absolute():
        p = root / rel_or_path
    try:
        if p.is_file():
            size = p.stat().st_size
            if dry_run:
                stats.deleted_paths.append(str(p))
            else:
                p.unlink()
            stats.files_deleted += 1
            stats.bytes_freed += size
    except OSError as e:
        stats.errors.append(f"{p}: {e}")


def _delete_originals(
    content_hashes: set[str], stats: PurgeStats, *, dry_run: bool
) -> None:
    """Delete originals whose filename starts with a now-unused hash.

    Originals live under ``originals/<prefix>/<hash>.<ext>``; we match
    by hash prefix on the filename stem.
    """
    root = originals_root()
    if not root.is_dir() or not content_hashes:
        return
    for h in content_hashes:
        if not h:
            continue
        prefix = h[:2]
        sub = root / prefix
        if not sub.is_dir():
            continue
        for f in sub.iterdir():
            if f.is_file() and f.stem.startswith(h):
                try:
                    size = f.stat().st_size
                    if dry_run:
                        stats.deleted_paths.append(str(f))
                    else:
                        f.unlink()
                    stats.originals_deleted += 1
                    stats.bytes_freed += size
                except OSError as e:
                    stats.errors.append(f"{f}: {e}")


def purge_chats(
    con: sqlite3.Connection,
    chat_ids: list[int],
    *,
    dry_run: bool = False,
) -> PurgeStats:
    """Remove the given chats: their messages, media rows, source_refs,
    participants, and the chat rows — plus every media/original file on
    disk no longer referenced by any remaining message.

    The caller owns the transaction (we don't commit on dry-run; on a
    real run we commit at the end). Pass a writable connection.
    """
    stats = PurgeStats()
    if not chat_ids:
        return stats
    placeholders = ",".join("?" * len(chat_ids))

    stats.chats = len(chat_ids)
    stats.messages = con.execute(
        f"SELECT COUNT(*) FROM message WHERE chat_id IN ({placeholders})",
        chat_ids,
    ).fetchone()[0]
    stats.media_rows = con.execute(
        f"""SELECT COUNT(*) FROM media WHERE message_id IN (
                SELECT id FROM message WHERE chat_id IN ({placeholders})
            )""",
        chat_ids,
    ).fetchone()[0]

    # Work out which files are safe to delete (not shared with other chats).
    del_srcs, del_hashes = _srcs_used_only_by(con, chat_ids)

    # Count shared files we're deliberately NOT deleting (for the report).
    total_srcs = {
        r[0] for r in con.execute(
            f"""SELECT DISTINCT src FROM media
                WHERE src IS NOT NULL AND message_id IN (
                    SELECT id FROM message WHERE chat_id IN ({placeholders})
                )""",
            chat_ids,
        )
    }
    stats.files_kept_shared = len(total_srcs - del_srcs)

    # Delete files first (so a crash leaves DB consistent-ish: orphan
    # rows are recoverable, orphan files are not).
    for src in del_srcs:
        _delete_file(src, stats, dry_run=dry_run)
    _delete_originals(del_hashes, stats, dry_run=dry_run)

    if dry_run:
        return stats

    # Then the DB rows.
    con.execute(
        f"""DELETE FROM media WHERE message_id IN (
                SELECT id FROM message WHERE chat_id IN ({placeholders})
            )""",
        chat_ids,
    )
    con.execute(
        f"""DELETE FROM source_ref WHERE message_id IN (
                SELECT id FROM message WHERE chat_id IN ({placeholders})
            )""",
        chat_ids,
    )
    con.execute(
        f"DELETE FROM message WHERE chat_id IN ({placeholders})", chat_ids
    )
    con.execute(
        f"DELETE FROM chat_participant WHERE chat_id IN ({placeholders})",
        chat_ids,
    )
    # chat_source is the per-chat adapter anchor (may not exist on old DBs).
    try:
        con.execute(
            f"DELETE FROM chat_source WHERE chat_id IN ({placeholders})",
            chat_ids,
        )
    except sqlite3.OperationalError:
        pass
    con.execute(f"DELETE FROM chat WHERE id IN ({placeholders})", chat_ids)
    con.commit()
    return stats


def purge_chat_by_slug(
    con: sqlite3.Connection, slug: str, *, dry_run: bool = False
) -> Optional[PurgeStats]:
    """Convenience: purge one chat by its slug. None if not found."""
    row = con.execute("SELECT id FROM chat WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        return None
    return purge_chats(con, [row[0]], dry_run=dry_run)


def purge_device(
    con: sqlite3.Connection, device_id: int, *, dry_run: bool = False
) -> PurgeStats:
    """Purge every chat of a device (files + rows), then the device row."""
    chat_ids = _chat_ids_for_device(con, device_id)
    stats = purge_chats(con, chat_ids, dry_run=dry_run)
    if not dry_run:
        con.execute("DELETE FROM device WHERE id = ?", (device_id,))
        con.commit()
    return stats
