#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build a mini Apple chat.db fixture for the pytest suite.

Schema mirrors the layout of ~/Library/Messages/chat.db (not our
visualizer.db schema!), reduced to the columns export_data.py /
core/sync.py actually read.

Usage:
    python3 tests/fixtures/build_sample_chat_db.py

Writes: tests/fixtures/sample_chat.db (overwrites any existing file).

Apple epoch: 2001-01-01 UTC. Apple's date columns (date, date_edited,
date_retracted) are nanoseconds since that epoch.

Contents (see README):
    - 1 chat (+491701234567, iMessage)
    - 1 handle (+491701234567)
    - 7 messages: 3 from me + 4 from the other side, alternating,
      spread across 2 days
    - 1 message with an attachment (photo) + attachment row
    - 1 tapback (associated_message_type=2000) on message 1
    - 1 edited message (date_edited set, message_summary_info = empty blob)
"""
from __future__ import annotations

import datetime as _dt
import os
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "sample_chat.db"

# Apple epoch: 2001-01-01 UTC.
APPLE_EPOCH = _dt.datetime(2001, 1, 1, tzinfo=_dt.timezone.utc)


def apple_ns(dt: _dt.datetime) -> int:
    """Convert a tz-aware UTC datetime into Apple nanoseconds since 2001."""
    if dt.tzinfo is None:
        raise ValueError("dt must be tz-aware (use UTC)")
    delta = dt - APPLE_EPOCH
    return int(delta.total_seconds() * 1_000_000_000)


SCHEMA_SQL = """
-- Apple chat.db – reduced original layout (only the columns the visualizer
-- reads). Column types are intentionally loose (SQLite type affinity) so
-- the fixture stays compatible with both real and test values.

CREATE TABLE handle (
    ROWID            INTEGER PRIMARY KEY AUTOINCREMENT,
    id               TEXT,                 -- phone number or email
    country          TEXT,
    service          TEXT,                 -- 'iMessage' | 'SMS'
    uncanonicalized_id TEXT,
    person_centric_id TEXT
);

CREATE TABLE chat (
    ROWID                       INTEGER PRIMARY KEY AUTOINCREMENT,
    guid                        TEXT,
    style                       INTEGER,
    state                       INTEGER,
    account_id                  TEXT,
    properties                  BLOB,
    chat_identifier             TEXT,      -- '+491701234567' for 1:1
    service_name                TEXT,      -- 'iMessage'
    room_name                   TEXT,
    account_login               TEXT,
    is_archived                 INTEGER,
    last_addressed_handle       TEXT,
    display_name                TEXT,
    group_id                    TEXT,
    is_filtered                 INTEGER,
    successful_query            INTEGER,
    engram_id                   TEXT,
    server_change_token         TEXT,
    ck_sync_state               INTEGER,
    original_group_id           TEXT,
    last_read_message_timestamp INTEGER,
    cloudkit_record_id          TEXT,
    last_addressed_sim_id       TEXT,
    is_blackholed               INTEGER
);

