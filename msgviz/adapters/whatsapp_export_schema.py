# -*- coding: utf-8 -*-
"""
WhatsApp *export* (``_chat.txt``) format contract.

The export adapter parses a text file, not SQLite — so its "schema
contract" is not tables and columns but the set of **date-line format
variants** and **localized markers** the parser understands (proposal
§13.12). This proves the drift mechanism generalizes past SQLite: same
:class:`~msgviz.core.drift.DriftEvent` / :class:`SchemaReport` types,
different ``kind`` taxonomy:

* ``unknown_export_format`` — a line looks like a message header
  (starts with a bracketed/parenthesised date) but matches none of our
  known date-line regexes. Severity ``fatal`` when it's the *first*
  message line (we'd mis-parse the entire file), ``warn`` for stray
  later lines.
* ``unknown_export_locale`` — an ``<attached: …>``-style marker in a
  language whose spelling we don't recognise. Severity ``warn`` (the
  message text still imports; only the attachment link is missed).

WhatsApp localizes both the date format (by device region) and the
attachment / system markers (by device language). The current parser
(``whatsapp_export.py``) only matches ``[DD.MM.YY, HH:MM:SS]``; this
contract documents every variant we know and lets the probe tell the
user when an export doesn't fit, instead of silently folding
unparseable lines into the previous message.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from msgviz.core.drift import DriftEvent, SchemaReport

SOURCE_NAME = "whatsapp_export"
EXPORT_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Known date-line formats
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DateLineFormat:
    """One recognised WhatsApp export message-header format.

    Args:
        key: stable identifier recorded in drift ``observed`` so we know
            which variant matched (or that none did).
        label: human description for logs / the drift detail.
        regex: compiled pattern. Must match a full header line and
            capture the sender + the rest of the text; the date capture
            groups are format-specific and parsed by the adapter.
    """
    key: str
    label: str
    regex: re.Pattern


# The set the parser knows. Today whatsapp_export.py only *parses* the
# first one; the others are listed so the probe can recognise a valid
# export it can't yet parse and emit a precise drift event ("US 12-hour
# format detected, parser only handles DD.MM.YY") rather than a silent
# mis-parse. As the parser grows to handle more, move them from
# "recognised" to "parsed".
KNOWN_DATE_FORMATS: tuple[DateLineFormat, ...] = (
    DateLineFormat(
        key="bracket_dd_mm_yy_24h",
        label="[DD.MM.YY, HH:MM:SS]  (de/eu, 24-hour)  — parsed",
        regex=re.compile(
            r"^\[(\d{2})\.(\d{2})\.(\d{2}),\s*(\d{2}):(\d{2}):(\d{2})\]\s"
            r"([^:]+?):\s?(.*)$"
        ),
    ),
    DateLineFormat(
        key="bracket_mdy_12h",
        label="[M/D/YY, H:MM:SS AM/PM]  (us, 12-hour)  — recognised",
        regex=re.compile(
            r"^\[(\d{1,2})/(\d{1,2})/(\d{2,4}),\s*"
            r"(\d{1,2}):(\d{2}):(\d{2})\s*([AP]M)\]\s([^:]+?):\s?(.*)$",
            re.IGNORECASE,
        ),
    ),
    DateLineFormat(
        key="bracket_dmy_24h_slash",
        label="[DD/MM/YYYY, HH:MM:SS]  (uk/intl, 24-hour)  — recognised",
        regex=re.compile(
            r"^\[(\d{1,2})/(\d{1,2})/(\d{4}),\s*"
            r"(\d{2}):(\d{2}):(\d{2})\]\s([^:]+?):\s?(.*)$"
        ),
    ),
    DateLineFormat(
        key="nobracket_mdy_12h_dash",
        label="M/D/YY, H:MM PM - Sender:  (android export, no brackets)",
        regex=re.compile(
            r"^(\d{1,2})/(\d{1,2})/(\d{2,4}),\s*"
            r"(\d{1,2}):(\d{2})\s*([AP]M)?\s*-\s([^:]+?):\s?(.*)$",
            re.IGNORECASE,
        ),
    ),
)

# A loose detector: does a line *look like* a message header at all?
# (Opens with a bracket-or-digit date.) Used to distinguish "this is a
# header in a format we don't know" from "this is a continuation line".
_LOOKS_LIKE_HEADER = re.compile(r"^[\[\(]?\d{1,4}[\.\-/]\d{1,2}[\.\-/]\d{1,4}")


# ---------------------------------------------------------------------------
# Known localized markers
# ---------------------------------------------------------------------------
# Attachment markers by language. The parser's ATTACH_RE accepts these;
# a marker word we don't recognise → unknown_export_locale.
KNOWN_ATTACH_KEYWORDS = {
    "attached",   # en
    "Anhang",     # de
    "allegato",   # it
    "adjunto",    # es
    "bijlage",    # nl
}

KNOWN_SYSTEM_MARKERS = {
    "Messages and calls are end-to-end encrypted",
    "Nachrichten und Anrufe sind Ende-zu-Ende-verschlüsselt",
}

KNOWN_DELETED_MARKERS = {
    "This message was deleted.",
    "You deleted this message.",
    "Diese Nachricht wurde gelöscht.",
    "Du hast diese Nachricht gelöscht.",
}

# A generic "<word: filename>" detector, so we can spot an attachment
# marker in a language we don't have in KNOWN_ATTACH_KEYWORDS.
_GENERIC_ATTACH = re.compile(r"<\s*([^:<>]+?)\s*:\s*[^>]+\.\w{2,4}\s*>")


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------
def detect_format(line: str) -> Optional[DateLineFormat]:
    """Return the first known date format that matches the line, else None."""
    for fmt in KNOWN_DATE_FORMATS:
        if fmt.regex.match(line):
            return fmt
    return None


def looks_like_header(line: str) -> bool:
    """Heuristic: does the line open with a date (i.e. is it meant to be
    a message header rather than a continuation line)?"""
    return bool(_LOOKS_LIKE_HEADER.match(line))


def probe_export_text(
    sample_lines: list[str],
    *,
    now: Optional[int] = None,
) -> SchemaReport:
    """Inspect the head of a ``_chat.txt`` and report format drift.

    Args:
        sample_lines: the first N non-empty lines of the export
            (the adapter passes ~50; that's enough to find the first
            real message header past the E2E-notice preamble).
        now: timestamp override for the events (tests).

    Returns a :class:`SchemaReport`. ``is_fatal`` is True when no line
    in the sample matches any known date format *but* at least one line
    looks like a header — i.e. it's a real export in a format we can't
    parse, and proceeding would mis-attribute every line.
    """
    stamp = int(now) if now is not None else 0
    events: list[DriftEvent] = []

    header_like = [ln for ln in sample_lines if looks_like_header(ln)]
    matched_any = any(detect_format(ln) for ln in sample_lines)

    if header_like and not matched_any:
        # Looks like an export, but no known format matches → fatal.
        sample = header_like[0][:60]
        events.append(DriftEvent(
            source=SOURCE_NAME,
            severity="fatal",
            kind="unknown_export_format",
            table=None,
            column=None,
            observed=sample,
            expected=f"one of {[f.key for f in KNOWN_DATE_FORMATS]}",
            detail=(
                "_chat.txt has date-prefixed lines but none match a known "
                "WhatsApp export format. The exporting device likely uses a "
                "locale/region we don't parse yet (e.g. US 12-hour). Add a "
                "DateLineFormat to whatsapp_export_schema.py. Sample line: "
                f"{sample!r}"
            ),
            seen_at=stamp,
        ))

    # Unknown attachment-marker language: a <word: file.ext> where the
    # word isn't a known attach keyword (and isn't obviously a URL/time).
    for ln in sample_lines:
        m = _GENERIC_ATTACH.search(ln)
        if not m:
            continue
        word = m.group(1).strip()
        if word.lower() in {k.lower() for k in KNOWN_ATTACH_KEYWORDS}:
            continue
        # Avoid false-positives on things like "<https: //...>" — the
        # keyword should be a single alpha word.
        if not word.isalpha():
            continue
        events.append(DriftEvent(
            source=SOURCE_NAME,
            severity="warn",
            kind="unknown_export_locale",
            table=None,
            column=None,
            observed=word,
            expected=f"one of {sorted(KNOWN_ATTACH_KEYWORDS)}",
            detail=(
                f"attachment marker keyword {word!r} not recognised; the "
                "export is in a language whose 'attached:' word we don't "
                "know. Message text still imports, but this attachment "
                "link is missed. Add the keyword to KNOWN_ATTACH_KEYWORDS."
            ),
            seen_at=stamp,
        ))
        break  # one is enough to flag the locale

    return SchemaReport(schema_version=EXPORT_SCHEMA_VERSION, events=tuple(events))


__all__ = [
    "DateLineFormat",
    "EXPORT_SCHEMA_VERSION",
    "KNOWN_ATTACH_KEYWORDS",
    "KNOWN_DATE_FORMATS",
    "KNOWN_DELETED_MARKERS",
    "KNOWN_SYSTEM_MARKERS",
    "SOURCE_NAME",
    "detect_format",
    "looks_like_header",
    "probe_export_text",
]
