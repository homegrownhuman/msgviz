# -*- coding: utf-8 -*-
"""
Regression guard: the source anchor and origin tag must agree.

Background:
  - `chat.origin` (e.g. 'whatsapp', 'apple') describes the service.
  - `chat_source.source` (e.g. 'imessage_live:mac_alice') describes the
    concrete incremental source instance.

The formal answer to "can a WhatsApp chat carry an imessage_live anchor?"
is **no**. We check against the live DB.

The test is defensive:
  - Skipped if the live DB is missing.
  - 100% read-only.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
LIVE_DB = ROOT / "data" / "visualizer.db"


def _connect_ro() -> sqlite3.Connection | None:
    if not LIVE_DB.exists():
        return None
    con = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def test_whatsapp_chats_have_no_imessage_chat_source():
    con = _connect_ro()
    if con is None:
        pytest.skip("live DB missing — sanity check skipped")
    bad = con.execute(
        """SELECT c.slug, cs.source FROM chat c
           JOIN chat_source cs ON cs.chat_id = c.id
           WHERE c.origin = 'whatsapp'
             AND cs.source LIKE 'imessage%'"""
    ).fetchall()
    con.close()
    assert not bad, (
        f"WhatsApp chats with an iMessage anchor: "
        + ", ".join(f"{r['slug']}→{r['source']}" for r in bad)
    )


def test_whatsapp_messages_have_no_imessage_source_ref():
    con = _connect_ro()
    if con is None:
        pytest.skip("live DB missing — sanity check skipped")
    n = con.execute(
        """SELECT COUNT(*) FROM source_ref sr
           JOIN message m ON m.id = sr.message_id
           JOIN chat c    ON c.id = m.chat_id
           WHERE c.origin = 'whatsapp'
             AND sr.source LIKE 'imessage%'"""
    ).fetchone()[0]
    con.close()
    assert n == 0, (
        f"{n} WhatsApp messages have an iMessage source_ref. "
        "This indicates an accidental cross-mix of source tags."
    )


def test_apple_origin_chats_either_have_imessage_anchor_or_no_chat_source():
    """If a chat has a chat_source it must be consistent with origin.
    For origin='apple' we expect either no anchor at all (bulk import from
    an iMessage backup, e.g. an old Mac backup) or an imessage_live:* anchor.
    """
    con = _connect_ro()
    if con is None:
        pytest.skip("live DB missing — sanity check skipped")
    bad = con.execute(
        """SELECT c.slug, cs.source FROM chat c
           JOIN chat_source cs ON cs.chat_id = c.id
           WHERE c.origin = 'apple'
             AND cs.source NOT LIKE 'imessage%'"""
    ).fetchall()
    con.close()
    assert not bad, (
        f"Apple chats with a non-iMessage anchor: "
        + ", ".join(f"{r['slug']}→{r['source']}" for r in bad)
    )
