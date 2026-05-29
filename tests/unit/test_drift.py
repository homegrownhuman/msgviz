# -*- coding: utf-8 -*-
"""
Unit tests for msgviz.core.drift.

These pin the behaviour the proposal (docs/proposals/whatsapp_live.md
§13) promises: drift detection is loud, structured, deduped, and never
silent. Tests use in-memory SQLite for both the synthetic "source DB"
and the synthetic "msgviz DB" — no real WhatsApp / iMessage data is
touched.
"""
from __future__ import annotations

import sqlite3

import pytest

from msgviz.core import drift


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_source(ddl: str) -> sqlite3.Connection:
    """An in-memory SQLite playing the role of Apple/Meta's DB."""
    con = sqlite3.connect(":memory:")
    con.executescript(ddl)
    return con


def _make_mv() -> sqlite3.Connection:
    """An in-memory SQLite playing the role of msgviz's own DB."""
    con = sqlite3.connect(":memory:")
    drift.ensure_drift_event_table(con)
    return con


_WA_LIKE_CONTRACT = drift.SchemaContract(
    source="whatsapp_live",
    version=1,
    tables={
        "ZWAMESSAGE": drift.TableContract(
            required_columns={
                "Z_PK": "INTEGER",
                "ZSTANZAID": "TEXT",
                "ZMESSAGEDATE": "REAL",
                "ZFROMJID": "TEXT",
                "ZISFROMME": "INTEGER",
                "ZCHATSESSION": "INTEGER",
                "ZMESSAGETYPE": "INTEGER",
            },
            optional_columns={"ZTEXT", "ZSENTDATE", "ZPUSHNAME"},
        ),
    },
    known_enums={"ZWAMESSAGE.ZMESSAGETYPE": {0, 1, 2, 3, 7, 8}},
)


def _wa_ddl(
    *,
    drop_required: tuple[str, ...] = (),
    drop_optional: tuple[str, ...] = (),
    extra_columns: tuple[tuple[str, str], ...] = (),
    type_overrides: dict[str, str] | None = None,
) -> str:
    """Build a CREATE TABLE statement for ZWAMESSAGE with the requested
    deviations from the contract. Lets tests describe drift declaratively."""
    base = [
        ("Z_PK", "INTEGER PRIMARY KEY"),
        ("ZSTANZAID", "TEXT"),
        ("ZMESSAGEDATE", "REAL"),
        ("ZFROMJID", "TEXT"),
        ("ZISFROMME", "INTEGER"),
        ("ZCHATSESSION", "INTEGER"),
        ("ZMESSAGETYPE", "INTEGER"),
        ("ZTEXT", "TEXT"),
        ("ZSENTDATE", "REAL"),
        ("ZPUSHNAME", "TEXT"),
    ]
    type_overrides = type_overrides or {}
    cols = []
    for name, typ in base:
        if name in drop_required or name in drop_optional:
            continue
        cols.append(f"{name} {type_overrides.get(name, typ)}")
    for name, typ in extra_columns:
        cols.append(f"{name} {typ}")
    return f"CREATE TABLE ZWAMESSAGE ({', '.join(cols)})"


# ---------------------------------------------------------------------------
# Tests · probe_tables
# ---------------------------------------------------------------------------

def test_probe_clean_match_zero_events() -> None:
    src = _make_source(_wa_ddl())
    report = drift.probe_tables(src, _WA_LIKE_CONTRACT)
    assert report.events == ()
    assert report.fatal_count == 0
    assert report.warn_count == 0
    assert report.is_fatal is False


def test_probe_missing_required_column_is_fatal() -> None:
    src = _make_source(_wa_ddl(drop_required=("ZSTANZAID",)))
    report = drift.probe_tables(src, _WA_LIKE_CONTRACT)
    assert report.is_fatal
    fatals = [e for e in report.events if e.severity == "fatal"]
    assert len(fatals) == 1
    e = fatals[0]
    assert e.kind == "missing_required_column"
    assert e.table == "ZWAMESSAGE"
    assert e.column == "ZSTANZAID"
    assert "ZSTANZAID" in e.detail


