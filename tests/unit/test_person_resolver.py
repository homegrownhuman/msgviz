# -*- coding: utf-8 -*-
"""
Spec for the central PersonResolver.

The resolver maps sender names and source handles to a person id, creates
unknown persons on the fly, and respects an alias table in the DB. Four
historical resolution paths all funnel through this module.

Expected behavior:

1) Resolution via `handle` (phone number, email):
   - known handle value → existing person id.
   - unknown handle → new person (display_name = handle value),
     handle row inserted.

2) Resolution via `display_name`:
   - exact match → person id.
   - unknown name → new person.

3) Alias table `person_alias(value, person_id)`:
   - known alias → person id behind the alias.
   - aliasing is case-insensitive and respects whitespace.

4) Centralized rather than scattered:
   - same resolver instance called twice for the same handle/name
     returns the same person id.
"""
from __future__ import annotations

import sqlite3
import pytest


@pytest.fixture
def resolver(tmp_visualizer_db):
    """tmp_visualizer_db with the person_alias table; PersonResolver imported
    if available. Turns green as soon as schema + resolver exist."""
    from msgviz.core.person_resolver import PersonResolver
    r = PersonResolver(tmp_visualizer_db)
    return r, tmp_visualizer_db


def test_handle_unknown_creates_person_and_handle(resolver):
    r, con = resolver
    pid = r.resolve_handle("+491701234567")
    assert pid > 0
    name = con.execute("SELECT display_name FROM person WHERE id=?", (pid,)).fetchone()[0]
    assert name == "+491701234567"
    cnt = con.execute("SELECT COUNT(*) FROM handle WHERE person_id=?", (pid,)).fetchone()[0]
    assert cnt == 1


def test_handle_known_returns_existing_person(resolver):
    r, con = resolver
    p = con.execute("INSERT INTO person(display_name) VALUES('Alice')").lastrowid
    con.execute("INSERT INTO handle(value, person_id) VALUES(?,?)", ("+491701234567", p))
    con.commit()
    pid = r.resolve_handle("+491701234567")
    assert pid == p


def test_name_known_returns_existing_person(resolver):
    r, con = resolver
    p = con.execute("INSERT INTO person(display_name) VALUES('Alice')").lastrowid
    con.commit()
    pid = r.resolve_name("Alice")
    assert pid == p


def test_name_unknown_creates_person(resolver):
    r, con = resolver
    pid = r.resolve_name("Neu Da")
    assert pid > 0
    name = con.execute("SELECT display_name FROM person WHERE id=?", (pid,)).fetchone()[0]
    assert name == "Neu Da"


def test_alias_maps_to_canonical_person(resolver):
    """`Alice K. Example` is an alias for `Alice`."""
    r, con = resolver
    p = con.execute("INSERT INTO person(display_name) VALUES('Alice')").lastrowid
    con.execute(
        "INSERT INTO person_alias(value, person_id) VALUES(?,?)",
        ("Alice K. Example", p))
    con.commit()
    pid = r.resolve_name("Alice K. Example")
    assert pid == p
    # No new person row created.
    n = con.execute(
        "SELECT COUNT(*) FROM person WHERE display_name='Alice K. Example'"
    ).fetchone()[0]
    assert n == 0


def test_alias_is_case_insensitive(resolver):
    r, con = resolver
    p = con.execute("INSERT INTO person(display_name) VALUES('Alice')").lastrowid
    con.execute(
        "INSERT INTO person_alias(value, person_id) VALUES(?,?)",
        ("Alice Example", p))
    con.commit()
    assert r.resolve_name("alice example") == p


def test_repeated_calls_idempotent(resolver):
    r, con = resolver
    pid1 = r.resolve_name("Andere")
    pid2 = r.resolve_name("Andere")
    assert pid1 == pid2
    # Only ONE person row.
    n = con.execute("SELECT COUNT(*) FROM person WHERE display_name='Andere'").fetchone()[0]
    assert n == 1


def test_handle_then_name_yield_same_person_when_alias_set(resolver):
    """If I create a handle first and later resolve the same person by name
    (via an alias), I get the same id."""
    r, con = resolver
    p = r.resolve_handle("+491701234567")
    # Person auto-created with display_name = handle.
    # Now give them a real name and add the handle as an alias.
    con.execute("UPDATE person SET display_name='Alice' WHERE id=?", (p,))
    con.execute(
        "INSERT INTO person_alias(value, person_id) VALUES(?,?)",
        ("+491701234567", p))
    con.commit()
    assert r.resolve_handle("+491701234567") == p
    assert r.resolve_name("Alice") == p
