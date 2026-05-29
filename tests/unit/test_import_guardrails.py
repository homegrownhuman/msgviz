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
# No-selection → error pointing at discovery (not a silent "import nothing")
# ---------------------------------------------------------------------------

def test_no_selection_errors_and_points_to_discovery(home) -> None:
    res = runner.invoke(app, [
        "import", "whatsapp-live", "--device", "mac_wa", "--db", str(WA_DB),
    ])
    assert res.exit_code != 0
    assert "No chat selected" in res.output
    assert "msgviz whatsapp chats" in res.output
    assert _msg_count(home) == 0


# ---------------------------------------------------------------------------
# Discovery: msgviz whatsapp chats (no device needed)
# ---------------------------------------------------------------------------

def test_whatsapp_chats_lists_without_device(tmp_path, monkeypatch) -> None:
    # A bare MSGVIZ_HOME with NO initialized DB / device at all.
    # Fixture chats have 4 messages each, below the default ≥10 filter,
    # so pass -m 0 to see them all.
    monkeypatch.setenv("MSGVIZ_HOME", str(tmp_path))
    res = runner.invoke(app, ["whatsapp", "chats", "-m", "0", "--db", str(WA_DB)])
    assert res.exit_code == 0
    assert "Alice" in res.stdout
    assert "Dev Team" in res.stdout
    assert "2 WhatsApp chat(s)" in res.stdout


def test_whatsapp_chats_default_min_is_10(tmp_path, monkeypatch) -> None:
    # Bare invocation applies the ≥10 default; the 4-msg fixture chats
    # are hidden without the user passing anything.
    monkeypatch.setenv("MSGVIZ_HOME", str(tmp_path))
    res = runner.invoke(app, ["whatsapp", "chats", "--db", str(WA_DB)])
    assert res.exit_code == 0
    assert "Alice" not in res.stdout
    assert "below the threshold" in res.stdout


def test_whatsapp_chats_footer_no_devices(tmp_path, monkeypatch) -> None:
    # No archive at all → footer falls back to <slug> + the create hint.
    monkeypatch.setenv("MSGVIZ_HOME", str(tmp_path))
    res = runner.invoke(app, ["whatsapp", "chats", "-m", "0", "--db", str(WA_DB)])
    assert res.exit_code == 0
    assert "--device <slug>" in res.stdout
    assert "No devices yet" in res.stdout


def test_whatsapp_chats_footer_lists_real_devices(tmp_path, monkeypatch) -> None:
    # An archive with devices → footer uses a real slug + lists them.
    monkeypatch.setenv("MSGVIZ_HOME", str(tmp_path))
    from msgviz.paths import db_file, schema_sql, data_dir
    data_dir().mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_file())
    con.executescript(Path(schema_sql()).read_text())
    pid = con.execute("INSERT INTO person(display_name) VALUES('Me')").lastrowid
    for slug in ("ipad_levi", "mac_wa"):
        con.execute(
            "INSERT INTO device(slug,name,type,owner_person_id) "
            "VALUES(?,?,'mac_live',?)", (slug, slug, pid))
    con.commit()
    con.close()
    res = runner.invoke(app, ["whatsapp", "chats", "-m", "0", "--db", str(WA_DB)])
    assert res.exit_code == 0
    assert "--device <slug>" not in res.stdout      # real slug used
    assert "Your devices: ipad_levi, mac_wa" in res.stdout


def test_import_typo_device_shows_existing(tmp_path, monkeypatch) -> None:
    # A mistyped --device surfaces existing devices before creating one.
    monkeypatch.setenv("MSGVIZ_HOME", str(tmp_path))
    from msgviz.paths import db_file, schema_sql, data_dir
    data_dir().mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_file())
    con.executescript(Path(schema_sql()).read_text())
    pid = con.execute("INSERT INTO person(display_name) VALUES('Me')").lastrowid
    con.execute(
        "INSERT INTO device(slug,name,type,owner_person_id) "
        "VALUES('mac_wa','Mac WA','mac_live',?)", (pid,))
    con.commit()
    con.close()
    res = runner.invoke(app, [
        "import", "whatsapp-live", "--device", "mac_w",  # typo
        "--chat", "Alice", "--no-media", "--no-progress", "--db", str(WA_DB),
    ], input="n\n")  # decline creation
    assert "Existing devices: mac_wa" in res.stdout


