# Database Schema

Message Visualizer stores everything in a single SQLite database —
`data/visualizer.db` by default, relocatable via `MSGVIZ_HOME`. The
schema is intentionally small (10 tables) and **source-agnostic**: no
column belongs to Apple or WhatsApp specifically. Adapter-specific
identifiers live in two bridge tables (`chat_source`, `source_ref`).

Source of truth: [`msgviz/core/schema.sql`](../msgviz/core/schema.sql).
Runtime additive migrations: [`msgviz/core/schema_migrate.py`](../msgviz/core/schema_migrate.py).

---

## Overview

```
        person ─< handle
           │ (owner)         │ (sender)
        device ─< chat ─< message ─< media
                    │
                    └─ chat_participant >─ person
                    └─ chat_source        (adapter-specific chat IDs)
                              source_ref  (adapter-specific message IDs)

        person ─< person_alias
        meta                              (schema_version etc.)
```

A **person** is the unit of identity. They can have many **handles**
(phone numbers, emails) and many **aliases** (alternate display
spellings). A **device** is a message source owned by exactly one
person (the "me"-perspective). A **chat** belongs to one device. A
**message** belongs to one chat, was sent by one person, and may have
**media** attachments. Group chats use **chat_participant** to enumerate
who's in the room.

The two `*_source` / `source_ref` tables are bridges to external
adapters that need to round-trip identity (e.g. iMessage live sync
needs to remember "I already imported chat.ROWID=42"). Adapters that
do one-shot bulk imports leave them empty.

---

## Tables

### `person`

The identity table. One row per real human.

| Column        | Type    | Notes |
|---|---|---|
| `id`          | INTEGER | PK |
| `display_name`| TEXT NOT NULL | What the UI shows |
| `note`        | TEXT    | Optional free text (private notes) |
| `avatar_src`  | TEXT    | Relative web path to an avatar image, or NULL |

`avatar_src` follows the same hash-based layout as media:
`media/avatars/<prefix>/<hash>.<ext>`. NULL means "render initials".

### `handle`

Phone numbers, emails, WhatsApp IDs — anything that uniquely identifies
a person in some source. One person ↔ many handles.

| Column     | Type    | Notes |
|---|---|---|
| `id`       | INTEGER | PK |
| `value`    | TEXT NOT NULL UNIQUE | Normalized form (E.164 phone, lowercase email) |
| `person_id`| INTEGER NOT NULL → person | Owner |

Index: `idx_handle_person` on `person_id`.

### `person_alias`

Alternate display names that should resolve to the same person.
Example: a WhatsApp export labels Alice as `Alice K. Example`, but
internally she's `Alice`. Matched case-insensitively by the person
resolver.

| Column     | Type    | Notes |
|---|---|---|
| `id`       | INTEGER | PK |
| `value`    | TEXT NOT NULL UNIQUE | Alternate display name |
| `person_id`| INTEGER NOT NULL → person | Canonical person |

Index: `idx_alias_person` on `person_id`.

### `device`

One per source declared in `config/sources.json`. Determines the
"me"-perspective for everything below it.

| Column            | Type    | Notes |
|---|---|---|
| `id`              | INTEGER | PK |
| `slug`            | TEXT NOT NULL UNIQUE | e.g. `my_mac`, `wa_archive` |
| `name`            | TEXT NOT NULL | Display name, e.g. "MacBook Pro M1 Max" |
| `type`            | TEXT NOT NULL | `mac_live`, `static`, `ios_backup`, … |
| `owner_person_id` | INTEGER NOT NULL → person | The "me" for chats on this device |

`type` controls behavior:
- `mac_live` — polled live by the iMessage watcher (macOS only)
- `static` — read-once bulk import (WhatsApp exports, iMessage backups)
- `ios_backup` — historical, kept for compatibility

### `chat`

A conversation thread on a device. Source-agnostic.

| Column     | Type    | Notes |
|---|---|---|
| `id`       | INTEGER | PK |
| `slug`     | TEXT NOT NULL UNIQUE | `<device-slug>/<chat-slug>`, e.g. `my_mac/bob` |
| `device_id`| INTEGER NOT NULL → device | |
| `title`    | TEXT NOT NULL | Display name (counterpart name or group title) |
| `subtitle` | TEXT | Free-text (phone number, group description) |
| `is_group` | INTEGER NOT NULL DEFAULT 0 | 0 = 1:1, 1 = group |
| `origin`   | TEXT NOT NULL DEFAULT `'apple'` | `apple`, `whatsapp`, … (drives the source-badge icon) |

