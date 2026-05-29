-- ===========================================================================
-- Message Visualizer v2 — normalisiertes Schema (SQLite)
--
-- Hierarchie:  person ─< handle
--                 │ (owner)            │ (sender)
--              device ─< chat ─< message ─< media
--                            └─ chat_participant >─ person
--
-- Designnotizen:
--  * Eine PERSON kann viele HANDLES haben (Tel + mehrere Mails) -> n:1.
--  * DEVICE.owner_person_id = die "me"-Perspektive dieses Geräts.
--  * is_me bleibt denormalisiert an message (spart Joins, = Apple is_from_me).
--  * edits/reactions/apps als JSON-Spalten (werden nur mit der Msg gelesen).
--  * source_ref: Brücke zu externen Quellen (z.B. imessage_live) für
--    inkrementellen Sync. Quellen-Spezifika bleiben aus dem Kernschema raus.
-- ===========================================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- --- Menschen --------------------------------------------------------------
CREATE TABLE IF NOT EXISTS person (
    id            INTEGER PRIMARY KEY,
    display_name  TEXT NOT NULL,
    note          TEXT,
    avatar_src    TEXT                       -- web-relative path or NULL (NULL -> frontend renders initials)
);

-- --- Identifikatoren (Telefon / E-Mail) -> Person ---------------------------
CREATE TABLE IF NOT EXISTS handle (
    id         INTEGER PRIMARY KEY,
    value      TEXT NOT NULL UNIQUE,
    person_id  INTEGER NOT NULL REFERENCES person(id)
);
CREATE INDEX IF NOT EXISTS idx_handle_person ON handle(person_id);

-- --- Alias-Namen -> Person --------------------------------------------------
-- Ermöglicht, dass eine Person unter verschiedenen Schreibweisen erkannt
-- wird (z.B. "Alice K. Example" aus WhatsApp-Export -> "Alice").
-- Aliase werden case-insensitiv gematched (siehe PersonResolver).
CREATE TABLE IF NOT EXISTS person_alias (
    id         INTEGER PRIMARY KEY,
    value      TEXT NOT NULL UNIQUE,
    person_id  INTEGER NOT NULL REFERENCES person(id)
);
CREATE INDEX IF NOT EXISTS idx_alias_person ON person_alias(person_id);

-- --- Geräte (jede Quelle aus sources.json) ---------------------------------
CREATE TABLE IF NOT EXISTS device (
    id               INTEGER PRIMARY KEY,
    slug             TEXT NOT NULL UNIQUE,
    name             TEXT NOT NULL,
    type             TEXT NOT NULL,          -- ios_backup | mac_live | ...
    owner_person_id  INTEGER NOT NULL REFERENCES person(id)
);

-- --- Chats (Gesprächsfäden auf einem Gerät) --------------------------------
-- Quellenagnostisch: weder Apple noch sonst ein Adapter hat hier eine Spalte.
-- Adapter-spezifische IDs wandern in `chat_source` (analog source_ref für
-- Nachrichten), falls ein Adapter sie für die Sync-Round-Trip-Identität
-- benötigt.
CREATE TABLE IF NOT EXISTS chat (
    id             INTEGER PRIMARY KEY,
    slug           TEXT NOT NULL UNIQUE,     -- "<device>/<chat>" kombiniert
    device_id      INTEGER NOT NULL REFERENCES device(id),
    title          TEXT NOT NULL,
    subtitle       TEXT,
    is_group       INTEGER NOT NULL DEFAULT 0,
    origin         TEXT NOT NULL DEFAULT 'apple'
);
CREATE INDEX IF NOT EXISTS idx_chat_device ON chat(device_id);

