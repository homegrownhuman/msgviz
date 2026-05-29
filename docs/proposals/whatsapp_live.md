# Proposal: `whatsapp_live` adapter + adapter drift detection

**Status:** ✅ Implemented (msgviz main) — this doc is kept as the
design rationale. WhatsApp-live + the cross-cutting drift detection
shipped across the adapters, the `import whatsapp-live` and `drift`
CLI commands, the `/api/drift` endpoint + UI banner, and a
reference-counted DB+disk purge as the removal safety net.
**Target:** msgviz v0.2
**Scope:**
  - macOS only for WhatsApp (Linux/Windows discussion at the end)
  - **All adapters** (iMessage live, iMessage backup, WhatsApp export,
    WhatsApp live) get drift detection in the same PR

---

## TL;DR

Two things shipped together:

1. **`whatsapp_live` adapter** — reads the **WhatsApp Desktop** app's
   on-disk SQLite database the same way `imessage_live` reads Apple's
   `chat.db`. Pure local file access, zero network, zero risk of an
   account ban, no QR pairing, no Node/Go dependency.

2. **Adapter drift detection** (§13) — a cross-cutting mechanism that
   notices when Apple or Meta change their on-disk schema and refuses
   to silently produce wrong data. Every adapter that reads a vendor-
   controlled SQLite gets a schema contract, fatal-vs-warn
   classification, persistent `drift_event` log, dedup, and
   acknowledgement workflow surfaced through CLI, API, and UI.

Reason for the bundling: WhatsApp drift is the most likely failure
mode of this adapter, so designing the mechanism is mandatory anyway —
and once it exists, iMessage gets it for free. Retrofitting drift
detection onto iMessage in a later PR means writing the contract
twice.

Expected effort: ~7 dev-days for both pieces (see §9).

---

## 1 · Background

msgviz currently has three sources:

| Source | Mode | Adapter |
|---|---|---|
| iMessage live (running macOS) | live, incremental | `imessage_live` |
| iMessage iOS backup | bulk import | `imessage_backup` |
| WhatsApp export `.txt`/`.zip` | bulk import | `whatsapp_export` |

The gap: **no live WhatsApp.** Users who want their WhatsApp chats in
msgviz today have to manually export each chat from the phone and
re-import on every refresh. That's painful and stops being useful for
ongoing archival.

## 2 · Why "local file" and not the WhatsApp Web protocol

Before settling on the local-file approach we surveyed every realistic
option for monitoring a personal WhatsApp account in 2026:

| Approach | Verdict |
|---|---|
| Local SQLite (Desktop app) | **Picked.** Plaintext, no network, no ban risk. |
| `whatsmeow` (Go) / `neonize` (Python wrapper) | Linked-device pairing; real ban risk reported in 2025-26 even for read-only clients (see `tulir/whatsmeow#810`). Keep as opt-in fallback for non-mac users. |
| `Baileys` (Node) | Same ban risk + Sept 2025 npm supply-chain incident. JS in a Python tool. |
| `whatsapp-web.js` (Node + Puppeteer) | Adds ~300 MB Chromium, breaks on every UI change, easy to fingerprint. |
| WhatsApp Business Cloud API | Not usable for personal accounts. |

The local-file path is the only option that's (a) implementable in
pure Python with msgviz's existing dependencies, (b) carries zero
account-ban risk because no protocol traffic touches Meta's servers,
and (c) "fails closed" — a schema change means we stop ingesting new
rows, not that someone's account gets locked.

Trade-off accepted: macOS only at first. Windows ChatStorage is in
LevelDB IndexedDB segments inside WebView2, parseable but much higher
effort. Linux has no first-party WhatsApp client.

## 3 · Feasibility (empirically verified)

WhatsApp Desktop for macOS stores chats here:

```
~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite
```

Verified on a live install (May 2026):

