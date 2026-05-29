# -*- coding: utf-8 -*-
"""
iMessage (Apple ``chat.db``) schema contract.

Shared by both iMessage adapters — ``imessage_live`` (the running
macOS Messages DB) and ``imessage_backup`` (a frozen iOS-backup
snapshot, same table shape). Fed to
:func:`msgviz.core.drift.probe_tables` so a macOS / iOS update that
reshapes ``chat.db`` surfaces as loud, structured drift instead of a
silent ``getattr``-and-pray fall-through (proposal §13.10).

Required vs optional is calibrated against what
:mod:`msgviz.adapters.imessage_db` actually reads:

* Columns it SELECTs unconditionally → **required** (missing → fatal).
* Columns it already degrades to NULL when absent
  (``date_edited`` / ``date_retracted`` / ``attachment.uti`` /
  ``attachment.emoji_image_short_description``) → **optional**
  (missing → warn, ingestion continues exactly as today).

The ``source`` of the contract is parameterised because the two
adapters write different ``source`` tags into ``drift_event``
(``imessage_live`` vs ``imessage_backup``) but probe the identical
schema. Use :func:`contract_for` to get a contract bound to one
adapter's source name.

Apple epoch note: ``message.date`` etc. are **nanoseconds** since
2001-01-01 UTC (unlike WhatsApp's seconds). See
:data:`msgviz.adapters.imessage_db.APPLE_EPOCH`.
"""
from __future__ import annotations

from msgviz.core.drift import SchemaContract, TableContract

IMESSAGE_SCHEMA_VERSION = 1

# The two adapter source tags that share this schema.
SOURCE_LIVE = "imessage_live"
SOURCE_BACKUP = "imessage_backup"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
# chat.style: 43 = group, 45 = 1:1 (direct). imessage_db treats
# style != 45 as group.
KNOWN_CHAT_STYLES = {43, 45}

# message.associated_message_type: 0 = normal; 2000-2005 add a tapback;
# 3000-3005 remove one. (Stickers/edits use other signals.) Listed so a
# brand-new reaction code is noticed rather than silently treated as a
# normal message.
KNOWN_ASSOCIATED_MESSAGE_TYPES = {
    0,
    2000, 2001, 2002, 2003, 2004, 2005,    # tapback added
    3000, 3001, 3002, 3003, 3004, 3005,    # tapback removed
}

# message.service: the transport. RCS appeared on macOS in 2024-25.
KNOWN_SERVICES = {"iMessage", "SMS", "RCS"}


# ---------------------------------------------------------------------------
# Table contracts (Apple chat.db layout)
# ---------------------------------------------------------------------------
_MESSAGE = TableContract(
    required_columns={
        "ROWID": "INTEGER",                  # external_id / sync watermark
        "guid": "TEXT",                      # tapback target matching
        "text": "TEXT",                      # body
        "attributedBody": "BLOB",            # text fallback (NSArchiver)
        "handle_id": "INTEGER",              # FK → handle
        "date": "INTEGER",                   # Apple nanoseconds
        "is_from_me": "INTEGER",             # is_me
        "cache_has_attachments": "INTEGER",  # gate for the attachment join
        "associated_message_type": "INTEGER",  # tapback discriminator
        "associated_message_guid": "TEXT",   # tapback target guid
        "balloon_bundle_id": "TEXT",         # app-message label
        "message_summary_info": "BLOB",      # edit history (plist)
    },
    optional_columns={
        # imessage_db already degrades these to NULL when absent
        # (older macOS). Missing → warn, not fatal.
        "date_edited",
        "date_retracted",
        # Commonly-present extras the reader doesn't require but that
        # exist on essentially every modern chat.db — listed so they
        # don't trip new_column drift on a normal Mac.
        "service",
        "subject",
        "account",
        "account_guid",
        "error",
        "date_read",
        "date_delivered",
        "is_delivered",
        "is_read",
        "is_sent",
        "is_audio_message",
        "is_spam",
        "item_type",
        "group_title",
        "group_action_type",
        "expressive_send_style_id",
        "thread_originator_guid",
        "thread_originator_part",
        "payload_data",
        "share_status",
        "share_direction",
        "reply_to_guid",
        "destination_caller_id",
    },
    # Apple's `message` table has ~60 columns and grows a few each macOS
    # release. We read 14. Flagging every unlisted column as new_column
    # would bury the user in noise on a perfectly healthy DB (§13.11),
    # so we only care about *losing* a column we depend on (caught via
    # required/optional), not Apple adding one.
    flag_new_columns=False,
)

