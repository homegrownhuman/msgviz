# -*- coding: utf-8 -*-
"""
Spec for the source-agnostic CanonicalMessage and the SourceAdapter
protocol.

CanonicalMessage is the value form every adapter translates a message
into. The DB writer is generic.
"""
from __future__ import annotations

import pytest


def test_canonical_message_has_expected_fields():
    from msgviz.core.canonical import CanonicalMessage
    m = CanonicalMessage(
        external_id="42",
        ts=1700000000,
        sender_raw="Alice K. Example",
        is_me=False,
        text="Hallo",
    )
    assert m.external_id == "42"
    assert m.ts == 1700000000
    assert m.sender_raw == "Alice K. Example"
    assert m.is_me is False
    assert m.text == "Hallo"
    # Optional fields have default values.
    assert m.retracted is False
    assert m.edits == []
    assert m.reactions == []
    assert m.apps == []
    assert m.attachments == []


def test_canonical_message_external_id_optional():
    """Bulk importers without sync requirements set external_id=None."""
    from msgviz.core.canonical import CanonicalMessage
    m = CanonicalMessage(
        external_id=None, ts=1, sender_raw="X", is_me=True, text=None,
    )
    assert m.external_id is None


def test_attachment_minimal_fields():
    from msgviz.core.canonical import Attachment
    a = Attachment(source_ref="~/Library/Messages/.../IMG_001.png",
                   mime="image/png", filename="IMG_001.png")
    assert a.source_ref.endswith("IMG_001.png")
    assert a.mime == "image/png"
    assert a.filename == "IMG_001.png"
    assert a.is_sticker is False


def test_reaction_fields():
    from msgviz.core.canonical import Reaction
    r = Reaction(emoji="❤️", label="geliebt", sender_raw="Alice", ts=1700000000)
    assert r.emoji == "❤️"
    assert r.label == "geliebt"
    assert r.sender_raw == "Alice"
    assert r.ts == 1700000000


def test_edit_fields():
    from msgviz.core.canonical import Edit
    e = Edit(text="Hallo", ts=1700000000)
    assert e.text == "Hallo"
    assert e.ts == 1700000000


def test_source_adapter_protocol_attrs():
    """SourceAdapter is a Protocol with declared methods."""
    from msgviz.core.source_adapter import SourceAdapter
    # Has name + supports_incremental as declared attributes.
    assert hasattr(SourceAdapter, "list_chats")
    assert hasattr(SourceAdapter, "iter_messages")


def test_mock_adapter_implements_protocol():
    """A class with the right methods is considered a SourceAdapter."""
    from msgviz.core.canonical import CanonicalMessage, ChatSpec
    from msgviz.core.source_adapter import SourceAdapter

    class FakeAdapter:
        name = "fake"
        supports_incremental = False

        def list_chats(self):
            return [ChatSpec(slug="fake/x", title="X", source_id="x",
                             subtitle=None, is_group=False, origin="apple")]

        def iter_messages(self, chat):
            yield CanonicalMessage(
                external_id=None, ts=1, sender_raw="A", is_me=True, text="hi"
            )

        def resolve_attachment(self, ref):
            return None

    f = FakeAdapter()
    # Duck-typed protocols don't support isinstance without
    # @runtime_checkable; we check the attributes themselves.
    assert callable(f.list_chats)
    assert callable(f.iter_messages)
    chats = list(f.list_chats())
    assert len(chats) == 1 and chats[0].slug == "fake/x"
    msgs = list(f.iter_messages(chats[0]))
    assert len(msgs) == 1 and msgs[0].text == "hi"