- Plaintext SQLite, no key required
- ~24 k messages / ~330 chats in a single multi-month archive
- Last write timestamp updates within seconds of a new message
  arriving on the phone (so the Desktop app already does the
  primary-device sync; msgviz only needs to tail the SQLite)
- Same access model as `~/Library/Messages/chat.db` — sandbox grants
  read access to user-space processes running as the same user

Sibling files in the same container:

```
ChatStorage.sqlite          ← messages, chat sessions
ContactsV2.sqlite           ← display names, push names
Axolotl.sqlite              ← Signal protocol state (we don't touch this)
Message/Media/<chat>/…      ← decoded media files on disk
```

Media files are already decoded (jpg/mp4/ogg/pdf) — no decryption
needed, msgviz just hashes them into the content-addressed media
store like with iMessage attachments.

## 4 · Schema map · WhatsApp → CanonicalMessage

`ZWAMESSAGE` (the message table, Apple Core Data naming convention):

| WhatsApp column | Type | msgviz target | Notes |
|---|---|---|---|
| `Z_PK` | INT | (internal) | Local row-id; only used as the watermark for incremental sync. |
| `ZSTANZAID` | TEXT | `source_ref` | Globally-unique WhatsApp message ID — perfect dedup key. |
| `ZMESSAGEDATE` | REAL | `ts` | Apple seconds-since-2001; add 978307200 for Unix epoch. |
| `ZSENTDATE` | REAL | (server-confirmed-at) | Optional; falls back to ZMESSAGEDATE. |
| `ZTEXT` | TEXT | `body` | NULL for media-only messages. |
| `ZFROMJID` | TEXT | sender handle | `<phone>@s.whatsapp.net` or `<lid>@lid` (privacy-preserving). |
| `ZTOJID` | TEXT | (recipient handle) | For routing/group lookup. |
| `ZISFROMME` | INT | `is_me` | Boolean. |
| `ZCHATSESSION` | INT | (FK → ChatSpec) | → `ZWACHATSESSION.Z_PK`. |
| `ZGROUPMEMBER` | INT | (FK → group sender) | In group chats, the actual sender; see §5.3. |
| `ZMEDIAITEM` | INT | (FK → attachment) | → `ZWAMEDIAITEM.Z_PK`. |
| `ZPARENTMESSAGE` | INT | (FK → quoted msg) | Reply chains; → `ZWAMESSAGE.Z_PK`. |
| `ZMESSAGETYPE` | INT | derived `kind` | 0=text, 1=image, 2=video, 3=audio, … (see §5.4). |
| `ZGROUPEVENTTYPE` | INT | `system_event` | Joins, subject changes, etc. |

`ZWACHATSESSION` → `ChatSpec`:

| Column | Target |
|---|---|
| `Z_PK` | `source_id` |
| `ZPARTNERNAME` | `title` |
| `ZCONTACTJID` | `subtitle` (the JID of the 1:1 or group) |
| `ZSESSIONTYPE` | `is_group` (0 = 1:1, 1 = group, 2 = broadcast list, 3 = status) |

`ZWAMEDIAITEM` → media file resolution:

| Column | Use |
|---|---|
| `ZMEDIALOCALPATH` | Path relative to container; absolute = `<container>/Message/Media/<ZMEDIALOCALPATH>`. |
| `ZMOVIEDURATION` | Audio/video length in seconds. |
| `ZFILESIZE` | Bytes. |
| `ZVCARDSTRING` | Contact-card payload (rare). |

## 5 · Edge cases

### 5.1 Apple epoch conversion

`ZMESSAGEDATE` is **seconds since 2001-01-01 UTC** (Core Data
convention), not 1970. Add 978307200 to get Unix epoch. Mishandle
this and every message lands 31 years in the past.

### 5.2 `@lid` JIDs (linked-device IDs)

WhatsApp now hides phone numbers behind opaque `@lid` IDs in many
contexts. A handle might appear as `114242002940112@lid` in one
message and `4915xxxxxxxxx@s.whatsapp.net` in another for the same
person.