_CHAT = TableContract(
    required_columns={
        "ROWID": "INTEGER",          # source_id
        "chat_identifier": "TEXT",   # the 1:1 handle / group id
        "service_name": "TEXT",      # 'iMessage' | 'SMS' | …
        "display_name": "TEXT",      # group title (NULL for 1:1)
        "style": "INTEGER",          # 43 group / 45 direct
    },
    optional_columns={
        "guid",
        "state",
        "room_name",
        "group_id",
        "is_archived",
        "last_addressed_handle",
        "last_read_message_timestamp",
        "original_group_id",
    },
    flag_new_columns=False,    # ~30-column Apple table; same reasoning.
)

_HANDLE = TableContract(
    required_columns={
        "ROWID": "INTEGER",
        "id": "TEXT",                # phone number or email
    },
    optional_columns={
        "country",
        "service",
        "uncanonicalized_id",
        "person_centric_id",
    },
)

_ATTACHMENT = TableContract(
    required_columns={
        "ROWID": "INTEGER",
        "filename": "TEXT",          # the on-disk path
        "mime_type": "TEXT",
        "transfer_name": "TEXT",     # original display name
        "is_sticker": "INTEGER",
    },
    optional_columns={
        # imessage_db selects these only when present.
        "uti",
        "emoji_image_short_description",
        "guid",
        "created_date",
        "total_bytes",
        "is_outgoing",
        "hide_attachment",
        "original_guid",
    },
    flag_new_columns=False,    # ~25-column Apple table; same reasoning.
)

_CHAT_MESSAGE_JOIN = TableContract(
    required_columns={
        "chat_id": "INTEGER",
        "message_id": "INTEGER",
    },
    optional_columns={"message_date"},
)

_MESSAGE_ATTACHMENT_JOIN = TableContract(
    required_columns={
        "message_id": "INTEGER",
        "attachment_id": "INTEGER",
    },
)

_TABLES = {
    "message": _MESSAGE,
    "chat": _CHAT,
    "handle": _HANDLE,
    "attachment": _ATTACHMENT,
    "chat_message_join": _CHAT_MESSAGE_JOIN,
    "message_attachment_join": _MESSAGE_ATTACHMENT_JOIN,
}

_KNOWN_ENUMS = {
    "chat.style": KNOWN_CHAT_STYLES,
    "message.associated_message_type": KNOWN_ASSOCIATED_MESSAGE_TYPES,
}


def contract_for(source: str) -> SchemaContract:
    """Return the iMessage schema contract bound to one adapter's
    source tag (``imessage_live`` or ``imessage_backup``).

    Both adapters probe the identical Apple ``chat.db`` shape; only the
    ``source`` recorded in ``drift_event`` differs.
    """
    return SchemaContract(
        source=source,
        version=IMESSAGE_SCHEMA_VERSION,
        tables=_TABLES,
        known_enums=_KNOWN_ENUMS,
    )


# Convenience pre-bound contracts.
CONTRACT_LIVE = contract_for(SOURCE_LIVE)
CONTRACT_BACKUP = contract_for(SOURCE_BACKUP)


__all__ = [
    "CONTRACT_BACKUP",
    "CONTRACT_LIVE",
    "IMESSAGE_SCHEMA_VERSION",
    "KNOWN_ASSOCIATED_MESSAGE_TYPES",
    "KNOWN_CHAT_STYLES",
    "KNOWN_SERVICES",
    "SOURCE_BACKUP",
    "SOURCE_LIVE",
    "contract_for",
]
