# -*- coding: utf-8 -*-
"""
Unit tests for msgviz.adapters.whatsapp_export_schema.

The export adapter parses text, not SQLite, so its drift contract is
date-line formats + localized markers (proposal §13.12). Verifies the
probe recognises known formats, fatals on genuinely-unknown ones, and
warns on unknown attachment-marker locales — using the same DriftEvent
/ SchemaReport types as the SQLite adapters.
"""
from __future__ import annotations

import unicodedata
from pathlib import Path

from msgviz.adapters import whatsapp_export_schema as wes

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures" / "sample_whatsapp" / "_chat.txt"
)


def _strip(s: str) -> str:
    return "".join(ch for ch in s if unicodedata.category(ch) != "Cf")


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

def test_detect_german_bracket_format() -> None:
    fmt = wes.detect_format("[10.05.18, 12:30:00] Owner: hey")
    assert fmt is not None
    assert fmt.key == "bracket_dd_mm_yy_24h"


def test_detect_us_12h_format() -> None:
    fmt = wes.detect_format("[5/10/18, 12:30:00 PM] Owner: hi")
    assert fmt is not None
    assert fmt.key == "bracket_mdy_12h"


def test_detect_uk_24h_slash_format() -> None:
    fmt = wes.detect_format("[10/05/2018, 12:30:00] Owner: hi")
    assert fmt is not None
    assert fmt.key == "bracket_dmy_24h_slash"


def test_detect_android_nobracket_format() -> None:
    fmt = wes.detect_format("5/10/18, 12:30 PM - Owner: hi")
    assert fmt is not None
    assert fmt.key == "nobracket_mdy_12h_dash"


def test_detect_unknown_returns_none() -> None:
    assert wes.detect_format("2018-05-10T12:30:00 Owner: hi") is None
    assert wes.detect_format("just a plain continuation line") is None


def test_looks_like_header() -> None:
    assert wes.looks_like_header("[10.05.18, 12:30:00] Owner: hey")
    assert wes.looks_like_header("2018-05-10T12:30 Owner: hey")
    assert not wes.looks_like_header("a normal continuation line")


# ---------------------------------------------------------------------------
# Probe — the real fixture
# ---------------------------------------------------------------------------

def test_probe_real_fixture_is_clean() -> None:
    assert FIXTURE.exists()
    lines = [_strip(ln.rstrip("\n")) for ln in FIXTURE.open(encoding="utf-8")]
    report = wes.probe_export_text(lines)
    assert report.fatal_count == 0
    assert report.warn_count == 0


# ---------------------------------------------------------------------------
# Probe — drift cases
# ---------------------------------------------------------------------------

def test_probe_recognised_us_format_no_fatal() -> None:
    lines = [
        "[5/10/18, 12:30:00 PM] Owner: Hey there",
        "[5/10/18, 12:30:15 PM] Alice: hi!",
    ]
    report = wes.probe_export_text(lines)
    # Recognised (even though the parser can't yet parse it) → no fatal.
    assert report.fatal_count == 0


def test_probe_unknown_format_is_fatal() -> None:
    lines = [
        "2018-05-10T12:30:00 Owner: hey",
        "2018-05-10T12:30:15 Alice: hi",
    ]
    report = wes.probe_export_text(lines)
    assert report.is_fatal
    e = next(x for x in report.events if x.kind == "unknown_export_format")
    assert e.severity == "fatal"
    assert "2018-05-10" in (e.observed or "")


def test_probe_no_headers_at_all_is_not_fatal() -> None:
    # A file with no date-like lines (e.g. just a preamble) shouldn't
    # fatal — there's nothing claiming to be a header to mis-parse.
    lines = [
        "Messages and calls are end-to-end encrypted.",
        "some random note without a date",
    ]
    report = wes.probe_export_text(lines)
    assert report.is_fatal is False


def test_probe_unknown_attach_locale_warns() -> None:
    lines = ["[10.05.18, 12:30:00] Owner: regarde <fichier: photo.jpg>"]
    report = wes.probe_export_text(lines)
    assert report.fatal_count == 0
    warns = [e for e in report.events if e.kind == "unknown_export_locale"]
    assert len(warns) == 1
    assert warns[0].observed == "fichier"
    assert warns[0].severity == "warn"


def test_probe_known_attach_locale_no_warn() -> None:
    lines = ["[10.05.18, 12:30:00] Owner: look <attached: photo.jpg>"]
    report = wes.probe_export_text(lines)
    assert not any(
        e.kind == "unknown_export_locale" for e in report.events
    )


def test_probe_german_attach_keyword_no_warn() -> None:
    lines = ["[10.05.18, 12:30:00] Owner: guck <Anhang: foto.jpg>"]
    report = wes.probe_export_text(lines)
    assert not any(
        e.kind == "unknown_export_locale" for e in report.events
    )


def test_source_name_is_whatsapp_export() -> None:
    lines = ["2018-05-10T12:30:00 Owner: hey"]
    report = wes.probe_export_text(lines)
    assert all(e.source == "whatsapp_export" for e in report.events)
