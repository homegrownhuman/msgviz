# -*- coding: utf-8 -*-
"""
Regression guard for msgviz.core.backup — DB backups before migrate.

Verifies:
1. backup_db() skips if the DB does not exist (bootstrap case).
2. backup_db() skips if the DB is empty (skip_if_empty=True).
3. backup_db() writes a copy to data/db-backups/pre-<tag>-<ts>.db when
   the DB has content.
4. The `tag` parameter is sanitized into the filename.
5. Old backups are pruned (FIFO) once N > MAX.
6. list_backups() returns paths sorted by mtime, newest first.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import pytest


@pytest.fixture
def msgviz_home(tmp_path, monkeypatch):
    """Isolates msgviz paths into tmp_path via the MSGVIZ_HOME override."""
    monkeypatch.setenv("MSGVIZ_HOME", str(tmp_path))
    # paths.py reads the env at call time; the data dir must exist already
    # for the backup target.
    (tmp_path / "data").mkdir()
    return tmp_path


def _make_seeded_db(path: Path) -> None:
    """Creates a tiny DB with one person row (so it has content)."""
    con = sqlite3.connect(str(path))
    con.executescript(
        """
        CREATE TABLE person (id INTEGER PRIMARY KEY, display_name TEXT);
        CREATE TABLE device (id INTEGER PRIMARY KEY, slug TEXT);
        CREATE TABLE chat   (id INTEGER PRIMARY KEY, slug TEXT);
        CREATE TABLE message(id INTEGER PRIMARY KEY, chat_id INTEGER);
        INSERT INTO person(display_name) VALUES('Alice');
        """
    )
    con.commit()
    con.close()


def _make_empty_db(path: Path) -> None:
    """DB with schema but no rows."""
    con = sqlite3.connect(str(path))
    con.executescript(
        """
        CREATE TABLE person (id INTEGER PRIMARY KEY, display_name TEXT);
        CREATE TABLE device (id INTEGER PRIMARY KEY, slug TEXT);
        CREATE TABLE chat   (id INTEGER PRIMARY KEY, slug TEXT);
        CREATE TABLE message(id INTEGER PRIMARY KEY, chat_id INTEGER);
        """
    )
    con.commit()
    con.close()


def test_backup_skipped_when_db_missing(msgviz_home):
    from msgviz.core.backup import backup_db

    result = backup_db("test")
    assert result is None


def test_backup_skipped_when_db_empty(msgviz_home):
    from msgviz.core.backup import backup_db
    from msgviz.paths import db_file

    _make_empty_db(db_file())
    result = backup_db("test")
    assert result is None


def test_backup_created_when_db_has_content(msgviz_home):
    from msgviz.core.backup import backup_db
    from msgviz.paths import db_file

    _make_seeded_db(db_file())
    result = backup_db("test")
    assert result is not None
    assert result.is_file()
    assert "pre-test-" in result.name
    assert result.name.endswith(".db")


def test_backup_tag_is_sanitized(msgviz_home):
    from msgviz.core.backup import backup_db
    from msgviz.paths import db_file

    _make_seeded_db(db_file())
    # tag with special characters — they must be replaced by _
    result = backup_db("migrate/people!out")
    assert result is not None
    # Allowed: alphanum, -, _
    name = result.name
    assert "/" not in name
    assert "!" not in name
    assert "pre-migrate_people_out-" in name


def test_backup_prunes_old_files(msgviz_home):
    from msgviz.core.backup import backup_db, list_backups
    from msgviz.paths import db_file

    _make_seeded_db(db_file())
    # Create 5 backups, keep only 3.
    for i in range(5):
        backup_db(f"run{i}", keep=3)
        time.sleep(0.01)  # distinct mtimes
    files = list_backups()
    assert len(files) == 3


def test_list_backups_sorted_by_mtime_descending(msgviz_home):
    from msgviz.core.backup import backup_db, list_backups
    from msgviz.paths import db_file

    _make_seeded_db(db_file())
    paths = []
    for i in range(3):
        p = backup_db(f"r{i}")
        paths.append(p)
    # Force distinct mtimes (otherwise they would all share the same second).
    now = time.time()
    os.utime(paths[0], (now - 30, now - 30))   # oldest
    os.utime(paths[1], (now - 15, now - 15))
    os.utime(paths[2], (now, now))             # newest
    listed = list_backups()
    # Newest first.
    assert listed[0].name.startswith("pre-r2-")
    assert listed[2].name.startswith("pre-r0-")


def test_backup_can_be_forced_on_empty_db(msgviz_home):
    """skip_if_empty=False writes a backup even without content."""
    from msgviz.core.backup import backup_db
    from msgviz.paths import db_file

    _make_empty_db(db_file())
    result = backup_db("forced", skip_if_empty=False)
    assert result is not None
    assert result.is_file()