**Resolution strategy:**

1. Treat each unique JID as a distinct `Handle` row initially.
2. Use the existing `person_alias` table (case-insensitive name
   matching) to merge them when `ContactsV2.sqlite` reveals the
   display name behind both JIDs.
3. Periodic reconciliation job: scan `ZWAPROFILEPUSHNAME` to glue
   `@lid` handles to phone-number JIDs by shared push name.

This is opportunistic — perfect resolution requires reading
`ContactsV2.sqlite`, which the v1 adapter will do.

### 5.3 Group sender disambiguation

In a 1:1 chat `ZFROMJID` directly identifies the sender. In a group,
`ZFROMJID` is the **group JID** (e.g. `1203...@g.us`) and the real
sender is `ZGROUPMEMBER → ZWAGROUPMEMBER.ZMEMBERJID`.

Adapter must left-join `ZWAGROUPMEMBER` when the chat session has
`ZSESSIONTYPE=1` and prefer `ZWAGROUPMEMBER.ZMEMBERJID` over
`ZWAMESSAGE.ZFROMJID`.

### 5.4 Message types

`ZMESSAGETYPE` maps roughly to:

| Code | Meaning | Canonical kind |
|---|---|---|
| 0 | Text | `text` |
| 1 | Image | `image` |
| 2 | Video | `video` |
| 3 | Audio / voice note | `audio` |
| 4 | Contact card | `system` (with vcard payload) |
| 5 | Location | `system` (with lat/lon) |
| 7 | System event | `system` |
| 8 | Document | `file` |
| 10 | Sticker | `image` |
| 15 | GIF (as MP4) | `video` |
| (other) | … | log unknown, keep as `text` if `ZTEXT` non-null |

Unknown codes get logged once per code per run, not dropped.

### 5.5 Deletions

If a user deletes a message on the phone, it's removed from
`ZWAMESSAGE` on Desktop within a few seconds. msgviz **keeps** the
archived copy (point of the tool) but should:

- Not panic when a previously-imported `ZSTANZAID` disappears.
- Optionally annotate the canonical message with
  `deleted_at = <first run that didn't see it>` to surface it in the
  UI. Default off in v1; reconcile in v2.

### 5.6 WAL hygiene

WhatsApp Desktop holds an open writer on `ChatStorage.sqlite` with
WAL mode. msgviz must open it `mode=ro&immutable=0` (read-only **but
not immutable**, so SQLite still consults the WAL). The
`imessage_live` adapter already does this; copy the pattern.

### 5.7 Schema drift

Meta has changed `ZWA*` columns at least three times since
multi-device launch. Schema drift is the most likely thing to break
this adapter, and the failure mode must be **loud, structured, and
visible in every surface** — never a silent fall-through. See §13
for the full drift-detection design.

## 6 · Architecture · file layout

```
msgviz/
  adapters/
    whatsapp_db.py          ← new · row iteration, joins, type mapping
    whatsapp_live.py        ← new · Adapter class implementing the
                                       msgviz adapter protocol
  cli/
    import_cmd.py           ← add: msgviz import whatsapp-live
  workers/
    watcher.py              ← add whatsapp_live to the watch loop
  paths.py                  ← add WA container path resolver
docs/
  proposals/
    whatsapp_live.md        ← this file
  CLI.md                    ← document new subcommand
  ARCHITECTURE.md           ← add to source matrix
tests/
  unit/
    test_whatsapp_db.py     ← parsing, edge cases, type map
    test_whatsapp_live.py   ← adapter shape, incremental anchor
  fixtures/
    whatsapp_minimal.sql    ← synthetic ZWA* schema + a few rows
```

## 7 · Source-ref convention

To slot into the existing dedup machinery (`core/sync.py`):

```
whatsapp_live:<device_slug>:<ZSTANZAID>
```