Index: `idx_chat_device` on `device_id`.

### `chat_source`

Bridge to external adapter identifiers, **only** for adapters that
need incremental sync. Keyed by `(source, external_id)` so the same
external ID under two adapter instances doesn't collide.

| Column         | Type    | Notes |
|---|---|---|
| `chat_id`      | INTEGER NOT NULL → chat ON DELETE CASCADE | |
| `source`       | TEXT NOT NULL | Adapter instance, e.g. `imessage_live:mac_christian` |
| `external_id`  | TEXT NOT NULL | Adapter-side ID (e.g. Apple `chat.ROWID`) |

Primary key: `(source, external_id)`.
Index: `idx_chat_source_chat` on `chat_id`.

Bulk importers (WhatsApp, iMessage backup) leave this empty — they
don't round-trip.

### `chat_participant`

Group-chat membership. For 1:1 chats it's redundant but harmless.

| Column     | Type    | Notes |
|---|---|---|
| `chat_id`  | INTEGER NOT NULL → chat | |
| `person_id`| INTEGER NOT NULL → person | |

Primary key: `(chat_id, person_id)`.

### `message`

The big one. Source-agnostic; no Apple/WhatsApp-specific columns.

| Column             | Type    | Notes |
|---|---|---|
| `id`               | INTEGER | PK |
| `chat_id`          | INTEGER NOT NULL → chat | |
| `sender_person_id` | INTEGER → person | Nullable (system messages, unknown senders) |
| `ts`               | INTEGER NOT NULL | Unix seconds |
| `is_me`            | INTEGER NOT NULL DEFAULT 0 | Denormalized for fast filtering — equal to Apple's `is_from_me` |
| `text`             | TEXT | NULL for attachment-only messages |
| `retracted`        | INTEGER NOT NULL DEFAULT 0 | 1 if the sender un-sent the message |
| `edits`            | TEXT | JSON: `[{text, ts}, …]`, oldest first; current text in `text` |
| `reactions`        | TEXT | JSON: `[{emoji, label, sender}, …]` |
| `apps`             | TEXT | JSON: `["🔗 Shared link", …]` — iMessage app integrations as plain badges |
| `media_status`     | TEXT NOT NULL DEFAULT `'none'` | `none`, `pending`, `ready` — workflow flag for the media worker |
| `sync_state`       | TEXT NOT NULL DEFAULT `'published'` | `new` (unread, drives `new_count`) or `published` |

Indexes:
- `idx_msg_chat_ts` on `(chat_id, ts)` — drives all timeline queries
- `idx_msg_sync` on `sync_state` — `POST /api/chat/.../seen` flips `new` → `published`
- `idx_msg_media` on `media_status` — used by the media worker
- `idx_msg_sender` on `sender_person_id` — used by the per-person media filter

Why JSON columns for `edits` / `reactions` / `apps`? They're always
read with the message, never queried independently. Splitting them into
normalized tables would triple the join cost for no query benefit.

### `source_ref`

Per-message bridge to external adapters. Same shape as `chat_source`,
keyed by `(source, external_id)`.

| Column        | Type    | Notes |
|---|---|---|
| `message_id`  | INTEGER NOT NULL → message ON DELETE CASCADE | |
| `source`      | TEXT NOT NULL | e.g. `imessage_live:mac_christian` |
| `external_id` | TEXT NOT NULL | e.g. Apple `message.ROWID` |

Primary key: `(source, external_id)`.
Index: `idx_source_ref_msg` on `message_id`.

Used by the live iMessage watcher to dedupe on re-poll. WhatsApp and
iMessage backup leave it empty.

### `media`

Attachments. Files live on disk under `media/<kind>/<prefix>/<hash>.<ext>`
(hash-based layout); only metadata is in the DB.

| Column         | Type    | Notes |
|---|---|---|
| `id`           | INTEGER | PK |
| `message_id`   | INTEGER NOT NULL → message | |
| `kind`         | TEXT NOT NULL | `image`, `video`, `audio`, `other` |
| `src`          | TEXT | Web-relative path: `media/<kind>/<prefix>/<hash>.<ext>` |
| `cat`          | TEXT | Image sub-category: `foto`, `sticker`, `emoji`. NULL for non-images |
| `portrait`     | INTEGER NOT NULL DEFAULT 0 | Orientation hint for the layout grid |
| `done`         | INTEGER NOT NULL DEFAULT 0 | Has the media worker finished thumbnailing / converting this? |
| `bytes`        | INTEGER NOT NULL DEFAULT 0 | Web-file size, used by per-chat byte totals |
| `content_hash` | TEXT | First 16 hex chars of the source's SHA-256 |

Indexes:
- `idx_media_msg` on `message_id`
- `idx_media_hash` on `content_hash` — used for dedup across imports

### `meta`

Schema version and any future global flags.

| Column | Type | Notes |
|---|---|---|
| `key`  | TEXT PRIMARY KEY | |
| `value`| TEXT | |

Known keys:
- `schema_version` — currently `'1'`. Bumped by additive migrations only.

---

## Conventions

### Timestamps
All `ts` columns are **Unix seconds**, integer. Imports normalize on
the way in:

| Source | Native format | Convert with |
|---|---|---|
| Apple chat.db | nanoseconds since 2001-01-01 UTC | `(apple_ts // 1_000_000_000) + 978307200` |
| WhatsApp export | `[DD.MM.YY, HH:MM:SS]` local | `datetime.strptime().timestamp()` |

### Foreign keys
Enabled via `PRAGMA foreign_keys = ON`. SQLite enforces them at write
time. `ON DELETE CASCADE` is set on `chat_source.chat_id` and
`source_ref.message_id` only — every other FK is "restrict" by default
(deleting a referenced row fails).

### WAL mode
`PRAGMA journal_mode = WAL` is set at schema-creation time. Means
readers don't block writers — the FastAPI server holds long-lived
read-only connections while the iMessage watcher writes new messages
in the background.

### Avatars
`person.avatar_src` is a web-relative path of the form
`media/avatars/<prefix>/<hash>.<ext>`. The CLI's `person set-avatar`
copies an image file, computes a content hash, stores it under
`media/avatars/<2-char-prefix>/<hash>.<ext>`, and writes the path to
the `avatar_src` column. NULL means "render initials in the UI".

### Hash-based media layout
Media files are stored by SHA-256 of the source bytes, never by
original filename. This means:
- The same image attached to many messages stores **one** file
- Filenames carry no path metadata that could leak to the web
- Deduping across imports is automatic

The DB column `media.src` holds the relative path, `media.content_hash`
holds the first 16 hex chars of the hash (useful for dedup queries).

---

## Migrations

### Runtime additive migrations

The CLI auto-runs additive migrations the first time it opens the DB
for writing. Defined in
[`msgviz/core/schema_migrate.py`](../msgviz/core/schema_migrate.py).

Current migrations:

| Migration | What it adds |
|---|---|
| `person.avatar_src` | Adds the avatar column to existing DBs that predate the avatar feature |

Migration policy:
- **Additive only** — new columns, new tables.
- **Never destructive** — never `DROP`, never `CHANGE TYPE`.
- **Idempotent** — checks if the column already exists before altering.

Read-only connections never trigger migrations — they'd fail anyway
(can't alter from a read-only handle). Only writable opens through
`open_db()` run them.

### Legacy one-shot migration

[`msgviz/core/migrate.py`](../msgviz/core/migrate.py) is a separate
tool: it imports legacy `data/chats/*.json` files (from a pre-SQLite
prototype) into the current schema. Not needed for new installs.

---

## Inspecting the DB

```bash
# Quick overview from the CLI
msgviz status

# Open the live DB in the sqlite shell (read-only is safest)
sqlite3 -readonly data/visualizer.db

# Common ad-hoc queries
sqlite> .schema person
sqlite> SELECT COUNT(*) FROM message GROUP BY chat_id ORDER BY 1 DESC LIMIT 10;
sqlite> SELECT slug, title FROM chat WHERE is_group = 1;
sqlite> SELECT name FROM sqlite_master WHERE type='index';
```

---

## Adding a new column or table

1. Add the `CREATE TABLE IF NOT EXISTS …` or `ALTER TABLE …` to
   `msgviz/core/schema.sql`.
2. Add a runtime migration function in
   `msgviz/core/schema_migrate.py` and wire it into `apply_all()`.
   Mirror the existing `ensure_avatar_column` pattern: check existence
   first, return True only if the change was actually applied.
3. Add a regression test in
   `tests/unit/test_avatars.py` (the existing avatar migration tests
   are the template) verifying that:
   - The migration runs on a legacy DB without the column,
   - It's idempotent (second run is a no-op),
   - Data in the table survives the migration.
4. Update the table reference in this document.

Never write a migration that drops or renames anything. If you need
to retire a column, leave it `NULL`able and document it as deprecated
in the table reference.