def test_probe_missing_table_is_fatal() -> None:
    contract = drift.SchemaContract(
        source="whatsapp_live",
        version=1,
        tables={
            "ZWAMESSAGE": _WA_LIKE_CONTRACT.tables["ZWAMESSAGE"],
            "ZWANOTHERE": drift.TableContract(
                required_columns={"Z_PK": "INTEGER"},
            ),
        },
    )
    src = _make_source(_wa_ddl())
    report = drift.probe_tables(src, contract)
    assert report.is_fatal
    missing_table = [e for e in report.events if e.kind == "missing_table"]
    assert len(missing_table) == 1
    assert missing_table[0].table == "ZWANOTHERE"


def test_probe_missing_optional_column_is_warn_not_fatal() -> None:
    src = _make_source(_wa_ddl(drop_optional=("ZPUSHNAME",)))
    report = drift.probe_tables(src, _WA_LIKE_CONTRACT)
    assert report.is_fatal is False
    assert report.warn_count == 1
    e = report.events[0]
    assert e.kind == "missing_optional_column"
    assert e.column == "ZPUSHNAME"


def test_probe_new_column_is_warn() -> None:
    src = _make_source(_wa_ddl(extra_columns=(("ZFUTURE", "TEXT"),)))
    report = drift.probe_tables(src, _WA_LIKE_CONTRACT)
    assert report.is_fatal is False
    new_cols = [e for e in report.events if e.kind == "new_column"]
    assert len(new_cols) == 1
    e = new_cols[0]
    assert e.column == "ZFUTURE"
    assert e.observed == "TEXT"


def test_probe_type_change_on_required_column_is_fatal() -> None:
    # Required ZMESSAGEDATE was REAL; ship it as TEXT instead.
    src = _make_source(_wa_ddl(type_overrides={"ZMESSAGEDATE": "TEXT"}))
    report = drift.probe_tables(src, _WA_LIKE_CONTRACT)
    assert report.is_fatal
    type_changes = [e for e in report.events if e.kind == "type_changed"]
    assert len(type_changes) == 1
    e = type_changes[0]
    assert e.column == "ZMESSAGEDATE"
    assert e.observed == "TEXT"
    assert e.expected == "REAL"


@pytest.mark.parametrize(
    "declared,bucket",
    [
        ("INT", "INTEGER"),
        ("BIGINT", "INTEGER"),
        ("BOOLEAN", "INTEGER"),
        ("DOUBLE", "REAL"),
        ("FLOAT", "REAL"),
        ("VARCHAR", "TEXT"),
        ("VARCHAR(200)", "TEXT"),
        ("DECIMAL(10,2)", "NUMERIC"),
        ("", "BLOB"),
    ],
)
def test_storage_class_aliases_are_collapsed(
    declared: str, bucket: str
) -> None:
    # If Apple/Meta declares a column as one of the SQLite affinity-
    # aliases, the probe must treat it as the canonical storage class
    # and NOT flag a spurious type_changed event.
    src = _make_source(
        f"CREATE TABLE T (col {declared})" if declared else
        "CREATE TABLE T (col)"
    )
    contract = drift.SchemaContract(
        source="test",
        version=1,
        tables={"T": drift.TableContract(required_columns={"col": bucket})},
    )
    report = drift.probe_tables(src, contract)
    assert report.events == (), f"{declared!r} should normalise to {bucket}"


# ---------------------------------------------------------------------------
# Tests · enum allowlist
# ---------------------------------------------------------------------------

def test_check_enum_known_value_returns_none() -> None:
    assert drift.check_enum(
        _WA_LIKE_CONTRACT, "ZWAMESSAGE", "ZMESSAGETYPE", 0
    ) is None
    assert drift.check_enum(
        _WA_LIKE_CONTRACT, "ZWAMESSAGE", "ZMESSAGETYPE", 7
    ) is None


def test_check_enum_unknown_value_returns_warn_event() -> None:
    ev = drift.check_enum(
        _WA_LIKE_CONTRACT, "ZWAMESSAGE", "ZMESSAGETYPE", 999
    )
    assert ev is not None
    assert ev.severity == "warn"
    assert ev.kind == "unknown_enum_value"
    assert ev.observed == "999"


def test_check_enum_column_not_registered_returns_none() -> None:
    # No allowlist registered → caller doesn't care, we don't fire.
    assert drift.check_enum(
        _WA_LIKE_CONTRACT, "ZWAMESSAGE", "ZSENTDATE", "anything"
    ) is None


# ---------------------------------------------------------------------------
# Tests · persistence + dedup
# ---------------------------------------------------------------------------

