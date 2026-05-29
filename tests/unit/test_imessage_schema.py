# -*- coding: utf-8 -*-
"""
Unit tests for msgviz.adapters.imessage_schema.

Probes the iMessage contract against the synthetic sample_chat.db
fixture (real Apple chat.db shape) and verifies:

* a healthy chat.db produces ZERO drift (the §13.11 "don't cry wolf"
  calibration — Apple's wide tables must not spam new_column),
* removing a column the reader depends on is still fatal,
* both adapter source tags share the schema but record under their
  own source.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from msgviz.adapters import imessage_schema as ims
from msgviz.core import drift

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_chat.db"


@pytest.fixture()
def con():
    assert FIXTURE.exists(), "sample_chat.db fixture missing"
    c = sqlite3.connect(f"file:{FIXTURE}?mode=ro", uri=True)
    yield c
    c.close()


def test_healthy_chatdb_zero_drift(con) -> None:
    # The whole point of §13.11: a normal Mac's chat.db must not light
    # up the banner. Apple's wide tables (message ~60 cols, we read 14)
    # have flag_new_columns=False, so unlisted columns are not drift.
    report = drift.probe_tables(con, ims.CONTRACT_LIVE)
    assert report.fatal_count == 0
    assert report.warn_count == 0, [
        (e.kind, e.table, e.column) for e in report.events
    ]


def test_backup_contract_also_clean(con) -> None:
    report = drift.probe_tables(con, ims.CONTRACT_BACKUP)
    assert report.fatal_count == 0
    assert report.warn_count == 0


def test_source_tag_distinguishes_live_vs_backup() -> None:
    assert ims.CONTRACT_LIVE.source == "imessage_live"
    assert ims.CONTRACT_BACKUP.source == "imessage_backup"
    # ...but the same table shape underneath.
    assert ims.CONTRACT_LIVE.tables is ims.CONTRACT_BACKUP.tables


def test_removing_required_column_is_fatal() -> None:
    mem = sqlite3.connect(":memory:")
    mem.executescript("""
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY, guid TEXT, attributedBody BLOB,
            handle_id INTEGER, date INTEGER, is_from_me INTEGER,
            cache_has_attachments INTEGER, associated_message_type INTEGER,
            associated_message_guid TEXT, balloon_bundle_id TEXT,
            message_summary_info BLOB
        );  -- 'text' (required) deliberately omitted
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT,
            service_name TEXT, display_name TEXT, style INTEGER);
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE attachment (ROWID INTEGER PRIMARY KEY, filename TEXT,
            mime_type TEXT, transfer_name TEXT, is_sticker INTEGER);
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        CREATE TABLE message_attachment_join (
            message_id INTEGER, attachment_id INTEGER);
    """)
    report = drift.probe_tables(mem, ims.CONTRACT_LIVE)
    assert report.is_fatal
    assert any(
        e.kind == "missing_required_column" and e.column == "text"
        for e in report.events
    )


def test_new_apple_column_not_flagged_on_wide_table() -> None:
    # A future macOS adds a column to `message`. flag_new_columns=False
    # there → no new_column drift (would otherwise nag every user).
    mem = sqlite3.connect(":memory:")
    mem.executescript("""
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT,
            attributedBody BLOB, handle_id INTEGER, date INTEGER,
            is_from_me INTEGER, cache_has_attachments INTEGER,
            associated_message_type INTEGER, associated_message_guid TEXT,
            balloon_bundle_id TEXT, message_summary_info BLOB,
            date_edited INTEGER, date_retracted INTEGER, service TEXT,
            subject TEXT, account TEXT, account_guid TEXT, error INTEGER,
            date_read INTEGER, date_delivered INTEGER, is_delivered INTEGER,
            is_read INTEGER, is_sent INTEGER, is_audio_message INTEGER,
            is_spam INTEGER, item_type INTEGER, group_title TEXT,
            group_action_type INTEGER, expressive_send_style_id TEXT,
            thread_originator_guid TEXT, thread_originator_part TEXT,
            payload_data BLOB, share_status INTEGER, share_direction INTEGER,
            reply_to_guid TEXT, destination_caller_id TEXT,
            brand_new_macos17_column TEXT
        );
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT,
            service_name TEXT, display_name TEXT, style INTEGER, guid TEXT,
            state INTEGER, room_name TEXT, group_id TEXT, is_archived INTEGER,
            last_addressed_handle TEXT, last_read_message_timestamp INTEGER,
            original_group_id TEXT);
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT,
            country TEXT, service TEXT, uncanonicalized_id TEXT,
            person_centric_id TEXT);
        CREATE TABLE attachment (ROWID INTEGER PRIMARY KEY, filename TEXT,
            mime_type TEXT, transfer_name TEXT, is_sticker INTEGER, uti TEXT,
            emoji_image_short_description TEXT, guid TEXT, created_date INTEGER,
            total_bytes INTEGER, is_outgoing INTEGER, hide_attachment INTEGER,
            original_guid TEXT);
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER,
            message_date INTEGER);
        CREATE TABLE message_attachment_join (
            message_id INTEGER, attachment_id INTEGER);
    """)
    report = drift.probe_tables(mem, ims.CONTRACT_LIVE)
    assert report.fatal_count == 0
    assert report.warn_count == 0, [
        (e.kind, e.table, e.column) for e in report.events
    ]
    assert not any(
        e.column == "brand_new_macos17_column" for e in report.events
    )