CREATE TABLE message (
    ROWID                          INTEGER PRIMARY KEY AUTOINCREMENT,
    guid                           TEXT,
    text                           TEXT,
    replace                        INTEGER DEFAULT 0,
    service_center                 TEXT,
    handle_id                      INTEGER DEFAULT 0,
    subject                        TEXT,
    country                        TEXT,
    attributedBody                 BLOB,
    version                        INTEGER DEFAULT 0,
    type                           INTEGER DEFAULT 0,
    service                        TEXT,
    account                        TEXT,
    account_guid                   TEXT,
    error                          INTEGER DEFAULT 0,
    date                           INTEGER,        -- Apple nanoseconds since 2001
    date_read                      INTEGER DEFAULT 0,
    date_delivered                 INTEGER DEFAULT 0,
    is_delivered                   INTEGER DEFAULT 0,
    is_finished                    INTEGER DEFAULT 0,
    is_emote                       INTEGER DEFAULT 0,
    is_from_me                     INTEGER DEFAULT 0,
    is_empty                       INTEGER DEFAULT 0,
    is_delayed                     INTEGER DEFAULT 0,
    is_auto_reply                  INTEGER DEFAULT 0,
    is_prepared                    INTEGER DEFAULT 0,
    is_read                        INTEGER DEFAULT 0,
    is_system_message              INTEGER DEFAULT 0,
    is_sent                        INTEGER DEFAULT 0,
    has_dd_results                 INTEGER DEFAULT 0,
    is_service_message             INTEGER DEFAULT 0,
    is_forward                     INTEGER DEFAULT 0,
    was_downgraded                 INTEGER DEFAULT 0,
    is_archive                     INTEGER DEFAULT 0,
    cache_has_attachments          INTEGER DEFAULT 0,
    cache_roomnames                TEXT,
    was_data_detected              INTEGER DEFAULT 0,
    was_deduplicated               INTEGER DEFAULT 0,
    is_audio_message               INTEGER DEFAULT 0,
    is_played                      INTEGER DEFAULT 0,
    date_played                    INTEGER DEFAULT 0,
    item_type                      INTEGER DEFAULT 0,
    other_handle                   INTEGER DEFAULT 0,
    group_title                    TEXT,
    group_action_type              INTEGER DEFAULT 0,
    share_status                   INTEGER DEFAULT 0,
    share_direction                INTEGER DEFAULT 0,
    is_expirable                   INTEGER DEFAULT 0,
    expire_state                   INTEGER DEFAULT 0,
    message_action_type            INTEGER DEFAULT 0,
    message_source                 INTEGER DEFAULT 0,
    associated_message_guid        TEXT,
    associated_message_type        INTEGER DEFAULT 0,
    balloon_bundle_id              TEXT,
    payload_data                   BLOB,
    expressive_send_style_id       TEXT,
    associated_message_range_location INTEGER DEFAULT 0,
    associated_message_range_length   INTEGER DEFAULT 0,
    time_expressive_send_played    INTEGER DEFAULT 0,
    message_summary_info           BLOB,
    ck_sync_state                  INTEGER DEFAULT 0,
    ck_record_id                   TEXT,
    ck_record_change_tag           TEXT,
    destination_caller_id          TEXT,
    is_corrupt                     INTEGER DEFAULT 0,
    reply_to_guid                  TEXT,
    sort_id                        INTEGER,
    is_spam                        INTEGER DEFAULT 0,
    has_unseen_mention             INTEGER DEFAULT 0,
    thread_originator_guid         TEXT,
    thread_originator_part         TEXT,
    syndication_ranges             TEXT,
    was_delivered_quietly          INTEGER DEFAULT 0,
    did_notify_recipient           INTEGER DEFAULT 0,
    synced_syndication_ranges      TEXT,
    date_edited                    INTEGER DEFAULT 0,
    date_retracted                 INTEGER DEFAULT 0
);

CREATE TABLE chat_message_join (
    chat_id          INTEGER,
    message_id       INTEGER,
    message_date     INTEGER DEFAULT 0,
    PRIMARY KEY (chat_id, message_id)
);

CREATE TABLE attachment (
    ROWID                          INTEGER PRIMARY KEY AUTOINCREMENT,
    guid                           TEXT,
    created_date                   INTEGER,
    start_date                     INTEGER,
    filename                       TEXT,
    uti                            TEXT,
    mime_type                      TEXT,
    transfer_state                 INTEGER,
    is_outgoing                    INTEGER,
    user_info                      BLOB,
    transfer_name                  TEXT,
    total_bytes                    INTEGER,
    is_sticker                     INTEGER DEFAULT 0,
    sticker_user_info              BLOB,
    attribution_info               BLOB,
    hide_attachment                INTEGER DEFAULT 0,
    ck_sync_state                  INTEGER DEFAULT 0,
    ck_server_change_token_blob    BLOB,
    ck_record_id                   TEXT,
    original_guid                  TEXT,
    sr_ck_sync_state               INTEGER DEFAULT 0,
    sr_ck_server_change_token_blob BLOB,
    sr_ck_record_id                TEXT,
    is_commsafety_sensitive        INTEGER DEFAULT 0,
    emoji_image_short_description  TEXT
);

CREATE TABLE message_attachment_join (
    message_id     INTEGER,
    attachment_id  INTEGER,
    PRIMARY KEY (message_id, attachment_id)
);

