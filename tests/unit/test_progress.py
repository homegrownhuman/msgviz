# -*- coding: utf-8 -*-
"""Spec for ProgressReporter (EventReporter and NullReporter).

The TerminalReporter isn't tested directly (it renders via Rich into a
live region on stdout — hard to reproduce). We test that the phase API
behaves identically on every reporter.
"""
import json
import time
from pathlib import Path

import pytest


def test_null_reporter_works_silently():
    from msgviz.core.progress import make_reporter
    r = make_reporter("null")
    with r.phase("Quelle parsen") as p:
        p.set_total(3)
        for _ in range(3):
            p.tick()
        p.note("ok")
    r.close()


def test_event_reporter_writes_jsonl(tmp_path: Path):
    from msgviz.core.progress import make_reporter
    out = tmp_path / "events.jsonl"
    r = make_reporter("events", events_path=str(out))
    with r.phase("Quelle parsen", total=10) as p:
        for _ in range(10):
            p.tick()
    with r.phase("Medien", total=2) as p:
        with p.subtask("att_001.jpg") as s:
            s.note("HEIC→JPG")
        p.tick()
        with p.subtask("att_002.mp4") as s:
            s.note("mov→mp4")
        p.tick()
    r.close()
    lines = out.read_text().splitlines()
    events = [json.loads(l) for l in lines]
    kinds = [e["kind"] for e in events]
    assert kinds.count("phase_start") == 4   # 2 top + 2 sub
    assert kinds.count("phase_done") == 4
    # subtask has a path with 2 components.
    sub_starts = [e for e in events if e["kind"] == "phase_start" and len(e["path"]) == 2]
    assert len(sub_starts) == 2
    assert sub_starts[0]["path"] == ["Medien", "att_001.jpg"]


def test_event_reporter_records_counts_and_duration(tmp_path: Path):
    from msgviz.core.progress import make_reporter
    out = tmp_path / "ev.jsonl"
    r = make_reporter("events", events_path=str(out))
    with r.phase("Test", total=5) as p:
        for _ in range(5):
            p.tick()
        time.sleep(0.02)
    r.close()
    events = [json.loads(l) for l in out.read_text().splitlines()]
    done = next(e for e in events if e["kind"] == "phase_done")
    assert done["current"] == 5
    assert done["total"] == 5
    assert done["duration"] >= 0.02


def test_event_reporter_records_errors(tmp_path: Path):
    from msgviz.core.progress import make_reporter
    out = tmp_path / "ev.jsonl"
    r = make_reporter("events", events_path=str(out))
    with pytest.raises(RuntimeError):
        with r.phase("Boom"):
            raise RuntimeError("boom")
    r.close()
    events = [json.loads(l) for l in out.read_text().splitlines()]
    err = next(e for e in events if e["kind"] == "phase_error")
    assert "boom" in err["error"]


def test_factory_rejects_unknown_kind():
    from msgviz.core.progress import make_reporter
    with pytest.raises(ValueError):
        make_reporter("nope")


def test_factory_events_requires_path():
    from msgviz.core.progress import make_reporter
    with pytest.raises(ValueError):
        make_reporter("events")
