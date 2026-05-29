# -*- coding: utf-8 -*-
"""
Unit tests for tools/import_whatsapp_live.import_live.

Builds a real visualizer.db (from schema.sql) in a temp MSGVIZ_HOME,
registers a device, and ingests the synthetic ChatStorage.sqlite
fixture. Verifies: full ingest, incremental dedup on re-run, dry-run
writes nothing, sender resolution, drift persisted to the DB, and the
fatal-drift abort leaving nothing written.
"""
from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

import pytest

FIX = Path(__file__).resolve().parents[1] / "fixtures"
WA_DB = FIX / "sample_whatsapp.db"

# tools/ isn't a package on the path by default.
_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


@pytest.fixture(autouse=True)
def _quiet():
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Temp MSGVIZ_HOME with an initialized visualizer.db + one device."""
    monkeypatch.setenv("MSGVIZ_HOME", str(tmp_path))
    # Re-resolve paths under the new env.
    from msgviz.paths import db_file, schema_sql, data_dir
    data_dir().mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_file())
    con.executescript(Path(schema_sql()).read_text())
    pid = con.execute("INSERT INTO person(display_name) VALUES('Me')").lastrowid
    con.execute(
        "INSERT INTO device(slug,name,type,owner_person_id) "
        "VALUES('mac_wa','Mac WA','mac_live',?)",
        (pid,),
    )
    con.commit()
    con.close()
    return tmp_path


def _import(**kw):
    from tools.import_whatsapp_live import import_live
    defaults = dict(
        device_slug="mac_wa", db_path=str(WA_DB), me_name="Me",
        with_media=False,
    )
    defaults.update(kw)
    return import_live(**defaults)


def _db(home):
    from msgviz.paths import db_file
    con = sqlite3.connect(db_file())
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

def test_full_ingest(home) -> None:
    stats = _import()
    assert stats["chats"] == 2
    assert stats["new"] == 9           # 5 one-to-one + 4 group
    assert stats["skipped_existing"] == 0
    con = _db(home)
    assert con.execute("SELECT COUNT(*) FROM message").fetchone()[0] == 9
    assert con.execute("SELECT COUNT(*) FROM chat").fetchone()[0] == 2
    con.close()


def test_source_refs_written(home) -> None:
    _import()
    con = _db(home)
    n = con.execute(
        "SELECT COUNT(*) FROM source_ref WHERE source='whatsapp_live:mac_wa'"
    ).fetchone()[0]
    assert n == 9
    con.close()


def test_reimport_is_incremental(home) -> None:
    _import()
    stats2 = _import()
    assert stats2["new"] == 0
    assert stats2["skipped_existing"] == 9
    con = _db(home)
    # No duplication.
    assert con.execute("SELECT COUNT(*) FROM message").fetchone()[0] == 9
    con.close()


def test_dry_run_writes_nothing(home) -> None:
    stats = _import(report_only=True)
    assert stats["new"] == 9           # would-import count
    con = _db(home)
    assert con.execute("SELECT COUNT(*) FROM message").fetchone()[0] == 0
    con.close()


def test_sender_resolution(home) -> None:
    _import()
    con = _db(home)
    rows = {
        r["text"]: (r["is_me"], r["display_name"])
        for r in con.execute(
            "SELECT m.text, m.is_me, p.display_name FROM message m "
            "JOIN person p ON p.id = m.sender_person_id"
        )
    }
    # My own message → is_me, "Me".
    assert rows["Got it, thanks!"][0] == 1
    # Group sender resolved to a person (from the member JID), not the group.
    assert rows["Standup in 5?"][0] == 0
    con.close()


def test_chat_filter(home) -> None:
    stats = _import(chat_filter="Alice")
    assert stats["chats"] == 1
    con = _db(home)
    assert con.execute("SELECT COUNT(*) FROM chat").fetchone()[0] == 1
    con.close()


# ---------------------------------------------------------------------------
# Drift persistence
# ---------------------------------------------------------------------------

def test_drift_recorded_to_db(home) -> None:
    _import()
    con = _db(home)
    # The fixture omits optional columns (warn) and has a type-99 row
    # (unknown_enum_value) → drift rows exist under whatsapp_live.
    n = con.execute(
        "SELECT COUNT(*) FROM drift_event WHERE source='whatsapp_live'"
    ).fetchone()[0]
    assert n > 0
    assert con.execute(
        "SELECT COUNT(*) FROM drift_event WHERE kind='unknown_enum_value'"
    ).fetchone()[0] >= 1
    con.close()


def test_fatal_drift_aborts_and_writes_nothing(home, tmp_path) -> None:
    broken = tmp_path / "broken.db"
    b = sqlite3.connect(broken)
    b.executescript("""
        CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY,
            ZMESSAGEDATE TIMESTAMP, ZFROMJID VARCHAR, ZISFROMME INTEGER,
            ZCHATSESSION INTEGER, ZMESSAGETYPE INTEGER);
        CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY,
            ZCONTACTJID VARCHAR, ZSESSIONTYPE INTEGER);
        CREATE TABLE ZWAMEDIAITEM (Z_PK INTEGER PRIMARY KEY, ZMESSAGE INTEGER);
        CREATE TABLE ZWAGROUPMEMBER (Z_PK INTEGER PRIMARY KEY, ZMEMBERJID VARCHAR);
    """)  # ZSTANZAID (required) missing → fatal
    b.commit()
    b.close()
    with pytest.raises(SystemExit):
        _import(db_path=str(broken))
    con = _db(home)
    assert con.execute("SELECT COUNT(*) FROM message").fetchone()[0] == 0
    assert con.execute(
        "SELECT COUNT(*) FROM drift_event WHERE severity='fatal'"
    ).fetchone()[0] >= 1
    con.close()


def test_unknown_device_raises(home) -> None:
    with pytest.raises(SystemExit):
        _import(device_slug="does_not_exist")
