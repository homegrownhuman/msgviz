# -*- coding: utf-8 -*-
"""
Characterization tests for msgviz.core.migrate.Migrator (pre-phase-0.7).

These tests pin the CURRENT behavior of Migrator.load_config():
* sources.json[people] -> persons + handles
* sources.json[devices] -> device rows with owner_person_id
* Chat metadata (title/subtitle/origin) is remembered from the chats array
* OWNER_ALIAS maps short names (e.g. "Alice") to canonical
  ("Alice Example") via the env override `MSGVIZ_OWNER_ALIASES`

Before 0.7 retires the people map, these tests document what must NOT
break:
1. Migrator is idempotent — calling load_config() twice produces no
   duplicates.
2. Migrator works without a people key (empty or missing people map).
3. Migrator preserves existing persons/handles in the DB.
4. PERSON_BY_HANDLE logic (for sender resolution) works.

The real data/visualizer.db is NEVER touched — all tests run against
tmp_visualizer_db (a fresh tmp DB with the current schema).
"""
from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------
def _patch_config(monkeypatch, cfg_dict):
    """Point msgviz.core.migrate.CONFIG at a tmp path with the given dict."""
    import tempfile
    import os

    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg_dict, f)
    monkeypatch.setattr("msgviz.core.migrate.CONFIG", path, raising=True)
    return path


def _run_load_config(con, monkeypatch, cfg_dict):
    """Patch CONFIG and call Migrator.load_config() once."""
    from msgviz.core.migrate import Migrator

    _patch_config(monkeypatch, cfg_dict)
    mig = Migrator(con)
    return mig.load_config()


# ---------------------------------------------------------------------------
#  Baseline: default config
# ---------------------------------------------------------------------------
SAMPLE_CFG = {
    "people": {
        "+491701234567": "Alice",
        "alice@example.com": "Alice",
        "+491709876543": "Bob",
    },
    "devices": [
        {
            "type": "mac_live",
            "slug": "mac_test",
            "name": "Test-Mac",
            "me_name": "Alice",
            "chats": [
                {
                    "slug": "bob",
                    "title": "Bob",
                    "subtitle": "+49 170 9876543",
                    "is_group": False,
                    "origin": "apple",
                    "source_id": "1",
                }
            ],
        },
    ],
}


# ---------------------------------------------------------------------------
#  1. Baseline: persons, handles, devices are created correctly
# ---------------------------------------------------------------------------
def test_load_config_creates_persons_from_people_map(tmp_visualizer_db, monkeypatch):
    cfg, dev_ids = _run_load_config(tmp_visualizer_db, monkeypatch, SAMPLE_CFG)
    # 2 unique persons (Alice 2×, Bob 1× → 2 persons).
    persons = tmp_visualizer_db.execute(
        "SELECT display_name FROM person ORDER BY display_name"
    ).fetchall()
    names = [r[0] for r in persons]
    assert "Alice" in names
    assert "Bob" in names


def test_load_config_links_handles_to_persons(tmp_visualizer_db, monkeypatch):
    _run_load_config(tmp_visualizer_db, monkeypatch, SAMPLE_CFG)
    rows = tmp_visualizer_db.execute(
        """SELECT h.value, p.display_name
           FROM handle h JOIN person p ON p.id = h.person_id
           ORDER BY h.value"""
    ).fetchall()
    handle_map = {r[0]: r[1] for r in rows}
    assert handle_map["+491701234567"] == "Alice"
    assert handle_map["alice@example.com"] == "Alice"
    assert handle_map["+491709876543"] == "Bob"


def test_load_config_creates_device_with_owner(tmp_visualizer_db, monkeypatch):
    cfg, dev_ids = _run_load_config(tmp_visualizer_db, monkeypatch, SAMPLE_CFG)
    row = tmp_visualizer_db.execute(
        """SELECT d.slug, d.name, d.type, p.display_name AS owner
           FROM device d JOIN person p ON p.id = d.owner_person_id"""
    ).fetchone()
    assert row["slug"] == "mac_test"
    assert row["name"] == "Test-Mac"
    assert row["type"] == "mac_live"
    assert row["owner"] == "Alice"
    assert dev_ids["mac_test"] == row["slug"] or isinstance(dev_ids["mac_test"], int)


