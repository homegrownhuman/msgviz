# -*- coding: utf-8 -*-
"""
Shared test fixtures for the msgviz characterization test suite.

These fixtures create *fresh* test DBs loading the schema from
core/schema.sql. The real data/visualizer.db is never touched.
"""
import os
import sqlite3
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQL = PROJECT_ROOT / "msgviz" / "core" / "schema.sql"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _apply_schema(con: sqlite3.Connection) -> None:
    """Load core/schema.sql into the connection."""
    con.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    con.commit()


@pytest.fixture
def visualizer_db_path(tmp_path: Path) -> Path:
    """Path to a fresh visualizer.db inside tmpdir (file not yet created)."""
    return tmp_path / "visualizer.db"


@pytest.fixture
def tmp_visualizer_db(visualizer_db_path: Path):
    """Fresh, empty visualizer.db with the current schema. Yields the connection."""
    con = sqlite3.connect(str(visualizer_db_path))
    con.row_factory = sqlite3.Row
    _apply_schema(con)
    try:
        yield con
    finally:
        con.close()


def _seed_minimal_device(
    con: sqlite3.Connection,
    *,
    device_slug: str = "mac_alice",
    device_name: str = "Mac Book Pro M1 Max",
    device_type: str = "mac_live",
    me_name: str = "Owner",
) -> dict:
    """Insert a minimal Person + Device (mirrors migrate.py, but without the
    JSON pass). Returns {device_id, owner_person_id, me_name}."""
    pid = con.execute(
        "INSERT INTO person(display_name) VALUES(?)", (me_name,)
    ).lastrowid
    did = con.execute(
        "INSERT INTO device(slug,name,type,owner_person_id) VALUES(?,?,?,?)",
        (device_slug, device_name, device_type, pid),
    ).lastrowid
    con.commit()
    return {"device_id": did, "owner_person_id": pid, "me_name": me_name}


@pytest.fixture
def seed_device():
    """Factory fixture: inserts a device + owner into the given connection."""
    return _seed_minimal_device


@pytest.fixture
def seeded_visualizer_db(tmp_visualizer_db):
    """Fresh DB with one mac_alice device + Owner.
    Enough for e.g. the WhatsApp importer to find a device row."""
    _seed_minimal_device(tmp_visualizer_db)
    return tmp_visualizer_db


@pytest.fixture
def sample_chat_db_path() -> Path:
    return FIXTURES_DIR / "sample_chat.db"


@pytest.fixture
def sample_whatsapp_dir() -> Path:
    return FIXTURES_DIR / "sample_whatsapp"


@pytest.fixture
def sample_imgs_dir() -> Path:
    return FIXTURES_DIR / "sample_imgs"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR
