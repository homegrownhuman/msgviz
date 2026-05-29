# -*- coding: utf-8 -*-
"""
WhatsApp Desktop schema contract.

Describes the on-disk layout of WhatsApp Desktop's
``ChatStorage.sqlite`` (macOS, Core Data ``ZWA*`` tables) that the
``whatsapp_live`` adapter relies on. Fed to
:func:`msgviz.core.drift.probe_tables` at the start of every sync so
schema changes shipped by Meta surface as loud, structured drift
events instead of silent data corruption.

Column names and storage classes here were verified against a real
WhatsApp Desktop install (May 2026, schema as of that build). When
Meta ships a schema change:

* a missing required column / table → ``fatal`` (sync aborts), and
* a new column / missing optional column → ``warn`` (sync continues),

both pointing the maintainer back at this file. Update the contract,
bump :data:`WHATSAPP_SCHEMA_VERSION`, done.

Storage-class note: WhatsApp declares its date columns as
``TIMESTAMP`` and its string columns as ``VARCHAR``. SQLite type
affinity collapses ``TIMESTAMP`` → ``NUMERIC`` and ``VARCHAR`` →
``TEXT``; :func:`msgviz.core.drift.probe_tables` normalises the same
way, so the contract is written in the *normalised* storage classes
(``NUMERIC`` / ``TEXT`` / ``INTEGER`` / ``BLOB``).
"""
from __future__ import annotations

from msgviz.core.drift import SchemaContract, TableContract

# WhatsApp/Apple Core Data epoch: seconds since 2001-01-01 UTC. The
# ZMESSAGEDATE / ZSENTDATE columns are floats counting from here.
# (Same epoch as iMessage, but iMessage counts *nanoseconds* — see
# msgviz/adapters/imessage_db.py APPLE_EPOCH; WhatsApp uses seconds.)
WHATSAPP_EPOCH = 978307200

# Bump whenever the *contract* changes shape, so old drift_event rows
# can be cleanly compared against a new contract version.
WHATSAPP_SCHEMA_VERSION = 1

SOURCE_NAME = "whatsapp_live"

# Core Data bookkeeping columns present on EVERY ZWA* table. They carry
# no message data and we never read them, but they're always there — so
# list them as known-optional on every table to keep the drift probe
# from crying "new_column" on a perfectly normal DB (proposal §13.11:
# don't train users to ignore the banner).
_CORE_DATA_COLUMNS = {"Z_ENT", "Z_OPT"}


# ---------------------------------------------------------------------------
# Message types · ZWAMESSAGE.ZMESSAGETYPE → canonical kind
# ---------------------------------------------------------------------------
# Verified codes from a real install plus the documented WhatsApp set.
# Anything not here fires an `unknown_enum_value` drift warning (the
# adapter still keeps the row if it has text, see whatsapp_db.py §kind).
MESSAGE_TYPE_TEXT = 0
MESSAGE_TYPE_IMAGE = 1
MESSAGE_TYPE_VIDEO = 2
MESSAGE_TYPE_AUDIO = 3
MESSAGE_TYPE_CONTACT = 4
MESSAGE_TYPE_LOCATION = 5
MESSAGE_TYPE_CALL = 6
MESSAGE_TYPE_SYSTEM = 7
MESSAGE_TYPE_DOCUMENT = 8
MESSAGE_TYPE_STICKER = 10
MESSAGE_TYPE_GIF = 15

# code → canonical "kind" tag. The canonical model itself has no
# explicit kind column; the adapter uses this to decide text vs media
# vs system handling. Values are descriptive strings used internally
# and for the apps[] label fallback.
MESSAGE_KIND = {
    MESSAGE_TYPE_TEXT: "text",
    MESSAGE_TYPE_IMAGE: "image",
    MESSAGE_TYPE_VIDEO: "video",
    MESSAGE_TYPE_AUDIO: "audio",
    MESSAGE_TYPE_CONTACT: "contact",
    MESSAGE_TYPE_LOCATION: "location",
    MESSAGE_TYPE_CALL: "call",
    MESSAGE_TYPE_SYSTEM: "system",
    MESSAGE_TYPE_DOCUMENT: "file",
    MESSAGE_TYPE_STICKER: "sticker",
    MESSAGE_TYPE_GIF: "gif",
}

KNOWN_MESSAGE_TYPES = set(MESSAGE_KIND.keys())

# ZWACHATSESSION.ZSESSIONTYPE
SESSION_TYPE_ONE_TO_ONE = 0
SESSION_TYPE_GROUP = 1
SESSION_TYPE_BROADCAST = 2
SESSION_TYPE_STATUS = 3
KNOWN_SESSION_TYPES = {
    SESSION_TYPE_ONE_TO_ONE,
    SESSION_TYPE_GROUP,
    SESSION_TYPE_BROADCAST,
    SESSION_TYPE_STATUS,
}