Mirror of the iMessage live convention. The existing sync code
already keys deduplication on the full `source_ref` string, no
changes there.

For the watcher, the per-source anchor is the highest `Z_PK` seen so
far — written to the existing `sync_anchor` row, polled every
`watcher_poll_seconds`.

## 8 · CLI surface

```bash
# one-shot incremental sync
msgviz import whatsapp-live

# specify a non-default DB path (rare — only if user moved it)
msgviz import whatsapp-live --db ~/Library/Group\ Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite

# limit to specific chats
msgviz import whatsapp-live --chat "Mom" --chat "Dev Team"

# dry-run, no DB writes
msgviz import whatsapp-live --dry-run
```

Live watching uses the existing `msgviz serve` watcher — no new
flag needed; once the source exists in `sources.json` the watcher
includes it automatically.

`sources.json` entry:

```json
{
  "kind": "whatsapp_live",
  "device_slug": "mac_christian_wa",
  "me_name": "Me",
  "db_path": null
}
```

`db_path: null` → resolve to the default macOS path. Explicit path
overrides.

## 9 · Build plan & estimate

| # | Phase | Effort |
|---|---|---|
| 1a | `core/drift.py` — shared `DriftEvent`, `SchemaReport`, `record_drift()`, `probe_tables()` generic helper | 0.5 day |
| 1b | `drift_event` table migration + dedup index + `core/drift.py` query layer | 0.5 day |
| 2a | `whatsapp_schema.py` — contract, `probe()`, KNOWN_* enums | 0.5 day |
| 2b | `whatsapp_db.py` — open, row iterator, type map, group-member join, guarded `_safe_canonicalize` | 1 day |
| 2c | `whatsapp_live.py` — Adapter class, ChatSpec listing, attachment resolution | 0.5 day |
| 3 | `imessage_schema.py` — contract for `chat.db` shared by live + backup adapters; calibrated to macOS 14/15/16 reality | 0.5 day |
| 4 | `whatsapp_export_schema.py` — locale + date-format contract for the export parser (§13.12) | 0.25 day |
| 5 | Wire schema probe + safe-row helper into the four adapters (`imessage_live`, `imessage_backup`, `whatsapp_live`, `whatsapp_export`) | 0.75 day |
| 6 | CLI: `import whatsapp-live`, `drift` subcommand (list / explain / ack / ack-all / --json), `check` integration, `sources.json` schema bump | 1 day |
| 7 | Watcher integration (drift-respect: pause source on fatal) | 0.5 day |
| 8a | Server `/api/drift` endpoint | 0.25 day |
| 8b | Frontend drift banner + details panel | 0.5 day |
| 9 | Tests: synthetic fixtures + unit tests + golden output + drift-specific tests from §13.7 + iMessage fixture variants for each known macOS schema | 2 days |
| 10 | Docs: CLI.md (drift subcommand), ARCHITECTURE.md (drift_event row + four-adapter matrix), README sources matrix, SCHEMA.md, GETTING_STARTED.md (one-line note) | 0.75 day |
| **Total** | | **~9 dev-days** |

## 10 · Out of scope for v1

- **Linux / Windows.** Discussed in §2; deferred.
- **Sending messages.** msgviz is read-only by design.
- **Deletion sync** (§5.5). v2 if useful.
- **Contact-name backfill from `ContactsV2.sqlite`** as a separate
  reconciliation pass. v1 reads it inline; a periodic backfill job
  would be v2 polish.
- **Status updates / Channels.** Different table semantics
  (`status@broadcast`, broadcast-only); v2.
- **End-to-end-encrypted backup decryption** for archive-only
  scenarios where the user wants to import an `.crypt15` blob from
  Android. That's a whole adapter on its own.