# ---------------------------------------------------------------------------
#  2. OWNER_ALIAS behavior (short name -> canonical name)
# ---------------------------------------------------------------------------
def test_owner_alias_default_empty(tmp_visualizer_db, monkeypatch):
    """Default OWNER_ALIAS is empty. me_name lands in the DB 1:1."""
    monkeypatch.delenv("MSGVIZ_OWNER_ALIASES", raising=False)
    cfg = {
        "people": {"+491701234567": "Alice Example"},
        "devices": [
            {
                "type": "mac_live",
                "slug": "mac_x",
                "name": "Mac",
                "me_name": "Alice",  # short name — without env override stays "Alice"
                "chats": [],
            }
        ],
    }
    _run_load_config(tmp_visualizer_db, monkeypatch, cfg)
    persons = tmp_visualizer_db.execute(
        "SELECT display_name FROM person ORDER BY display_name"
    ).fetchall()
    names = [r[0] for r in persons]
    # TWO persons — because the map is empty: "Alice" and "Alice Example".
    assert "Alice" in names
    assert "Alice Example" in names


def test_owner_alias_via_env_override(tmp_visualizer_db, monkeypatch):
    """With MSGVIZ_OWNER_ALIASES="Alice:Alice Example", the short name is
    resolved to the full name — back to one person."""
    monkeypatch.setenv("MSGVIZ_OWNER_ALIASES", "Alice: Alice Example")
    cfg = {
        "people": {"+491701234567": "Alice Example"},
        "devices": [
            {
                "type": "mac_live",
                "slug": "mac_x",
                "name": "Mac",
                "me_name": "Alice",
                "chats": [],
            }
        ],
    }
    _run_load_config(tmp_visualizer_db, monkeypatch, cfg)
    persons = tmp_visualizer_db.execute(
        "SELECT display_name FROM person ORDER BY display_name"
    ).fetchall()
    names = [r[0] for r in persons]
    assert names == ["Alice Example"], names


# ---------------------------------------------------------------------------
#  3. Edge cases: no people key
# ---------------------------------------------------------------------------
def test_load_config_without_people_key(tmp_visualizer_db, monkeypatch):
    """sources.json without a 'people' key must not crash. Persons then
    only come from device owners.
    """
    cfg = {
        "devices": [
            {
                "type": "static",
                "slug": "dev_x",
                "name": "Dev",
                "me_name": "Charlie",
                "chats": [],
            }
        ]
    }
    _run_load_config(tmp_visualizer_db, monkeypatch, cfg)
    persons = tmp_visualizer_db.execute("SELECT display_name FROM person").fetchall()
    names = [r[0] for r in persons]
    assert "Charlie" in names
    handles = tmp_visualizer_db.execute("SELECT COUNT(*) FROM handle").fetchone()[0]
    assert handles == 0  # no people map -> no handles


def test_load_config_with_empty_people_dict(tmp_visualizer_db, monkeypatch):
    """`people: {}` (empty but present) behaves like a missing key."""
    cfg = {"people": {}, "devices": []}
    _run_load_config(tmp_visualizer_db, monkeypatch, cfg)
    # No crash, no persons, no handles.
    n_p = tmp_visualizer_db.execute("SELECT COUNT(*) FROM person").fetchone()[0]
    n_h = tmp_visualizer_db.execute("SELECT COUNT(*) FROM handle").fetchone()[0]
    assert n_p == 0 and n_h == 0


# ---------------------------------------------------------------------------
#  4. Existing DB content is preserved
# ---------------------------------------------------------------------------
def test_existing_persons_preserved_when_alias_matches(tmp_visualizer_db, monkeypatch):
    """If the DB already has 'Alice', load_config() does NOT add her again —
    PersonResolver matches on display_name."""
    # Setup: Alice directly in the DB.
    tmp_visualizer_db.execute(
        "INSERT INTO person(display_name) VALUES(?)", ("Alice",)
    )
    tmp_visualizer_db.commit()
    alice_id = tmp_visualizer_db.execute(
        "SELECT id FROM person WHERE display_name='Alice'"
    ).fetchone()[0]

    cfg = {
        "people": {"+491701234567": "Alice"},
        "devices": [],
    }
    _run_load_config(tmp_visualizer_db, monkeypatch, cfg)

    persons = tmp_visualizer_db.execute(
        "SELECT id, display_name FROM person WHERE display_name='Alice'"
    ).fetchall()
    assert len(persons) == 1, "Alice was duplicated"
    assert persons[0]["id"] == alice_id

    handle = tmp_visualizer_db.execute(
        "SELECT person_id FROM handle WHERE value=?", ("+491701234567",)
    ).fetchone()
    assert handle is not None
    assert handle["person_id"] == alice_id