-- --- Quell-Brücke pro Chat (für inkrementellen Sync) -----------------------
-- `source` identifiziert eine konkrete Quell-Instanz (nicht nur einen Typ),
-- damit gleiche external_ids in verschiedenen Welten (z.B. chat.ROWID=2 auf
-- Mac vs iPad) sich nicht in die Quere kommen.
-- Beispiele:
--   source='imessage_live:mac_christian' external_id='<chat.ROWID in Apple-DB>'
--   source='imessage_live:ipad2'         external_id='<chat.ROWID>'
--   source='whatsapp_export'             external_id='<Pfad zum Export-Ordner>'
-- Adapter ohne Sync-Bedarf (Bulk-Imports) tragen hier nichts ein.
CREATE TABLE IF NOT EXISTS chat_source (
    chat_id      INTEGER NOT NULL REFERENCES chat(id) ON DELETE CASCADE,
    source       TEXT NOT NULL,
    external_id  TEXT NOT NULL,
    PRIMARY KEY (source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_chat_source_chat ON chat_source(chat_id);

-- --- Teilnehmer eines Chats (v.a. für Gruppen) -----------------------------
CREATE TABLE IF NOT EXISTS chat_participant (
    chat_id    INTEGER NOT NULL REFERENCES chat(id),
    person_id  INTEGER NOT NULL REFERENCES person(id),
    PRIMARY KEY (chat_id, person_id)
);

-- --- Nachrichten -----------------------------------------------------------
-- Quellenagnostisch: keine Apple-/WhatsApp-/...-Spalte. Wenn eine Quelle
-- inkrementellen Sync braucht (Dedup gegen Re-Lesen), trägt sie ihren
-- Anker in `source_ref` (siehe unten).
CREATE TABLE IF NOT EXISTS message (
    id                INTEGER PRIMARY KEY,
    chat_id           INTEGER NOT NULL REFERENCES chat(id),
    sender_person_id  INTEGER REFERENCES person(id),
    ts                INTEGER NOT NULL,         -- Unix-Sekunden
    is_me             INTEGER NOT NULL DEFAULT 0,
    text              TEXT,
    retracted         INTEGER NOT NULL DEFAULT 0,
    edits             TEXT,                     -- JSON: [{text,ts}, ...] oder NULL
    reactions         TEXT,                     -- JSON: [{emoji,label,sender}, ...]
    apps              TEXT,                     -- JSON: ["🔗 Geteilter Link", ...]
    media_status      TEXT NOT NULL DEFAULT 'none',   -- none | pending | ready
    sync_state        TEXT NOT NULL DEFAULT 'published' -- new | published
);
CREATE INDEX IF NOT EXISTS idx_msg_chat_ts ON message(chat_id, ts);
CREATE INDEX IF NOT EXISTS idx_msg_sync    ON message(sync_state);
CREATE INDEX IF NOT EXISTS idx_msg_media   ON message(media_status);
CREATE INDEX IF NOT EXISTS idx_msg_sender  ON message(sender_person_id);

-- --- Quell-Anker pro Nachricht (für inkrementellen Sync-Dedup) -------------
-- `source` ist quell-instanz-spezifisch (siehe chat_source).
-- Beispiele:
--   source='imessage_live:mac_christian'  external_id='<message.ROWID>'
--   source='imessage_live:ipad2'          external_id='<message.ROWID>'
-- Bulk-Importer (WhatsApp-Export, iMessage-Backup) schreiben hier nichts.
CREATE TABLE IF NOT EXISTS source_ref (
    message_id   INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    source       TEXT NOT NULL,
    external_id  TEXT NOT NULL,
    PRIMARY KEY (source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_source_ref_msg ON source_ref(message_id);

-- --- Medien-Anhänge --------------------------------------------------------
CREATE TABLE IF NOT EXISTS media (
    id            INTEGER PRIMARY KEY,
    message_id    INTEGER NOT NULL REFERENCES message(id),
    kind          TEXT NOT NULL,               -- image | video | audio | other
    src           TEXT,                        -- Web-Pfad, seit Schritt 1
                                               -- hash-basiert: media_v2/by-hash/<kind>/<prefix>/<hash>.<ext>
    cat           TEXT,                        -- foto | sticker | emoji (nur image)
    portrait      INTEGER NOT NULL DEFAULT 0,
    done          INTEGER NOT NULL DEFAULT 0,  -- Medien fertig aufbereitet?
    bytes         INTEGER NOT NULL DEFAULT 0,  -- Größe der Web-Datei (für Stats)
    content_hash  TEXT                         -- SHA-256-Prefix (16 hex) der Original-Quelle
);
CREATE INDEX IF NOT EXISTS idx_media_msg  ON media(message_id);
CREATE INDEX IF NOT EXISTS idx_media_hash ON media(content_hash);

-- --- Metadaten zur DB selbst (Schema-Version etc.) -------------------------
CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT
);
