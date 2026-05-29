# -*- coding: utf-8 -*-
"""
Unit tests for msgviz.adapters.imessage_db.iter_canonical.

Runs the iMessage row iterator against the synthetic Apple chat.db
fixture (tests/fixtures/build_sample_chat_db.py — no real Messages
data). The focus here is the per-row safety net (mirroring
test_whatsapp_db.py): a malformed message row must become a
``row_parse_failed`` warn drift event and be skipped, not abort the
whole chat. The happy-path / attachment / tapback behaviour is already
exercised by tests/integration/test_adapter_imessage.py.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from msgviz.adapters import imessage_db as imdb
from msgviz.core import drift

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_chat.db"

# chat.ROWID of the single 1:1 chat in the fixture.
CHAT_ROWID = 1


@pytest.fixture()
def con():
    assert FIXTURE.exists(), (
        "run tests/fixtures/build_sample_chat_db.py to build the fixture"
    )
    c = sqlite3.connect(f"file:{FIXTURE}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def _ids(con, **kw):
    return {m.external_id for m in
            imdb.iter_canonical(con, CHAT_ROWID, "Me", **kw)}


# ---------------------------------------------------------------------------
# Baseline: the iterator works without a drift sink
# ---------------------------------------------------------------------------

def test_iter_without_drift_sink_does_not_raise(con) -> None:
    # on_drift defaults to a no-op; iterating must still yield the real
    # messages (7 in the fixture; the tapback is folded into reactions).
    msgs = list(imdb.iter_canonical(con, CHAT_ROWID, "Me"))
    assert len(msgs) == 7
    assert all(m.external_id for m in msgs)


# ---------------------------------------------------------------------------
# Malformed row → row_parse_failed, not a crash
# ---------------------------------------------------------------------------

def test_malformed_row_becomes_drift_not_crash(monkeypatch, con) -> None:
    # Force _build_canonical to blow up on one specific row to prove the
    # safe_canonicalize wrapper turns it into a drift event rather than
    # aborting the whole chat iteration.
    real_build = imdb._build_canonical

    def boom(c, m, **kw):
        if m["guid"] == "MSG-0001":
            raise ValueError("synthetic explosion")
        return real_build(c, m, **kw)

    monkeypatch.setattr(imdb, "_build_canonical", boom)
    captured: list[drift.DriftEvent] = []
    msgs = list(imdb.iter_canonical(con, CHAT_ROWID, "Me",
                                    on_drift=captured.append))

    # The exploding row's rowid is gone, the rest survive. MSG-0001 is the
    # first message (rowid 1); the others (rowids 2..7) remain.
    ids = {m.external_id for m in msgs}
    assert "1" not in ids
    assert len(msgs) == 6

    # ...and recorded as a single row_parse_failed warn event on `message`.
    parse_fails = [e for e in captured if e.kind == "row_parse_failed"]
    assert len(parse_fails) == 1
    assert parse_fails[0].severity == "warn"
    assert parse_fails[0].table == "message"
    assert "synthetic explosion" in parse_fails[0].detail


def test_malformed_row_source_tag_is_passed_through(monkeypatch, con) -> None:
    # The caller chooses the drift `source` tag; iter_canonical must stamp
    # it onto the row_parse_failed event (imessage_live vs imessage_backup).
    real_build = imdb._build_canonical

    def boom(c, m, **kw):
        if m["guid"] == "MSG-0001":
            raise ValueError("kaboom")
        return real_build(c, m, **kw)

    monkeypatch.setattr(imdb, "_build_canonical", boom)
    captured: list[drift.DriftEvent] = []
    list(imdb.iter_canonical(con, CHAT_ROWID, "Me",
                             source="imessage_backup",
                             on_drift=captured.append))
    parse_fails = [e for e in captured if e.kind == "row_parse_failed"]
    assert len(parse_fails) == 1
    assert parse_fails[0].source == "imessage_backup"


def test_no_drift_on_healthy_chat(con) -> None:
    # A clean fixture must not emit any row_parse_failed noise.
    captured: list[drift.DriftEvent] = []
    list(imdb.iter_canonical(con, CHAT_ROWID, "Me", on_drift=captured.append))
    assert [e for e in captured if e.kind == "row_parse_failed"] == []
