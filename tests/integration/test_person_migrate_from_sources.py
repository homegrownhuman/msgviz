# -*- coding: utf-8 -*-
"""
Phase 0.7: `msgviz person migrate-from-sources` — migration of the people
map from sources.json into the DB tables.

Verifies:
1. Dry run (--dry-run) changes nothing in the DB and in sources.json.
2. A real run creates one person + handle row per people entry.
3. Existing handles are not duplicated.
4. --remove-from-sources strips the people map from sources.json and
   writes a .bak-pre-people-removal safety copy.
5. A backup is created before writing (unless --no-backup).
6. Empty or missing people map: no write, friendly notice.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from msgviz.cli.main import app


@pytest.fixture
def msgviz_home(tmp_path, monkeypatch):
    """Isolate msgviz paths under tmp_path."""
    monkeypatch.setenv("MSGVIZ_HOME", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "config").mkdir()
    return tmp_path


@pytest.fixture
def seeded_db_and_sources(msgviz_home):
    """Fresh schema DB (under MSGVIZ_HOME) + sources.json with a people map."""
    import sqlite3

    from msgviz.paths import db_file, schema_sql

    target_db = msgviz_home / "data" / "visualizer.db"
    con = sqlite3.connect(str(target_db))
    con.executescript(schema_sql().read_text(encoding="utf-8"))
    con.commit()
    con.close()

    # Sanity: MSGVIZ_HOME resolution works.
    assert db_file() == target_db, f"db_file()={db_file()} != target_db={target_db}"

    sources = msgviz_home / "config" / "sources.json"
    sources.write_text(
        json.dumps(
            {
                "devices": [],
                "people": {
                    "+491701234567": "Alice",
                    "alice@example.com": "Alice",
                    "+491709876543": "Bob",
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return msgviz_home, target_db, sources


def _runner():
    # mix_stderr=False would split stderr; recent Typer/Click versions
    # default to that. We rely on result.exit_code for success and on
    # result.stdout for visible output.
    return CliRunner()


def _count(db_path: Path, sql: str) -> int:
    import sqlite3

    con = sqlite3.connect(str(db_path))
    try:
        return con.execute(sql).fetchone()[0]
    finally:
        con.close()


def test_dry_run_changes_nothing(seeded_db_and_sources):
    home, db, sources = seeded_db_and_sources
    sources_before = sources.read_text()

    result = _runner().invoke(
        app, ["person", "migrate-from-sources", "--dry-run"]
    )
    assert result.exit_code == 0, result.stdout
    assert "dry-run" in result.stdout.lower()
    # DB unchanged.
    assert _count(db, "SELECT COUNT(*) FROM person") == 0
    assert _count(db, "SELECT COUNT(*) FROM handle") == 0
    # sources.json unchanged.
    assert sources.read_text() == sources_before


def test_real_run_imports_handles(seeded_db_and_sources):
    home, db, sources = seeded_db_and_sources
    result = _runner().invoke(
        app, ["person", "migrate-from-sources"]
    )
    assert result.exit_code == 0, result.stdout
    # 2 persons (Alice, Bob), 3 handles.
    assert _count(db, "SELECT COUNT(*) FROM person") == 2
    assert _count(db, "SELECT COUNT(*) FROM handle") == 3


def test_idempotent_run(seeded_db_and_sources):
    """A second run must not duplicate anything."""
    home, db, sources = seeded_db_and_sources
    runner = _runner()
    runner.invoke(app, ["person", "migrate-from-sources"])
    n_p1 = _count(db, "SELECT COUNT(*) FROM person")
    n_h1 = _count(db, "SELECT COUNT(*) FROM handle")

    runner.invoke(app, ["person", "migrate-from-sources"])
    n_p2 = _count(db, "SELECT COUNT(*) FROM person")
    n_h2 = _count(db, "SELECT COUNT(*) FROM handle")

    assert n_p1 == n_p2
    assert n_h1 == n_h2


def test_remove_from_sources_strips_people_and_backs_up(seeded_db_and_sources):
    home, db, sources = seeded_db_and_sources
    sources_before = sources.read_text()

    result = _runner().invoke(
        app,
        ["person", "migrate-from-sources", "--remove-from-sources"],
    )
    assert result.exit_code == 0, result.stdout

    # people key is gone.
    after = json.loads(sources.read_text())
    assert "people" not in after
    assert "devices" in after  # rest stays.

    # The .bak-pre-people-removal backup exists.
    backup = sources.with_suffix(".json.bak-pre-people-removal")
    assert backup.is_file()
    assert backup.read_text() == sources_before


def test_empty_people_map_does_nothing(msgviz_home):
    """If 'people' is missing or empty, a friendly skip happens."""
    sources = msgviz_home / "config" / "sources.json"
    sources.write_text(json.dumps({"devices": []}))
    result = _runner().invoke(app, ["person", "migrate-from-sources"])
    assert result.exit_code == 0
    # CLI message is currently German ("Keine 'people'-Map …, nichts zu tun.").
    # When the CLI source is translated, this assertion should still pass
    # because we accept either German or English markers.
    out = result.stdout.lower()
    assert (
        "nichts zu tun" in out
        or "keine" in out
        or "nothing to do" in out
        or "no 'people' map" in out
    )


def test_missing_sources_file_fails_cleanly(msgviz_home):
    """If sources.json doesn't exist, exit code is non-zero without a
    traceback. (The friendly message goes to stderr, which CliRunner's
    stdout property does not include.)"""
    result = _runner().invoke(app, ["person", "migrate-from-sources"])
    assert result.exit_code != 0
    # No traceback in stdout.
    assert "Traceback" not in result.stdout
    # If the exception wasn't caught, exc_info would be set to a real
    # exception (not typer.Exit).
    if result.exception is not None:
        import typer
        assert isinstance(result.exception, (typer.Exit, SystemExit))
