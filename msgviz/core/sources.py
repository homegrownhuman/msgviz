#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Loader for `config/sources.json`.

Supports two formats for the `chats` list per device:

  LEGACY (Apple-centric, tuple list):
    [chat_id, title, subtitle, slug, is_group, origin?]
    – chat_id = Apple ROWID (or None for bulk imports)
    – origin optional, default 'apple'

  CURRENT (object-based, source-agnostic):
    {
      "slug": "...", "title": "...", "subtitle": "...",
      "is_group": false, "origin": "apple",
      "source": "imessage_live",      // optional, only for sync sources
      "source_id": "2"                 // adapter-specific id
    }

The loader normalizes both into the same dataclasses, so the rest of
the code only sees the current format.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union


# origin value → default source tag if `source` is missing in the legacy
# format. 'apple' (= iMessage) gets 'imessage_live' as the default,
# because the legacy tuple form was used only by iMessage sources.
_ORIGIN_DEFAULT_SOURCE = {
    "apple": "imessage_live",
}


@dataclass
class ChatConfig:
    slug: str
    title: str
    subtitle: Optional[str] = None
    is_group: bool = False
    origin: str = "apple"
    source: Optional[str] = None       # adapter name (see SourceAdapter.name)
    source_id: Optional[str] = None    # adapter-specific id; often None for bulk imports


@dataclass
class DeviceConfig:
    slug: str
    name: str
    type: str
    me_name: str = "Me"
    id: Optional[str] = None
    db: Optional[str] = None           # only for mac_live
    backup: Optional[str] = None       # only for ios_backup
    chats: list[ChatConfig] = field(default_factory=list)


@dataclass
class Sources:
    devices: list[DeviceConfig] = field(default_factory=list)
    people: dict[str, str] = field(default_factory=dict)


def _coerce_chat(item: Union[list, dict]) -> ChatConfig:
    """Convert an entry (legacy: tuple, current: dict) into a ChatConfig."""
    if isinstance(item, dict):
        return ChatConfig(
            slug=item["slug"],
            title=item.get("title", item["slug"]),
            subtitle=item.get("subtitle"),
            is_group=bool(item.get("is_group", False)),
            origin=item.get("origin", "apple"),
            source=item.get("source"),
            source_id=item.get("source_id"),
        )
    # Legacy format: [chat_id, title, subtitle, slug, is_group, origin?]
    chat_id = item[0]
    title = item[1]
    subtitle = item[2]
    slug = item[3]
    is_group = bool(item[4]) if len(item) > 4 else False
    origin = item[5] if len(item) > 5 else "apple"
    if chat_id is None:
        source = None
        source_id = None
    else:
        source = _ORIGIN_DEFAULT_SOURCE.get(origin)
        source_id = str(chat_id)
    return ChatConfig(
        slug=slug, title=title, subtitle=subtitle,
        is_group=is_group, origin=origin,
        source=source, source_id=source_id,
    )


def _coerce_device(item: dict) -> DeviceConfig:
    chats = [_coerce_chat(c) for c in item.get("chats", [])]
    return DeviceConfig(
        slug=item["slug"], name=item["name"], type=item["type"],
        me_name=item.get("me_name", "Me"),
        id=item.get("id"),
        db=item.get("db"),
        backup=item.get("backup"),
        chats=chats,
    )


def load_sources(path: Union[str, Path]) -> Sources:
    """Load sources.json and return a normalized Sources object."""
    p = Path(path)
    with open(p, encoding="utf-8") as f:
        cfg = json.load(f)
    devices = [_coerce_device(d) for d in cfg.get("devices", [])]
    people = cfg.get("people", {})
    return Sources(devices=devices, people=people)
