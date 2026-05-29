# -*- coding: utf-8 -*-
"""
Spec for the `sources.json` loader.

Legacy tuple format for chats:
  [chat_id, title, subtitle, slug, is_group, origin?]
with `chat_id` = Apple's ROWID at index 0.

Current object format:
  { "slug": "...", "title": "...", "subtitle": "...",
    "is_group": false, "origin": "apple",
    "source": "imessage_live", "source_id": "2" }

The loader in `core.sources` always returns **object-based** ChatConfig
items, regardless of whether the JSON is still in the legacy tuple or
the new object format (transition compatibility).
"""
from __future__ import annotations

import json
import pytest


def _write_cfg(tmp_path, data):
    p = tmp_path / "sources.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_loader_reads_old_tuple_format(tmp_path):
    from msgviz.core.sources import load_sources
    cfg_path = _write_cfg(tmp_path, {
        "devices": [
            {
                "type": "mac_live", "id": "mac-x", "slug": "macx",
                "name": "Mac", "me_name": "Owner", "db": None,
                "chats": [
                    [2, "Alice", "+49 170 1234567", "alice", False, "apple"],
                    [None, "Dave", "WhatsApp export", "wa_angela", False, "whatsapp"],
                ],
            }
        ],
        "people": {"+491701234567": "Alice"},
    })
    sources = load_sources(cfg_path)
    dev = sources.devices[0]
    assert dev.slug == "macx"
    assert len(dev.chats) == 2
    c0 = dev.chats[0]
    assert c0.slug == "alice"
    assert c0.title == "Alice"
    assert c0.subtitle == "+49 170 1234567"
    assert c0.is_group is False
    assert c0.origin == "apple"
    assert c0.source == "imessage_live"
    assert c0.source_id == "2"   # legacy chat_id translated into source_id
    c1 = dev.chats[1]
    assert c1.slug == "wa_angela"
    assert c1.origin == "whatsapp"
    assert c1.source is None
    assert c1.source_id is None


def test_loader_reads_new_object_format(tmp_path):
    from msgviz.core.sources import load_sources
    cfg_path = _write_cfg(tmp_path, {
        "devices": [
            {
                "type": "mac_live", "id": "mac-x", "slug": "macx",
                "name": "Mac", "me_name": "Owner",
                "chats": [
                    {"slug": "alice", "title": "Alice",
                     "subtitle": "+49 170 1234567", "is_group": False,
                     "origin": "apple", "source": "imessage_live",
                     "source_id": "2"},
                ],
            }
        ],
    })
    sources = load_sources(cfg_path)
    c = sources.devices[0].chats[0]
    assert c.title == "Alice"
    assert c.source == "imessage_live"
    assert c.source_id == "2"


def test_loader_keeps_people_map(tmp_path):
    from msgviz.core.sources import load_sources
    cfg_path = _write_cfg(tmp_path, {
        "devices": [], "people": {"+491701234567": "Alice"},
    })
    sources = load_sources(cfg_path)
    assert sources.people == {"+491701234567": "Alice"}


def test_loader_handles_missing_optional_origin(tmp_path):
    """Origin may be missing in the legacy format → 'apple' as default."""
    from msgviz.core.sources import load_sources
    cfg_path = _write_cfg(tmp_path, {
        "devices": [
            {"type": "mac_live", "slug": "macx", "name": "Mac",
             "me_name": "Owner",
             "chats": [[1, "X", None, "x", False]]}
        ]
    })
    c = load_sources(cfg_path).devices[0].chats[0]
    assert c.origin == "apple"


def test_devices_have_typed_fields(tmp_path):
    """Devices: slug, name, type, me_name, plus type-specific db/backup."""
    from msgviz.core.sources import load_sources
    cfg_path = _write_cfg(tmp_path, {
        "devices": [
            {"type": "mac_live", "slug": "macx", "name": "Mac",
             "me_name": "Owner", "db": "/path/to/chat.db",
             "chats": []},
            {"type": "ios_backup", "slug": "ipadx", "name": "iPad",
             "me_name": "Carol", "backup": "AAA-BBB", "chats": []},
        ]
    })
    s = load_sources(cfg_path)
    d0, d1 = s.devices
    assert d0.type == "mac_live"
    assert d0.db == "/path/to/chat.db"
    assert d1.type == "ios_backup"
    assert d1.backup == "AAA-BBB"