def test_record_report_writes_one_row_per_event() -> None:
    src = _make_source(_wa_ddl(drop_required=("ZSTANZAID",)))
    mv = _make_mv()
    report = drift.probe_tables(src, _WA_LIKE_CONTRACT, now=1_700_000_000)
    n = drift.record_report(mv, report)
    mv.commit()
    assert n == len(report.events)
    count = mv.execute("SELECT COUNT(*) FROM drift_event").fetchone()[0]
    assert count == n


def test_record_report_dedups_repeat_drift() -> None:
    src = _make_source(_wa_ddl(extra_columns=(("ZFUTURE", "TEXT"),)))
    mv = _make_mv()
    report = drift.probe_tables(src, _WA_LIKE_CONTRACT, now=1_700_000_000)
    drift.record_report(mv, report)
    drift.record_report(mv, report)
    drift.record_report(mv, report)
    mv.commit()
    rows = mv.execute(
        "SELECT occurrence_count FROM drift_event"
    ).fetchall()
    # Each (source, kind, table, column, observed) tuple is unique →
    # one row per distinct event, occurrence_count = 3.
    assert all(r[0] == 3 for r in rows)
    assert len(rows) == len(report.events)


def test_record_report_last_seen_advances() -> None:
    src = _make_source(_wa_ddl(extra_columns=(("ZFUTURE", "TEXT"),)))
    mv = _make_mv()
    r1 = drift.probe_tables(src, _WA_LIKE_CONTRACT, now=1_000)
    drift.record_report(mv, r1)
    r2 = drift.probe_tables(src, _WA_LIKE_CONTRACT, now=2_000)
    drift.record_report(mv, r2)
    mv.commit()
    rows = mv.execute(
        "SELECT first_seen, last_seen FROM drift_event"
    ).fetchall()
    for first, last in rows:
        assert first == 1_000, "first_seen must not change on re-record"
        assert last == 2_000, "last_seen must advance on re-record"


def test_record_event_dedup_handles_null_columns() -> None:
    # missing_table events have column_name=None; dedup must not
    # explode on NULL (we COALESCE to '').
    mv = _make_mv()
    ev = drift.DriftEvent(
        source="test", severity="fatal", kind="missing_table",
        table="T", column=None, observed=None, expected="present",
        detail="T missing", seen_at=1_000,
    )
    rep = drift.SchemaReport(schema_version=1, events=(ev, ev))
    drift.record_report(mv, rep)
    mv.commit()
    rows = mv.execute("SELECT COUNT(*), MAX(occurrence_count) "
                       "FROM drift_event").fetchone()
    assert rows == (1, 2)


# ---------------------------------------------------------------------------
# Tests · query layer
# ---------------------------------------------------------------------------

def test_list_events_returns_only_unacked_by_default() -> None:
    mv = _make_mv()
    drift.record_report(mv, drift.SchemaReport(
        schema_version=1,
        events=(
            drift.DriftEvent(
                source="a", severity="warn", kind="new_column",
                table="t", column="c1", observed="TEXT",
                expected=None, detail="", seen_at=1,
            ),
            drift.DriftEvent(
                source="a", severity="warn", kind="new_column",
                table="t", column="c2", observed="TEXT",
                expected=None, detail="", seen_at=1,
            ),
        ),
    ))
    mv.commit()
    assert drift.pending_count(mv) == 2
    events = drift.list_events(mv)
    assert len(events) == 2
    drift.acknowledge(mv, events[0].id, now=42)
    assert drift.pending_count(mv) == 1
    assert len(drift.list_events(mv)) == 1
    assert len(drift.list_events(mv, include_acknowledged=True)) == 2


def test_list_events_filters_by_source() -> None:
    mv = _make_mv()
    drift.record_report(mv, drift.SchemaReport(
        schema_version=1,
        events=(
            drift.DriftEvent(
                source="a", severity="warn", kind="new_column",
                table="t", column="c", observed="TEXT",
                expected=None, detail="", seen_at=1,
            ),
            drift.DriftEvent(
                source="b", severity="warn", kind="new_column",
                table="t", column="c", observed="TEXT",
                expected=None, detail="", seen_at=1,
            ),
        ),
    ))
    mv.commit()
    a_only = drift.list_events(mv, source="a")
    assert len(a_only) == 1
    assert a_only[0].source == "a"
    assert drift.pending_count(mv, source="b") == 1