## 11 · Risks & mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Schema change in a WA Desktop update | medium / often | Probe + log + assert; CI doesn't depend on real WA install. |
| `@lid` JID resolution incomplete | high | Document; lean on existing `person_alias` UX; reconcile lazily. |
| User panics about "msgviz reading my WhatsApp" | low | README + GETTING_STARTED explain it's the same file Spotlight already indexes; no network traffic; opt-in subcommand. |
| WhatsApp ships a future encrypted local store | unknown | If they do, fall back to opt-in `neonize`/whatsmeow with the ban-risk warning. Bridge survives. |

## 13 · Schema drift detection

> **Design principle:** if Meta changes the WhatsApp Desktop schema,
> msgviz must (a) refuse to silently produce wrong data, (b) tell
> the user clearly what changed and where, (c) keep ingesting
> whatever rows are still safely parseable, and (d) make the
> warning unmissable across CLI, server, and UI surfaces.
>
> **Anti-goal:** a `try: …  except Exception: pass` anywhere in the
> ingestion path.

### 13.1 Three drift severities

| Severity | Trigger | Behaviour |
|---|---|---|
| **`fatal`** | Required column missing, required table missing, or required column changed type | **Abort the sync run.** Write a `drift_event` row, surface in `msgviz check`, exit non-zero. Do NOT ingest partial data — better empty than wrong. |
| **`warn`** | New column appears, optional column missing, unknown `ZMESSAGETYPE` code, unknown `ZSESSIONTYPE`, unknown `ZGROUPEVENTTYPE` | **Continue ingestion.** Skip-and-log the affected row(s) when value is unknown; otherwise just record the drift. UI shows a yellow banner; CLI prints a summary. |
| **`info`** | Known-but-rare optional column appears (e.g. a new index), expected new chat type in a pre-registered allowlist | Recorded only, no UI noise. |

### 13.2 The schema contract

Encoded as data, not code, so it's diffable and one obvious file to
update when WA changes:

```
msgviz/adapters/whatsapp_schema.py

WHATSAPP_SCHEMA_VERSION = 1

REQUIRED_TABLES = {
    "ZWAMESSAGE": {
        "required_columns": {
            "Z_PK": "INTEGER",
            "ZSTANZAID": "TEXT",
            "ZMESSAGEDATE": "REAL",
            "ZFROMJID": "TEXT",
            "ZISFROMME": "INTEGER",
            "ZCHATSESSION": "INTEGER",
            "ZMESSAGETYPE": "INTEGER",
        },
        "optional_columns": {
            "ZTEXT", "ZSENTDATE", "ZTOJID", "ZMEDIAITEM",
            "ZGROUPMEMBER", "ZPARENTMESSAGE", "ZGROUPEVENTTYPE",
            "ZPUSHNAME", "ZSTARRED",
        },
    },
    "ZWACHATSESSION": { … },
    "ZWAMEDIAITEM":   { … },
    "ZWAGROUPMEMBER": { … },
}

KNOWN_MESSAGE_TYPES = {0, 1, 2, 3, 4, 5, 7, 8, 10, 15, …}
KNOWN_SESSION_TYPES = {0, 1, 2, 3}
KNOWN_GROUP_EVENT_TYPES = {…}
```

Type comparisons are coarse (`INTEGER` / `REAL` / `TEXT` / `BLOB`)
because SQLite's storage classes are coarse; we don't try to match
SQL declared types byte-for-byte.

### 13.3 Probe → classify → record

At the start of every sync run the adapter calls
`whatsapp_schema.probe(con)` which:

1. Runs `SELECT name FROM sqlite_master WHERE type='table'`.
2. For each required table, runs `PRAGMA table_info(<table>)`.
3. Compares against the contract above.
4. Returns a `SchemaReport`:

```python
@dataclass(frozen=True)
class DriftEvent:
    severity: Literal["fatal", "warn", "info"]
    table: str | None
    column: str | None
    kind: Literal[
        "missing_table",
        "missing_required_column",
        "type_changed",
        "new_column",
        "missing_optional_column",
        "unknown_enum_value",
    ]
    detail: str          # human-readable, no PII
    observed: str | None # e.g. "TEXT" or the unknown code "42"
    expected: str | None
    seen_at: datetime

@dataclass(frozen=True)
class SchemaReport:
    schema_version: int
    events: list[DriftEvent]
    fatal_count: int
    warn_count: int

    @property
    def is_fatal(self) -> bool: return self.fatal_count > 0
```

