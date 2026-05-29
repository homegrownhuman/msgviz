# -*- coding: utf-8 -*-
"""
Unit tests for the `msgviz drift` CLI command.

Seeds a temp visualizer.db with drift_event rows (via MSGVIZ_HOME) and
drives the command through Typer's CliRunner: list / --json / --explain
/ --ack / --ack-all, plus the exit-code-2-on-pending-fatal contract.
"""
from __future__ import annotations

import json
import logging
import sqlite3

import pytest
from typer.testing import CliRunner

from msgviz.cli.main import app
from msgviz.core import drift


@pytest.fixture(autouse=True)
def _quiet_drift_logs():
    # The drift recorder logs WARN per event; mute it so it doesn't
    # bleed into captured CLI output assertions.
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """A temp MSGVIZ_HOME with a visualizer.db carrying drift rows."""
    monkeypatch.setenv("MSGVIZ_HOME", str(tmp_path))
    data = tmp_path / "data"
    data.mkdir()
    con = sqlite3.connect(data / "visualizer.db")
    con.execute(
        "CREATE TABLE person (id INTEGER PRIMARY KEY, display_name TEXT, "
        "avatar_src TEXT)"
    )
    drift.ensure_drift_event_table(con)
    drift.record_report(con, drift.SchemaReport(
        schema_version=1,
        events=(
            drift.DriftEvent(
                source="whatsapp_live", severity="warn",
                kind="unknown_enum_value", table="ZWAMESSAGE",
                column="ZMESSAGETYPE", observed="99", expected="known",
                detail="unknown msg type 99", seen_at=1_700_000_000),
            drift.DriftEvent(
                source="imessage_live", severity="fatal",
                kind="missing_required_column", table="message",
                column="text", observed=None, expected="TEXT",
                detail="Apple removed message.text", seen_at=1_700_000_100),
        ),
    ))
    con.commit()
    con.close()
    return tmp_path


@pytest.fixture()
def empty_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MSGVIZ_HOME", str(tmp_path))
    data = tmp_path / "data"
    data.mkdir()
    con = sqlite3.connect(data / "visualizer.db")
    con.execute(
        "CREATE TABLE person (id INTEGER PRIMARY KEY, display_name TEXT, "
        "avatar_src TEXT)"
    )
    drift.ensure_drift_event_table(con)
    con.commit()
    con.close()
    return tmp_path


runner = CliRunner()


def test_list_shows_pending_and_exits_2_on_fatal(home) -> None:
    res = runner.invoke(app, ["drift"])
    assert res.exit_code == 2          # pending fatal present
    # The Rich table truncates long source names ("imessage_…"); the
    # exit code + the fatal banner are the stable signals here. Source
    # names are asserted against --json (test_json_output / source filter).
    assert "fatal" in res.stdout
    assert "fatal event(s)" in res.stdout


def test_empty_db_is_clean_exit_0(empty_home) -> None:
    res = runner.invoke(app, ["drift"])
    assert res.exit_code == 0
    assert "No schema-drift events" in res.stdout


def test_json_output(home) -> None:
    res = runner.invoke(app, ["drift", "--json"])
    assert res.exit_code == 0          # --json never exits 2
    payload = json.loads(res.stdout)
    assert payload["pending_count"] == 2
    kinds = {e["kind"] for e in payload["events"]}
    assert "missing_required_column" in kinds
    assert "unknown_enum_value" in kinds


def test_explain_one_source(home) -> None:
    res = runner.invoke(app, ["drift", "--explain", "imessage_live"])
    assert res.exit_code == 0
    assert "message.text" in res.stdout
    assert "Apple removed message.text" in res.stdout
    # The whatsapp event must not appear under this source.
    assert "ZWAMESSAGE" not in res.stdout


def test_source_filter(home) -> None:
    res = runner.invoke(app, ["drift", "--json", "--source", "whatsapp_live"])
    payload = json.loads(res.stdout)
    assert payload["pending_count"] == 1
    assert all(e["source"] == "whatsapp_live" for e in payload["events"])


def test_ack_one(home) -> None:
    res = runner.invoke(app, ["drift", "--ack", "1"])
    assert res.exit_code == 0
    assert "Acknowledged event 1" in res.stdout
    # Now only one pending remains.
    res2 = runner.invoke(app, ["drift", "--json"])
    assert json.loads(res2.stdout)["pending_count"] == 1


def test_ack_unknown_id_is_graceful(home) -> None:
    res = runner.invoke(app, ["drift", "--ack", "999"])
    assert res.exit_code == 0
    assert "not found or already acknowledged" in res.stdout


def test_ack_all(home) -> None:
    res = runner.invoke(app, ["drift", "--ack-all"])
    assert res.exit_code == 0
    assert "Acknowledged 2 event(s)" in res.stdout
    res2 = runner.invoke(app, ["drift"])
    assert res2.exit_code == 0
    assert "No schema-drift events" in res2.stdout


def test_ack_all_with_source_filter(home) -> None:
    res = runner.invoke(app, ["drift", "--ack-all", "--source", "whatsapp_live"])
    assert "Acknowledged 1 event(s)" in res.stdout
    # imessage fatal still pending → exit 2.
    res2 = runner.invoke(app, ["drift"])
    assert res2.exit_code == 2


def test_show_all_includes_acknowledged(home) -> None:
    runner.invoke(app, ["drift", "--ack-all"])
    # Default (pending) → empty.
    assert "No schema-drift events" in runner.invoke(app, ["drift"]).stdout
    # --all → the acked rows reappear (audit trail). Assert via --json
    # so the width-truncated table doesn't break the source-name check.
    res = runner.invoke(app, ["drift", "--all", "--json"])
    payload = json.loads(res.stdout)
    sources = {e["source"] for e in payload["events"]}
    assert sources == {"imessage_live", "whatsapp_live"}
    assert all(e["acknowledged_at"] is not None for e in payload["events"])
