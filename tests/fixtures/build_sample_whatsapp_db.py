#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build a mini WhatsApp Desktop ChatStorage.sqlite fixture for pytest.

Schema mirrors the real ``ZWA*`` Core Data layout (verified against a
live macOS WhatsApp Desktop install), reduced to the columns the
whatsapp_db adapter reads, plus a couple of extras so the schema-drift
probe has something realistic to look at.

Column declared-types deliberately use WhatsApp's real spellings
(``TIMESTAMP``, ``VARCHAR``) so the drift probe's storage-class
normalisation (TIMESTAMP→NUMERIC, VARCHAR→TEXT) is exercised.

Usage:
    python3 tests/fixtures/build_sample_whatsapp_db.py
Writes: tests/fixtures/sample_whatsapp.db (overwrites).

WhatsApp/Core Data epoch: 2001-01-01 UTC, **seconds** (not nanos).

Contents:
    - 2 chat sessions:
        * 1:1   (ZSESSIONTYPE=0) with +491700000001
        * group (ZSESSIONTYPE=1) "Dev Team" with 2 members
    - 1:1: 4 messages (2 me / 2 them), one with an image
    - group: 3 messages from 2 different members + me
    - 1 voice message (audio), 1 message with an unknown ZMESSAGETYPE
      (=99) carrying text, to exercise the enum-drift warn path
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "sample_whatsapp.db"

# WhatsApp epoch: 2001-01-01 UTC, seconds. A March-2024 base.
# 2024-03-14 09:00:00 UTC in Unix = 1710406800; minus 978307200 =
# 732099600 Core Data seconds.
WA_EPOCH = 978307200
BASE_CORE = 1710406800 - WA_EPOCH  # Core Data seconds for the base time


SCHEMA_SQL = """
CREATE TABLE ZWACHATSESSION (
    Z_PK             INTEGER PRIMARY KEY,
    Z_ENT            INTEGER,
    Z_OPT            INTEGER,
    ZSESSIONTYPE     INTEGER,
    ZARCHIVED        INTEGER,
    ZHIDDEN          INTEGER,
    ZREMOVED         INTEGER,
    ZUNREADCOUNT     INTEGER,
    ZMESSAGECOUNTER  INTEGER,
    ZGROUPINFO       INTEGER,
    ZLASTMESSAGEDATE TIMESTAMP,
    ZLASTMESSAGETEXT VARCHAR,
    ZCONTACTIDENTIFIER VARCHAR,
    ZCONTACTJID      VARCHAR,
    ZPARTNERNAME     VARCHAR
);

CREATE TABLE ZWAMESSAGE (
    Z_PK             INTEGER PRIMARY KEY,
    Z_ENT            INTEGER,
    Z_OPT            INTEGER,
    ZGROUPEVENTTYPE  INTEGER,
    ZISFROMME        INTEGER,
    ZMESSAGESTATUS   INTEGER,
    ZMESSAGETYPE     INTEGER,
    ZSORT            INTEGER,
    ZSTARRED         INTEGER,
    ZCHATSESSION     INTEGER,
    ZGROUPMEMBER     INTEGER,
    ZMEDIAITEM       INTEGER,
    ZMESSAGEINFO     INTEGER,
    ZPARENTMESSAGE   INTEGER,
    ZMESSAGEDATE     TIMESTAMP,
    ZSENTDATE        TIMESTAMP,
    ZFROMJID         VARCHAR,
    ZMEDIASECTIONID  VARCHAR,
    ZPHASH           VARCHAR,
    ZPUSHNAME        VARCHAR,
    ZSTANZAID        VARCHAR,
    ZTEXT            VARCHAR,
    ZTOJID           VARCHAR
);

CREATE TABLE ZWAMEDIAITEM (
    Z_PK             INTEGER PRIMARY KEY,
    Z_ENT            INTEGER,
    Z_OPT            INTEGER,
    ZFILESIZE        INTEGER,
    ZMOVIEDURATION   INTEGER,
    ZMESSAGE         INTEGER,
    ZLATITUDE        FLOAT,
    ZLONGITUDE       FLOAT,
    ZMEDIALOCALPATH  VARCHAR,
    ZTHUMBNAILLOCALPATH VARCHAR,
    ZTITLE           VARCHAR,
    ZVCARDNAME       VARCHAR,
    ZVCARDSTRING     VARCHAR
);

CREATE TABLE ZWAGROUPMEMBER (
    Z_PK             INTEGER PRIMARY KEY,
    Z_ENT            INTEGER,
    Z_OPT            INTEGER,
    ZISADMIN         INTEGER,
    ZISACTIVE        INTEGER,
    ZCHATSESSION     INTEGER,
    ZCONTACTIDENTIFIER VARCHAR,
    ZCONTACTNAME     VARCHAR,
    ZFIRSTNAME       VARCHAR,
    ZMEMBERJID       VARCHAR
);
"""


