# -*- coding: utf-8 -*-
"""
Unit tests for msgviz.core.purge.

The critical property: removing a chat deletes its media FILES from
disk, but a content-addressed file shared with another chat is kept
until the last referencing chat is gone. Plus dry-run touches nothing.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from msgviz.core import purge


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Temp MSGVIZ_HOME with a schema'd DB and two chats sharing media."""
    monkeypatch.setenv("MSGVIZ_HOME", str(tmp_path))
    from msgviz.paths import db_file, schema_sql, data_dir, project_root
    data_dir().mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_file())
    con.executescript(Path(schema_sql()).read_text())

    pid = con.execute("INSERT INTO person(display_name) VALUES('Me')").lastrowid
    did = con.execute(
        "INSERT INTO device(slug,name,type,owner_person_id) "
        "VALUES('d','D','mac_live',?)", (pid,)
    ).lastrowid
    cA = con.execute(
        "INSERT INTO chat(slug,device_id,title,is_group,origin) "
        "VALUES('d/a',?,'A',0,'whatsapp')", (did,)
    ).lastrowid
    cB = con.execute(
        "INSERT INTO chat(slug,device_id,title,is_group,origin) "
        "VALUES('d/b',?,'B',0,'whatsapp')", (did,)
    ).lastrowid

    def msg(chat):
        return con.execute(
            "INSERT INTO message(chat_id,sender_person_id,ts,is_me,"
            "media_status,sync_state) VALUES(?,?,?,0,'ready','published')",
            (chat, pid, 1),
        ).lastrowid

    mA1, mA2, mB1 = msg(cA), msg(cA), msg(cB)

    root = project_root()
    excl = "media/images/aa/aaexcl.jpg"
    shared = "media/videos/bb/bbshared.mp4"
    for rel in (excl, shared):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 100)

    con.execute(
        "INSERT INTO media(message_id,kind,src,content_hash,done,bytes) "
        "VALUES(?,?,?,?,1,100)", (mA1, "image", excl, "aaexcl"))
    con.execute(
        "INSERT INTO media(message_id,kind,src,content_hash,done,bytes) "
        "VALUES(?,?,?,?,1,100)", (mA2, "video", shared, "bbshared"))
    con.execute(
        "INSERT INTO media(message_id,kind,src,content_hash,done,bytes) "
        "VALUES(?,?,?,?,1,100)", (mB1, "video", shared, "bbshared"))
    con.commit()

    return {
        "con": con, "root": root, "excl": excl, "shared": shared,
        "cA": cA, "cB": cB, "mB1": mB1,
    }


def test_dry_run_touches_nothing(db) -> None:
    con, root = db["con"], db["root"]
    st = purge.purge_chat_by_slug(con, "d/a", dry_run=True)
    assert st.messages == 2
    assert st.media_rows == 2
    assert st.files_deleted == 1          # the exclusive file
    assert st.files_kept_shared == 1      # the shared video
    # Nothing actually removed.
    assert (root / db["excl"]).exists()
    assert (root / db["shared"]).exists()
    assert con.execute("SELECT COUNT(*) FROM chat WHERE slug='d/a'").fetchone()[0] == 1


def test_purge_deletes_exclusive_keeps_shared(db) -> None:
    con, root = db["con"], db["root"]
    st = purge.purge_chat_by_slug(con, "d/a")
    assert st.files_deleted == 1
    assert st.files_kept_shared == 1
    # Exclusive file gone, shared file survives (chat B still uses it).
    assert not (root / db["excl"]).exists()
    assert (root / db["shared"]).exists()
    # Chat A rows gone, chat B media intact.
    assert con.execute("SELECT COUNT(*) FROM chat WHERE slug='d/a'").fetchone()[0] == 0
    assert con.execute(
        "SELECT COUNT(*) FROM media WHERE message_id=?", (db["mB1"],)
    ).fetchone()[0] == 1


def test_purge_last_referencer_removes_shared(db) -> None:
    con, root = db["con"], db["root"]
    purge.purge_chat_by_slug(con, "d/a")
    assert (root / db["shared"]).exists()      # still there after A
    purge.purge_chat_by_slug(con, "d/b")
    assert not (root / db["shared"]).exists()  # gone after the last ref


def test_purge_removes_source_refs(db) -> None:
    con = db["con"]
    # Add a source_ref to a chat-A message, then purge → it must go.
    mid = con.execute(
        "SELECT id FROM message WHERE chat_id=? LIMIT 1", (db["cA"],)
    ).fetchone()[0]
    con.execute(
        "INSERT INTO source_ref(message_id,source,external_id) "
        "VALUES(?,?,?)", (mid, "whatsapp_live:d", "STANZA-X"))
    con.commit()
    purge.purge_chat_by_slug(con, "d/a")
    assert con.execute(
        "SELECT COUNT(*) FROM source_ref WHERE external_id='STANZA-X'"
    ).fetchone()[0] == 0


def test_purge_unknown_slug_returns_none(db) -> None:
    assert purge.purge_chat_by_slug(db["con"], "d/nope") is None


def test_purge_device_removes_all_and_device_row(db) -> None:
    con, root = db["con"], db["root"]
    did = con.execute("SELECT id FROM device WHERE slug='d'").fetchone()[0]
    st = purge.purge_device(con, did)
    assert st.chats == 2
    # Both files gone (whole device removed → nothing else references them).
    assert not (root / db["excl"]).exists()
    assert not (root / db["shared"]).exists()
    assert con.execute("SELECT COUNT(*) FROM device WHERE id=?", (did,)).fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM chat").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM message").fetchone()[0] == 0


def test_purge_handles_missing_file_gracefully(db) -> None:
    con, root = db["con"], db["root"]
    # Delete the exclusive file out from under us first.
    (root / db["excl"]).unlink()
    st = purge.purge_chat_by_slug(con, "d/a")
    # No crash; it just doesn't count a missing file as deleted.
    assert st.errors == []
    assert con.execute("SELECT COUNT(*) FROM chat WHERE slug='d/a'").fetchone()[0] == 0


def test_bytes_freed_reported(db) -> None:
    con = db["con"]
    st = purge.purge_chat_by_slug(con, "d/a")
    assert st.bytes_freed == 100          # the one exclusive 100-byte file
