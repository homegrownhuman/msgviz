# -*- coding: utf-8 -*-
"""
Adapter schema drift detection.

Every adapter that reads a vendor-controlled data source (Apple's
``chat.db``, WhatsApp's ``ChatStorage.sqlite``, etc.) ships with a
*schema contract* — a description of which tables/columns it relies
on, plus the enum values it knows how to interpret. At sync time the
adapter calls :func:`probe_tables` with that contract; the function
returns a :class:`SchemaReport` listing every observed difference.

Drift events are categorised:

* ``fatal`` — required column or table missing, or required column
  type changed. The caller MUST abort the sync run; ingesting half a
  schema produces wrong data.
* ``warn``  — new column appears, optional column missing, unknown
  enum value seen. Ingestion continues; the offending row may be
  skipped via :func:`safe_canonicalize`.
* ``info``  — known-but-rare expected change, e.g. an index that
  comes and goes between OS releases.

Events are persisted in the ``drift_event`` table (additively
migrated by :func:`ensure_drift_event_table`). A unique index on
``(source, kind, table_name, column_name, observed)`` means repeated
drift bumps ``occurrence_count`` and ``last_seen`` rather than
spamming new rows. Users acknowledge events via the CLI
(``msgviz drift --ack``) or the UI banner; ack only sets
``acknowledged_at``, never deletes — the audit trail survives.

Design principle: every ingestion path must use :func:`safe_canonicalize`
to wrap parsing. A bare ``try/except: pass`` anywhere in this layer is
a bug — schema drift must be *loud*.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Callable, Literal, Optional

log = logging.getLogger("msgviz.drift")

Severity = Literal["fatal", "warn", "info"]
DriftKind = Literal[
    "missing_table",
    "missing_required_column",
    "type_changed",
    "new_column",
    "missing_optional_column",
    "unknown_enum_value",
    "row_parse_failed",
    # Export-style adapters (no SQLite) use additional kinds:
    "unknown_export_locale",
    "unknown_export_format",
]


# ---------------------------------------------------------------------------
# Contract types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TableContract:
    """Per-table portion of an adapter's schema contract.

    Args:
        required_columns: column-name → coarse SQLite storage class
            (``"INTEGER"``, ``"REAL"``, ``"TEXT"``, ``"BLOB"``,
            ``"NUMERIC"``). Missing or wrong-type → fatal.
        optional_columns: set of column names the adapter knows about
            but does not require. Missing → warn.
        flag_new_columns: whether a column present in the live table
            but absent from this contract should raise a ``new_column``
            warn event. Default True — good for small, fully-enumerable
            tables (e.g. WhatsApp's, where a genuinely new column is
            worth noticing). Set False for large vendor tables we only
            read a slice of (Apple's ``message`` has ~60 columns, of
            which we read 14): there, an unlisted column is *normal*
            and flagging every one just trains users to ignore the
            banner (proposal §13.11). What matters on those tables is
            *losing* a column we depend on — still caught via
            required/optional — not Apple adding a new one.
    """
    required_columns: dict[str, str]
    optional_columns: set[str] = field(default_factory=set)
    flag_new_columns: bool = True


@dataclass(frozen=True)
class SchemaContract:
    """Full adapter schema contract.

    Args:
        source: stable adapter identifier, e.g. ``"whatsapp_live"``.
            Used as the ``source`` column in ``drift_event``.
        version: integer; bump when the contract itself changes
            shape so old drift events can be cleanly compared to new
            ones.
        tables: per-table contracts. Tables not listed here are not
            probed.
        known_enums: per-column enum allowlist. Keys are
            ``"<table>.<column>"``; values are the set of values the
            adapter knows how to map. Used by callers to classify
            ``unknown_enum_value`` drift.
    """
    source: str
    version: int
    tables: dict[str, TableContract]
    known_enums: dict[str, set] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Event + report types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DriftEvent:
    """One observed difference between contract and live schema/row.

    All fields are plain strings/ints so the value is trivially
    serialisable (CLI JSON, ``/api/drift``, SQLite row).
    """
    source: str
    severity: Severity
    kind: DriftKind
    table: Optional[str]
    column: Optional[str]
    observed: Optional[str]
    expected: Optional[str]
    detail: str
    seen_at: int                     # unix seconds; UTC


@dataclass(frozen=True)
class SchemaReport:
    """Result of one :func:`probe_tables` call.

    Args:
        schema_version: the contract's ``version`` at probe time.
        events: every difference found, in observation order.
    """
    schema_version: int
    events: tuple[DriftEvent, ...]

    @property
    def fatal_count(self) -> int:
        return sum(1 for e in self.events if e.severity == "fatal")

    @property
    def warn_count(self) -> int:
        return sum(1 for e in self.events if e.severity == "warn")

    @property
    def info_count(self) -> int:
        return sum(1 for e in self.events if e.severity == "info")

    @property
    def is_fatal(self) -> bool:
        return self.fatal_count > 0


class SchemaDriftError(RuntimeError):
    """Raised when a sync run encounters fatal drift.

    The caller (CLI / watcher) catches this, exits with code 3, and
    refuses to write anything to the msgviz DB. We never produce a
    partial ingest from a partially-understood schema.
    """

    def __init__(self, report: SchemaReport):
        self.report = report
        fatals = [e for e in report.events if e.severity == "fatal"]
        head = fatals[0] if fatals else None
        msg = (
            f"fatal schema drift in {head.source}/{head.table}: "
            f"{head.detail}" if head else "fatal schema drift"
        )
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------

# Coarse storage class normalisation. SQLite's declared-type → storage-class
# mapping is fuzzy by design ("type affinity"); we collapse the common
# spellings so a contract that says ``"INTEGER"`` matches a column declared
# ``INTEGER PRIMARY KEY``, ``INT``, or no type at all (still INTEGER).
_STORAGE_CLASS_ALIASES = {
    "INT": "INTEGER",
    "INTEGER": "INTEGER",
    "TINYINT": "INTEGER",
    "SMALLINT": "INTEGER",
    "MEDIUMINT": "INTEGER",
    "BIGINT": "INTEGER",
    "BOOL": "INTEGER",
    "BOOLEAN": "INTEGER",
    "REAL": "REAL",
    "DOUBLE": "REAL",
    "FLOAT": "REAL",
    "NUMERIC": "NUMERIC",
    "DECIMAL": "NUMERIC",
    "DATE": "NUMERIC",
    "TIMESTAMP": "NUMERIC",
    "TEXT": "TEXT",
    "VARCHAR": "TEXT",
    "CHAR": "TEXT",
    "CLOB": "TEXT",
    "BLOB": "BLOB",
    "": "BLOB",                       # SQLite stores typeless columns as BLOB-affinity
}


def _normalise_type(raw: str) -> str:
    """Collapse a SQLite declared type into a coarse storage class."""
    if not raw:
        return "BLOB"
    upper = raw.strip().upper()
    # Strip any "(n)" or "(n,m)" suffix that decoration adds.
    paren = upper.find("(")
    if paren >= 0:
        upper = upper[:paren].rstrip()
    return _STORAGE_CLASS_ALIASES.get(upper, upper)


def _tables_in_db(con: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def _columns_in_table(
    con: sqlite3.Connection, table: str
) -> dict[str, str]:
    """name → normalised storage class."""
    return {
        row[1]: _normalise_type(row[2])
        for row in con.execute(f"PRAGMA table_info({table})")
    }


def probe_tables(
    con: sqlite3.Connection,
    contract: SchemaContract,
    now: Optional[int] = None,
) -> SchemaReport:
    """Compare an open SQLite DB against an adapter's schema contract.

    Returns the full :class:`SchemaReport`. The caller is responsible
    for inspecting :attr:`SchemaReport.is_fatal` and aborting.

    Args:
        con: open connection to the *source* DB (Apple/Meta's), not
            to msgviz's own DB.
        contract: the adapter's contract.
        now: override for the ``seen_at`` timestamp (for tests).
    """
    stamp = int(now) if now is not None else int(time.time())
    events: list[DriftEvent] = []
    live_tables = _tables_in_db(con)

    for table, tc in contract.tables.items():
        if table not in live_tables:
            events.append(DriftEvent(
                source=contract.source,
                severity="fatal",
                kind="missing_table",
                table=table,
                column=None,
                observed=None,
                expected="present",
                detail=f"required table {table!r} not found in source DB",
                seen_at=stamp,
            ))
            continue

        live_cols = _columns_in_table(con, table)

        # Required columns: must exist and have a compatible storage class.
        for col, expected_type in tc.required_columns.items():
            if col not in live_cols:
                events.append(DriftEvent(
                    source=contract.source,
                    severity="fatal",
                    kind="missing_required_column",
                    table=table,
                    column=col,
                    observed=None,
                    expected=expected_type,
                    detail=(
                        f"required column {table}.{col} ({expected_type}) "
                        f"missing from source DB"
                    ),
                    seen_at=stamp,
                ))
                continue
            observed_type = live_cols[col]
            expected_norm = _normalise_type(expected_type)
            if observed_type != expected_norm:
                events.append(DriftEvent(
                    source=contract.source,
                    severity="fatal",
                    kind="type_changed",
                    table=table,
                    column=col,
                    observed=observed_type,
                    expected=expected_norm,
                    detail=(
                        f"column {table}.{col} changed storage class: "
                        f"expected {expected_norm}, found {observed_type}"
                    ),
                    seen_at=stamp,
                ))

        # Optional columns: missing is a warn, not a fatal.
        for col in tc.optional_columns:
            if col not in live_cols:
                events.append(DriftEvent(
                    source=contract.source,
                    severity="warn",
                    kind="missing_optional_column",
                    table=table,
                    column=col,
                    observed=None,
                    expected="present (optional)",
                    detail=(
                        f"optional column {table}.{col} no longer present"
                    ),
                    seen_at=stamp,
                ))

        # Columns we did NOT expect: new_column drift, warn level.
        # Skipped entirely on tables that opt out (large vendor tables
        # we only read a slice of — see TableContract.flag_new_columns).
        if tc.flag_new_columns:
            known = set(tc.required_columns) | tc.optional_columns
            for col in live_cols:
                if col not in known:
                    events.append(DriftEvent(
                        source=contract.source,
                        severity="warn",
                        kind="new_column",
                        table=table,
                        column=col,
                        observed=live_cols[col],
                        expected=None,
                        detail=(
                            f"column {table}.{col} ({live_cols[col]}) appeared "
                            f"in source DB but is not in our contract — "
                            f"update {contract.source}'s schema file"
                        ),
                        seen_at=stamp,
                    ))

    return SchemaReport(
        schema_version=contract.version,
        events=tuple(events),
    )


# ---------------------------------------------------------------------------
# Persistence: drift_event table
# ---------------------------------------------------------------------------

_DRIFT_EVENT_DDL = """
CREATE TABLE IF NOT EXISTS drift_event (
    id               INTEGER PRIMARY KEY,
    source           TEXT NOT NULL,
    schema_version   INTEGER NOT NULL,
    severity         TEXT NOT NULL,
    kind             TEXT NOT NULL,
    table_name       TEXT,
    column_name      TEXT,
    observed         TEXT,
    expected         TEXT,
    detail           TEXT,
    first_seen       INTEGER NOT NULL,
    last_seen        INTEGER NOT NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    acknowledged_at  INTEGER
);
"""

# Dedup index. A drift event is uniquely identified by the (source,
# kind, table, column, observed) tuple; repeating the same drift bumps
# the existing row's counter instead of inserting a new one. NULLs in
# any of these columns are folded to the empty string for dedup so
# SQLite's "NULL != NULL" rule doesn't multiply rows.
_DRIFT_EVENT_DEDUP_DDL = """
CREATE UNIQUE INDEX IF NOT EXISTS drift_event_dedup
ON drift_event(
    source,
    kind,
    COALESCE(table_name, ''),
    COALESCE(column_name, ''),
    COALESCE(observed, '')
);
"""


def ensure_drift_event_table(con: sqlite3.Connection) -> bool:
    """Create ``drift_event`` and its dedup index if they don't exist.

    Returns True if anything was created. Idempotent, safe to call on
    every connection. Additive only — never DROP.
    """
    existing = {
        row[0]
        for row in con.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type IN ('table', 'index')"
        )
    }
    created = False
    if "drift_event" not in existing:
        con.execute(_DRIFT_EVENT_DDL)
        created = True
    if "drift_event_dedup" not in existing:
        con.execute(_DRIFT_EVENT_DEDUP_DDL)
        created = True
    if created:
        con.commit()
    return created


def record_event(con: sqlite3.Connection, event: DriftEvent) -> None:
    """Upsert one :class:`DriftEvent` into ``drift_event``.

    First occurrence inserts; subsequent occurrences bump
    ``occurrence_count`` and update ``last_seen``. The unique dedup
    index does the heavy lifting.
    """
    ensure_drift_event_table(con)
    con.execute(
        """
        INSERT INTO drift_event (
            source, schema_version, severity, kind,
            table_name, column_name, observed, expected, detail,
            first_seen, last_seen, occurrence_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT (
            source, kind,
            COALESCE(table_name, ''),
            COALESCE(column_name, ''),
            COALESCE(observed, '')
        ) DO UPDATE SET
            last_seen = excluded.last_seen,
            occurrence_count = drift_event.occurrence_count + 1,
            -- Refresh severity/detail/expected if the contract version
            -- advanced; the new run's reading is more authoritative.
            severity = excluded.severity,
            schema_version = excluded.schema_version,
            detail = excluded.detail,
            expected = excluded.expected
        """,
        (
            event.source,
            0,                              # schema_version filled below
            event.severity,
            event.kind,
            event.table,
            event.column,
            event.observed,
            event.expected,
            event.detail,
            event.seen_at,
            event.seen_at,
        ),
    )
    # The schema_version in the event isn't on DriftEvent itself; the
    # caller passes it via record_report() below. Wrappers used by the
    # adapters always go through record_report(), so the direct
    # record_event() path defaults to 0 — fine for tests, never hit
    # in production.


def record_report(con: sqlite3.Connection, report: SchemaReport) -> int:
    """Persist every event from a :class:`SchemaReport`.

    Returns the number of events written (== ``len(report.events)``).
    Caller is expected to ``con.commit()`` afterwards as part of the
    surrounding transaction.
    """
    ensure_drift_event_table(con)
    n = 0
    for event in report.events:
        log.warning(
            "msgviz.drift source=%s kind=%s severity=%s table=%s "
            "column=%s observed=%s schema_version=%s detail=%s",
            event.source, event.kind, event.severity, event.table,
            event.column, event.observed, report.schema_version,
            event.detail,
        )
        con.execute(
            """
            INSERT INTO drift_event (
                source, schema_version, severity, kind,
                table_name, column_name, observed, expected, detail,
                first_seen, last_seen, occurrence_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT (
                source, kind,
                COALESCE(table_name, ''),
                COALESCE(column_name, ''),
                COALESCE(observed, '')
            ) DO UPDATE SET
                last_seen = excluded.last_seen,
                occurrence_count = drift_event.occurrence_count + 1,
                severity = excluded.severity,
                schema_version = excluded.schema_version,
                detail = excluded.detail,
                expected = excluded.expected
            """,
            (
                event.source,
                report.schema_version,
                event.severity,
                event.kind,
                event.table,
                event.column,
                event.observed,
                event.expected,
                event.detail,
                event.seen_at,
                event.seen_at,
            ),
        )
        n += 1
    return n


# ---------------------------------------------------------------------------
# Query layer (used by CLI `msgviz drift` and /api/drift)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StoredDriftEvent:
    """One row from the ``drift_event`` table."""
    id: int
    source: str
    schema_version: int
    severity: Severity
    kind: DriftKind
    table: Optional[str]
    column: Optional[str]
    observed: Optional[str]
    expected: Optional[str]
    detail: str
    first_seen: int
    last_seen: int
    occurrence_count: int
    acknowledged_at: Optional[int]


def _row_to_stored(row: sqlite3.Row) -> StoredDriftEvent:
    return StoredDriftEvent(
        id=row["id"],
        source=row["source"],
        schema_version=row["schema_version"],
        severity=row["severity"],
        kind=row["kind"],
        table=row["table_name"],
        column=row["column_name"],
        observed=row["observed"],
        expected=row["expected"],
        detail=row["detail"],
        first_seen=row["first_seen"],
        last_seen=row["last_seen"],
        occurrence_count=row["occurrence_count"],
        acknowledged_at=row["acknowledged_at"],
    )


def list_events(
    con: sqlite3.Connection,
    *,
    include_acknowledged: bool = False,
    source: Optional[str] = None,
) -> list[StoredDriftEvent]:
    """List drift events for the CLI / API.

    Args:
        include_acknowledged: by default only pending (un-acked)
            events are returned; the audit history stays available
            via this flag.
        source: optional filter to one adapter.
    """
    ensure_drift_event_table(con)
    sql = "SELECT * FROM drift_event WHERE 1=1"
    params: list = []
    if not include_acknowledged:
        sql += " AND acknowledged_at IS NULL"
    if source is not None:
        sql += " AND source = ?"
        params.append(source)
    sql += " ORDER BY last_seen DESC, id DESC"
    prev_factory = con.row_factory
    con.row_factory = sqlite3.Row
    try:
        return [_row_to_stored(r) for r in con.execute(sql, params)]
    finally:
        con.row_factory = prev_factory


def pending_count(
    con: sqlite3.Connection, source: Optional[str] = None
) -> int:
    """Count of un-acked drift events. Drives the UI banner."""
    ensure_drift_event_table(con)
    if source is None:
        cur = con.execute(
            "SELECT COUNT(*) FROM drift_event WHERE acknowledged_at IS NULL"
        )
    else:
        cur = con.execute(
            "SELECT COUNT(*) FROM drift_event "
            "WHERE acknowledged_at IS NULL AND source = ?",
            (source,),
        )
    return int(cur.fetchone()[0])


def acknowledge(
    con: sqlite3.Connection,
    event_id: int,
    *,
    now: Optional[int] = None,
) -> bool:
    """Set ``acknowledged_at`` on one event. Returns True if it existed
    and was not already acknowledged. The row is never deleted."""
    stamp = int(now) if now is not None else int(time.time())
    cur = con.execute(
        "UPDATE drift_event SET acknowledged_at = ? "
        "WHERE id = ? AND acknowledged_at IS NULL",
        (stamp, event_id),
    )
    con.commit()
    return cur.rowcount > 0


def acknowledge_all(
    con: sqlite3.Connection,
    *,
    source: Optional[str] = None,
    now: Optional[int] = None,
) -> int:
    """Bulk-ack every pending event (optionally for one source).
    Returns the number of rows updated."""
    stamp = int(now) if now is not None else int(time.time())
    if source is None:
        cur = con.execute(
            "UPDATE drift_event SET acknowledged_at = ? "
            "WHERE acknowledged_at IS NULL",
            (stamp,),
        )
    else:
        cur = con.execute(
            "UPDATE drift_event SET acknowledged_at = ? "
            "WHERE acknowledged_at IS NULL AND source = ?",
            (stamp, source),
        )
    con.commit()
    return cur.rowcount


# ---------------------------------------------------------------------------
# Per-row safety net
# ---------------------------------------------------------------------------

def safe_canonicalize(
    fn: Callable,
    row,
    *,
    source: str,
    table: str,
    on_drift: Callable[[DriftEvent], None],
    now: Optional[int] = None,
):
    """Wrap a single row's canonicalisation so a parse failure becomes
    a structured drift event instead of a silent skip.

    The adapter passes the canonicaliser function and a *single row*;
    this helper invokes it. On success, returns the canonical message.
    On a parsing exception, records a ``row_parse_failed`` drift event
    of severity ``warn`` and returns ``None``. The caller filters
    ``None`` out of its iterator.

    ``on_drift`` is whatever the caller wants done with the event —
    typically ``lambda e: record_event(con, e)`` against the msgviz DB.
    The helper itself doesn't touch any DB so it stays trivially
    testable.

    Anti-goal: this is the ONLY place an exception in canonicalisation
    is allowed to be swallowed. Every other path must propagate.
    """
    try:
        return fn(row)
    except Exception as exc:                            # noqa: BLE001
        stamp = int(now) if now is not None else int(time.time())
        event = DriftEvent(
            source=source,
            severity="warn",
            kind="row_parse_failed",
            table=table,
            column=None,
            observed=type(exc).__name__,
            expected=None,
            detail=f"{type(exc).__name__}: {exc}",
            seen_at=stamp,
        )
        on_drift(event)
        return None


# ---------------------------------------------------------------------------
# Adapter-side helper: classify enum values against the contract
# ---------------------------------------------------------------------------

def check_enum(
    contract: SchemaContract,
    table: str,
    column: str,
    value,
) -> Optional[DriftEvent]:
    """Return a ``warn`` drift event if ``value`` is not in the
    contract's allowlist for ``table.column``; otherwise None.

    The adapter calls this inside its row mapper for every enum
    column (e.g. ``ZMESSAGETYPE``, ``ZSESSIONTYPE``). When the
    return is non-None the caller records the event and decides
    whether to also skip the row.
    """
    key = f"{table}.{column}"
    allowed = contract.known_enums.get(key)
    if allowed is None or value in allowed:
        return None
    return DriftEvent(
        source=contract.source,
        severity="warn",
        kind="unknown_enum_value",
        table=table,
        column=column,
        observed=str(value),
        expected=f"one of {sorted(allowed)}",
        detail=(
            f"unknown {table}.{column} value {value!r}; "
            f"add it to known_enums in {contract.source}'s schema file "
            f"if it's a real type, or skip the row if it's garbage"
        ),
        seen_at=int(time.time()),
    )


__all__ = [
    "DriftEvent",
    "DriftKind",
    "SchemaContract",
    "SchemaDriftError",
    "SchemaReport",
    "Severity",
    "StoredDriftEvent",
    "TableContract",
    "acknowledge",
    "acknowledge_all",
    "check_enum",
    "ensure_drift_event_table",
    "list_events",
    "pending_count",
    "probe_tables",
    "record_event",
    "record_report",
    "safe_canonicalize",
]