def test_whatsapp_chats_filter(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MSGVIZ_HOME", str(tmp_path))
    res = runner.invoke(app, [
        "whatsapp", "chats", "--chat", "Alice", "-m", "0", "--db", str(WA_DB),
    ])
    assert res.exit_code == 0
    assert "Alice" in res.stdout
    assert "Dev Team" not in res.stdout


def test_whatsapp_chats_json(tmp_path, monkeypatch) -> None:
    import json
    monkeypatch.setenv("MSGVIZ_HOME", str(tmp_path))
    res = runner.invoke(app, [
        "whatsapp", "chats", "--json", "-m", "0", "--db", str(WA_DB),
    ])
    assert res.exit_code == 0
    data = json.loads(res.stdout)
    titles = {c["title"] for c in data["chats"]}
    assert titles == {"Alice", "Dev Team"}


def test_whatsapp_chats_min_messages_excludes_below(tmp_path, monkeypatch) -> None:
    # Fixture chats have 4 messages each → --min-messages 10 shows none.
    monkeypatch.setenv("MSGVIZ_HOME", str(tmp_path))
    res = runner.invoke(app, [
        "whatsapp", "chats", "--min-messages", "10", "--db", str(WA_DB),
    ])
    assert res.exit_code == 0
    assert "Alice" not in res.stdout
    assert "below the threshold" in res.stdout


def test_whatsapp_chats_min_messages_includes_at_threshold(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MSGVIZ_HOME", str(tmp_path))
    res = runner.invoke(app, [
        "whatsapp", "chats", "-m", "4", "--db", str(WA_DB),
    ])
    assert res.exit_code == 0
    assert "Alice" in res.stdout
    assert "Dev Team" in res.stdout


def test_whatsapp_chats_min_messages_json_filters(tmp_path, monkeypatch) -> None:
    import json
    monkeypatch.setenv("MSGVIZ_HOME", str(tmp_path))
    res = runner.invoke(app, [
        "whatsapp", "chats", "-m", "10", "--json", "--db", str(WA_DB),
    ])
    assert res.exit_code == 0
    assert json.loads(res.stdout)["chats"] == []


# ---------------------------------------------------------------------------
# Interactive device creation on import
# ---------------------------------------------------------------------------

def test_import_offers_device_creation(tmp_path, monkeypatch) -> None:
    # Fresh DB, NO device. Import should offer to create it.
    monkeypatch.setenv("MSGVIZ_HOME", str(tmp_path))
    from msgviz.paths import db_file, schema_sql, data_dir
    data_dir().mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_file())
    con.executescript(Path(schema_sql()).read_text())
    con.commit()
    con.close()
    # inputs: create? y / name (default) / owner (default) / proceed? y
    res = runner.invoke(app, [
        "import", "whatsapp-live", "--device", "new_wa", "--chat", "Alice",
        "--no-media", "--no-progress", "--db", str(WA_DB),
    ], input="y\n\n\ny\n")
    assert res.exit_code == 0
    con = sqlite3.connect(db_file())
    assert con.execute(
        "SELECT COUNT(*) FROM device WHERE slug='new_wa'"
    ).fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM message").fetchone()[0] == 5
    con.close()


def test_import_device_creation_declined_aborts(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MSGVIZ_HOME", str(tmp_path))
    from msgviz.paths import db_file, schema_sql, data_dir
    data_dir().mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_file())
    con.executescript(Path(schema_sql()).read_text())
    con.commit()
    con.close()
    # Decline device creation.
    res = runner.invoke(app, [
        "import", "whatsapp-live", "--device", "nope_wa", "--chat", "Alice",
        "--no-media", "--no-progress", "--db", str(WA_DB),
    ], input="n\n")
    assert res.exit_code != 0
    con = sqlite3.connect(db_file())
    assert con.execute(
        "SELECT COUNT(*) FROM device WHERE slug='nope_wa'"
    ).fetchone()[0] == 0
    con.close()


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
    assert _msg_count(home) == 9       # Alice 5 + Dev Team 4


def test_chat_filter_imports_only_match(home) -> None:
    res = runner.invoke(app, [
        "import", "whatsapp-live", "--device", "mac_wa", "--chat", "Alice",
        "--yes", "--no-media", "--no-progress", "--db", str(WA_DB),
    ])
    assert res.exit_code == 0
    # Only the 1:1 with Alice (5 messages), not the group.
    assert _msg_count(home) == 5


def test_one_to_one_creates_single_named_person(home) -> None:
    # The @lid-split fix: importing the Alice 1:1 must create ONE person
    # named "Alice", not two raw IDs (phone-JID + @lid).
    res = runner.invoke(app, [
        "import", "whatsapp-live", "--device", "mac_wa", "--chat", "Alice",
        "--yes", "--no-media", "--no-progress", "--db", str(WA_DB),
    ])
    assert res.exit_code == 0
    from msgviz.paths import db_file
    con = sqlite3.connect(db_file())
    # Exactly one non-"Me" person, and it's named "Alice".
    names = [r[0] for r in con.execute(
        "SELECT display_name FROM person WHERE display_name != 'Me'"
    )]
    con.close()
    assert names == ["Alice"]


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
    # 3 new senders: the 1:1 partner "Alice" (named) + the two group
    # member JIDs (group sender→name resolution is a separate concern).
    assert "Alice" in plan["new_persons"]
    assert len(plan["new_persons"]) == 3


def test_preview_live_matches_existing_person(home) -> None:
    # Pre-create the 1:1 partner "Alice"; the preview must then NOT list
    # Alice as new (the @lid-split collapse means the 1:1 resolves to
    # the partner name, which now matches an existing person).
    from msgviz.paths import db_file
    con = sqlite3.connect(db_file())
    con.execute("INSERT INTO person(display_name) VALUES('Alice')")
    con.commit()
    con.close()
    from tools.import_whatsapp_live import preview_live
    plan = preview_live(device_slug="mac_wa", db_path=str(WA_DB), me_name="Me")
    assert "Alice" not in plan["new_persons"]
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