def test_acknowledge_is_idempotent() -> None:
    mv = _make_mv()
    drift.record_report(mv, drift.SchemaReport(
        schema_version=1,
        events=(
            drift.DriftEvent(
                source="a", severity="warn", kind="new_column",
                table="t", column="c", observed="TEXT",
                expected=None, detail="", seen_at=1,
            ),
        ),
    ))
    mv.commit()
    eid = drift.list_events(mv)[0].id
    assert drift.acknowledge(mv, eid, now=10) is True
    # Second ack on the same event is a no-op (already acknowledged):
    assert drift.acknowledge(mv, eid, now=20) is False


def test_acknowledge_all_clears_pending() -> None:
    mv = _make_mv()
    drift.record_report(mv, drift.SchemaReport(
        schema_version=1,
        events=tuple(
            drift.DriftEvent(
                source="a", severity="warn", kind="new_column",
                table="t", column=f"c{i}", observed="TEXT",
                expected=None, detail="", seen_at=1,
            )
            for i in range(5)
        ),
    ))
    mv.commit()
    assert drift.pending_count(mv) == 5
    n = drift.acknowledge_all(mv, now=10)
    assert n == 5
    assert drift.pending_count(mv) == 0
    # Audit row count unchanged:
    total = mv.execute("SELECT COUNT(*) FROM drift_event").fetchone()[0]
    assert total == 5


def test_acknowledge_all_with_source_filter() -> None:
    mv = _make_mv()
    drift.record_report(mv, drift.SchemaReport(
        schema_version=1,
        events=(
            drift.DriftEvent(
                source="a", severity="warn", kind="new_column",
                table="t", column="c", observed="TEXT",
                expected=None, detail="", seen_at=1,
            ),
            drift.DriftEvent(
                source="b", severity="warn", kind="new_column",
                table="t", column="c", observed="TEXT",
                expected=None, detail="", seen_at=1,
            ),
        ),
    ))
    mv.commit()
    n = drift.acknowledge_all(mv, source="a")
    assert n == 1
    assert drift.pending_count(mv, source="a") == 0
    assert drift.pending_count(mv, source="b") == 1


# ---------------------------------------------------------------------------
# Tests · safe_canonicalize (the per-row safety net)
# ---------------------------------------------------------------------------

def test_safe_canonicalize_passes_through_on_success() -> None:
    captured: list[drift.DriftEvent] = []
    result = drift.safe_canonicalize(
        lambda row: {"body": row["text"]},
        {"text": "hello"},
        source="x",
        table="T",
        on_drift=captured.append,
    )
    assert result == {"body": "hello"}
    assert captured == []


def test_safe_canonicalize_records_and_returns_none_on_exception() -> None:
    captured: list[drift.DriftEvent] = []

    def bad_canon(row):
        raise ValueError("synthetic parse failure")

    result = drift.safe_canonicalize(
        bad_canon,
        {"raw": "row"},
        source="whatsapp_live",
        table="ZWAMESSAGE",
        on_drift=captured.append,
        now=5_000,
    )
    assert result is None
    assert len(captured) == 1
    e = captured[0]
    assert e.severity == "warn"
    assert e.kind == "row_parse_failed"
    assert e.source == "whatsapp_live"
    assert e.table == "ZWAMESSAGE"
    assert e.observed == "ValueError"
    assert "synthetic parse failure" in e.detail
    assert e.seen_at == 5_000


def test_safe_canonicalize_does_not_swallow_keyboard_interrupt() -> None:
    # Sanity check: an interrupt must propagate, not become a drift event.
    # We make sure the wrapper only catches Exception (not BaseException).
    def interrupt(row):
        raise KeyboardInterrupt("user hit ctrl-c")

    with pytest.raises(KeyboardInterrupt):
        drift.safe_canonicalize(
            interrupt, {}, source="x", table="T",
            on_drift=lambda _e: None,
        )


# ---------------------------------------------------------------------------
# Tests · SchemaDriftError
# ---------------------------------------------------------------------------

def test_schema_drift_error_carries_report() -> None:
    src = _make_source(_wa_ddl(drop_required=("ZSTANZAID",)))
    report = drift.probe_tables(src, _WA_LIKE_CONTRACT)
    err = drift.SchemaDriftError(report)
    assert err.report is report
    msg = str(err)
    assert "whatsapp_live" in msg
    assert "ZSTANZAID" in msg


def test_schema_drift_error_with_empty_events_has_generic_msg() -> None:
    report = drift.SchemaReport(schema_version=1, events=())
    err = drift.SchemaDriftError(report)
    assert "fatal schema drift" in str(err)
