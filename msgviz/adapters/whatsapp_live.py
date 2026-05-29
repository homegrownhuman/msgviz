#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WhatsAppLiveAdapter.

Reads from WhatsApp Desktop's on-disk ``ChatStorage.sqlite`` (macOS
Core Data ``ZWA*`` tables) and yields CanonicalMessage objects.
Incremental: ``supports_incremental=True``; the sync uses the
source_ref anchor ``whatsapp_live:<device_slug>`` for dedup, with the
WhatsApp ``ZSTANZAID`` as the per-message external id.

Mirrors :class:`msgviz.adapters.imessage_live.IMessageLiveAdapter` —
same protocol, same WAL-aware read-only open — but routes every read
through :mod:`msgviz.adapters.whatsapp_db`, which carries schema-drift
detection (proposal §13).

Drift handling:
* :meth:`open` runs the schema contract. On *fatal* drift (missing
  required table/column, type change) it raises
  :class:`~msgviz.core.drift.SchemaDriftError` — the caller aborts the
  sync and writes nothing.
* Warn-level drift (new column, unknown enum, malformed row) is fed to
  the ``on_drift`` callback the caller supplies, and ingestion
  continues. The most recent :class:`~msgviz.core.drift.SchemaReport`
  is kept on :attr:`last_report` for the caller to persist / surface.

The adapter never touches msgviz's own DB — it only reads WhatsApp's —
so it stays unit-testable against the synthetic ``ZWA*`` fixture.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

from msgviz import paths
from msgviz.core import drift
from msgviz.core.canonical import CanonicalMessage, ChatSpec
from . import whatsapp_db as wadb
from . import whatsapp_schema as ws


def _ignore_drift(_event: drift.DriftEvent) -> None:
    pass


class WhatsAppLiveAdapter:
    name = "whatsapp_live"
    supports_incremental = True

    def __init__(
        self,
        device_slug: str,
        db_path: Optional[str] = None,
        me_name: str = "Me",
        *,
        on_drift: Optional[Callable[[drift.DriftEvent], None]] = None,
        chat_specs: Optional[list[ChatSpec]] = None,
    ):
        """
        Args:
            device_slug: slug of the device this WhatsApp install
                represents (e.g. "mac_alice_wa"). Used for the source
                tag ``whatsapp_live:<device_slug>``.
            db_path: path to ChatStorage.sqlite. None → the default
                macOS WhatsApp Desktop container path.
            me_name: display name written into sender_raw for is_me rows.
            on_drift: sink for warn-level drift events (the caller
                typically records them into msgviz's DB). Fatal drift is
                raised, not routed here.
            chat_specs: optional explicit chat list; if None, every
                session in the DB is yielded.
        """
        self.device_slug = device_slug
        self.db_path = str(db_path) if db_path else str(paths.whatsapp_db_path())
        self.me_name = me_name
        self._on_drift = on_drift or _ignore_drift
        self._chat_specs = chat_specs
        self._con: Optional[sqlite3.Connection] = None
        #: the most recent schema probe, set on open(); None until then.
        self.last_report: Optional[drift.SchemaReport] = None
        #: is_group flag per chat source_id, cached from list_chats().
        self._is_group: dict[str, bool] = {}

    # -- lifecycle ----------------------------------------------------------
    def _open(self) -> sqlite3.Connection:
        if self._con is None:
            # Read-only but NOT immutable: WhatsApp Desktop keeps a
            # writer open with WAL, so SQLite must still consult the
            # -wal file (proposal §5.6).
            self._con = sqlite3.connect(
                f"file:{self.db_path}?mode=ro", uri=True
            )
            self._con.row_factory = sqlite3.Row
        return self._con

    def open(self) -> drift.SchemaReport:
        """Open the DB and run the schema contract.

        Returns the :class:`SchemaReport`. Raises
        :class:`~msgviz.core.drift.SchemaDriftError` on fatal drift —
        the caller must not ingest anything in that case. Warn-level
        events are forwarded to ``on_drift`` and also available on
        :attr:`last_report`.
        """
        con = self._open()
        report = wadb.probe(con)
        self.last_report = report
        # Forward every event to the sink (the caller persists them).
        for event in report.events:
            self._on_drift(event)
        if report.is_fatal:
            raise drift.SchemaDriftError(report)
        return report

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None

    # -- protocol -----------------------------------------------------------
    def list_chats(self) -> Iterable[ChatSpec]:
        if self._chat_specs is not None:
            yield from self._chat_specs
            return
        con = self._open()
        # Ensure the schema was probed before we read rows; open() is
        # idempotent on the connection but runs the contract once.
        if self.last_report is None:
            self.open()
        for c in wadb.list_chats_from_db(con):
            pk = c["pk"]
            session_type = c["session_type"]
            is_group = session_type == ws.SESSION_TYPE_GROUP
            self._is_group[str(pk)] = is_group
            jid = c["contact_jid"] or ""
            title = c["partner_name"] or jid or f"chat_{pk}"
            yield ChatSpec(
                slug=f"{self.device_slug}/chat_{pk}",
                title=title,
                source_id=str(pk),
                subtitle=jid or None,
                is_group=is_group,
                origin="whatsapp",
            )

    def iter_messages(self, chat: ChatSpec) -> Iterator[CanonicalMessage]:
        con = self._open()
        chat_pk = int(chat.source_id)
        # Prefer the ChatSpec's flag; fall back to the cached lookup.
        is_group = chat.is_group or self._is_group.get(chat.source_id, False)
        yield from wadb.iter_canonical(
            con, chat_pk, self.me_name,
            is_group=is_group,
            on_drift=self._on_drift,
        )

    def resolve_attachment(self, source_ref: str) -> Optional[Path]:
        """Resolve a ZMEDIALOCALPATH into an absolute file path.

        ZMEDIALOCALPATH is relative to the WhatsApp media root
        (``<container>/Message/Media``). Absolute paths and ``~``
        prefixes are honoured as-is for flexibility / tests.
        """
        if not source_ref:
            return None
        p = Path(source_ref)
        if source_ref.startswith("~"):
            p = p.expanduser()
        if not p.is_absolute():
            p = paths.whatsapp_media_root() / source_ref
        return p if p.exists() else None
