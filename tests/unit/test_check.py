# -*- coding: utf-8 -*-
"""
Smoke test for `msgviz check` — the selftest / dependency-audit command.

Locks in:
  1. JSON output is parseable and has the expected top-level keys.
  2. Each probe has feature/status/detail.
  3. status is one of ok/degraded/missing.
  4. baseline_ok is true on the test environment (the test runner has
     fastapi + typer + rich installed — otherwise tests couldn't run).
  5. Exit code is 0 when baseline is ok.
"""
from __future__ import annotations

import json

from typer.testing import CliRunner

from msgviz.cli.main import app

runner = CliRunner()


def test_check_default_runs_clean():
    """Default output should at least mention 'Feature matrix'."""
    result = runner.invoke(app, ["check"])
    assert result.exit_code in (0, 1), result.output
    assert "Feature matrix" in result.output


def test_check_json_output_is_parseable():
    result = runner.invoke(app, ["check", "--json"])
    assert result.exit_code in (0, 1), result.output
    # First non-blank line should be the start of the JSON blob.
    payload = json.loads(result.stdout)
    assert "platform" in payload
    assert "python" in payload
    assert "baseline_ok" in payload
    assert "probes" in payload
    assert isinstance(payload["probes"], list)
    assert payload["probes"], "expected at least one probe"


def test_check_json_probe_shape():
    """Each probe carries the canonical fields."""
    result = runner.invoke(app, ["check", "--json"])
    payload = json.loads(result.stdout)
    for p in payload["probes"]:
        assert {"feature", "status", "detail", "consequence", "fix"} <= p.keys()
        assert p["status"] in {"ok", "degraded", "missing"}


def test_check_baseline_ok_in_test_env():
    """If pytest is running, fastapi+typer+rich must be importable —
    so baseline_ok should be True on any CI/dev box."""
    result = runner.invoke(app, ["check", "--json"])
    payload = json.loads(result.stdout)
    assert payload["baseline_ok"] is True
    assert result.exit_code == 0


def test_check_lists_expected_probes():
    """The set of probes shouldn't shrink quietly."""
    result = runner.invoke(app, ["check", "--json"])
    payload = json.loads(result.stdout)
    names = {p["feature"] for p in payload["probes"]}
    assert "Python version" in names
    assert "FastAPI / uvicorn" in names
    assert "ffmpeg" in names
    assert "whisper-cli" in names
    assert "OCR engine" in names


def test_check_verbose_includes_probe_table():
    """`-v` shows the per-probe table in addition to the feature matrix."""
    result = runner.invoke(app, ["check", "--verbose"])
    assert result.exit_code in (0, 1)
    assert "Feature matrix" in result.output
    assert "Probes" in result.output
