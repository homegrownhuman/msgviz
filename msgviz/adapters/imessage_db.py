#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shared read logic for Apple's `chat.db`.

Used by IMessageLiveAdapter (live Mac DB) and IMessageBackupAdapter
(frozen snapshot). Translates the raw Apple data into CanonicalMessage
objects and parses the Apple-specific quirks (attributedBody, tapbacks,
edit history, balloon apps).

:func:`iter_canonical` wraps every message row in
:func:`~msgviz.core.drift.safe_canonicalize`, so a single unparseable
row (corrupt attributedBody, broken edit-summary plist, …) becomes a
``row_parse_failed`` warn drift event and is skipped — never a bare
``except: pass``, and never a crash that aborts the whole chat. The
caller passes the ``source`` tag (``imessage_live`` / ``imessage_backup``)
and an ``on_drift`` sink; this module never touches the msgviz DB itself.
"""
from __future__ import annotations

import datetime
import plistlib
import sqlite3
from typing import Callable, Iterator, Optional

from msgviz.core import drift
from msgviz.core.canonical import (
    CanonicalMessage, Attachment, Edit, Reaction,
)


APPLE_EPOCH = 978307200


# A no-op drift sink for callers that don't supply one (tests, dry probes).
def _ignore_drift(_event: drift.DriftEvent) -> None:  # pragma: no cover
    pass

# Tapback type -> (emoji, label). 2000-2005 = added, 3000-3005 = removed.
TAPBACKS = {
    2000: ("❤️", "loved"),
    2001: ("👍", "liked"),
    2002: ("👎", "disliked"),
    2003: ("😂", "laughed at"),
    2004: ("‼️", "emphasized"),
    2005: ("❓", "questioned"),
}


def apple_dt(ns: Optional[int]) -> Optional[datetime.datetime]:
    if ns is None:
        return None
    return datetime.datetime.fromtimestamp(ns / 1_000_000_000 + APPLE_EPOCH)


def clean_text(t: Optional[str]) -> str:
    if not t:
        return ""
    return t.replace("￼", "").replace("�", "").strip()


def decode_attributed_body(blob: Optional[bytes]) -> str:
    """Fallback when the text column is empty: extract plain text from
    the Apple 'streamtyped' (NSArchiver) attributedBody blob."""
    if not blob:
        return ""
    data = bytes(blob)
    marker = data.find(b"NSString")
    if marker == -1:
        return ""
    plus = data.find(b"\x2b", marker)
    if plus == -1:
        return ""
    p = plus + 1
    if p >= len(data):
        return ""
    b0 = data[p]
    if b0 == 0x81:
        length = int.from_bytes(data[p + 1:p + 3], "little")
        s = p + 3
    elif b0 == 0x82:
        length = int.from_bytes(data[p + 1:p + 5], "little")
        s = p + 5
    elif b0 == 0x80:
        length = data[p + 1]
        s = p + 2
    else:
        length = b0
        s = p + 1
    raw = data[s:s + length]
    return clean_text(raw.decode("utf-8", errors="replace"))


def extract_edit_history(summary_blob: Optional[bytes], final_text: str) -> list[Edit]:
    """Edit history from message_summary_info (plist)."""
    if not summary_blob:
        return []
    try:
        pl = plistlib.loads(bytes(summary_blob))
    except Exception:
        return []
    ec = pl.get("ec")
    if not isinstance(ec, dict):
        return []
    versions: list[Edit] = []
    for part in sorted(ec.keys()):
        for v in ec[part]:
            if not isinstance(v, dict):
                continue
            t = v.get("t")
            txt = (decode_attributed_body(t) if isinstance(t, (bytes, bytearray))
                   else (clean_text(t) if t else ""))
            if not txt:
                continue
            ts = None
            d = v.get("d")
            if isinstance(d, (int, float)) and d:
                try:
                    ts = int(d + APPLE_EPOCH)
                except Exception:
                    ts = None
            if versions and versions[-1].text == txt:
                continue
            versions.append(Edit(text=txt, ts=ts))
    # If only one version exists and matches the final text: no real
    # edit history.
    distinct = {e.text for e in versions}
    if len(distinct) < 2:
        return []
    return versions


def balloon_label(b: Optional[str]) -> Optional[str]:
    if not b:
        return None
    if "URLBalloonProvider" in b:
        return "🔗 Shared link"
    if "DigitalTouch" in b:
        return "✌️ Digital Touch"
    if "Handwriting" in b:
        return "✍️ Handwriting"
    if "AskToBuy" in b:
        return "💸 Purchase request"
    if "ScreenTime" in b:
        return "⏱️ Screen Time request"
    if "findmy" in b.lower() or "FindMy" in b:
        return "📍 Location shared"
    if "PhotosMessagesApp" in b:
        return "🖼️ Photo shared"
    if "Music" in b:
        return "🎵 Music shared"
    return "📲 App message"


def is_plugin_payload(transfer_name: Optional[str]) -> bool:
    return (transfer_name or "").endswith("pluginPayloadAttachment")


# ---------------------------------------------------------------------------
# Schema detection: macOS versions differ by a few columns
# (date_edited / date_retracted / attachment.uti / emoji_image_short_description
# were absent in older versions).
# ---------------------------------------------------------------------------
def _has_column(con: sqlite3.Connection, table: str, col: str) -> bool:
    try:
        rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.OperationalError:
        return False
    return any((r[1] == col) for r in rows)


def get_messages(con: sqlite3.Connection, chat_rowid: int) -> list:
    """All messages of one chat. Works with old and new chat.db schemas
    (missing columns selected as NULL)."""
    has_edited = _has_column(con, "message", "date_edited")
    has_retracted = _has_column(con, "message", "date_retracted")
    extra_edited = "m.date_edited" if has_edited else "NULL AS date_edited"
    extra_retracted = "m.date_retracted" if has_retracted else "NULL AS date_retracted"
    sql = f"""SELECT m.ROWID AS rowid, m.guid, m.text, m.attributedBody, m.is_from_me, m.date,
        {extra_edited}, {extra_retracted}, m.message_summary_info,
        m.cache_has_attachments, m.associated_message_type, m.associated_message_guid,
        m.balloon_bundle_id, h.id AS sender_handle
        FROM message m JOIN chat_message_join cmj ON cmj.message_id=m.ROWID
        LEFT JOIN handle h ON h.ROWID=m.handle_id
        WHERE cmj.chat_id=? ORDER BY m.date ASC, m.ROWID ASC"""
    cur = con.cursor()
    cur.row_factory = sqlite3.Row
    cur.execute(sql, (chat_rowid,))
    return cur.fetchall()


def get_attachments(con: sqlite3.Connection, message_rowid: int) -> list:
    """Attachments of a message. Works with older schemas
    (no emoji_image_short_description)."""
    has_emoji = _has_column(con, "attachment", "emoji_image_short_description")
    emoji_col = ("a.emoji_image_short_description AS emoji_desc"
                 if has_emoji else "'' AS emoji_desc")
    has_uti = _has_column(con, "attachment", "uti")
    uti_col = "a.uti" if has_uti else "'' AS uti"
    sql = f"""SELECT a.ROWID AS att_rowid, a.filename, a.mime_type,
        a.transfer_name, a.is_sticker, {uti_col}, {emoji_col}
        FROM attachment a
        JOIN message_attachment_join maj ON maj.attachment_id=a.ROWID
        WHERE maj.message_id=?"""
    cur = con.cursor()
    cur.row_factory = sqlite3.Row
    cur.execute(sql, (message_rowid,))
    return cur.fetchall()


# ---------------------------------------------------------------------------
# Row → CanonicalMessage
# ---------------------------------------------------------------------------
def _build_canonical(
    con: sqlite3.Connection,
    m: sqlite3.Row,
    *,
    me_name: str,
    guid_to_reactions: dict[str, dict[int, Reaction]],
) -> Optional[CanonicalMessage]:
    """Translate one Apple `message` row into a CanonicalMessage.

    Raises on genuinely-unexpected shapes (a malformed attributedBody
    blob, a corrupt edit-summary plist, etc.) so the caller's
    :func:`safe_canonicalize` wrapper records a ``row_parse_failed``
    drift event and skips the row instead of aborting the chat. Returns
    None for rows we intentionally skip (tapbacks — folded into
    reactions already — and pure system events with no displayable
    content).
    """
    amt = m["associated_message_type"] or 0
    if amt in TAPBACKS or 3000 <= amt <= 3005:
        return None
    text = clean_text(m["text"])
    if not text and amt == 0:
        text = decode_attributed_body(m["attributedBody"])
    has_att = bool(m["cache_has_attachments"])

    apps: list[str] = []
    if not text and not has_att and m["balloon_bundle_id"]:
        lbl = balloon_label(m["balloon_bundle_id"])
        if lbl:
            apps.append(lbl)

    if amt != 0 and not text and not has_att and not apps:
        return None
    if not text and not has_att and not apps:
        return None

    dt = apple_dt(m["date"])
    if dt is None:
        return None
    ts = int(dt.timestamp())
    is_me = bool(m["is_from_me"])

    attachments: list[Attachment] = []
    if has_att:
        for a in get_attachments(con, m["rowid"]):
            if is_plugin_payload(a["transfer_name"]):
                continue
            attachments.append(Attachment(
                source_ref=a["filename"] or "",
                mime=a["mime_type"] or "",
                filename=a["transfer_name"] or "",
                is_sticker=bool(a["is_sticker"]),
                emoji_desc=a["emoji_desc"] or "",
            ))

    edits = extract_edit_history(m["message_summary_info"], text)
    reactions = list(guid_to_reactions.get(m["guid"], {}).values()) if m["guid"] else []
    retracted = bool(m["date_retracted"])

    return CanonicalMessage(
        external_id=str(m["rowid"]),
        ts=ts,
        sender_raw=(me_name if is_me else (m["sender_handle"] or "")),
        is_me=is_me,
        text=text or None,
        retracted=retracted,
        edits=edits,
        reactions=reactions,
        apps=apps,
        attachments=attachments,
    )


# ---------------------------------------------------------------------------
# Generic iterator: Apple raw data → CanonicalMessages
# ---------------------------------------------------------------------------
def iter_canonical(
    con: sqlite3.Connection,
    chat_rowid: int,
    me_name: str,
    *,
    source: str = "imessage_live",
    on_drift: Optional[Callable[[drift.DriftEvent], None]] = None,
) -> Iterator[CanonicalMessage]:
    """Read one chat from the given Apple DB connection and yield
    CanonicalMessage objects. Tapbacks and tapback removes are attached
    as reactions to the respective target message — not emitted as their
    own rows.

    Every message row goes through :func:`safe_canonicalize`, so a single
    malformed row (a corrupt attributedBody blob, a broken edit-summary
    plist, …) becomes a ``row_parse_failed`` warn event and is skipped
    rather than blowing up the whole chat — mirroring the WhatsApp path.

    Args:
        con: open read-only connection to the Apple chat.db.
        chat_rowid: chat.ROWID of the chat to read.
        me_name: written into `sender_raw` as the unified marker for
            is_me=True (person resolution happens in the writer via
            PersonResolver).
        source: drift `source` tag — ``"imessage_live"`` or
            ``"imessage_backup"`` — the caller passes which DB this is.
        on_drift: sink for drift events; defaults to a no-op. The adapter
            passes its `self._on_drift`.
    """
    sink = on_drift or _ignore_drift
    rows = get_messages(con, chat_rowid)

    # Tapback detection needs two passes: first collect every tapback,
    # then emit messages.
    guid_to_reactions: dict[str, dict[int, Reaction]] = {}
    msg_by_guid: dict[str, dict] = {}
    for m in rows:
        guid = m["guid"]
        if guid:
            msg_by_guid[guid] = m
        amt = m["associated_message_type"] or 0
        if amt in TAPBACKS:
            amg = m["associated_message_guid"]
            tg = amg.split("/")[-1] if amg else None
            if not tg:
                continue
            emoji, label = TAPBACKS[amt]
            sender = me_name if m["is_from_me"] else (m["sender_handle"] or "")
            rdt = apple_dt(m["date"])
            rts = int(rdt.timestamp()) if rdt else None
            guid_to_reactions.setdefault(tg, {})[amt] = Reaction(
                emoji=emoji, label=label, sender_raw=sender, ts=rts)
        elif 3000 <= amt <= 3005:
            amg = m["associated_message_guid"]
            tg = amg.split("/")[-1] if amg else None
            if tg and tg in guid_to_reactions:
                guid_to_reactions[tg].pop(amt - 1000, None)

    # Zweiter Pass: echte Nachrichten ausgeben. Jede Zeile durch den
    # safe_canonicalize-Schutz, damit eine kaputte Zeile zu einem
    # row_parse_failed-Drift-Event wird statt den ganzen Chat zu killen.
    for m in rows:
        msg = drift.safe_canonicalize(
            lambda r: _build_canonical(
                con, r,
                me_name=me_name,
                guid_to_reactions=guid_to_reactions,
            ),
            m,
            source=source,
            table="message",
            on_drift=sink,
        )
        if msg is not None:
            yield msg


def list_chats_from_db(con: sqlite3.Connection) -> list[dict]:
    """Return all chats from the Apple DB (used by adapter list_chats())."""
    cur = con.cursor()
    cur.row_factory = sqlite3.Row
    cur.execute("""SELECT ROWID AS rowid, chat_identifier, service_name,
                          display_name, style
                   FROM chat""")
    return cur.fetchall()
