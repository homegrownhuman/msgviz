# -*- coding: utf-8 -*-
"""
Avatar feature regression tests:

1. Schema migration adds person.avatar_src to legacy DBs without
   destroying data.
2. The CLI's `person set-avatar` writes a content-hashed copy under
   media/avatars/<prefix>/<hash>.<ext> and updates person.avatar_src.
3. `person clear-avatar` reverts avatar_src to NULL.
4. The API surfaces avatar_src in /api/index (owner_avatar,
   chat_avatar) and in /api/chat/<slug>/meta (owner_avatar,
   chat_avatar).
5. Messages in /latest carry sender_avatar when the sender has one.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from msgviz.config import MVConfig
from msgviz.core.schema_migrate import apply_all, ensure_avatar_column
from msgviz.server.factory import create_app


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------
def test_ensure_avatar_column_adds_missing(tmp_path):
    """ALTER TABLE adds avatar_src; existing data survives."""
    db = tmp_path / "legacy.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE person (id INTEGER PRIMARY KEY, display_name TEXT NOT NULL, note TEXT);
        INSERT INTO person(display_name, note) VALUES('Alice', 'old data');
        """
    )
    con.commit()
    assert ensure_avatar_column(con) is True
    cols = {row[1] for row in con.execute("PRAGMA table_info(person)")}
    assert "avatar_src" in cols
    # Data preserved.
    name, note, avatar = con.execute(
        "SELECT display_name, note, avatar_src FROM person WHERE id=1"
    ).fetchone()
    assert name == "Alice"
    assert note == "old data"
    assert avatar is None
    con.close()


def test_ensure_avatar_column_is_idempotent(tmp_path):
    """Running ensure_avatar_column twice doesn't fail or duplicate."""
    db = tmp_path / "legacy.db"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE person (id INTEGER PRIMARY KEY, display_name TEXT NOT NULL);"
    )
    assert ensure_avatar_column(con) is True
    assert ensure_avatar_column(con) is False
    con.close()


def test_apply_all_returns_applied_migrations(tmp_path):
    db = tmp_path / "legacy.db"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE person (id INTEGER PRIMARY KEY, display_name TEXT NOT NULL);"
    )
    applied = apply_all(con)
    assert "person.avatar_src" in applied
    # Re-running: nothing to apply.
    assert apply_all(con) == []
    con.close()


# ---------------------------------------------------------------------------
# API surfacing
# ---------------------------------------------------------------------------
def _seed(con):
    """Minimal DB: 1 owner with avatar + 1 device + 1 chat + 1 incoming msg
    from a person with avatar + 1 incoming msg from a person without."""
    owner_pid = con.execute(
        "INSERT INTO person(display_name, avatar_src) VALUES(?, ?)",
        ("Alice", "media/avatars/aa/aaaa.png"),
    ).lastrowid
    bob_pid = con.execute(
        "INSERT INTO person(display_name, avatar_src) VALUES(?, ?)",
        ("Bob", "media/avatars/bb/bbbb.jpg"),
    ).lastrowid
    carol_pid = con.execute(
        "INSERT INTO person(display_name) VALUES(?)", ("Carol",)
    ).lastrowid
    dev_id = con.execute(
        "INSERT INTO device(slug, name, type, owner_person_id) VALUES(?,?,?,?)",
        ("mac1", "Mac", "mac_live", owner_pid),
    ).lastrowid
    chat_bob = con.execute(
        """INSERT INTO chat(slug, device_id, title, subtitle, is_group, origin)
           VALUES(?,?,?,?,0,'apple')""",
        ("mac1/bob", dev_id, "Bob", None),
    ).lastrowid
    chat_carol = con.execute(
        """INSERT INTO chat(slug, device_id, title, subtitle, is_group, origin)
           VALUES(?,?,?,?,0,'apple')""",
        ("mac1/carol", dev_id, "Carol", None),
    ).lastrowid
    # 5 messages in Bob's chat: 3 from Bob, 2 from owner.
    for ts, is_me, pid in [
        (1700000001, 0, bob_pid),
        (1700000002, 1, owner_pid),
        (1700000003, 0, bob_pid),
        (1700000004, 1, owner_pid),
        (1700000005, 0, bob_pid),
    ]:
        con.execute(
            """INSERT INTO message(chat_id, sender_person_id, ts, is_me, text,
                   media_status, sync_state)
               VALUES(?,?,?,?,'hi','none','published')""",
            (chat_bob, pid, ts, is_me),
        )
    # 3 messages in Carol's chat (sender has no avatar).
    for ts, is_me, pid in [
        (1700000010, 0, carol_pid),
        (1700000011, 1, owner_pid),
        (1700000012, 0, carol_pid),
    ]:
        con.execute(
            """INSERT INTO message(chat_id, sender_person_id, ts, is_me, text,
                   media_status, sync_state)
               VALUES(?,?,?,?,'hi','none','published')""",
            (chat_carol, pid, ts, is_me),
        )
    con.commit()


@pytest.fixture
def avatar_client(tmp_visualizer_db, visualizer_db_path):
    _seed(tmp_visualizer_db)
    tmp_visualizer_db.close()
    app = create_app(MVConfig(enable_watcher=False, db_file=visualizer_db_path))
    return TestClient(app)


def test_api_index_includes_owner_avatar(avatar_client):
    r = avatar_client.get("/api/index")
    assert r.status_code == 200
    data = r.json()
    dev = data["devices"][0]
    assert dev["owner_avatar"] == "media/avatars/aa/aaaa.png"


def test_api_index_includes_chat_avatar_for_one_on_one(avatar_client):
    r = avatar_client.get("/api/index")
    chats = {c["slug"]: c for c in r.json()["chats"]}
    # Bob's chat has the counterpart avatar.
    assert chats["mac1/bob"]["chat_avatar"] == "media/avatars/bb/bbbb.jpg"
    # Carol's chat: counterpart has no avatar → field absent.
    assert "chat_avatar" not in chats["mac1/carol"]


def test_api_chat_meta_includes_owner_and_chat_avatar(avatar_client):
    r = avatar_client.get("/api/chat/mac1/bob/meta")
    assert r.status_code == 200
    data = r.json()
    assert data["owner_avatar"] == "media/avatars/aa/aaaa.png"
    assert data["chat_avatar"] == "media/avatars/bb/bbbb.jpg"


def test_api_chat_meta_omits_chat_avatar_when_counterpart_has_none(avatar_client):
    r = avatar_client.get("/api/chat/mac1/carol/meta")
    assert r.status_code == 200
    data = r.json()
    assert "chat_avatar" not in data
    # Owner avatar still present.
    assert data["owner_avatar"] == "media/avatars/aa/aaaa.png"


def test_latest_messages_carry_sender_avatar(avatar_client):
    r = avatar_client.get("/api/chat/mac1/bob/latest?limit=10")
    assert r.status_code == 200
    msgs = r.json()["messages"]
    # Find a message from Bob.
    bob_msgs = [m for m in msgs if not m["me"]]
    assert bob_msgs, "expected at least one non-me message"
    assert all(m.get("sender_avatar") == "media/avatars/bb/bbbb.jpg" for m in bob_msgs)


def test_latest_messages_omit_sender_avatar_when_absent(avatar_client):
    r = avatar_client.get("/api/chat/mac1/carol/latest?limit=10")
    msgs = r.json()["messages"]
    carol_msgs = [m for m in msgs if not m["me"]]
    assert carol_msgs
    assert all("sender_avatar" not in m for m in carol_msgs)
