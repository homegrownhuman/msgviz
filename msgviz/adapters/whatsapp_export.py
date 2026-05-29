#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WhatsApp export adapter.

Parses a WhatsApp export folder (`_chat.txt` + media files) and yields
CanonicalMessage objects. Bulk adapter:
`supports_incremental=False` — re-imports go through "wipe-and-rebuild"
in the writer.

Detection:
  - Message line: `[DD.MM.YY, HH:MM:SS] Sender: text`
  - Continuation lines of a message have no date prefix.
  - Attachments: `<attached: filename.ext>` in the message body.
  - System markers (E2E notice) are filtered out.
  - "This message was deleted." / "Diese Nachricht wurde gelöscht." →
    `retracted=True`.
  - "Missed voice call" etc. → app hint (no media, no text).
"""
from __future__ import annotations

import os
import re
import datetime
import unicodedata
from pathlib import Path
from typing import Iterable, Iterator, Optional

from msgviz.core.canonical import CanonicalMessage, ChatSpec, Attachment


MSG_RE = re.compile(
    r"^\[(\d{2})\.(\d{2})\.(\d{2}),\s*(\d{2}):(\d{2}):(\d{2})\]\s([^:]+?):\s?(.*)$"
)
# WhatsApp localizes the attachment marker per language of the exporting
# device: 'attached:' (en), 'Anhang:' (de), 'allegato:' (it), …
# We accept multiple spellings.
ATTACH_RE = re.compile(r"<\s*(?:attached|Anhang|allegato|adjunto|bijlage)\s*:\s*([^>]+)>",
                       re.IGNORECASE)

SYSTEM_MARKERS = (
    "Messages and calls are end-to-end encrypted",
    "Nachrichten und Anrufe sind Ende-zu-Ende-verschlüsselt",
)
DELETED_MARKERS = (
    "This message was deleted.",
    "You deleted this message.",
    "Diese Nachricht wurde gelöscht.",
    "Du hast diese Nachricht gelöscht.",
)
APP_MARKERS = (
    "Missed voice call", "Missed video call",
    "Verpasster Sprachanruf", "Verpasster Videoanruf",
    "Voice call", "Video call",
)


def _strip_invisible(s: str) -> str:
    """Remove LTR/RTL/bidi control characters that WhatsApp injects."""
    return "".join(ch for ch in s if unicodedata.category(ch) != "Cf")


def _parse_ts(dd: str, mm: str, yy: str, h: str, mi: str, s: str) -> int:
    year = 2000 + int(yy)
    dt = datetime.datetime(year, int(mm), int(dd), int(h), int(mi), int(s))
    return int(dt.timestamp())


# Default MIME table (for Attachment.mime so the writer/processor picks
# the correct target format).
MIME_BY_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".heic": "image/heic", ".webp": "image/webp",
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".m4v": "video/mp4",
    ".opus": "audio/ogg", ".m4a": "audio/mp4", ".mp3": "audio/mpeg",
    ".aac": "audio/aac", ".ogg": "audio/ogg", ".wav": "audio/wav",
    ".pdf": "application/pdf", ".vcf": "text/vcard",
}


class WhatsAppExportAdapter:
    """SourceAdapter for a single WhatsApp export folder."""

    name = "whatsapp_export"
    supports_incremental = False

    def __init__(self, export_dir: str | Path, slug: str, title: str,
                 me_name: str, subtitle: Optional[str] = None,
                 is_group: bool = False):
        self.export_dir = Path(export_dir)
        self.slug = slug
        self.title = title
        self.subtitle = subtitle
        self.is_group = is_group
        self.me_name = me_name

    def list_chats(self) -> Iterable[ChatSpec]:
        """One export folder = one chat."""
        yield ChatSpec(
            slug=self.slug,
            title=self.title,
            # source_id for bulk imports is informational only — not
            # recorded in chat_source (supports_incremental=False).
            source_id=str(self.export_dir),
            subtitle=self.subtitle,
            is_group=self.is_group,
            origin="whatsapp",
        )

    def iter_messages(self, chat: ChatSpec) -> Iterator[CanonicalMessage]:
        txt = self.export_dir / "_chat.txt"
        if not txt.is_file():
            return
        # First gather raw records (date + sender + multi-line text).
        raw = []
        cur = None
        with open(txt, encoding="utf-8") as f:
            for line_raw in f:
                line = _strip_invisible(line_raw.rstrip("\n"))
                m = MSG_RE.match(line)
                if m:
                    if cur is not None:
                        raw.append(cur)
                    dd, mm, yy, h, mi, s, sender, rest = m.groups()
                    cur = {
                        "ts": _parse_ts(dd, mm, yy, h, mi, s),
                        "sender": sender.strip(),
                        "text_lines": [rest],
                    }
                else:
                    if cur is not None:
                        cur["text_lines"].append(line)
        if cur is not None:
            raw.append(cur)

        for r in raw:
            full = "\n".join(r["text_lines"]).strip()
            sender = r["sender"]
            ts = r["ts"]

            # System markers → skip entirely
            if any(mark in full for mark in SYSTEM_MARKERS):
                continue

            is_me = (sender == self.me_name) or (
                sender in ("Owner", "Owner")
                and self.me_name in ("Owner", "Owner")
            )

            # Deleted markers
            stripped = full.strip()
            if any(stripped == mark or stripped.startswith(mark) for mark in DELETED_MARKERS):
                yield CanonicalMessage(
                    external_id=None, ts=ts, sender_raw=sender,
                    is_me=is_me, text=None, retracted=True,
                )
                continue

            # Extract attachments
            attachments: list[Attachment] = []
            text_clean = full
            for m in ATTACH_RE.finditer(full):
                fname = m.group(1).strip()
                ext = os.path.splitext(fname)[1].lower()
                attachments.append(Attachment(
                    source_ref=fname,
                    mime=MIME_BY_EXT.get(ext, ""),
                    filename=fname,
                ))
            if attachments:
                text_clean = ATTACH_RE.sub("", full).strip() or None
            else:
                # App markers (missed call etc.)
                if any(stripped == mark for mark in APP_MARKERS):
                    yield CanonicalMessage(
                        external_id=None, ts=ts, sender_raw=sender,
                        is_me=is_me, text=None, apps=[stripped],
                    )
                    continue
                text_clean = full or None

            yield CanonicalMessage(
                external_id=None, ts=ts, sender_raw=sender,
                is_me=is_me, text=text_clean,
                attachments=attachments,
            )

    def resolve_attachment(self, source_ref: str) -> Optional[Path]:
        """The `source_ref` is just the filename inside the export folder."""
        p = self.export_dir / source_ref
        return p if p.is_file() else None
