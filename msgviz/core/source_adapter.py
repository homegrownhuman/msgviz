#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SourceAdapter protocol.

Every concrete adapter (iMessage live, iMessage backup, iOS backup,
WhatsApp export) implements this protocol. The writer to visualizer.db
and the sync loop only know the methods declared here, not the
source-internal details.
"""
from __future__ import annotations

from typing import Iterable, Iterator, Optional, Protocol, runtime_checkable
from pathlib import Path

from .canonical import CanonicalMessage, ChatSpec


@runtime_checkable
class SourceAdapter(Protocol):
    """Interface for source adapters.

    Attributes:
        name: short name; appears as the prefix of the `source` value in
              `source_ref` / `chat_source` (e.g. 'imessage_live'). The
              concrete source instance is built at sync time as
              f"{name}:{device_slug}".
        supports_incremental: True if `sync.py` should call this adapter
              incrementally. Bulk importers (WhatsApp export, old
              chat.db) set False.
    """
    name: str
    supports_incremental: bool

    def list_chats(self) -> Iterable[ChatSpec]:
        """All chats the adapter discovers in its source."""
        ...

    def iter_messages(self, chat: ChatSpec) -> Iterator[CanonicalMessage]:
        """Iterate the chat's messages as CanonicalMessage values."""
        ...

    def resolve_attachment(self, source_ref: str) -> Optional[Path]:
        """Resolve an adapter-specific attachment reference into a real
        file path (e.g. iOS-backup hash, ~/Library/...-style path,
        relative path inside an export folder). None if unresolvable."""
        ...
