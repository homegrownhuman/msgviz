#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IMessageBackupAdapter.

Reads from a **frozen** snapshot of an Apple `chat.db`, e.g. from a
Time Machine backup. Bulk adapter: `supports_incremental=False`.

Difference from the live adapter:
- `attachment.filename` points at `~/Library/Messages/...` on the
  original Mac, not at the backup volume. We remap that onto a
  configured `attachments_root`.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

from msgviz.core import drift
from msgviz.core.canonical import CanonicalMessage, ChatSpec
from . import imessage_db
from . import imessage_schema


def _ignore_drift(_event: drift.DriftEvent) -> None:
    pass


class IMessageBackupAdapter:
    name = "imessage_backup"
    supports_incremental = False

    def __init__(self, db_path: str, attachments_root: str,
                 device_slug: str, me_name: str = "Me",
                 chat_specs: Optional[list[ChatSpec]] = None,
                 *, on_drift: Optional[Callable[[drift.DriftEvent], None]] = None):
        """
        Args:
            db_path: path to the snapshot chat.db.
            attachments_root: directory that corresponds to
                              `~/Library/Messages` inside the backup
                              (the Library/Messages folder containing
                              `Attachments/...`).
            device_slug: slug for the chat path ("<slug>/chat_<rowid>").
            chat_specs: optional explicit list of chats to import.
            on_drift: sink for warn-level schema-drift events. Fatal
                      drift is raised from open(), not routed here.
        """
        self.db_path = db_path
        self.attachments_root = Path(attachments_root)
        self.device_slug = device_slug
        self.me_name = me_name
        self._chat_specs = chat_specs
        self._on_drift = on_drift or _ignore_drift
        self._con: Optional[sqlite3.Connection] = None
        #: most recent schema probe; None until open()/list_chats().
        self.last_report: Optional[drift.SchemaReport] = None

    def _open(self) -> sqlite3.Connection:
        if self._con is None:
            self._con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            self._con.row_factory = sqlite3.Row
        return self._con

    def open(self) -> drift.SchemaReport:
        """Open the snapshot DB and run the Apple chat.db schema contract.

        Same semantics as IMessageLiveAdapter.open(); recorded under the
        ``imessage_backup`` source tag.
        """
        con = self._open()
        report = drift.probe_tables(
            con, imessage_schema.contract_for(self.name)
        )
        self.last_report = report
        for event in report.events:
            self._on_drift(event)
        if report.is_fatal:
            raise drift.SchemaDriftError(report)
        return report

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None

    def list_chats(self) -> Iterable[ChatSpec]:
        if self._chat_specs is not None:
            yield from self._chat_specs
            return
        con = self._open()
        if self.last_report is None:
            self.open()
        for c in imessage_db.list_chats_from_db(con):
            yield ChatSpec(
                slug=f"{self.device_slug}/chat_{c['rowid']}",
                title=c["display_name"] or c["chat_identifier"] or f"chat_{c['rowid']}",
                source_id=str(c["rowid"]),
                subtitle=c["chat_identifier"],
                is_group=bool((c["style"] or 0) != 45),
                origin="apple",
            )

    def iter_messages(self, chat: ChatSpec) -> Iterator[CanonicalMessage]:
        con = self._open()
        chat_rowid = int(chat.source_id)
        yield from imessage_db.iter_canonical(
            con, chat_rowid, self.me_name,
            source=self.name, on_drift=self._on_drift,
        )

    def resolve_attachment(self, source_ref: str) -> Optional[Path]:
        if not source_ref:
            return None
        # Backup paths typically begin with ~/Library/Messages/Attachments/...
        # We remap anything after "Library/Messages/" onto attachments_root.
        rel = source_ref
        if rel.startswith("~/Library/Messages/"):
            rel = rel[len("~/Library/Messages/"):]
        elif rel.startswith("/Library/Messages/"):
            rel = rel[len("/Library/Messages/"):]
        elif "Library/Messages/" in rel:
            rel = rel.split("Library/Messages/", 1)[1]
        p = self.attachments_root / rel
        return p if p.exists() else None
