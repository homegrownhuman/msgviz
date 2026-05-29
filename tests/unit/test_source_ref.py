# -*- coding: utf-8 -*-
"""
Spec for `source_ref`.

After the refactor:

- `message` no longer has an `apple_rowid` column.
- `chat` no longer has an `apple_chat_id` column.
- The new `source_ref(message_id, source, external_id)` table carries
  the dedup anchor for incremental sources (today only `imessage_live`).
- Bulk importers (WhatsApp export, iMessage backup imports) do NOT write
  `source_ref` rows — their idempotency hangs on the re-import
  "clear-the-chat-and-rebuild" path.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def _table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def test_message_has_no_apple_rowid(tmp_visualizer_db):
    cols = _table_columns(tmp_visualizer_db, "message")
    assert "apple_rowid" not in cols, (
        "message.apple_rowid still exists — refactor not complete"
    )


def test_chat_has_no_apple_chat_id(tmp_visualizer_db):
    cols = _table_columns(tmp_visualizer_db, "chat")
    assert "apple_chat_id" not in cols, (
        "chat.apple_chat_id still exists — refactor not complete"
    )


def test_source_ref_table_exists_with_expected_columns(tmp_visualizer_db):
    cols = _table_columns(tmp_visualizer_db, "source_ref")
    assert {"message_id", "source", "external_id"}.issubset(cols), (
        f"source_ref columns incomplete: {cols}"
    )


def _seed_chat(con, slug="d/x/c"):
    """Helper: insert person/device/chat, return (chat_id, person_id)."""
    p = con.execute("INSERT INTO person(display_name) VALUES('Me')").lastrowid
    dev = con.execute(
        "INSERT INTO device(slug,name,type,owner_person_id) VALUES(?,?,?,?)",
        ("d/x", "D", "mac_live", p),
    ).lastrowid
    chat = con.execute(
        """INSERT INTO chat(slug,device_id,title,subtitle,is_group,origin)
           VALUES(?,?,?,?,?,?)""",
        (slug, dev, "Chat", None, 0, "apple"),
    ).lastrowid
    return chat, p


def test_source_ref_dedup_constraint(tmp_visualizer_db):
    """(source, external_id) must be UNIQUE so duplicate links can never
    form in the first place."""
    con = tmp_visualizer_db
    chat, p = _seed_chat(con)
    m1 = con.execute(
        """INSERT INTO message(chat_id,sender_person_id,ts,is_me,text,sync_state,media_status)
           VALUES(?,?,1000,1,'a','published','none')""", (chat, p)
    ).lastrowid
    m2 = con.execute(
        """INSERT INTO message(chat_id,sender_person_id,ts,is_me,text,sync_state,media_status)
           VALUES(?,?,2000,0,'b','published','none')""", (chat, p)
    ).lastrowid
    con.execute(
        "INSERT INTO source_ref(message_id,source,external_id) VALUES(?,?,?)",
        (m1, "imessage_live", "42"),
    )
    # Same (source, external_id) again → must fail.
    import pytest as _pt
    with _pt.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO source_ref(message_id,source,external_id) VALUES(?,?,?)",
            (m2, "imessage_live", "42"),
        )


def test_source_ref_cascades_on_message_delete(tmp_visualizer_db):
    """When a message is deleted, the source_ref anchor goes with it."""
    con = tmp_visualizer_db
    con.execute("PRAGMA foreign_keys = ON")
    chat, p = _seed_chat(con, slug="d/y/c")
    m = con.execute(
        """INSERT INTO message(chat_id,sender_person_id,ts,is_me,text,sync_state,media_status)
           VALUES(?,?,1000,1,'a','published','none')""", (chat, p)
    ).lastrowid
    con.execute(
        "INSERT INTO source_ref(message_id,source,external_id) VALUES(?,?,?)",
        (m, "imessage_live", "99"),
    )
    con.execute("DELETE FROM message WHERE id=?", (m,))
    n = con.execute(
        "SELECT COUNT(*) FROM source_ref WHERE message_id=?", (m,)
    ).fetchone()[0]
    assert n == 0, "source_ref row should be removed when the message is deleted"
