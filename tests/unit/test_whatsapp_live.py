# -*- coding: utf-8 -*-
"""
Unit tests for msgviz.adapters.whatsapp_live.WhatsAppLiveAdapter.

Drives the adapter against the synthetic ChatStorage.sqlite fixture
(tests/fixtures/build_sample_whatsapp_db.py). Verifies protocol
conformance, ChatSpec shape, message iteration, drift forwarding, the
fatal-drift abort, and attachment resolution.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from msgviz.adapters.whatsapp_live import WhatsAppLiveAdapter
from msgviz.core import drift
from msgviz.core.source_adapter import SourceAdapter

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_whatsapp.db"


@pytest.fixture()
def adapter():
    assert FIXTURE.exists(), (
        "run tests/fixtures/build_sample_whatsapp_db.py to build the fixture"
    )
    events: list[drift.DriftEvent] = []
    a = WhatsAppLiveAdapter(
        device_slug="mac_test_wa",
        db_path=str(FIXTURE),
        me_name="Me",
        on_drift=events.append,
    )
    a.drift_events = events  # type: ignore[attr-defined]  # test convenience
    yield a
    a.close()


# ---------------------------------------------------------------------------
# Protocol + identity
# ---------------------------------------------------------------------------

def test_conforms_to_source_adapter_protocol(adapter) -> None:
    assert isinstance(adapter, SourceAdapter)
    assert adapter.name == "whatsapp_live"
    assert adapter.supports_incremental is True


def test_default_db_path_is_macos_container() -> None:
    a = WhatsAppLiveAdapter(device_slug="x")
    assert a.db_path.endswith(
        "group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite"
    )


# ---------------------------------------------------------------------------
# open() / schema probe
# ---------------------------------------------------------------------------

def test_open_returns_report_no_fatal(adapter) -> None:
    report = adapter.open()
    assert report.is_fatal is False
    assert adapter.last_report is report


def test_open_forwards_drift_events_to_sink(adapter) -> None:
    adapter.open()
    # The fixture omits some optional columns → warn events forwarded.
    assert any(
        e.kind == "missing_optional_column" for e in adapter.drift_events
    )
    assert all(e.severity != "fatal" for e in adapter.drift_events)


def test_open_raises_on_fatal_drift(tmp_path) -> None:
    # A DB missing a required ZWAMESSAGE column → fatal.
    broken = tmp_path / "broken.db"
    con = sqlite3.connect(broken)
    con.executescript("""
        CREATE TABLE ZWAMESSAGE (
            Z_PK INTEGER PRIMARY KEY, ZMESSAGEDATE TIMESTAMP,
            ZFROMJID VARCHAR, ZISFROMME INTEGER, ZCHATSESSION INTEGER,
            ZMESSAGETYPE INTEGER
        );  -- no ZSTANZAID → required column missing
        CREATE TABLE ZWACHATSESSION (
            Z_PK INTEGER PRIMARY KEY, ZCONTACTJID VARCHAR, ZSESSIONTYPE INTEGER
        );
        CREATE TABLE ZWAMEDIAITEM (Z_PK INTEGER PRIMARY KEY, ZMESSAGE INTEGER);
        CREATE TABLE ZWAGROUPMEMBER (Z_PK INTEGER PRIMARY KEY, ZMEMBERJID VARCHAR);
    """)
    con.commit()
    con.close()

    events: list[drift.DriftEvent] = []
    a = WhatsAppLiveAdapter(
        device_slug="x", db_path=str(broken), on_drift=events.append
    )
    with pytest.raises(drift.SchemaDriftError):
        a.open()
    # The fatal event was still surfaced through the sink before raising.
    assert any(e.severity == "fatal" for e in events)
    a.close()


# ---------------------------------------------------------------------------
# list_chats
# ---------------------------------------------------------------------------

def test_list_chats_shape(adapter) -> None:
    chats = {c.source_id: c for c in adapter.list_chats()}
    assert set(chats) == {"1", "2", "3"}

    one = chats["1"]
    assert one.slug == "mac_test_wa/chat_1"
    assert one.title == "Alice"
    assert one.is_group is False
    assert one.origin == "whatsapp"
    assert one.subtitle == "491700000001@s.whatsapp.net"

    grp = chats["2"]
    assert grp.title == "Dev Team"
    assert grp.is_group is True


def test_list_chats_runs_probe_if_not_opened(adapter) -> None:
    # list_chats() without a prior open() should still probe.
    assert adapter.last_report is None
    list(adapter.list_chats())
    assert adapter.last_report is not None


# ---------------------------------------------------------------------------
# iter_messages
# ---------------------------------------------------------------------------

def test_iter_messages_one_to_one(adapter) -> None:
    chats = {c.source_id: c for c in adapter.list_chats()}
    msgs = list(adapter.iter_messages(chats["1"]))
    assert len(msgs) == 5
    ids = {m.external_id for m in msgs}
    assert ids == {
        "STANZA-100", "STANZA-101", "STANZA-102", "STANZA-103", "STANZA-104",
    }


def test_iter_messages_one_to_one_collapses_to_partner(adapter) -> None:
    # The adapter attributes every non-me 1:1 message to the chat
    # partner (ZPARTNERNAME='Alice'), collapsing the phone-JID/@lid split
    # — one person, not two raw IDs.
    chats = {c.source_id: c for c in adapter.list_chats()}
    msgs = list(adapter.iter_messages(chats["1"]))
    non_me = {m.sender_raw for m in msgs if not m.is_me}
    assert non_me == {"Alice"}


def test_iter_messages_unnamed_one_to_one_collapses_to_contact_jid(adapter) -> None:
    # An un-named 1:1 (no ZPARTNERNAME) that also went phone→@lid must
    # collapse to the session's stable ZCONTACTJID, not split per message.
    chats = {c.source_id: c for c in adapter.list_chats()}
    msgs = list(adapter.iter_messages(chats["3"]))
    non_me = {m.sender_raw for m in msgs if not m.is_me}
    assert non_me == {"491700000009@s.whatsapp.net"}


def test_iter_messages_group_resolves_senders(adapter) -> None:
    chats = {c.source_id: c for c in adapter.list_chats()}
    msgs = {m.external_id: m for m in adapter.iter_messages(chats["2"])}
    assert msgs["STANZA-300"].sender_raw == "491700000002@s.whatsapp.net"
    assert msgs["STANZA-301"].sender_raw == "491700000003@s.whatsapp.net"
    assert msgs["STANZA-302"].is_me is True
    # group JID never leaks as a sender
    for m in msgs.values():
        assert "@g.us" not in (m.sender_raw or "")


def test_iter_messages_forwards_enum_drift(adapter) -> None:
    chats = {c.source_id: c for c in adapter.list_chats()}
    list(adapter.iter_messages(chats["2"]))  # contains the type-99 row
    assert any(
        e.kind == "unknown_enum_value" and e.observed == "99"
        for e in adapter.drift_events
    )


# ---------------------------------------------------------------------------
# resolve_attachment
# ---------------------------------------------------------------------------

def test_resolve_attachment_missing_returns_none(adapter) -> None:
    assert adapter.resolve_attachment("Media/nope/x.jpg") is None
    assert adapter.resolve_attachment("") is None


def test_resolve_attachment_absolute_existing(adapter, tmp_path) -> None:
    f = tmp_path / "real.jpg"
    f.write_bytes(b"\xff\xd8\xff")  # JPEG magic
    assert adapter.resolve_attachment(str(f)) == f


def test_resolve_attachment_relative_uses_media_root(adapter, monkeypatch, tmp_path) -> None:
    # Point the WhatsApp media root at a temp dir and place a file
    # at the relative path; resolve_attachment should find it.
    from msgviz import paths
    monkeypatch.setattr(paths, "whatsapp_media_root", lambda: tmp_path)
    rel = "chatX/pic.jpg"
    target = tmp_path / rel
    target.parent.mkdir(parents=True)
    target.write_bytes(b"\xff\xd8\xff")
    assert adapter.resolve_attachment(rel) == target


def test_media_root_is_message_not_message_media() -> None:
    # Regression for the "all attachments missing" bug: ZMEDIALOCALPATH
    # values already start with "Media/…", and that Media segment IS the
    # Message/Media dir. So the root must be <container>/Message, NOT
    # <container>/Message/Media — otherwise the join doubles to
    # …/Message/Media/Media/… and nothing resolves.
    from msgviz import paths
    root = paths.whatsapp_media_root()
    assert root.name == "Message"
    assert root.parent.name == "group.net.whatsapp.WhatsApp.shared"


def test_resolve_attachment_does_not_double_media_segment(adapter, monkeypatch, tmp_path) -> None:
    # Simulate the real layout: container/Message/Media/<jid>/.../file.jpg
    # with a ZMEDIALOCALPATH of "Media/<jid>/.../file.jpg".
    from msgviz import paths
    message_dir = tmp_path / "Message"
    monkeypatch.setattr(paths, "whatsapp_media_root", lambda: message_dir)
    rel = "Media/34699@s.whatsapp.net/b/3/file.jpg"
    target = message_dir / rel
    target.parent.mkdir(parents=True)
    target.write_bytes(b"\xff\xd8\xff")
    got = adapter.resolve_attachment(rel)
    assert got == target
    assert "Media/Media" not in str(got)   # no doubled segment
