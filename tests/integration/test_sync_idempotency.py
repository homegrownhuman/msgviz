# -*- coding: utf-8 -*-
"""
Integration test: core/sync.py is idempotent (dedup via source_ref).

Steps:
- core.sync.DB is pointed at a tmpdir DB (fresh schema).
- export_data.CONFIG is replaced with a synthetic config dict declaring
  one mac_live device backed by the sample chat.db.
- We migrate minimally: device + chat row + chat_source anchor
  (source='imessage_live', external_id=<chat-rowid in sample>).
- Run sync() once → N >= 1 messages in the DB.
- Run sync() a second time → NO new rows (idempotency).

Note: if the sample fixture isn't present or its structure changes, we
skip with a clear reason.
"""
from __future__ import annotations

import os
import sys
import sqlite3
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "v2"))


def _inspect_sample_chat(db_path: Path) -> dict:
    """Read from the sample chat.db: chat_rowid, handle value, and the
    number of 'real' messages (= rows without a tapback associated_message_type)."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    chat = con.execute(
        "SELECT ROWID AS rowid, chat_identifier FROM chat ORDER BY ROWID LIMIT 1"
    ).fetchone()
    if chat is None:
        con.close()
        raise RuntimeError("Sample chat.db has no chat row")
    # 'Real' messages in this chat: not a tapback, not a tapback-remove,
    # with text OR an attachment (mirrors the msg_payload filter in sync.py).
    rows = con.execute(
        """SELECT m.text, m.attributedBody, m.cache_has_attachments,
                  m.associated_message_type, m.balloon_bundle_id
           FROM message m
           JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
           WHERE cmj.chat_id = ?""",
        (chat["rowid"],),
    ).fetchall()
    real = 0
    for r in rows:
        amt = r["associated_message_type"] or 0
        if amt in (2000, 2001, 2002, 2003, 2004, 2005):
            continue
        if 3000 <= amt <= 3005:
            continue
        has_text = bool((r["text"] or "").strip()) or bool(r["attributedBody"])
        has_att = bool(r["cache_has_attachments"])
        has_app = bool(r["balloon_bundle_id"])
        if has_text or has_att or has_app:
            real += 1
    con.close()
    return {
        "chat_rowid": chat["rowid"],
        "chat_identifier": chat["chat_identifier"],
        "expected_real_msgs": real,
    }


@pytest.fixture
def patched_sync(tmp_visualizer_db, visualizer_db_path, sample_chat_db_path,
                 monkeypatch):
    """Import core.sync and patch DB + export_data.CONFIG."""
    if not sample_chat_db_path.is_file():
        pytest.skip(f"Sample chat.db missing: {sample_chat_db_path}")

    info = _inspect_sample_chat(sample_chat_db_path)

    # Import export_data + sync before patching.
    from msgviz.legacy import export_data as ex  # type: ignore

    # Synthetic config: one mac_live device using the sample chat.db,
    # with a single chat (apple_chat_id = sample chat rowid).
    fake_cfg = {
        "devices": [
            {
                "type": "mac_live",
                "id": "test-mac",
                "slug": "mac_test",
                "name": "Test Mac",
                "me_name": "Owner",
                "db": str(sample_chat_db_path),
                "chats": [
                    {"slug": "sample", "title": "Sample Chat",
                     "subtitle": info["chat_identifier"],
                     "is_group": False, "origin": "apple",
                     "source": "imessage_live",
                     "source_id": str(info["chat_rowid"])},
                ],
            }
        ],
        "people": {},
    }
    monkeypatch.setattr(ex, "CONFIG", fake_cfg, raising=True)
    monkeypatch.setattr(ex, "PERSON_BY_HANDLE", {}, raising=True)

    # Import core.sync after the config patch (or reload it), redirect DB.
    import msgviz.core.sync as sync_mod  # type: ignore
    monkeypatch.setattr(sync_mod, "DB", str(visualizer_db_path), raising=True)

    # Minimal migration into the test DB: owner person, device, chat row.
    con = tmp_visualizer_db
    owner_pid = con.execute(
        "INSERT INTO person(display_name) VALUES(?)", ("Owner",)
    ).lastrowid
    device_id = con.execute(
        "INSERT INTO device(slug,name,type,owner_person_id) VALUES(?,?,?,?)",
        ("mac_test", "Test Mac", "mac_live", owner_pid),
    ).lastrowid
    chat_id = con.execute(
        """INSERT INTO chat(slug,device_id,title,subtitle,is_group,origin)
           VALUES(?,?,?,?,0,'apple')""",
        ("mac_test/sample", device_id, "Sample Chat", info["chat_identifier"]),
    ).lastrowid
    # chat_source anchor so sync() recognizes the chat as live-iMessage.
    con.execute(
        "INSERT INTO chat_source(chat_id, source, external_id) VALUES(?, 'imessage_live:mac_test', ?)",
        (chat_id, str(info["chat_rowid"])),
    )
    con.commit()
    # IMPORTANT: close the visualizer.db connection so sync.py can open a
    # fresh connection with its own pragmas.
    con.close()

    return sync_mod, info


def test_sync_writes_expected_message_count(patched_sync, visualizer_db_path):
    sync_mod, info = patched_sync
    stats = sync_mod.sync(report_only=False)
    assert stats["new"] >= 1, "first sync should insert messages"

    con = sqlite3.connect(str(visualizer_db_path))
    con.row_factory = sqlite3.Row
    n = con.execute(
        "SELECT COUNT(*) FROM message WHERE chat_id=(SELECT id FROM chat WHERE slug=?)",
        ("mac_test/sample",),
    ).fetchone()[0]
    con.close()
    # Tapbacks are NOT stored as separate rows; they hang on the target
    # message as JSON reactions.
    assert n == info["expected_real_msgs"], (
        f"message count in DB ({n}) != real messages in sample "
        f"({info['expected_real_msgs']})"
    )


def test_sync_is_idempotent(patched_sync, visualizer_db_path):
    sync_mod, info = patched_sync

    first = sync_mod.sync(report_only=False)
    assert first["new"] >= 1

    con = sqlite3.connect(str(visualizer_db_path))
    n_after_first = con.execute("SELECT COUNT(*) FROM message").fetchone()[0]
    con.close()

    second = sync_mod.sync(report_only=False)
    assert second["new"] == 0, (
        f"second sync should have 0 new messages, got {second['new']}"
    )

    con = sqlite3.connect(str(visualizer_db_path))
    n_after_second = con.execute("SELECT COUNT(*) FROM message").fetchone()[0]
    con.close()

    assert n_after_first == n_after_second, (
        "idempotency violated: second sync changed the row count"
    )