def test_existing_handle_not_duplicated(tmp_visualizer_db, monkeypatch):
    """An existing handle is not inserted twice."""
    # Alice + handle directly.
    alice_id = tmp_visualizer_db.execute(
        "INSERT INTO person(display_name) VALUES('Alice')"
    ).lastrowid
    tmp_visualizer_db.execute(
        "INSERT INTO handle(value, person_id) VALUES(?, ?)",
        ("+491701234567", alice_id),
    )
    tmp_visualizer_db.commit()

    cfg = {
        "people": {"+491701234567": "Alice"},
        "devices": [],
    }
    _run_load_config(tmp_visualizer_db, monkeypatch, cfg)

    n = tmp_visualizer_db.execute(
        "SELECT COUNT(*) FROM handle WHERE value=?", ("+491701234567",)
    ).fetchone()[0]
    assert n == 1, "Handle was duplicated"


# ---------------------------------------------------------------------------
#  5. Idempotency: load_config() twice on the same DB
# ---------------------------------------------------------------------------
def test_load_config_idempotent_persons_and_handles(tmp_visualizer_db, monkeypatch):
    """load_config() twice -> identical persons + handles counts."""
    from msgviz.core.migrate import Migrator

    _patch_config(monkeypatch, SAMPLE_CFG)

    Migrator(tmp_visualizer_db).load_config()
    n_p1 = tmp_visualizer_db.execute("SELECT COUNT(*) FROM person").fetchone()[0]
    n_h1 = tmp_visualizer_db.execute("SELECT COUNT(*) FROM handle").fetchone()[0]

    # IMPORTANT: device inserts would hit the UNIQUE(slug) constraint on the
    # second run — so delete the device rows first so load_config() only
    # re-iterates persons/handles.
    tmp_visualizer_db.execute("DELETE FROM device")
    tmp_visualizer_db.commit()

    Migrator(tmp_visualizer_db).load_config()
    n_p2 = tmp_visualizer_db.execute("SELECT COUNT(*) FROM person").fetchone()[0]
    n_h2 = tmp_visualizer_db.execute("SELECT COUNT(*) FROM handle").fetchone()[0]

    assert n_p2 == n_p1, f"persons duplicated: {n_p1} -> {n_p2}"
    assert n_h2 == n_h1, f"handles duplicated: {n_h1} -> {n_h2}"


# ---------------------------------------------------------------------------
#  6. Chat metadata is remembered
# ---------------------------------------------------------------------------
def test_chat_meta_remembered_from_config(tmp_visualizer_db, monkeypatch):
    """After load_config(), every chat slug is in Migrator._chat_meta."""
    from msgviz.core.migrate import Migrator

    _patch_config(monkeypatch, SAMPLE_CFG)
    mig = Migrator(tmp_visualizer_db)
    mig.load_config()
    assert "mac_test/bob" in mig._chat_meta
    meta = mig._chat_meta["mac_test/bob"]
    assert meta["title"] == "Bob"
    assert meta["subtitle"] == "+49 170 9876543"
    assert meta["origin"] == "apple"
    assert meta["is_group"] is False


# ---------------------------------------------------------------------------
#  7. PersonResolver is the central authority
# ---------------------------------------------------------------------------
def test_load_config_uses_person_resolver_case_insensitive(tmp_visualizer_db, monkeypatch):
    """PersonResolver.resolve_name() is case-INsensitive: 'alice' and 'Alice'
    map to the SAME person. This protects the people map from creating
    duplicate persons due to spelling differences.

    Documents CURRENT behavior — if 0.7 changes this, the test must be
    updated.
    """
    cfg = {
        "people": {
            "+491701234567": "alice",   # lowercase
            "alice@example.com": "Alice",  # capitalized
        },
        "devices": [],
    }
    _run_load_config(tmp_visualizer_db, monkeypatch, cfg)
    persons = tmp_visualizer_db.execute(
        "SELECT display_name FROM person"
    ).fetchall()
    names = [r[0] for r in persons]
    # Exactly ONE person — whichever spelling wins.
    assert len(names) == 1, f"expected 1 person, got {names}"
    # Both handles point at the same person.
    handles = tmp_visualizer_db.execute(
        "SELECT value, person_id FROM handle ORDER BY value"
    ).fetchall()
    assert len(handles) == 2
    assert handles[0]["person_id"] == handles[1]["person_id"]