5. The CLI flow:
   - If `is_fatal` → log every fatal event, write to `drift_event`
     table, raise `SchemaDriftError`, exit with code `3`
     (distinct from generic "error" `1`).
   - If `warn_count > 0` → continue, but ingestion code wraps each
     row in a guarded helper (§13.5) and counts skipped rows.

### 13.4 Persistence: `drift_event` table

Add an additive schema migration (msgviz already has the additive-
only invariant from `docs/SCHEMA.md`):

```sql
CREATE TABLE IF NOT EXISTS drift_event (
    id            INTEGER PRIMARY KEY,
    source        TEXT NOT NULL,        -- e.g. "whatsapp_live"
    schema_version INTEGER NOT NULL,    -- the contract version we
                                         -- were running against
    severity      TEXT NOT NULL,        -- "fatal" | "warn" | "info"
    kind          TEXT NOT NULL,        -- enum from DriftEvent.kind
    table_name    TEXT,
    column_name   TEXT,
    observed      TEXT,
    expected      TEXT,
    detail        TEXT,
    first_seen    INTEGER NOT NULL,     -- unix seconds
    last_seen     INTEGER NOT NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    acknowledged_at INTEGER             -- NULL until user clicks "ack" in UI
);

CREATE UNIQUE INDEX IF NOT EXISTS drift_event_dedup
ON drift_event(source, kind, table_name, column_name, observed);
```

The unique index means repeated runs against the same drift bump
`occurrence_count` and `last_seen` instead of spamming new rows.

### 13.5 Per-row safety net

For `warn`-level drift we still ingest, but every row goes through
a guarded helper that converts a parse exception into a `drift_event`
of severity `warn`, kind `row_parse_failed`, with the failing column
identified — **not** a silent skip:

```python
def _safe_canonicalize(row, schema_report) -> CanonicalMessage | None:
    try:
        return _canonicalize(row)
    except (KeyError, TypeError, ValueError) as exc:
        record_drift(
            kind="row_parse_failed",
            severity="warn",
            detail=f"{type(exc).__name__}: {exc}",
            table="ZWAMESSAGE",
            column=_blamed_column(exc),
        )
        return None
```

Skipped rows are counted; the CLI run summary always includes
`X messages skipped due to schema drift — run "msgviz drift" to see why.`

### 13.6 Surfacing — every channel speaks up

**CLI (`msgviz import whatsapp-live`):**

```
✗ schema drift detected (fatal)
  whatsapp_live · ZWAMESSAGE
    missing required column: ZSTANZAID

ingestion aborted. nothing was written.
run `msgviz drift --explain whatsapp_live` for details, or update msgviz.
```

**CLI (`msgviz check`):** new probe at the bottom of the feature
matrix, e.g.:

```
schema contract
  iMessage live           ✓ ok
  WhatsApp Desktop        ⚠ 2 warnings (1 unknown message type, 1 new column)
  → review:  msgviz drift
```

**CLI (`msgviz drift`)** — new subcommand:

```
msgviz drift                     # list active drift events (un-acked)
msgviz drift --all               # include acknowledged
msgviz drift --json              # machine-readable
msgviz drift --explain SOURCE    # full detail for one source
msgviz drift --ack <id>          # mark one event acknowledged
msgviz drift --ack-all           # acknowledge everything outstanding
```

`drift --ack` does NOT delete the row — it just sets
`acknowledged_at`. So the audit trail survives.

**Server / API:** new endpoint
`GET /api/drift` → `{ events: [DriftEvent, ...], pending_count: N }`.
The frontend bootstrap (`app/msgviz-base.js`) hits this on page
load; if `pending_count > 0` it renders a sticky banner above the
chat list:

