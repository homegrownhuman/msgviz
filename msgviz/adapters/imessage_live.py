#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IMessageLiveAdapter.

Reads from the running macOS Messages DB (`~/Library/Messages/chat.db`)
and yields CanonicalMessage objects. Incremental:
`supports_incremental=True`; the sync uses the source_ref anchor
('imessage_live:<device_slug>') for dedup.

Attachment resolution: in the live DB `attachment.filename` is the
actual filesystem path (often with `~/` as the prefix).
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Iterable, Iterator, Optional

from msgviz.core.canonical import CanonicalMessage, ChatSpec
from . import imessage_db


class IMessageLiveAdapter:
    name = "imessage_live"
    supports_incremental = True

    def __init__(self, db_path: str, device_slug: str, me_name: str = "Me",
                 chat_specs: Optional[list[ChatSpec]] = None):
        """
        Args:
            db_path: path to the Apple chat.db (e.g. ~/Library/Messages/chat.db).
            device_slug: slug of the device this DB represents
                         (e.g. "mac_alice"). Used for the source tag
                         f"imessage_live:{device_slug}".
            me_name: display name of the "me" person for is_me=True.
            chat_specs: optional explicit list of chats the adapter
                        delivers. If None, every chat in the DB is yielded.
        """
        self.db_path = db_path
        self.device_slug = device_slug
        self.me_name = me_name
        self._chat_specs = chat_specs
        self._con: Optional[sqlite3.Connection] = None

    def _open(self) -> sqlite3.Connection:
        if self._con is None:
            self._con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            self._con.row_factory = sqlite3.Row
        return self._con

    def list_chats(self) -> Iterable[ChatSpec]:
        if self._chat_specs is not None:
            yield from self._chat_specs
            return
        con = self._open()
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
        yield from imessage_db.iter_canonical(con, chat_rowid, self.me_name)

    def resolve_attachment(self, source_ref: str) -> Optional[Path]:
        if not source_ref:
            return None
        p = os.path.expanduser(source_ref) if source_ref.startswith("~") else source_ref
        path = Path(p)
        return path if path.exists() else None
