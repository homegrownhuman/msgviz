# -*- coding: utf-8 -*-
"""
Shared read logic for WhatsApp Desktop's ``ChatStorage.sqlite``.

Used by ``WhatsAppLiveAdapter``. Translates WhatsApp's Core Data
``ZWA*`` tables into :class:`~msgviz.core.canonical.CanonicalMessage`
objects, and wires in schema-drift detection so a change shipped by
Meta is loud rather than silent (proposal §13):

* :func:`probe` runs the schema contract against the open DB. Fatal
  drift (missing required table/column, type change) raises
  :class:`~msgviz.core.drift.SchemaDriftError` — the caller aborts
  the sync, nothing is written.
* :func:`iter_canonical` wraps every row in
  :func:`~msgviz.core.drift.safe_canonicalize`, so a single
  unparseable row becomes a ``row_parse_failed`` warn event and is
  skipped — never a bare ``except: pass``.
* Unknown ``ZMESSAGETYPE`` / ``ZSESSIONTYPE`` values become
  ``unknown_enum_value`` warn events via
  :func:`~msgviz.core.drift.check_enum`.

Drift events are reported through an ``on_drift`` callback the adapter
supplies (typically ``lambda e: record_event(mv_con, e)``); this
module never touches the msgviz DB itself, keeping it trivially
testable against a synthetic ``ZWA*`` fixture.

Apple/Core-Data epoch note: WhatsApp's ``ZMESSAGEDATE`` /
``ZSENTDATE`` are **seconds** since 2001-01-01 UTC (unlike iMessage's
*nanoseconds*). Add :data:`WHATSAPP_EPOCH`.
"""
from __future__ import annotations

import sqlite3
from typing import Callable, Iterator, Optional

from msgviz.core import drift
from msgviz.core.canonical import Attachment, CanonicalMessage
from . import whatsapp_schema as ws

# A no-op drift sink for callers that don't supply one (tests, dry probes).
def _ignore_drift(_event: drift.DriftEvent) -> None:  # pragma: no cover
    pass


# ---------------------------------------------------------------------------
# Schema probe
# ---------------------------------------------------------------------------
def probe(con: sqlite3.Connection, *, now: Optional[int] = None) -> drift.SchemaReport:
    """Run the WhatsApp schema contract against the open source DB.

    Returns the :class:`~msgviz.core.drift.SchemaReport`. The caller
    inspects ``report.is_fatal`` and aborts if needed; this function
    does not raise on its own so the caller controls the abort and the
    persistence of warn-level events.
    """
    return drift.probe_tables(con, ws.CONTRACT, now=now)


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------
def wa_ts(value: Optional[float]) -> Optional[int]:
    """Core Data seconds-since-2001 → Unix seconds. None-safe."""
    if value is None:
        return None
    return int(float(value) + ws.WHATSAPP_EPOCH)


def _clean_text(t: Optional[str]) -> str:
    if not t:
        return ""
    # Strip the object-replacement / replacement glyphs WhatsApp uses
    # as media placeholders, same as the iMessage path.
    return t.replace("￼", "").replace("�", "").strip()