> ⚠ Schema drift detected · WhatsApp Desktop  ·  2 warnings · click for details

Banner links to a small drift-events panel showing the same data
`msgviz drift` prints. Pending fatal events would have prevented
the sync, so the banner there says "ingestion paused — last
successful sync 14:05".

**Logs:** every drift event is logged at WARN/ERROR via the
existing logger. Structured fields so `grep` works.

### 13.7 Tests

Backed by synthetic fixtures, not the live DB:

- `test_schema_drift_fatal_missing_required_column.py` — fixture
  DB has `ZWAMESSAGE` without `ZSTANZAID`; assert
  `SchemaDriftError`, exit code 3, `drift_event` row written, no
  `message` rows written.
- `test_schema_drift_warn_new_column.py` — fixture adds
  `ZWAMESSAGE.ZFUTURECOLUMN_TEXT`; assert sync completes, warn
  event recorded, all rows ingested.
- `test_schema_drift_warn_unknown_message_type.py` — row with
  `ZMESSAGETYPE=42`; assert that row is skipped (drift `warn`),
  other rows ingested, count surfaced.
- `test_drift_event_dedup.py` — same drift twice → one row,
  `occurrence_count=2`, `last_seen` advanced.
- `test_drift_cli.py` — `msgviz drift`, `--json`, `--ack`,
  `--ack-all` exit-code + output shape.
- `test_api_drift.py` — `/api/drift` returns the right shape;
  `pending_count` reflects un-acked rows.

### 13.8 Cross-cutting: this isn't WhatsApp-specific

Once built, the same machinery applies to every adapter. Future
iMessage schema changes (Apple ships a Messages.app update with
new columns every 18 months or so) get the same treatment for
free. The `source` column in `drift_event` is what makes it
multi-source — no special-casing.

The schema contract for `imessage_live` and `imessage_backup`
should be added in the same PR so they get drift detection from
day one rather than retrofitting later.

### 13.9 What "loud" looks like — examples

A real drift event surfaced through all four channels:

> **CLI:**
> `⚠ whatsapp_live: 1 unknown message type (47) seen 3× — sample stanza 3EB0F0…`
>
> **`msgviz check`:**
> `schema contract: WhatsApp Desktop ⚠ 1 warning`
>
> **Server banner:**
> `⚠ Schema drift · WhatsApp Desktop · 1 warning · details ›`
>
> **Log line (one per first-occurrence):**
> `WARN msgviz.drift source=whatsapp_live kind=unknown_enum_value table=ZWAMESSAGE column=ZMESSAGETYPE observed=47 schema_version=1 first_seen=2026-06-…`

Acknowledged via `msgviz drift --ack 17` or the UI button. Stops
nagging. Audit trail kept.

### 13.10 iMessage schema contracts

Apple's `chat.db` (live) and the iOS-backup variant are vendor-
controlled SQLite that Apple revises with macOS / iOS major releases
roughly every 12-18 months. Recent observed changes:

- macOS 13 → 14: added `message.thread_originator_guid`
  (reply threading), `attachment.transcription`
- macOS 14 → 15: changed `message.payload_data` encoding for
  some link previews
- macOS 15 → 16: added unsent-message bookkeeping columns

Today msgviz handles this with `getattr`-style row access and
prays. With drift detection it gets the same first-class
treatment as WhatsApp:

