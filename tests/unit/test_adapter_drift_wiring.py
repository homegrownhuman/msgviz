# -*- coding: utf-8 -*-
"""
Phase-5 tests: the schema/format probe is wired into every adapter's
read path.

Covers IMessageLiveAdapter, IMessageBackupAdapter, and
WhatsAppExportAdapter — that open() runs the contract, forwards drift
to on_drift, stashes last_report, and raises SchemaDriftError on fatal.
(WhatsAppLiveAdapter's wiring is covered in test_whatsapp_live.py.)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from msgviz.adapters.imessage_live import IMessageLiveAdapter
from msgviz.adapters.imessage_backup import IMessageBackupAdapter
from msgviz.adapters.whatsapp_export import WhatsAppExportAdapter
from msgviz.core import drift

FIX = Path(__file__).resolve().parents[1] / "fixtures"
CHATDB = FIX / "sample_chat.db"
WA_EXPORT = FIX / "sample_whatsapp"


def _broken_chatdb(path: Path) -> None:
    """Write a chat.db missing a required `message` column."""
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY, guid TEXT, attributedBody BLOB,
            handle_id INTEGER, date INTEGER, is_from_me INTEGER,
            cache_has_attachments INTEGER, associated_message_type INTEGER,
            associated_message_guid TEXT, balloon_bundle_id TEXT,
            message_summary_info BLOB
        );  -- 'text' (required) omitted
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT,
            service_name TEXT, display_name TEXT, style INTEGER);
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE attachment (ROWID INTEGER PRIMARY KEY, filename TEXT,
            mime_type TEXT, transfer_name TEXT, is_sticker INTEGER);
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        CREATE TABLE message_attachment_join (
            message_id INTEGER, attachment_id INTEGER);
    """)
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# iMessage live
# ---------------------------------------------------------------------------

def test_imessage_live_open_clean_on_healthy_db() -> None:
    events: list[drift.DriftEvent] = []
    a = IMessageLiveAdapter(str(CHATDB), "mac_test", "Me", on_drift=events.append)
    report = a.open()
    assert report.is_fatal is False
    assert a.last_report is report
    assert events == []
    a.close()


def test_imessage_live_list_chats_runs_probe() -> None:
    a = IMessageLiveAdapter(str(CHATDB), "mac_test", "Me")
    assert a.last_report is None
    list(a.list_chats())
    assert a.last_report is not None
    a.close()


def test_imessage_live_open_raises_on_fatal(tmp_path) -> None:
    broken = tmp_path / "broken.db"
    _broken_chatdb(broken)
    events: list[drift.DriftEvent] = []
    a = IMessageLiveAdapter(str(broken), "mac_test", "Me", on_drift=events.append)
    with pytest.raises(drift.SchemaDriftError):
        a.open()
    assert any(e.severity == "fatal" for e in events)
    # The fatal event is tagged with the live source.
    assert all(e.source == "imessage_live" for e in events if e.severity == "fatal")
    a.close()


# ---------------------------------------------------------------------------
# iMessage backup
# ---------------------------------------------------------------------------

def test_imessage_backup_open_clean_and_source_tag() -> None:
    events: list[drift.DriftEvent] = []
    b = IMessageBackupAdapter(
        str(CHATDB), "/tmp", "mac_test", "Me", on_drift=events.append
    )
    report = b.open()
    assert report.is_fatal is False
    b.close()


def test_imessage_backup_open_raises_on_fatal_with_backup_source(tmp_path) -> None:
    broken = tmp_path / "broken.db"
    _broken_chatdb(broken)
    events: list[drift.DriftEvent] = []
    b = IMessageBackupAdapter(
        str(broken), "/tmp", "mac_test", "Me", on_drift=events.append
    )
    with pytest.raises(drift.SchemaDriftError):
        b.open()
    # Same schema, but recorded under the backup source tag.
    assert any(
        e.severity == "fatal" and e.source == "imessage_backup"
        for e in events
    )
    b.close()


# ---------------------------------------------------------------------------
# WhatsApp export
# ---------------------------------------------------------------------------

def test_export_open_clean_on_german_fixture() -> None:
    events: list[drift.DriftEvent] = []
    e = WhatsAppExportAdapter(
        str(WA_EXPORT), "wa1", "Test", "Owner", on_drift=events.append
    )
    report = e.open()
    assert report.is_fatal is False
    assert e.last_report is report


def test_export_iter_messages_runs_probe() -> None:
    e = WhatsAppExportAdapter(str(WA_EXPORT), "wa1", "Test", "Owner")
    assert e.last_report is None
    chat = next(iter(e.list_chats()))
    list(e.iter_messages(chat))
    assert e.last_report is not None


def test_export_open_raises_on_unknown_format(tmp_path) -> None:
    # An export folder whose _chat.txt is in a format we don't parse.
    folder = tmp_path / "weird_export"
    folder.mkdir()
    (folder / "_chat.txt").write_text(
        "2018-05-10T12:30:00 Owner: hey\n"
        "2018-05-10T12:30:15 Alice: hi\n",
        encoding="utf-8",
    )
    events: list[drift.DriftEvent] = []
    e = WhatsAppExportAdapter(
        str(folder), "wa2", "Weird", "Owner", on_drift=events.append
    )
    with pytest.raises(drift.SchemaDriftError):
        e.open()
    assert any(
        e_.kind == "unknown_export_format" and e_.severity == "fatal"
        for e_ in events
    )


def test_export_iter_messages_aborts_on_unknown_format(tmp_path) -> None:
    # The fatal must fire through the iter_messages path too, so a real
    # import never silently mis-parses.
    folder = tmp_path / "weird_export2"
    folder.mkdir()
    (folder / "_chat.txt").write_text(
        "2018-05-10T12:30:00 Owner: hey\n",
        encoding="utf-8",
    )
    e = WhatsAppExportAdapter(str(folder), "wa3", "Weird", "Owner")
    chat = next(iter(e.list_chats()))
    with pytest.raises(drift.SchemaDriftError):
        list(e.iter_messages(chat))
