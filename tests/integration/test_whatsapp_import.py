# -*- coding: utf-8 -*-
"""
Characterization test: tools/import_whatsapp_export.py creates a chat
with origin='whatsapp', the expected message count and at least one
media row with kind='image'.

Setup:
- import_whatsapp_export.DB points at the test DB.
- export_data.MEDIA_ROOT / ORIG_ROOT point at tmpdir (otherwise the
  importer would write into the repo).
- Device 'mac_alice' (declared in config/sources.json) is pre-inserted
  into seeded_visualizer_db so the device lookup inside the importer
  works.

Notes:
- The importer reads config/sources.json directly. The chat_slug
  'test_wa' isn't listed there — that's fine: the code falls back to
  using the slug as title (cmeta=None path).
- If the WhatsApp sample fixtures are missing, the test is skipped.
"""
from __future__ import annotations

import os
import sys
import sqlite3
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "v2"))


def _count_chat_txt_messages(chat_txt: Path) -> dict:
    """Mirrors the parsing rules from import_whatsapp_export.parse_chat
    without calling the parser itself: counts lines starting with the
    date pattern, minus system markers (e2e notice)."""
    import re, unicodedata
    MSG_RE = re.compile(
        r"^\[(\d{2})\.(\d{2})\.(\d{2}),\s*(\d{2}):(\d{2}):(\d{2})\]\s([^:]+?):\s?(.*)$"
    )
    SYSTEM_MARKERS = (
        "Messages and calls are end-to-end encrypted",
        "Nachrichten und Anrufe sind Ende-zu-Ende-verschlüsselt",
    )
    total = 0
    system = 0
    deleted = 0
    DELETED_MARKERS = (
        "This message was deleted.",
        "You deleted this message.",
        "Diese Nachricht wurde gelöscht.",
        "Du hast diese Nachricht gelöscht.",
    )
    with open(chat_txt, encoding="utf-8") as f:
        lines = f.readlines()
    cur_text = None
    for raw in lines:
        line = "".join(ch for ch in raw.rstrip("\n")
                       if unicodedata.category(ch) != "Cf")
        m = MSG_RE.match(line)
        if m:
            # Close the previous message.
            if cur_text is not None:
                if any(s in cur_text for s in SYSTEM_MARKERS):
                    system += 1
                elif any(cur_text.strip().startswith(d) for d in DELETED_MARKERS):
                    deleted += 1
                total += 1
            cur_text = m.group(8)
        else:
            if cur_text is not None:
                cur_text += "\n" + line
    if cur_text is not None:
        if any(s in cur_text for s in SYSTEM_MARKERS):
            system += 1
        elif any(cur_text.strip().startswith(d) for d in DELETED_MARKERS):
            deleted += 1
        total += 1
    return {"total": total, "system": system, "deleted": deleted,
            "real": total - system}


@pytest.fixture
def patched_whatsapp_importer(tmp_path, visualizer_db_path,
                              seeded_visualizer_db, monkeypatch):
    """Import tools.import_whatsapp_export and patch DB / media paths.

    Writes an isolated test sources.json with device 'mac_alice' into
    tmp_path/config/sources.json and ensures the importer reads it
    instead of the real local config (via MSGVIZ_HOME override).
    """
    # Close first so the importer can connect itself.
    seeded_visualizer_db.close()

    # Isolated test config.
    import json as _json
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(exist_ok=True)
    test_sources = cfg_dir / "sources.json"
    test_sources.write_text(_json.dumps({
        "devices": [
            {
                "type": "mac_live",
                "slug": "mac_alice",
                "name": "Mac Book Pro M1 Max",
                "me_name": "Owner",
                "chats": [],
            }
        ]
    }), encoding="utf-8")
    # The importer calls load_sources(os.path.join(ROOT, "config", "sources.json")).
    # We redirect ROOT to tmp_path -> picks up our test file.
    monkeypatch.setenv("MSGVIZ_HOME", str(tmp_path))

    from msgviz.legacy import export_data as ex  # type: ignore
    monkeypatch.setattr(ex, "MEDIA_ROOT", str(tmp_path / "media"), raising=True)
    monkeypatch.setattr(ex, "ORIG_ROOT", str(tmp_path / "media_orig"), raising=True)
    monkeypatch.setattr(ex, "FAST", False, raising=True)

    import tools.import_whatsapp_export as wae  # type: ignore
    monkeypatch.setattr(wae, "DB", str(visualizer_db_path), raising=True)
    monkeypatch.setattr(wae, "ROOT", str(tmp_path), raising=True)
    # The importer sets its own MEDIA_ROOTs at module load; overwrite again
    # in case the import order is different here.
    monkeypatch.setattr(wae.ex, "MEDIA_ROOT", str(tmp_path / "media"), raising=True)
    monkeypatch.setattr(wae.ex, "ORIG_ROOT", str(tmp_path / "media_orig"), raising=True)
    return wae


def test_whatsapp_import_creates_chat_with_expected_counts(
    patched_whatsapp_importer, sample_whatsapp_dir, visualizer_db_path,
):
    if not sample_whatsapp_dir.is_dir():
        pytest.skip(f"sample WhatsApp folder missing: {sample_whatsapp_dir}")
    chat_txt = sample_whatsapp_dir / "_chat.txt"
    if not chat_txt.is_file():
        pytest.skip(f"_chat.txt missing: {chat_txt}")

    counts = _count_chat_txt_messages(chat_txt)
    expected_real = counts["real"]

    wae = patched_whatsapp_importer
    slug = wae.import_export(
        str(sample_whatsapp_dir),
        device_slug="mac_alice",
        chat_slug="test_wa",
        me_name="Owner",
        limit=None,
        with_media=True,
    )
    assert slug == "mac_alice/test_wa"

    con = sqlite3.connect(str(visualizer_db_path))
    con.row_factory = sqlite3.Row

    chat = con.execute(
        "SELECT id, slug, title, origin FROM chat WHERE slug=?", (slug,)
    ).fetchone()
    assert chat is not None, "chat was not created"
    assert chat["origin"] == "whatsapp", \
        f"origin should be 'whatsapp', was {chat['origin']!r}"

    n_msgs = con.execute(
        "SELECT COUNT(*) FROM message WHERE chat_id=?", (chat["id"],)
    ).fetchone()[0]
    # Real count: exactly the non-system lines in _chat.txt.
    assert n_msgs == expected_real, (
        f"messages in DB ({n_msgs}) != real _chat.txt lines "
        f"({expected_real})"
    )

    # At least 1 image as kind='image' (sample_whatsapp/ contains 1 photo).
    n_images = con.execute(
        """SELECT COUNT(*) FROM media md
           JOIN message m ON m.id = md.message_id
           WHERE m.chat_id=? AND md.kind='image'""", (chat["id"],),
    ).fetchone()[0]
    assert n_images >= 1, "expected at least 1 image-media row"

    con.close()
