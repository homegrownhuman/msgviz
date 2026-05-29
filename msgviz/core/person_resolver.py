#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Central person/handle resolution.

Previously several code paths created persons independently of each
other (migrator initial fill, live sync, WhatsApp import, …).

Result: every bulk import with new sender-name spellings produced
duplicate persons in the DB (e.g. "Alice K. Example" vs "Alice") that
had to be merged via SQL afterwards.

Solution: this module exposes ONE resolver with three entry points
(`resolve_handle`, `resolve_name`, `merge_persons`) and a DB table
`person_alias` that stores multiple spellings.

Example:
  >>> r = PersonResolver(con)
  >>> r.resolve_handle("+491701234567")   # → person id, created if new
  >>> r.resolve_name("Alice")             # → existing person
  >>> r.add_alias("Alice K. Example", person_id=42)
  >>> r.resolve_name("alice k. example") == 42   # case-insensitive
"""
from __future__ import annotations

import sqlite3
from typing import Optional


def norm_handle(value: str | None) -> str | None:
    """Roughly normalize phone numbers / emails for handle lookup."""
    if not value:
        return value
    h = value.strip()
    if any(c.isdigit() for c in h) and "@" not in h:
        h = h.replace(" ", "").replace("-", "")
    return h.lower()


def _norm_name(name: str) -> str:
    """Name normalization for alias lookup: trim + lowercase + whitespace collapse."""
    return " ".join(name.lower().split())


class PersonResolver:
    """Resolver with connection state + in-memory cache.

    `con` is a sqlite3.Connection on visualizer.db. The resolver
    refreshes its caches automatically on first init from the DB.
    """

    def __init__(self, con: sqlite3.Connection):
        self.con = con
        # Caches: fast paths without DB roundtrips.
        self._by_handle: dict[str, int] = {}
        self._by_name: dict[str, int] = {}        # exact display_name
        self._by_alias_norm: dict[str, int] = {}  # _norm_name(alias) -> pid
        self._preload()

    # --- Init ----------------------------------------------------------------
    def _preload(self) -> None:
        for r in self.con.execute("SELECT id, display_name FROM person"):
            self._by_name[r[1]] = r[0]
            # The display name itself is also an implicit alias.
            self._by_alias_norm[_norm_name(r[1])] = r[0]
        for r in self.con.execute("SELECT value, person_id FROM handle"):
            self._by_handle[r[0]] = r[1]
        for r in self.con.execute("SELECT value, person_id FROM person_alias"):
            self._by_alias_norm[_norm_name(r[0])] = r[1]

    # --- Resolution ----------------------------------------------------------
    def resolve_handle(self, handle_value: str | None) -> Optional[int]:
        """Person id for a phone/email; creates an unknown one
        (display_name = handle value, handle row inserted)."""
        if not handle_value:
            return None
        nv = norm_handle(handle_value)
        if nv in self._by_handle:
            return self._by_handle[nv]
        # Cache miss: ask the DB (cache may have been built before external inserts).
        row = self.con.execute(
            "SELECT person_id FROM handle WHERE value=?", (nv,)
        ).fetchone()
        if row:
            self._by_handle[nv] = row[0]
            return row[0]
        # Unknown handle: new person, name = original handle value.
        pid = self._insert_person(handle_value)
        self._insert_handle(nv, pid)
        return pid

    def resolve_name(self, name: str | None) -> Optional[int]:
        """Person id for a sender display name; respects aliases
        (case-insensitive). Creates unknown names as a new person."""
        if not name:
            return None
        key = name.strip()
        # 1) Exact match in the DB (not just in the cache — external
        #    INSERTs may have happened after __init__).
        if key in self._by_name:
            return self._by_name[key]
        row = self.con.execute(
            "SELECT id FROM person WHERE display_name=?", (key,)
        ).fetchone()
        if row:
            self._by_name[key] = row[0]
            self._by_alias_norm.setdefault(_norm_name(key), row[0])
            return row[0]
        # 2) Alias lookup (case-insensitive, whitespace-normalized).
        nk = _norm_name(key)
        if nk in self._by_alias_norm:
            pid = self._by_alias_norm[nk]
            self._by_name.setdefault(key, pid)
            return pid
        row = self.con.execute(
            """SELECT person_id FROM person_alias
               WHERE lower(value) = ?""",
            (nk,),
        ).fetchone()
        if row:
            self._by_alias_norm[nk] = row[0]
            self._by_name.setdefault(key, row[0])
            return row[0]
        # 3) New person.
        return self._insert_person(key)

    # --- Mutations -----------------------------------------------------------
    def add_alias(self, value: str, person_id: int) -> None:
        """Record an additional spelling for an existing person."""
        v = value.strip()
        self.con.execute(
            "INSERT OR IGNORE INTO person_alias(value, person_id) VALUES(?, ?)",
            (v, person_id),
        )
        self._by_alias_norm[_norm_name(v)] = person_id

    def add_handle(self, handle_value: str, person_id: int) -> None:
        """Attach a handle (phone/email) to an existing person."""
        nv = norm_handle(handle_value)
        self._insert_handle(nv, person_id)

    def merge_persons(self, src_id: int, dst_id: int) -> None:
        """Move every reference from src_id to dst_id, then delete src_id.

        Idempotent: if src_id is already gone, nothing happens."""
        if src_id == dst_id:
            return
        if not self.con.execute(
            "SELECT 1 FROM person WHERE id=?", (src_id,)
        ).fetchone():
            return
        # Rewrite foreign keys.
        self.con.execute("UPDATE message SET sender_person_id=? WHERE sender_person_id=?",
                         (dst_id, src_id))
        self.con.execute(
            "UPDATE OR IGNORE chat_participant SET person_id=? WHERE person_id=?",
            (dst_id, src_id))
        self.con.execute("DELETE FROM chat_participant WHERE person_id=?", (src_id,))
        self.con.execute("UPDATE OR IGNORE handle SET person_id=? WHERE person_id=?",
                         (dst_id, src_id))
        self.con.execute("DELETE FROM handle WHERE person_id=?", (src_id,))
        self.con.execute("UPDATE OR IGNORE person_alias SET person_id=? WHERE person_id=?",
                         (dst_id, src_id))
        self.con.execute("DELETE FROM person_alias WHERE person_id=?", (src_id,))
        # Keep the src person's display_name as an alias on dst (for
        # future lookups using the old spelling).
        old_name = self.con.execute(
            "SELECT display_name FROM person WHERE id=?", (src_id,)
        ).fetchone()
        if old_name:
            self.add_alias(old_name[0], dst_id)
        self.con.execute("DELETE FROM person WHERE id=?", (src_id,))
        # Invalidate caches after the merge -> reload cycle.
        self._by_handle.clear()
        self._by_name.clear()
        self._by_alias_norm.clear()
        self._preload()

    # --- Internal helpers ----------------------------------------------------
    def _insert_person(self, display_name: str, note: str | None = None) -> int:
        pid = self.con.execute(
            "INSERT INTO person(display_name, note) VALUES(?, ?)",
            (display_name, note),
        ).lastrowid
        self._by_name[display_name] = pid
        self._by_alias_norm[_norm_name(display_name)] = pid
        return pid

    def _insert_handle(self, normalized_value: str, person_id: int) -> None:
        if not normalized_value:
            return
        if normalized_value in self._by_handle:
            return
        try:
            self.con.execute(
                "INSERT INTO handle(value, person_id) VALUES(?, ?)",
                (normalized_value, person_id),
            )
        except sqlite3.IntegrityError:
            # Race / duplicate insert — read value from DB and fill cache.
            row = self.con.execute(
                "SELECT person_id FROM handle WHERE value=?", (normalized_value,)
            ).fetchone()
            if row:
                self._by_handle[normalized_value] = row[0]
            return
        self._by_handle[normalized_value] = person_id
