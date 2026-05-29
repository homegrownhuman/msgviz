# -*- coding: utf-8 -*-
"""
Tests for the GET /api/drift endpoint.

Builds an isolated app via create_app(MVConfig) against a fresh DB and
checks the endpoint: empty when the table is absent, returns pending
events + count, honours ?all=true, and excludes acknowledged rows by
default. The server opens the DB read-only, so the endpoint must NOT
try to create the drift_event table.
"""
from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from msgviz.config import MVConfig
from msgviz.core import drift
from msgviz.server.factory import create_app


def _seed_drift(con: sqlite3.Connection) -> None:
    drift.ensure_drift_event_table(con)
    drift.record_report(con, drift.SchemaReport(
        schema_version=1,
        events=(
            drift.DriftEvent(
                source="whatsapp_live", severity="warn",
                kind="unknown_enum_value", table="ZWAMESSAGE",
                column="ZMESSAGETYPE", observed="99", expected="known",
                detail="unknown type 99", seen_at=1_700_000_000),
            drift.DriftEvent(
                source="imessage_live", severity="fatal",
                kind="missing_required_column", table="message",
                column="text", observed=None, expected="TEXT",
                detail="Apple removed message.text", seen_at=1_700_000_100),
        ),
    ))
    con.commit()


def test_drift_empty_when_no_table(tmp_visualizer_db, visualizer_db_path):
    # Fresh DB, no drift_event table at all.
    tmp_visualizer_db.close()
    app = create_app(MVConfig(db_file=visualizer_db_path, enable_watcher=False))
    r = TestClient(app).get("/api/drift")
    assert r.status_code == 200
    assert r.json() == {"events": [], "pending_count": 0}


def test_drift_lists_pending(tmp_visualizer_db, visualizer_db_path):
    _seed_drift(tmp_visualizer_db)
    tmp_visualizer_db.close()
    app = create_app(MVConfig(db_file=visualizer_db_path, enable_watcher=False))
    data = TestClient(app).get("/api/drift").json()
    assert data["pending_count"] == 2
    kinds = {e["kind"] for e in data["events"]}
    assert "unknown_enum_value" in kinds
    assert "missing_required_column" in kinds
    # severity carried through for the banner's fatal styling.
    assert any(e["severity"] == "fatal" for e in data["events"])


def test_drift_excludes_acknowledged_by_default(
    tmp_visualizer_db, visualizer_db_path
):
    _seed_drift(tmp_visualizer_db)
    # Acknowledge the warn one (id 1).
    drift.acknowledge(tmp_visualizer_db, 1)
    tmp_visualizer_db.close()
    app = create_app(MVConfig(db_file=visualizer_db_path, enable_watcher=False))
    client = TestClient(app)

    default = client.get("/api/drift").json()
    assert default["pending_count"] == 1          # only the fatal remains
    assert all(e["acknowledged_at"] is None for e in default["events"])

    show_all = client.get("/api/drift?all=true").json()
    # ?all returns both (audit), but pending_count still reflects un-acked.
    assert len(show_all["events"]) == 2
    assert show_all["pending_count"] == 1


def test_drift_does_not_write_to_readonly_db(
    tmp_visualizer_db, visualizer_db_path
):
    # The server DB is opened mode=ro. Hitting /api/drift on a DB that
    # already has the table must not error (no CREATE attempted).
    _seed_drift(tmp_visualizer_db)
    tmp_visualizer_db.close()
    app = create_app(MVConfig(db_file=visualizer_db_path, enable_watcher=False))
    r = TestClient(app).get("/api/drift")
    assert r.status_code == 200
