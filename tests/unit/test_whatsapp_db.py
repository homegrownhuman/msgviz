# -*- coding: utf-8 -*-
"""
Unit tests for msgviz.adapters.whatsapp_db + whatsapp_schema.

Runs the WhatsApp row iterator against a synthetic ChatStorage.sqlite
fixture (tests/fixtures/build_sample_whatsapp_db.py — no real WhatsApp
data). Verifies:

* the schema contract probes clean (no fatal, no new_column) on a
  healthy DB, and fires the right drift on a broken one,
* timestamps convert from Core Data seconds to Unix,
* 1:1 vs group sender resolution (the ZWAGROUPMEMBER join, §5.3),
* media attachment resolution,
* unknown message types warn-but-keep (§5.4),
* a malformed row becomes a row_parse_failed drift event, not a crash.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from msgviz.adapters import whatsapp_db as wadb
from msgviz.adapters import whatsapp_schema as ws
from msgviz.core import drift

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_whatsapp.db"


@pytest.fixture()
def con():
    assert FIXTURE.exists(), (
        "run tests/fixtures/build_sample_whatsapp_db.py to build the fixture"
    )
    c = sqlite3.connect(f"file:{FIXTURE}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Schema probe
# ---------------------------------------------------------------------------

def test_probe_healthy_fixture_no_fatal(con) -> None:
    report = wadb.probe(con)
    assert report.is_fatal is False
    assert report.fatal_count == 0


def test_probe_healthy_fixture_no_spurious_new_columns(con) -> None:
    # Z_ENT / Z_OPT are Core Data bookkeeping columns present on every
    # ZWA* table; the contract knows them, so a healthy DB must NOT
    # report new_column drift (proposal §13.11: don't cry wolf).
    report = wadb.probe(con)
    new_cols = [e for e in report.events if e.kind == "new_column"]
    assert new_cols == [], f"unexpected new_column drift: {new_cols}"


def test_probe_missing_required_column_is_fatal() -> None:
    # In-memory DB missing ZSTANZAID (a required ZWAMESSAGE column).
    mem = sqlite3.connect(":memory:")
    mem.executescript("""
        CREATE TABLE ZWAMESSAGE (
            Z_PK INTEGER PRIMARY KEY, ZMESSAGEDATE TIMESTAMP,
            ZFROMJID VARCHAR, ZISFROMME INTEGER, ZCHATSESSION INTEGER,
            ZMESSAGETYPE INTEGER
        );
        CREATE TABLE ZWACHATSESSION (
            Z_PK INTEGER PRIMARY KEY, ZCONTACTJID VARCHAR, ZSESSIONTYPE INTEGER
        );
        CREATE TABLE ZWAMEDIAITEM (Z_PK INTEGER PRIMARY KEY, ZMESSAGE INTEGER);
        CREATE TABLE ZWAGROUPMEMBER (Z_PK INTEGER PRIMARY KEY, ZMEMBERJID VARCHAR);
    """)
    report = wadb.probe(mem)
    assert report.is_fatal
    fatals = [e for e in report.events if e.kind == "missing_required_column"]
    assert any(e.column == "ZSTANZAID" for e in fatals)


# ---------------------------------------------------------------------------
# Chats
# ---------------------------------------------------------------------------

def test_list_chats(con) -> None:
    chats = {c["pk"]: c for c in wadb.list_chats_from_db(con)}
    assert set(chats) == {1, 2}
    assert chats[1]["session_type"] == ws.SESSION_TYPE_ONE_TO_ONE
    assert chats[1]["partner_name"] == "Alice"
    assert chats[2]["session_type"] == ws.SESSION_TYPE_GROUP
    assert chats[2]["partner_name"] == "Dev Team"


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------

def test_wa_ts_converts_core_data_seconds() -> None:
    # Core Data 0 → Unix WHATSAPP_EPOCH.
    assert wadb.wa_ts(0) == ws.WHATSAPP_EPOCH
    # A known value: BASE_CORE used in the fixture maps to 2024-03-14.
    assert wadb.wa_ts(None) is None


def test_one_to_one_timestamps_are_unix(con) -> None:
    msgs = list(wadb.iter_canonical(con, 1, "Me", is_group=False))
    # First message base time = 2024-03-14 09:00:00 UTC = 1710406800.
    assert msgs[0].ts == 1710406800
    # Strictly increasing.
    ts = [m.ts for m in msgs]
    assert ts == sorted(ts)


# ---------------------------------------------------------------------------
# 1:1 sender resolution + media
# ---------------------------------------------------------------------------

def test_one_to_one_senders_and_is_me(con) -> None:
    msgs = list(wadb.iter_canonical(con, 1, "Me", is_group=False))
    by_id = {m.external_id: m for m in msgs}
    assert by_id["STANZA-100"].is_me is False
    assert by_id["STANZA-100"].sender_raw == "491700000001@s.whatsapp.net"
    assert by_id["STANZA-101"].is_me is True
    assert by_id["STANZA-101"].sender_raw == "Me"


def test_one_to_one_external_id_is_stanza(con) -> None:
    msgs = list(wadb.iter_canonical(con, 1, "Me", is_group=False))
    ids = {m.external_id for m in msgs}
    assert ids == {"STANZA-100", "STANZA-101", "STANZA-102", "STANZA-103"}


def test_image_message_has_attachment(con) -> None:
    msgs = {m.external_id: m for m in
            wadb.iter_canonical(con, 1, "Me", is_group=False)}
    img = msgs["STANZA-102"]
    assert len(img.attachments) == 1
    assert img.attachments[0].source_ref == "Media/491700000001/photo.jpg"
    assert img.attachments[0].mime == "image/jpeg"
    assert img.text is None


def test_voice_message_has_attachment(con) -> None:
    msgs = {m.external_id: m for m in
            wadb.iter_canonical(con, 1, "Me", is_group=False)}
    voice = msgs["STANZA-103"]
    assert len(voice.attachments) == 1
    assert voice.attachments[0].source_ref.endswith("voice.ogg")
    assert voice.attachments[0].mime == "audio/ogg"
    assert voice.is_me is True


# ---------------------------------------------------------------------------
# Group sender resolution (§5.3)
# ---------------------------------------------------------------------------

def test_group_sender_resolved_via_group_member(con) -> None:
    msgs = {m.external_id: m for m in
            wadb.iter_canonical(con, 2, "Me", is_group=True)}
    # Bob (group member pk 10) and Carol (pk 11), not the group JID.
    assert msgs["STANZA-300"].sender_raw == "491700000002@s.whatsapp.net"
    assert msgs["STANZA-301"].sender_raw == "491700000003@s.whatsapp.net"
    # My own message: marked is_me, sender "Me".
    assert msgs["STANZA-302"].is_me is True
    assert msgs["STANZA-302"].sender_raw == "Me"
    # None of the group senders is the group JID itself.
    for m in msgs.values():
        assert m.sender_raw != "120363000000000001@g.us"


# ---------------------------------------------------------------------------
# Unknown message type (§5.4)
# ---------------------------------------------------------------------------

def test_unknown_message_type_warns_but_keeps_text_row(con) -> None:
    captured: list[drift.DriftEvent] = []
    msgs = {m.external_id: m for m in
            wadb.iter_canonical(con, 2, "Me", is_group=True,
                                on_drift=captured.append)}
    # The type-99 row carries text → kept.
    assert "STANZA-303" in msgs
    assert msgs["STANZA-303"].text == "weird future type"
    # ...and a drift warning fired for the unknown type.
    enum_warns = [e for e in captured if e.kind == "unknown_enum_value"]
    assert any(e.observed == "99" for e in enum_warns)
    assert all(e.severity == "warn" for e in enum_warns)


# ---------------------------------------------------------------------------
# Malformed row → row_parse_failed, not a crash
# ---------------------------------------------------------------------------

def test_malformed_row_becomes_drift_not_crash(monkeypatch, con) -> None:
    # Force _build_canonical to blow up on one specific row to prove the
    # safe_canonicalize wrapper turns it into a drift event.
    real_build = wadb._build_canonical

    def boom(c, row, **kw):
        if row["stanza_id"] == "STANZA-101":
            raise ValueError("synthetic explosion")
        return real_build(c, row, **kw)

    monkeypatch.setattr(wadb, "_build_canonical", boom)
    captured: list[drift.DriftEvent] = []
    msgs = list(wadb.iter_canonical(con, 1, "Me", is_group=False,
                                    on_drift=captured.append))
    # The exploding row is skipped, the rest survive.
    ids = {m.external_id for m in msgs}
    assert "STANZA-101" not in ids
    assert "STANZA-100" in ids
    # ...and recorded as row_parse_failed.
    parse_fails = [e for e in captured if e.kind == "row_parse_failed"]
    assert len(parse_fails) == 1
    assert parse_fails[0].severity == "warn"
    assert "synthetic explosion" in parse_fails[0].detail


def test_iter_without_drift_sink_does_not_raise(con) -> None:
    # on_drift defaults to a no-op; iterating must still work even with
    # the deliberate unknown-type row present.
    msgs = list(wadb.iter_canonical(con, 2, "Me", is_group=True))
    assert len(msgs) == 4