# ZWAMESSAGE.ZGROUPEVENTTYPE — non-zero on system/group-event messages
# (member added/removed, subject change, icon change, …). 0 = a normal
# message. We don't need the full taxonomy to archive; we only flag
# unknown *non-zero* values so a new event type is noticed. The set
# below is the documented range; extend as new ones appear.
KNOWN_GROUP_EVENT_TYPES = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12}


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------
CONTRACT = SchemaContract(
    source=SOURCE_NAME,
    version=WHATSAPP_SCHEMA_VERSION,
    tables={
        # The message table. Required = columns the adapter genuinely
        # cannot produce a correct CanonicalMessage without.
        "ZWAMESSAGE": TableContract(
            required_columns={
                "Z_PK": "INTEGER",          # incremental-sync watermark
                "ZSTANZAID": "TEXT",        # source_ref dedup key
                "ZMESSAGEDATE": "NUMERIC",  # ts (Core Data seconds)
                "ZFROMJID": "TEXT",         # sender (1:1) / group JID
                "ZISFROMME": "INTEGER",     # is_me
                "ZCHATSESSION": "INTEGER",  # FK → ZWACHATSESSION
                "ZMESSAGETYPE": "INTEGER",  # kind discriminator
            },
            optional_columns={
                "ZTEXT",            # body (NULL on media-only)
                "ZSENTDATE",        # server-confirmed timestamp
                "ZTOJID",           # recipient JID
                "ZMEDIAITEM",       # FK → ZWAMEDIAITEM
                "ZGROUPMEMBER",     # FK → ZWAGROUPMEMBER (group sender)
                "ZPARENTMESSAGE",   # FK → ZWAMESSAGE (reply target)
                "ZGROUPEVENTTYPE",  # system/group-event discriminator
                "ZPUSHNAME",        # sender's profile name at send time
                "ZSTARRED",         # starred flag
                "ZMESSAGESTATUS",   # delivery status
                "ZSORT",            # in-chat ordering
                "ZMESSAGEINFO",     # FK → ZWAMESSAGEINFO (receipts)
                "ZSPOTLIGHTSTATUS",
                "ZMESSAGEERRORSTATUS",
                "ZMEDIASECTIONID",
                "ZFLAGS",
                "ZPHASH",
            } | _CORE_DATA_COLUMNS,
        ),
        # Chat sessions → ChatSpec.
        "ZWACHATSESSION": TableContract(
            required_columns={
                "Z_PK": "INTEGER",          # source_id
                "ZCONTACTJID": "TEXT",      # JID of the 1:1 / group
                "ZSESSIONTYPE": "INTEGER",  # 0=1:1 1=group 2=bcast 3=status
            },
            optional_columns={
                "ZPARTNERNAME",     # display title
                "ZGROUPINFO",       # FK → ZWAGROUPINFO
                "ZLASTMESSAGEDATE",
                "ZLASTMESSAGETEXT",
                "ZARCHIVED",
                "ZHIDDEN",
                "ZREMOVED",
                "ZUNREADCOUNT",
                "ZMESSAGECOUNTER",
                "ZCONTACTIDENTIFIER",
            } | _CORE_DATA_COLUMNS,
        ),
        # Media items → Attachment.
        "ZWAMEDIAITEM": TableContract(
            required_columns={
                "Z_PK": "INTEGER",
                "ZMESSAGE": "INTEGER",      # FK back to ZWAMESSAGE
            },
            optional_columns={
                "ZMEDIALOCALPATH",      # the decoded file on disk
                "ZTHUMBNAILLOCALPATH",
                "ZXMPPTHUMBPATH",
                "ZFILESIZE",
                "ZMOVIEDURATION",       # audio/video length (s)
                "ZVCARDSTRING",         # contact-card payload
                "ZVCARDNAME",
                "ZLATITUDE",            # location messages
                "ZLONGITUDE",
                "ZTITLE",
                "ZMEDIAURL",
                "ZMEDIAKEY",
                "ZMETADATA",
                "ZASPECTRATIO",
            } | _CORE_DATA_COLUMNS,
        ),
        # Group membership → real sender of a group message.
        "ZWAGROUPMEMBER": TableContract(
            required_columns={
                "Z_PK": "INTEGER",
                "ZMEMBERJID": "TEXT",       # the actual sender JID
            },
            optional_columns={
                "ZCHATSESSION",     # FK → ZWACHATSESSION
                "ZCONTACTNAME",
                "ZFIRSTNAME",
                "ZCONTACTIDENTIFIER",
                "ZISADMIN",
                "ZISACTIVE",
            } | _CORE_DATA_COLUMNS,
        ),
    },
    known_enums={
        "ZWAMESSAGE.ZMESSAGETYPE": KNOWN_MESSAGE_TYPES,
        "ZWACHATSESSION.ZSESSIONTYPE": KNOWN_SESSION_TYPES,
        "ZWAMESSAGE.ZGROUPEVENTTYPE": KNOWN_GROUP_EVENT_TYPES,
    },
)


__all__ = [
    "CONTRACT",
    "KNOWN_GROUP_EVENT_TYPES",
    "KNOWN_MESSAGE_TYPES",
    "KNOWN_SESSION_TYPES",
    "MESSAGE_KIND",
    "SESSION_TYPE_BROADCAST",
    "SESSION_TYPE_GROUP",
    "SESSION_TYPE_ONE_TO_ONE",
    "SESSION_TYPE_STATUS",
    "SOURCE_NAME",
    "WHATSAPP_EPOCH",
    "WHATSAPP_SCHEMA_VERSION",
]
