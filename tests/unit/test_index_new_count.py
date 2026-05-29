# -*- coding: utf-8 -*-
"""
Characterization test: /api/index returns per chat
- total      = number of displayable messages
- new_count  = number of messages with sync_state='new'

Steps:
- create a fresh test DB with device + chat + 3 messages
  (1 sync_state='new', 2 sync_state='published').
- point server.app.DB at the test DB (before the test client starts).
- assert via fastapi.testclient.TestClient + GET /api/index.
"""
from __future__ import annotations

import os
import sys
import sqlite3
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


def _seed_chat_with_messages(con: sqlite3.Connection) -> int:
    """Create device + chat + 3 messages (1 new, 2 published)."""
    pid = con.execute(
        "INSERT INTO person(display_name) VALUES(?)", ("Owner",)
    ).lastrowid
    did = con.execute(
        "INSERT INTO device(slug,name,type,owner_person_id) VALUES(?,?,?,?)",
        ("mac_alice", "Mac", "mac_live", pid),
    ).lastrowid
    chat_id = con.execute(
        """INSERT INTO chat(slug,device_id,title,subtitle,is_group,origin)
           VALUES(?,?,?,?,0,'apple')""",
        ("mac_alice/sample", did, "Sample", "sub"),
    ).lastrowid
    # imessage_live anchor (so the chat is flagged live in /api/index).
    con.execute(
        "INSERT INTO chat_source(chat_id, source, external_id) VALUES(?, 'imessage_live:mac_alice', ?)",
        (chat_id, "42"),
    )
    base_ts = 1_700_000_000
    rows = [
        # (sync_state, ts, is_me, text)
        ("new",       base_ts + 30, 0, "neu"),
        ("published", base_ts + 20, 1, "alt2"),
        ("published", base_ts + 10, 0, "alt1"),
    ]
    for state, ts, is_me, text in rows:
        con.execute(
            """INSERT INTO message(chat_id,sender_person_id,ts,is_me,
                   text,media_status,sync_state)
               VALUES(?,?,?,?,?,?,?)""",
            (chat_id, pid, ts, is_me, text, "none", state),
        )
    con.commit()
    return chat_id


@pytest.fixture
def index_client(tmp_visualizer_db, visualizer_db_path, tmp_path):
    _seed_chat_with_messages(tmp_visualizer_db)
    # Close before app construction so the server takes its own RO connection.
    tmp_visualizer_db.close()

    # Since Phase 0.4: no more monkeypatching, we build a config directly.
    # The test DB lives in tmp_path; all other paths stay default.
    from msgviz.config import MVConfig
    from msgviz.server.factory import create_app

    cfg = MVConfig(
        db_file=visualizer_db_path,
        enable_watcher=False,  # no live sync against ~/Library/Messages in tests
    )
    app = create_app(cfg)

    # We don't start the TestClient as a context manager, so the
    # startup hook doesn't run (also covered by enable_watcher=False).
    from fastapi.testclient import TestClient
    return TestClient(app)


def test_api_index_reports_new_count_and_total(index_client):
    resp = index_client.get("/api/index")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    chats = data["chats"]
    assert len(chats) == 1, f"expected exactly 1 chat, got {len(chats)}"
    c = chats[0]
    assert c["slug"] == "mac_alice/sample"
    assert c["total"] == 3, f"total: expected 3, got {c['total']}"
    assert c["new_count"] == 1, f"new_count: expected 1, got {c['new_count']}"