```
msgviz/adapters/imessage_schema.py

IMESSAGE_SCHEMA_VERSION = 1

REQUIRED_TABLES = {
    "message": {
        "required_columns": {
            "ROWID":              "INTEGER",
            "guid":               "TEXT",
            "text":               "TEXT",
            "handle_id":          "INTEGER",
            "date":               "INTEGER",   # Apple nanos since 2001
            "is_from_me":         "INTEGER",
            "service":            "TEXT",
            "associated_message_guid": "TEXT",  # reactions/edits
        },
        "optional_columns": {
            "attributedBody", "payload_data", "subject",
            "thread_originator_guid", "thread_originator_part",
            "is_audio_message", "expressive_send_style_id",
            "balloon_bundle_id", "associated_message_type",
            "date_read", "date_delivered", "is_read",
            "share_status", "share_direction",
            …
        },
    },
    "chat": {
        "required_columns": {
            "ROWID":     "INTEGER",
            "guid":      "TEXT",
            "style":     "INTEGER",         # 43=group, 45=1:1
            "chat_identifier": "TEXT",
        },
        "optional_columns": {
            "display_name", "is_archived",
            "last_addressed_handle", "service_name",
            "last_read_message_timestamp", …
        },
    },
    "handle":            { … },
    "chat_message_join": { … },
    "attachment":        { … },
    "chat_handle_join":  { … },
    "message_attachment_join": { … },
}

KNOWN_CHAT_STYLES = {43, 45}                  # group, 1:1
KNOWN_ASSOCIATED_MESSAGE_TYPES = {
    0, 2000, 2001, 2002, 2003, 2004, 2005,    # reactions
    3000, 3001, 3002, 3003, 3004, 3005,       # un-reactions
    # edits use a separate signal; map known ones
}
KNOWN_SERVICES = {"iMessage", "SMS", "RCS"}
```

`imessage_backup` shares the same tables (Apple uses the same
schema for the backup variant) but lives under a different file
path and exposes `attachment.filename` differently. It re-uses the
same contract — just probes a different file.

### 13.11 First-run UX after the upgrade

Drift detection turning on against an existing user's already-
populated DB is itself an event. Two things to get right so it's
helpful, not scary:

**(a) Don't surface old data.** The probe only runs against the
*source* DBs (Apple's, Meta's) — never against msgviz's own DB.
Existing imported rows are not re-validated. No "you have
2,000 messages with an unknown shape" panic.

**(b) Calibrate the macOS contract conservatively.** First
release of the contract lists only columns msgviz actually reads.
Optional columns Apple has shipped through macOS 14 / 15 / 16 are
included in `optional_columns` so they don't trigger `new_column`
warnings. Result: on a typical mac, the first run produces zero
drift events. On macOS 17+ (or a beta), Apple's new columns
appear as `info`-level new-column events, visible only when the
user explicitly runs `msgviz drift`.

This matters for the trust contract: a warning banner that fires
the moment the user upgrades msgviz, against a schema that hasn't
actually drifted, would train people to ignore the banner. The
schema contract is calibrated against today's reality, not
yesterday's.

### 13.12 Adapter coverage matrix

| Adapter | Schema source | Contract file | Drift surfaces |
|---|---|---|---|
| `imessage_live` | `~/Library/Messages/chat.db` | `imessage_schema.py` | CLI / check / drift / UI banner / log |
| `imessage_backup` | iOS backup DB (same shape) | `imessage_schema.py` (shared) | same |
| `whatsapp_live` | macOS WA Desktop SQLite | `whatsapp_schema.py` | same |
| `whatsapp_export` | text format (regex parser) | `whatsapp_export_schema.py` (regex variants, locale tags) | same — but `kind=unknown_export_locale` rather than `missing_column` |

The export adapter doesn't read SQLite, so its "contract" is the
set of locale variants and date-format regexes the parser knows.
Same mechanism, slightly different `kind` taxonomy. Builds out the
abstraction so future adapters slot in without re-inventing.

---

## 14 · Decision

Proceed with implementation in the order of §9, with the drift
detection mechanism from §13 implemented **as part of phase 1**
(not bolted on later). The schema contract file
(`whatsapp_schema.py`) is the first piece of code; everything
else depends on it.

v1 ships macOS-only. Reassess Linux/Windows when there's request
volume to justify either a Windows IndexedDB parser or a guarded
`neonize` backend.