def build() -> Path:
    if DB_PATH.exists():
        DB_PATH.unlink()
    con = sqlite3.connect(DB_PATH)
    try:
        con.executescript(SCHEMA_SQL)

        # --- Chat sessions ---------------------------------------------------
        # 1:1 session (pk=1)
        con.execute(
            """INSERT INTO ZWACHATSESSION
               (Z_PK, ZSESSIONTYPE, ZCONTACTJID, ZPARTNERNAME, ZARCHIVED,
                ZHIDDEN, ZREMOVED)
               VALUES (1, 0, '491700000001@s.whatsapp.net', 'Alice', 0, 0, 0)"""
        )
        # group session (pk=2)
        con.execute(
            """INSERT INTO ZWACHATSESSION
               (Z_PK, ZSESSIONTYPE, ZCONTACTJID, ZPARTNERNAME, ZARCHIVED,
                ZHIDDEN, ZREMOVED)
               VALUES (2, 1, '120363000000000001@g.us', 'Dev Team', 0, 0, 0)"""
        )

        # --- Group members (for session 2) -----------------------------------
        con.execute(
            """INSERT INTO ZWAGROUPMEMBER
               (Z_PK, ZCHATSESSION, ZMEMBERJID, ZCONTACTNAME)
               VALUES (10, 2, '491700000002@s.whatsapp.net', 'Bob')"""
        )
        con.execute(
            """INSERT INTO ZWAGROUPMEMBER
               (Z_PK, ZCHATSESSION, ZMEMBERJID, ZCONTACTNAME)
               VALUES (11, 2, '491700000003@s.whatsapp.net', 'Carol')"""
        )

        # --- 1:1 messages (session 1) ----------------------------------------
        # (pk, offset_s, is_me, type, text, stanza, from_jid)
        one_to_one = [
            (100, 0,    0, 0, "Hey! WhatsApp from Alice", "STANZA-100",
             "491700000001@s.whatsapp.net"),
            (101, 60,   1, 0, "Got it, thanks!",          "STANZA-101", None),
            (102, 3600, 0, 1, None,                       "STANZA-102",
             "491700000001@s.whatsapp.net"),  # image, no text
            (103, 3660, 1, 3, None,                       "STANZA-103", None),  # voice note from me
        ]
        for pk, off, is_me, mtype, text, stanza, from_jid in one_to_one:
            core = BASE_CORE + off
            con.execute(
                """INSERT INTO ZWAMESSAGE
                   (Z_PK, ZCHATSESSION, ZISFROMME, ZMESSAGETYPE, ZTEXT,
                    ZMESSAGEDATE, ZSENTDATE, ZFROMJID, ZSTANZAID,
                    ZGROUPEVENTTYPE, ZGROUPMEMBER, ZSORT)
                   VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?)""",
                (pk, is_me, mtype, text, core, core, from_jid, stanza, off),
            )

        # image media for pk=102
        con.execute(
            """INSERT INTO ZWAMEDIAITEM
               (Z_PK, ZMESSAGE, ZMEDIALOCALPATH, ZTITLE, ZFILESIZE)
               VALUES (200, 102, 'Media/491700000001/photo.jpg', 'photo.jpg', 54321)"""
        )
        # audio media for pk=103
        con.execute(
            """INSERT INTO ZWAMEDIAITEM
               (Z_PK, ZMESSAGE, ZMEDIALOCALPATH, ZMOVIEDURATION)
               VALUES (201, 103, 'Media/491700000001/voice.ogg', 7)"""
        )

        # --- group messages (session 2) --------------------------------------
        # group msgs carry ZGROUPMEMBER, not a usable ZFROMJID for others.
        group = [
            # (pk, offset, is_me, type, text, stanza, group_member_pk)
            (300, 0,   0, 0, "Standup in 5?",        "STANZA-300", 10),  # Bob
            (301, 120, 0, 0, "On my way",            "STANZA-301", 11),  # Carol
            (302, 240, 1, 0, "Joining now",          "STANZA-302", None),  # me
            # an unknown message type (99) that still has text — should
            # warn (enum drift) but keep the row:
            (303, 360, 0, 99, "weird future type",   "STANZA-303", 10),
        ]
        for pk, off, is_me, mtype, text, stanza, gm in group:
            core = BASE_CORE + off
            # group JID lands in ZFROMJID for group rows in real data
            con.execute(
                """INSERT INTO ZWAMESSAGE
                   (Z_PK, ZCHATSESSION, ZISFROMME, ZMESSAGETYPE, ZTEXT,
                    ZMESSAGEDATE, ZSENTDATE, ZFROMJID, ZSTANZAID,
                    ZGROUPEVENTTYPE, ZGROUPMEMBER, ZSORT)
                   VALUES (?, 2, ?, ?, ?, ?, ?, '120363000000000001@g.us', ?,
                           0, ?, ?)""",
                (pk, is_me, mtype, text, core, core, stanza, gm, off),
            )

        con.commit()
    finally:
        con.close()
    return DB_PATH


if __name__ == "__main__":
    path = build()
    size = os.path.getsize(path)
    print(f"OK: {path}  ({size} bytes)")