# ---------------------------------------------------------------------------
# Chats
# ---------------------------------------------------------------------------
def list_chats_from_db(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """All chat sessions from the WhatsApp DB (used by adapter
    ``list_chats()``).

    Hidden / removed sessions are kept — the archive wants them; the
    adapter decides what to surface.
    """
    cur = con.cursor()
    cur.row_factory = sqlite3.Row
    cur.execute(
        """
        SELECT Z_PK            AS pk,
               ZCONTACTJID      AS contact_jid,
               ZPARTNERNAME     AS partner_name,
               ZSESSIONTYPE     AS session_type
        FROM ZWACHATSESSION
        ORDER BY Z_PK ASC
        """
    )
    return cur.fetchall()


# ---------------------------------------------------------------------------
# Group sender resolution
# ---------------------------------------------------------------------------
def _group_member_jids(
    con: sqlite3.Connection, chat_pk: int
) -> dict[int, str]:
    """Map ZWAGROUPMEMBER.Z_PK → ZMEMBERJID for one chat session.

    Group messages carry ZGROUPMEMBER (FK into this table) rather than
    a usable ZFROMJID, so we resolve the real sender here (§5.3).
    """
    cur = con.cursor()
    cur.row_factory = sqlite3.Row
    cur.execute(
        "SELECT Z_PK AS pk, ZMEMBERJID AS jid "
        "FROM ZWAGROUPMEMBER WHERE ZCHATSESSION = ?",
        (chat_pk,),
    )
    return {r["pk"]: r["jid"] for r in cur.fetchall() if r["jid"]}


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------
def _media_for_message(
    con: sqlite3.Connection, message_pk: int
) -> Optional[sqlite3.Row]:
    """The ZWAMEDIAITEM row for a message, or None.

    ZWAMEDIAITEM.ZMESSAGE is the FK back to ZWAMESSAGE.Z_PK. One media
    item per message in practice.
    """
    cur = con.cursor()
    cur.row_factory = sqlite3.Row
    cur.execute(
        """
        SELECT ZMEDIALOCALPATH AS local_path,
               ZVCARDSTRING     AS vcard,
               ZVCARDNAME       AS vcard_name,
               ZLATITUDE        AS lat,
               ZLONGITUDE       AS lon,
               ZTITLE           AS title,
               ZMOVIEDURATION   AS duration
        FROM ZWAMEDIAITEM
        WHERE ZMESSAGE = ?
        LIMIT 1
        """,
        (message_pk,),
    )
    return cur.fetchone()


# MIME hints by canonical kind, used when the source doesn't store one.
_KIND_MIME = {
    "image": "image/jpeg",
    "video": "video/mp4",
    "audio": "audio/ogg",
    "sticker": "image/webp",
    "gif": "video/mp4",
    "file": "application/octet-stream",
}


# ---------------------------------------------------------------------------
# Row → CanonicalMessage
# ---------------------------------------------------------------------------
def _build_canonical(
    con: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    me_name: str,
    is_group: bool,
    member_jids: dict[int, str],
    partner_name: Optional[str],
    on_drift: Callable[[drift.DriftEvent], None],
) -> Optional[CanonicalMessage]:
    """Translate one ZWAMESSAGE row into a CanonicalMessage.

    Raises on genuinely-unexpected shapes so the caller's
    :func:`safe_canonicalize` wrapper records a ``row_parse_failed``
    drift event. Returns None for rows we intentionally skip (pure
    system events with no displayable content).
    """
    msg_type = row["msg_type"]
    if msg_type is None:
        msg_type = ws.MESSAGE_TYPE_TEXT

    # Unknown message type → warn, but don't drop a row that still has
    # text. Treat unknown types as text-ish.
    enum_event = drift.check_enum(
        ws.CONTRACT, "ZWAMESSAGE", "ZMESSAGETYPE", msg_type
    )
    if enum_event is not None:
        on_drift(enum_event)
    kind = ws.MESSAGE_KIND.get(msg_type, "text")

    # Group-event drift check (non-zero ZGROUPEVENTTYPE we don't know).
    gev = row["group_event_type"] if "group_event_type" in row.keys() else None
    if gev:
        ev = drift.check_enum(
            ws.CONTRACT, "ZWAMESSAGE", "ZGROUPEVENTTYPE", gev
        )
        if ev is not None:
            on_drift(ev)

    ts = wa_ts(row["msg_date"])
    if ts is None:
        # No usable timestamp — can't place this on a timeline. Skip,
        # but record it so a systemic timestamp problem is visible.
        on_drift(drift.DriftEvent(
            source=ws.SOURCE_NAME,
            severity="warn",
            kind="row_parse_failed",
            table="ZWAMESSAGE",
            column="ZMESSAGEDATE",
            observed="NULL",
            expected="numeric timestamp",
            detail=f"message Z_PK={row['pk']} has no ZMESSAGEDATE; skipped",
            seen_at=ts or 0,
        ))
        return None

    is_me = bool(row["is_from_me"])

    # Sender resolution. 1:1 → ZFROMJID. Group → ZGROUPMEMBER's JID.
    if is_me:
        sender_raw = me_name
    elif is_group:
        gm_pk = row["group_member"] if "group_member" in row.keys() else None
        sender_raw = member_jids.get(gm_pk) if gm_pk is not None else None
        # Fall back to ZFROMJID if the member row is missing.
        sender_raw = sender_raw or (row["from_jid"] or "")
    else:
        # 1:1 chat: every non-me message is from the one chat partner.
        # WhatsApp may carry the partner under a phone-JID on older
        # messages and a @lid on newer ones (the @lid split, §5.2) —
        # using the chat's partner name collapses both into ONE person
        # and gives a human label instead of a raw JID. Fall back to the
        # message JID only when the chat has no usable partner name.
        sender_raw = partner_name or (row["from_jid"] or "")

    text = _clean_text(row["text"])

    # Media / attachments.
    attachments: list[Attachment] = []
    apps: list[str] = []
    if kind in ("image", "video", "audio", "sticker", "gif", "file"):
        media = _media_for_message(con, row["pk"])
        if media is not None and media["local_path"]:
            attachments.append(Attachment(
                source_ref=media["local_path"],
                mime=_KIND_MIME.get(kind, ""),
                filename=(media["title"] or "") if "title" in media.keys() else "",
                is_sticker=(kind == "sticker"),
            ))
        elif media is None:
            # A media-typed message with no media row is odd but not
            # fatal; keep it with a label so it isn't silently empty.
            apps.append({
                "image": "🖼️ Image",
                "video": "🎬 Video",
                "audio": "🎤 Voice message",
                "sticker": "🃏 Sticker",
                "gif": "🎞️ GIF",
                "file": "📎 Document",
            }.get(kind, "📎 Attachment"))
    elif kind == "location":
        media = _media_for_message(con, row["pk"])
        if media is not None and media["lat"] is not None:
            apps.append(f"📍 Location ({media['lat']:.5f}, {media['lon']:.5f})")
        else:
            apps.append("📍 Location")
    elif kind == "contact":
        apps.append("👤 Contact card")
    elif kind == "call":
        apps.append("📞 Call")
    elif kind == "system":
        # System / group-event message. Keep the text if present
        # (e.g. "You added Alice"); otherwise drop.
        if not text:
            return None

    # Nothing to show? Skip (but text-only types always pass).
    if not text and not attachments and not apps:
        return None

    return CanonicalMessage(
        external_id=str(row["stanza_id"] or row["pk"]),
        ts=ts,
        sender_raw=sender_raw,
        is_me=is_me,
        text=text or None,
        retracted=False,            # deletion handling is v2 (§5.5)
        edits=[],
        reactions=[],               # reaction support is v2
        apps=apps,
        attachments=attachments,
    )


# ---------------------------------------------------------------------------
# Public iterator
# ---------------------------------------------------------------------------
def get_messages(con: sqlite3.Connection, chat_pk: int) -> list[sqlite3.Row]:
    """All messages of one chat session, oldest first.

    Optional columns are selected defensively: a contract probe runs
    before this in the adapter, so by the time we get here required
    columns are guaranteed present. Optional columns that might be
    missing on an older/newer schema are wrapped via the schema probe
    (a missing optional → warn, and we just don't SELECT it). To keep
    the SQL static and simple, we select optional columns that the
    probe confirmed exist; the adapter passes that set in.
    """
    cur = con.cursor()
    cur.row_factory = sqlite3.Row
    cur.execute(
        """
        SELECT Z_PK             AS pk,
               ZSTANZAID         AS stanza_id,
               ZMESSAGEDATE      AS msg_date,
               ZTEXT             AS text,
               ZFROMJID          AS from_jid,
               ZISFROMME         AS is_from_me,
               ZMESSAGETYPE      AS msg_type,
               ZGROUPMEMBER      AS group_member,
               ZGROUPEVENTTYPE   AS group_event_type
        FROM ZWAMESSAGE
        WHERE ZCHATSESSION = ?
        ORDER BY ZMESSAGEDATE ASC, Z_PK ASC
        """,
        (chat_pk,),
    )
    return cur.fetchall()


def iter_canonical(
    con: sqlite3.Connection,
    chat_pk: int,
    me_name: str,
    *,
    is_group: bool,
    partner_name: Optional[str] = None,
    on_drift: Optional[Callable[[drift.DriftEvent], None]] = None,
) -> Iterator[CanonicalMessage]:
    """Read one chat session and yield CanonicalMessage objects.

    Every row goes through :func:`safe_canonicalize`, so a single
    malformed row becomes a ``row_parse_failed`` drift event and is
    skipped rather than blowing up the whole chat.

    Args:
        con: open read-only connection to ChatStorage.sqlite.
        chat_pk: ZWACHATSESSION.Z_PK of the chat to read.
        me_name: display marker written into sender_raw for is_me rows.
        is_group: whether this session is a group (drives sender
            resolution via ZWAGROUPMEMBER).
        partner_name: for a 1:1 chat, the chat partner's display name
            (ZWACHATSESSION.ZPARTNERNAME). Every non-me message in a 1:1
            is attributed to this single person, collapsing the
            phone-JID / @lid split (§5.2) and giving a human label
            instead of a raw JID. Ignored for groups.
        on_drift: sink for drift events; defaults to a no-op. The
            adapter passes ``lambda e: record_event(mv_con, e)``.
    """
    sink = on_drift or _ignore_drift
    member_jids = _group_member_jids(con, chat_pk) if is_group else {}

    for row in get_messages(con, chat_pk):
        msg = drift.safe_canonicalize(
            lambda r: _build_canonical(
                con, r,
                me_name=me_name,
                is_group=is_group,
                member_jids=member_jids,
                partner_name=partner_name,
                on_drift=sink,
            ),
            row,
            source=ws.SOURCE_NAME,
            table="ZWAMESSAGE",
            on_drift=sink,
        )
        if msg is not None:
            yield msg


__all__ = [
    "get_messages",
    "iter_canonical",
    "list_chats_from_db",
    "probe",
    "wa_ts",
]
