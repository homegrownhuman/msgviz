# -*- coding: utf-8 -*-
"""
Integration tests for the WhatsAppExportAdapter.

The adapter reads a WhatsApp export folder and yields CanonicalMessage
objects. We test against `tests/fixtures/sample_whatsapp/`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


def test_adapter_lists_one_chat(sample_whatsapp_dir):
    from msgviz.adapters.whatsapp_export import WhatsAppExportAdapter
    a = WhatsAppExportAdapter(
        export_dir=sample_whatsapp_dir,
        slug="mac_alice/wa_sample",
        title="Sample WhatsApp",
        me_name="Owner",
    )
    chats = list(a.list_chats())
    assert len(chats) == 1
    c = chats[0]
    assert c.slug == "mac_alice/wa_sample"
    assert c.origin == "whatsapp"
    assert c.is_group is False


def test_adapter_iterates_messages(sample_whatsapp_dir):
    from msgviz.adapters.whatsapp_export import WhatsAppExportAdapter
    a = WhatsAppExportAdapter(
        export_dir=sample_whatsapp_dir,
        slug="mac_alice/wa_sample", title="Sample",
        me_name="Owner",
    )
    chat = next(iter(a.list_chats()))
    msgs = list(a.iter_messages(chat))
    # Sample export has 10 lines.
    assert len(msgs) >= 5, f"expected ≥5 messages, got {len(msgs)}"
    # At least one message with an attachment.
    with_att = [m for m in msgs if m.attachments]
    assert with_att, "expected at least one message with attachment"
    # At least one deleted message (retracted=True).
    deleted = [m for m in msgs if m.retracted]
    assert deleted, "expected at least one retracted message"
    # is_me flag is set.
    me_count = sum(1 for m in msgs if m.is_me)
    them_count = sum(1 for m in msgs if not m.is_me)
    assert me_count > 0 and them_count > 0


def test_adapter_resolves_attachment(sample_whatsapp_dir):
    from msgviz.adapters.whatsapp_export import WhatsAppExportAdapter
    a = WhatsAppExportAdapter(
        export_dir=sample_whatsapp_dir,
        slug="mac_alice/wa_sample", title="Sample",
        me_name="Owner",
    )
    chat = next(iter(a.list_chats()))
    msgs = list(a.iter_messages(chat))
    att = next((att for m in msgs for att in m.attachments), None)
    assert att is not None
    p = a.resolve_attachment(att.source_ref)
    assert p is not None and p.exists()


def test_adapter_supports_incremental_is_false(sample_whatsapp_dir):
    """WhatsApp export is a bulk adapter, not an incremental one."""
    from msgviz.adapters.whatsapp_export import WhatsAppExportAdapter
    a = WhatsAppExportAdapter(
        export_dir=sample_whatsapp_dir,
        slug="x/y", title="X", me_name="Owner")
    assert a.supports_incremental is False
    assert a.name == "whatsapp_export"
