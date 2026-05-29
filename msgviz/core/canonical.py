#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Source-agnostic value types.

Every adapter (iMessage live, iMessage backup, iOS backup, WhatsApp
export, …) translates a source message into a `CanonicalMessage`. The
writer to the `message` table is generic and only knows these value
classes — not Apple- or WhatsApp-specific details.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Edit:
    """An edited version of a message."""
    text: str
    ts: Optional[int] = None    # edit timestamp (Unix seconds); None if source doesn't provide one


@dataclass
class Reaction:
    """A reaction (tapback in iMessage or emoji reaction in WhatsApp)."""
    emoji: str
    label: str                  # display text, e.g. "loved"
    sender_raw: str             # sender in source format (adapter does the mapping)
    ts: Optional[int] = None


@dataclass
class Attachment:
    """An attachment in the source world. The concrete file path is
    resolved by the adapter via `resolve_attachment(source_ref)`."""
    source_ref: str             # adapter-specific reference (e.g. ~/Library/Messages/.../IMG.png or a path inside the export folder)
    mime: str = ""              # MIME type if the source knows it
    filename: str = ""          # original display name
    is_sticker: bool = False    # only relevant for iMessage
    emoji_desc: str = ""        # for sticker / emoji classification


@dataclass
class CanonicalMessage:
    """A message in the internal, source-agnostic form."""
    external_id: Optional[str]  # for source_ref anchors; None for bulk imports without sync requirements
    ts: int                     # Unix seconds
    sender_raw: str             # sender in source format (PersonResolver does the mapping)
    is_me: bool
    text: Optional[str]
    retracted: bool = False
    edits: list[Edit] = field(default_factory=list)
    reactions: list[Reaction] = field(default_factory=list)
    apps: list[str] = field(default_factory=list)  # balloon labels, e.g. "🔗 Shared link"
    attachments: list[Attachment] = field(default_factory=list)


@dataclass
class ChatSpec:
    """Describes a chat as an adapter discovers it in its source."""
    slug: str                   # "<device>/<chat-slug>"
    title: str
    source_id: str              # adapter-specific ID (e.g. chat.ROWID as string, path to the export folder)
    subtitle: Optional[str] = None
    is_group: bool = False
    origin: str = "apple"       # 'apple' | 'whatsapp' | 'signal' | 'sms' — drives logo selection
