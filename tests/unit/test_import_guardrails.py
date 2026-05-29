# -*- coding: utf-8 -*-
"""
Tests for the whatsapp-live import guardrails:

* refuse-and-list when no chat is selected (writes nothing),
* dry-run preview shows new-person creation and writes nothing,
* explicit selection (--all-chats / --chat) imports,
* preview_live() person-safety detection (new vs matched),
* PersonResolver.would_match_name() is non-mutating.
"""
from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from msgviz.cli.main import app

FIX = Path(__file__).resolve().parents[1] / "fixtures"
WA_DB = FIX / "sample_whatsapp.db"
_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

runner = CliRunner()


@pytest.fixture(autouse=True)
def _quiet():
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MSGVIZ_HOME", str(tmp_path))
    from msgviz.paths import db_file, schema_sql, data_dir
    data_dir().mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_file())
    con.executescript(Path(schema_sql()).read_text())
    pid = con.execute("INSERT INTO person(display_name) VALUES('Me')").lastrowid
    con.execute(
        "INSERT INTO device(slug,name,type,owner_person_id) "
        "VALUES('mac_wa','M','mac_live',?)", (pid,)
    )
    con.commit()
    con.close()
    return tmp_path


def _msg_count(home):
    from msgviz.paths import db_file
    con = sqlite3.connect(db_file())
    n = con.execute("SELECT COUNT(*) FROM message").fetchone()[0]
    con.close()
    return n


# ---------------------------------------------------------------------------
# Refuse-and-list
# ---------------------------------------------------------------------------

def test_bare_device_lists_and_writes_nothing(home) -> None:
    res = runner.invoke(app, [
        "import", "whatsapp-live", "--device", "mac_wa", "--db", str(WA_DB),
    ])
    assert res.exit_code == 0
    assert "No chat selected" in res.stdout
    assert "Alice" in res.stdout
    assert "Dev Team" in res.stdout
    assert "Nothing was written" in res.stdout
    assert _msg_count(home) == 0


# ---------------------------------------------------------------------------
# Dry-run preview
# ---------------------------------------------------------------------------

def test_dry_run_previews_new_persons_writes_nothing(home) -> None:
    res = runner.invoke(app, [
        "import", "whatsapp-live", "--device", "mac_wa", "--all-chats",
        "--dry-run", "--db", str(WA_DB),
    ])
    assert res.exit_code == 0
    assert "new person(s) will be created" in res.stdout
    # The raw JIDs show up as the new persons.
    assert "@s.whatsapp.net" in res.stdout
    assert "nothing written" in res.stdout.lower()
    assert _msg_count(home) == 0


# ---------------------------------------------------------------------------
# Explicit selection imports
# ---------------------------------------------------------------------------

def test_all_chats_yes_imports(home) -> None:
    res = runner.invoke(app, [
        "import", "whatsapp-live", "--device", "mac_wa", "--all-chats",
        "--yes", "--no-media", "--no-progress", "--db", str(WA_DB),
    ])
    assert res.exit_code == 0
    assert "Imported:" in res.stdout
    assert _msg_count(home) == 8


def test_chat_filter_imports_only_match(home) -> None:
    res = runner.invoke(app, [
        "import", "whatsapp-live", "--device", "mac_wa", "--chat", "Alice",
        "--yes", "--no-media", "--no-progress", "--db", str(WA_DB),
    ])
    assert res.exit_code == 0
    # Only the 1:1 with Alice (4 messages), not the group.
    assert _msg_count(home) == 4


def test_confirmation_abort_writes_nothing(home) -> None:
    # No --yes: feed "n" to the confirm prompt.
    runner.invoke(app, [
        "import", "whatsapp-live", "--device", "mac_wa", "--all-chats",
        "--no-media", "--no-progress", "--db", str(WA_DB),
    ], input="n\n")
    assert _msg_count(home) == 0


# ---------------------------------------------------------------------------
# preview_live person safety
# ---------------------------------------------------------------------------

def test_preview_live_detects_new_persons(home) -> None:
    from tools.import_whatsapp_live import preview_live
    plan = preview_live(device_slug="mac_wa", db_path=str(WA_DB), me_name="Me")
    assert len(plan["chats"]) == 2
    # All three non-me senders are new (fresh DB).
    assert len(plan["new_persons"]) == 3
    assert all("@" in n for n in plan["new_persons"])


def test_preview_live_matches_existing_person(home) -> None:
    # Pre-create a person whose display_name equals one sender JID; the
    # preview must then NOT list that JID as new.
    from msgviz.paths import db_file
    con = sqlite3.connect(db_file())
    con.execute(
        "INSERT INTO person(display_name) VALUES('491700000001@s.whatsapp.net')"
    )
    con.commit()
    con.close()
    from tools.import_whatsapp_live import preview_live
    plan = preview_live(device_slug="mac_wa", db_path=str(WA_DB), me_name="Me")
    assert "491700000001@s.whatsapp.net" not in plan["new_persons"]
    assert len(plan["new_persons"]) == 2


# ---------------------------------------------------------------------------
# PersonResolver.would_match_name is non-mutating
# ---------------------------------------------------------------------------

def test_would_match_name_does_not_create(home) -> None:
    from msgviz.paths import db_file
    from msgviz.core.person_resolver import PersonResolver
    con = sqlite3.connect(db_file())
    con.row_factory = sqlite3.Row
    res = PersonResolver(con)
    before = con.execute("SELECT COUNT(*) FROM person").fetchone()[0]
    assert res.would_match_name("Totally New Name") is None
    after = con.execute("SELECT COUNT(*) FROM person").fetchone()[0]
    assert before == after          # no person created
    # Existing name matches.
    assert res.would_match_name("Me") is not None
    con.close()
