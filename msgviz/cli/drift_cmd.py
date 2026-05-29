# -*- coding: utf-8 -*-
"""
msgviz drift — view and acknowledge schema-drift events.

When an adapter notices that a source's on-disk schema or export format
has changed (Apple's chat.db, WhatsApp's ChatStorage.sqlite, a
_chat.txt locale we don't parse), it records a structured row in the
``drift_event`` table. This command surfaces those rows so a change
shipped by Apple or Meta is visible and actionable rather than a silent
mis-parse (proposal §13.6).

    msgviz drift                 # pending (un-acknowledged) events
    msgviz drift --all           # include acknowledged ones too
    msgviz drift --json          # machine-readable
    msgviz drift --explain SRC   # full detail for one source
    msgviz drift --ack ID        # acknowledge one event
    msgviz drift --ack-all        # acknowledge everything pending
    msgviz drift --ack-all --source whatsapp_live

Acknowledging never deletes — it only sets ``acknowledged_at``, so the
audit trail survives.

Exit code:
    0  no pending events (or pure list/ack operations)
    2  pending fatal events exist (scriptable: CI / cron can detect)
"""
from __future__ import annotations

import datetime
from typing import Optional

import typer
from rich.table import Table
from rich.text import Text

from msgviz.core import drift as drift_core
from ._helpers import console, open_db

app = typer.Typer(
    name="drift",
    help="View / acknowledge source schema-drift events.",
    no_args_is_help=False,
    invoke_without_command=True,
)


_SEV_STYLE = {"fatal": "bold red", "warn": "yellow", "info": "dim"}


def _fmt_ts(ts: Optional[int]) -> str:
    if not ts:
        return ""
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _events_table(events: list[drift_core.StoredDriftEvent], *, title: str) -> Table:
    tbl = Table(title=title, show_header=True, header_style="bold cyan")
    tbl.add_column("id", justify="right")
    tbl.add_column("sev")
    tbl.add_column("source")
    tbl.add_column("kind")
    tbl.add_column("where")
    tbl.add_column("seen ×", justify="right")
    tbl.add_column("last seen")
    tbl.add_column("ack")
    for e in events:
        where = ".".join(p for p in (e.table, e.column) if p) or "—"
        sev = Text(e.severity, style=_SEV_STYLE.get(e.severity, ""))
        ack = "✓" if e.acknowledged_at else ""
        tbl.add_row(
            str(e.id), sev, e.source, e.kind, where,
            str(e.occurrence_count), _fmt_ts(e.last_seen), ack,
        )
    return tbl


def _to_dict(e: drift_core.StoredDriftEvent) -> dict:
    return {
        "id": e.id,
        "source": e.source,
        "schema_version": e.schema_version,
        "severity": e.severity,
        "kind": e.kind,
        "table": e.table,
        "column": e.column,
        "observed": e.observed,
        "expected": e.expected,
        "detail": e.detail,
        "first_seen": e.first_seen,
        "last_seen": e.last_seen,
        "occurrence_count": e.occurrence_count,
        "acknowledged_at": e.acknowledged_at,
    }


@app.callback()
def drift(
    ctx: typer.Context,
    show_all: bool = typer.Option(
        False, "--all", "-a",
        help="Include acknowledged events (default: pending only).",
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Machine-readable output.",
    ),
    source: Optional[str] = typer.Option(
        None, "--source",
        help="Filter to one source (e.g. whatsapp_live). With --ack-all, "
             "limits the bulk-ack to that source.",
    ),
    explain: Optional[str] = typer.Option(
        None, "--explain",
        help="Print full detail for every event of one source.",
    ),
    ack: Optional[int] = typer.Option(
        None, "--ack", help="Acknowledge one event by id.",
    ),
    ack_all: bool = typer.Option(
        False, "--ack-all", help="Acknowledge all pending events.",
    ),
) -> None:
    """View or acknowledge schema-drift events."""
    # A subcommand (none defined yet) would set ctx.invoked_subcommand.
    if ctx.invoked_subcommand is not None:
        return

    with open_db() as con:
        # --- acknowledgements first (they mutate, then we show state) ---
        if ack is not None:
            ok = drift_core.acknowledge(con, ack)
            if ok:
                console.print(f"[green]Acknowledged event {ack}.[/green]")
            else:
                console.print(
                    f"[yellow]Event {ack} not found or already "
                    f"acknowledged.[/yellow]"
                )
            return
        if ack_all:
            n = drift_core.acknowledge_all(con, source=source)
            scope = f" for {source}" if source else ""
            console.print(f"[green]Acknowledged {n} event(s){scope}.[/green]")
            return

        # --- explain one source -----------------------------------------
        if explain is not None:
            events = drift_core.list_events(
                con, include_acknowledged=True, source=explain
            )
            if not events:
                console.print(f"[dim]No drift events for {explain!r}.[/dim]")
                return
            for e in events:
                sev = Text(e.severity.upper(), style=_SEV_STYLE.get(e.severity, ""))
                console.print(sev, f"#{e.id} {e.source} · {e.kind}")
                where = ".".join(p for p in (e.table, e.column) if p)
                if where:
                    console.print(f"   where:     {where}")
                if e.observed is not None:
                    console.print(f"   observed:  {e.observed}")
                if e.expected is not None:
                    console.print(f"   expected:  {e.expected}")
                console.print(f"   detail:    {e.detail}")
                console.print(
                    f"   seen:      {e.occurrence_count}× · "
                    f"first {_fmt_ts(e.first_seen)} · last {_fmt_ts(e.last_seen)}"
                )
                if e.acknowledged_at:
                    console.print(
                        f"   ack:       {_fmt_ts(e.acknowledged_at)}"
                    )
                console.print()
            return

        # --- list (default) ---------------------------------------------
        events = drift_core.list_events(
            con, include_acknowledged=show_all, source=source
        )

        if json_out:
            console.print_json(
                data={
                    "events": [_to_dict(e) for e in events],
                    "pending_count": drift_core.pending_count(con, source=source),
                }
            )
            return

        if not events:
            console.print(
                "[green]No schema-drift events.[/green] "
                "[dim]All sources match their contracts.[/dim]"
            )
            return

        scope = f" · {source}" if source else ""
        title = (
            f"Schema drift{scope} "
            f"({'all' if show_all else 'pending'})"
        )
        console.print(_events_table(events, title=title))

        fatal = [e for e in events if e.severity == "fatal"]
        if fatal:
            console.print(
                f"\n[bold red]{len(fatal)} fatal event(s)[/bold red] — the "
                f"affected source(s) cannot ingest until resolved. "
                f"See [bold]msgviz drift --explain <source>[/bold]."
            )
        console.print(
            "[dim]Acknowledge with [bold]msgviz drift --ack <id>[/bold] "
            "or [bold]--ack-all[/bold].[/dim]"
        )
        # Exit 2 when un-acknowledged fatals exist (scriptable).
        if not show_all and any(e.severity == "fatal" for e in events):
            raise typer.Exit(code=2)