CREATE INDEX idx_msg_handle ON message(handle_id);
CREATE INDEX idx_cmj_chat   ON chat_message_join(chat_id);
CREATE INDEX idx_cmj_msg    ON chat_message_join(message_id);
"""


def build() -> Path:
    if DB_PATH.exists():
        DB_PATH.unlink()
    con = sqlite3.connect(DB_PATH)
    try:
        con.executescript(SCHEMA_SQL)

        # --- Handle ---------------------------------------------------------
        cur = con.execute(
            "INSERT INTO handle(id, country, service) VALUES (?, ?, ?)",
            ("+491701234567", "de", "iMessage"),
        )
        handle_rowid = cur.lastrowid

        # --- Chat -----------------------------------------------------------
        cur = con.execute(
            """INSERT INTO chat(guid, style, state, chat_identifier, service_name,
                                account_login, is_archived)
               VALUES(?, ?, ?, ?, ?, ?, ?)""",
            (
                "iMessage;-;+491701234567",
                45,      # 45 = 1:1
                3,
                "+491701234567",
                "iMessage",
                "E:owner@example.com",
                0,
            ),
        )
        chat_rowid = cur.lastrowid

        # --- Messages --------------------------------------------------------
        # Spread across two days, alternating senders.
        base = _dt.datetime(2024, 3, 14, 9, 0, 0, tzinfo=_dt.timezone.utc)
        msgs = [
            # (offset_seconds, is_from_me, text, guid, has_attachment, edited, retracted)
            (0,         0, "Hey! How's it going?",           "MSG-0001", False, False, False),
            (90,        1, "All good, thanks! You?",         "MSG-0002", False, False, False),
            (3 * 3600,  0, "Good too. Check this out:",      "MSG-0003", True,  False, False),  # with photo
            (3 * 3600 + 60, 1, "Nice shot!",                 "MSG-0004", False, False, False),
            # Day 2:
            (26 * 3600, 0, "Meet tomorrow at 6?",            "MSG-0005", False, False, False),
            (26 * 3600 + 120, 1, "Sure, sounds great!",      "MSG-0006", False, True,  False),  # edited
            (27 * 3600, 0, "Perfect, see you then.",         "MSG-0007", False, False, False),
        ]

        rowid_by_guid: dict[str, int] = {}
        for offset, is_me, text, guid, has_att, edited, retracted in msgs:
            ts_dt = base + _dt.timedelta(seconds=offset)
            ts_ns = apple_ns(ts_dt)
            date_edited_ns = apple_ns(ts_dt + _dt.timedelta(seconds=30)) if edited else 0
            date_retracted_ns = apple_ns(ts_dt + _dt.timedelta(seconds=60)) if retracted else 0
            # empty plist blob as a placeholder for message_summary_info on edits
            summary_info = b"bplist00\xd0\x08\x00\x00\x00\x00\x00\x00\x01\x01" if edited else None
            cur = con.execute(
                """INSERT INTO message(
                       guid, text, handle_id, service, date, is_from_me,
                       is_delivered, is_finished, is_sent, is_read,
                       cache_has_attachments, associated_message_type,
                       associated_message_guid, balloon_bundle_id,
                       message_summary_info, date_edited, date_retracted, item_type)
                   VALUES(?, ?, ?, 'iMessage', ?, ?, 1, 1, ?, 1, ?, 0, NULL, NULL,
                          ?, ?, ?, 0)""",
                (
                    guid, text, handle_rowid, ts_ns, is_me,
                    1 if is_me else 0,
                    1 if has_att else 0,
                    summary_info, date_edited_ns, date_retracted_ns,
                ),
            )
            mrow = cur.lastrowid
            rowid_by_guid[guid] = mrow
            con.execute(
                "INSERT INTO chat_message_join(chat_id, message_id, message_date) VALUES(?, ?, ?)",
                (chat_rowid, mrow, ts_ns),
            )

        # --- Tapback (heart from the other side on MSG-0002) ----------------
        target_guid = "MSG-0002"
        tb_dt = base + _dt.timedelta(seconds=180)
        tb_ns = apple_ns(tb_dt)
        cur = con.execute(
            """INSERT INTO message(
                   guid, text, handle_id, service, date, is_from_me,
                   is_delivered, is_finished, is_sent, is_read,
                   cache_has_attachments, associated_message_type,
                   associated_message_guid, balloon_bundle_id, item_type)
               VALUES(?, ?, ?, 'iMessage', ?, 0, 1, 1, 0, 1, 0, 2000, ?, NULL, 0)""",
            (
                "TAPBACK-0001",
                "Liked “All good, thanks! You?”",
                handle_rowid,
                tb_ns,
                f"p:0/{target_guid}",
            ),
        )
        tb_rowid = cur.lastrowid
        con.execute(
            "INSERT INTO chat_message_join(chat_id, message_id, message_date) VALUES(?, ?, ?)",
            (chat_rowid, tb_rowid, tb_ns),
        )

        # --- Attachment for MSG-0003 (photo) --------------------------------
        att_rowid_msg = rowid_by_guid["MSG-0003"]
        cur = con.execute(
            """INSERT INTO attachment(
                   guid, created_date, filename, uti, mime_type,
                   transfer_state, is_outgoing, transfer_name, total_bytes,
                   is_sticker)
               VALUES(?, ?, ?, 'public.jpeg', 'image/jpeg', 5, 0, ?, ?, 0)""",
            (
                "ATT-0001",
                apple_ns(base + _dt.timedelta(seconds=3 * 3600)),
                "~/Library/Messages/Attachments/aa/00/sample.jpg",
                "sample.jpg",
                12345,
            ),
        )
        att_rowid = cur.lastrowid
        con.execute(
            "INSERT INTO message_attachment_join(message_id, attachment_id) VALUES(?, ?)",
            (att_rowid_msg, att_rowid),
        )

        con.commit()
    finally:
        con.close()
    return DB_PATH


if __name__ == "__main__":
    path = build()
    size = os.path.getsize(path)
    print(f"OK: {path}  ({size} bytes)")
