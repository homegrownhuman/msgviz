# -*- coding: utf-8 -*-
"""
Integration tests for IMessageLiveAdapter and IMessageBackupAdapter.

Both adapters read an Apple chat.db. The difference:
- IMessageLiveAdapter starts from ~/Library/Messages/chat.db and resolves
  attachments by filesystem path (where they live today).
- IMessageBackupAdapter takes a snapshot path to the chat.db AND a path
  to its Attachments directory — `attachment.filename` begins with
  `~/Library/Messages/...` and we redirect that to the backup path.

We test against the sample chat.db, which mirrors a modern Apple DB schema.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def sample_attachment_root(tmp_path, sample_chat_db_path):
    """Lays out a minimal 'Attachments' directory matching the relative
    path stored in the sample chat.db."""
    # Build the expected file: sample_chat_db has ONE attachment.
    import sqlite3
    con = sqlite3.connect(f"file:{sample_chat_db_path}?mode=ro", uri=True)
    row = con.execute("SELECT filename FROM attachment LIMIT 1").fetchone()
    con.close()
    assert row is not None and row[0]
    rel = row[0]
    # rel may be '~/Library/Messages/...' or absolute — we test both
    # adapters, so we drop the file in BOTH places.
    targets = []
    if rel.startswith("~/Library/Messages/"):
        # Live variant: the file would sit under the expanded ~/.
        # In tests we don't want to write into the real HOME. Instead we
        # patch the adapter via its attachments_root.
        pass
    # A sample file under tmp_path / Attachments / <rel-without-leading-paths>.
    sub = rel.lstrip("~/").lstrip("/")
    if sub.startswith("Library/Messages/"):
        sub = sub[len("Library/Messages/"):]
    dst = tmp_path / sub
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(b"FAKEIMG")
    targets.append(dst)
    return tmp_path  # = the "Library/Messages" equivalent


def test_backup_adapter_iterates_messages(sample_chat_db_path, sample_attachment_root):
    from msgviz.adapters.imessage_backup import IMessageBackupAdapter
    a = IMessageBackupAdapter(
        db_path=str(sample_chat_db_path),
        attachments_root=str(sample_attachment_root),
        device_slug="testdev",
    )
    chats = list(a.list_chats())
    assert len(chats) == 1
    c = chats[0]
    assert c.origin == "apple"

    msgs = list(a.iter_messages(c))
    # Sample DB has 8 messages, of which 1 is a tapback -> 7 real messages.
    assert len(msgs) >= 5, f"expected ≥5 messages, got {len(msgs)}"
    # external_id is set (for source_ref).
    assert all(m.external_id for m in msgs)
    # At least one message with an attachment.
    with_att = [m for m in msgs if m.attachments]
    assert with_att


def test_backup_adapter_resolves_attachment(sample_chat_db_path, sample_attachment_root):
    """The Apple-DB attachment path is remapped to the backup location."""
    from msgviz.adapters.imessage_backup import IMessageBackupAdapter
    a = IMessageBackupAdapter(
        db_path=str(sample_chat_db_path),
        attachments_root=str(sample_attachment_root),
        device_slug="testdev",
    )
    chat = next(iter(a.list_chats()))
    msgs = list(a.iter_messages(chat))
    att = next((att for m in msgs for att in m.attachments), None)
    assert att is not None
    p = a.resolve_attachment(att.source_ref)
    assert p is not None and p.exists()


def test_backup_adapter_supports_incremental_is_false(sample_chat_db_path, tmp_path):
    """iMessage backup is a snapshot, not a live stream."""
    from msgviz.adapters.imessage_backup import IMessageBackupAdapter
    a = IMessageBackupAdapter(
        db_path=str(sample_chat_db_path),
        attachments_root=str(tmp_path),
        device_slug="testdev",
    )
    assert a.supports_incremental is False


def test_live_adapter_name_and_incremental():
    """The live adapter carries supports_incremental=True and name='imessage_live'."""
    from msgviz.adapters.imessage_live import IMessageLiveAdapter
    # Without a DB path: the adapter must still be constructible;
    # list_chats is not called in this test (no real live DB available).
    a = IMessageLiveAdapter(db_path="/nonexistent", device_slug="mac_test")
    assert a.supports_incremental is True
    assert a.name == "imessage_live"
