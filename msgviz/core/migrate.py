#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Migration: existing JSON data (data/chats/*.json) + config/sources.json
-> the normalized SQLite DB (data/visualizer.db).

Usage:
  python3 -m core.migrate              # migrate all chats
  python3 -m core.migrate <slug>       # migrate only one chat
  python3 -m core.migrate --fresh      # wipe the DB first and rebuild
"""
import os, sys, json, sqlite3

from msgviz.paths import project_root as _project_root
ROOT = str(_project_root())
DB   = os.path.join(ROOT, "data", "visualizer.db")
SCHEMA = os.path.join(os.path.dirname(__file__), "schema.sql")
CONFIG = os.path.join(ROOT, "config", "sources.json")
CHATS_DIR = os.path.join(ROOT, "data", "chats")

sys.path.insert(0, ROOT)
from msgviz.core.person_resolver import PersonResolver, norm_handle


class Migrator:
    def __init__(self, con):
        self.con = con
        self.resolver = PersonResolver(con)
        self._chat_meta = {}          # slug -> {title,subtitle,is_group,origin} from sources.json

    # --- Persons / handles --------------------------------------------------
    # Convenience wrappers. Person creation goes exclusively through the
    # central PersonResolver, so duplicate persons across the four import
    # paths are avoided.
    def person(self, display_name, note=None):
        # `note` is not forwarded today (no caller sets it).
        return self.resolver.resolve_name(display_name)

    def add_handle(self, value, person_id):
        self.resolver.add_handle(value, person_id)

    def person_for_handle(self, value):
        return self.resolver.resolve_handle(value)

    def person_for_sender(self, sender_name):
        return self.resolver.resolve_name(sender_name) if sender_name else None

    # Owner display name (me_name from sources.json) -> canonical name.
    # Prevents the same human being created as two persons if the
    # me_name in sources.json is a short form and the people map uses
    # the longer one.
    #
    # Recommendation: put the full name into sources.json (the form the
    # DB should store). Then this map stays empty.
    #
    # Override via env (for migration scripts with legacy configs):
    #   MSGVIZ_OWNER_ALIASES="Short1:Full1,Short2:Full2"
    OWNER_ALIAS: dict = {}

    @classmethod
    def _load_owner_aliases(cls) -> dict:
        """Read owner aliases from env `MSGVIZ_OWNER_ALIASES`.

        Expects comma-separated pairs ``Short:Full`` (whitespace ignored).
        Example: ``"Alice:Alice Example, Bob: Bob Smith"``.
        """
        raw = os.environ.get("MSGVIZ_OWNER_ALIASES", "").strip()
        if not raw:
            return dict(cls.OWNER_ALIAS)
        out = dict(cls.OWNER_ALIAS)
        for pair in raw.split(","):
            if ":" not in pair:
                continue
            short, full = pair.split(":", 1)
            short, full = short.strip(), full.strip()
            if short and full:
                out[short] = full
        return out

    # --- Load configuration (devices + optional people map) ----------------
    def load_config(self):
        cfg = json.load(open(CONFIG, encoding="utf-8"))
        owner_aliases = self._load_owner_aliases()
        # 1) people-Map (optional, ab Phase 0.7 deprecated):
        #    Handle -> Personenname. Bestehende Configs nutzen das noch;
        #    neue Setups schreiben Personen direkt in die DB.
        people = cfg.get("people", {})
        for handle_val, name in people.items():
            pid = self.person(name)
            self.add_handle(handle_val, pid)
        # 2) Devices + their owner (the owner name can be resolved via
        #    OWNER_ALIAS to the full name — otherwise used as given).
        dev_ids = {}
        for d in cfg["devices"]:
            owner_name = d.get("me_name", "Ich")
            canonical = owner_aliases.get(owner_name, owner_name)
            owner_pid = self.person(canonical)
            cur = self.con.execute(
                "INSERT INTO device(slug,name,type,owner_person_id) VALUES(?,?,?,?)",
                (d["slug"], d["name"], d["type"], owner_pid))
            dev_ids[d["slug"]] = cur.lastrowid
            # Remember chat metadata from the config (for chats without JSON).
            # Current object format: chats are dicts.
            for c in d["chats"]:
                self._chat_meta[f"{d['slug']}/{c['slug']}"] = {
                    "title": c.get("title", c["slug"]),
                    "subtitle": c.get("subtitle"),
                    "is_group": bool(c.get("is_group", False)),
                    "origin": c.get("origin", "apple"),
                }
        return cfg, dev_ids

    # --- Migrate one chat --------------------------------------------------
    def migrate_chat(self, slug, device_id, apple_chat_id, import_messages=True):
        """Insert the chat row. import_messages=True: import messages
        from JSON (static iPad chats). False: only create the chat row —
        the sync fills the messages directly from chat.db (live Mac
        chats), recognized via `source_ref` (source='imessage_live').

        `apple_chat_id` (or None) points at the chat in the Apple DB;
        we record it as the chat_source anchor so the sync recognizes
        the chat."""
        path = os.path.join(CHATS_DIR, slug + ".json")
        chat = json.load(open(path, encoding="utf-8")) if os.path.isfile(path) else {}
        # Chat metadata: from JSON; otherwise from sources.json (via the caller).
        cur = self.con.execute(
            """INSERT INTO chat(slug,device_id,title,subtitle,is_group,origin)
               VALUES(?,?,?,?,?,?)""",
            (slug, device_id,
             chat.get("title", self._chat_meta.get(slug, {}).get("title", slug)),
             chat.get("subtitle", self._chat_meta.get(slug, {}).get("subtitle")),
             1 if (chat.get("is_group") or self._chat_meta.get(slug, {}).get("is_group")) else 0,
             chat.get("origin", self._chat_meta.get(slug, {}).get("origin", "apple"))))
        chat_id = cur.lastrowid
        if apple_chat_id is not None:
            # device-slug aus DB ableiten -> source-Wert pro Quell-Instanz
            dslug = self.con.execute(
                "SELECT slug FROM device WHERE id=?", (device_id,)
            ).fetchone()[0]
            self.con.execute(
                """INSERT OR IGNORE INTO chat_source(chat_id, source, external_id)
                   VALUES(?, ?, ?)""",
                (chat_id, f"imessage_live:{dslug}", str(apple_chat_id)),
            )
        owner_pid = self.con.execute(
            "SELECT owner_person_id FROM device WHERE id=?", (device_id,)).fetchone()[0]

        if not import_messages:
            # Owner als Teilnehmer eintragen; Rest macht der Sync
            self.con.execute(
                "INSERT OR IGNORE INTO chat_participant(chat_id,person_id) VALUES(?,?)",
                (chat_id, owner_pid))
            print(f"  {slug}: chat angelegt (Nachrichten via Sync)")
            return 0

        participants = set()
        n = 0
        for m in chat.get("messages", []):
            is_me = bool(m.get("me"))
            if is_me:
                sender_pid = owner_pid
            else:
                sender_pid = self.person_for_sender(m.get("sender"))
            if sender_pid:
                participants.add(sender_pid)

            media = m.get("media") or []
            media_status = "ready" if media else "none"

            cur = self.con.execute(
                """INSERT INTO message(chat_id,sender_person_id,ts,is_me,
                       text,retracted,edits,reactions,apps,media_status,sync_state)
                   VALUES(?,?,?,?,?,?,?,?,?,?,'published')""",
                (chat_id, sender_pid, m.get("ts"), 1 if is_me else 0,
                 m.get("text"), 1 if m.get("retracted") else 0,
                 json.dumps(m["edits"], ensure_ascii=False) if m.get("edits") else None,
                 json.dumps(m["reactions"], ensure_ascii=False) if m.get("reactions") else None,
                 json.dumps(m["apps"], ensure_ascii=False) if m.get("apps") else None,
                 media_status))
            msg_id = cur.lastrowid
            for it in media:
                self.con.execute(
                    """INSERT INTO media(message_id,kind,src,cat,portrait,done)
                       VALUES(?,?,?,?,?,1)""",
                    (msg_id, it.get("kind"), it.get("src"), it.get("cat"),
                     1 if it.get("portrait") else 0))
            n += 1

        # Teilnehmer eintragen (Owner immer dabei)
        participants.add(owner_pid)
        for pid in participants:
            self.con.execute(
                "INSERT OR IGNORE INTO chat_participant(chat_id,person_id) VALUES(?,?)",
                (chat_id, pid))
        print(f"  {slug}: {n} Nachrichten")
        return n


def build_db(only_slug=None, fresh=False, backup=True):
    """Migrate sources.json + data/chats/*.json into visualizer.db.

    `backup=True` (default): writes a backup to data/db-backups/pre-migrate-…
    before every run on an existing DB with content. With fresh=True
    backup is especially important — the old DB would otherwise be lost.
    """
    if backup:
        from msgviz.core.backup import backup_db as _backup_db
        backup_path = _backup_db("migrate")
        if backup_path is not None:
            print(f"[migrate] DB backup -> {backup_path}")
    if fresh and os.path.exists(DB):
        for ext in ("", "-wal", "-shm"):
            try: os.remove(DB + ext)
            except OSError: pass
    con = sqlite3.connect(DB)
    con.executescript(open(SCHEMA, encoding="utf-8").read())
    mig = Migrator(con)
    cfg, dev_ids = mig.load_config()

    total = 0
    for d in cfg["devices"]:
        # mac_live = live chats: only create the chat row; the sync fills
        # the messages (so they get source_ref anchors). iPad backups:
        # import from JSON.
        import_msgs = d.get("type") != "mac_live"
        for c in d["chats"]:
            cslug = c["slug"]
            source_id = c.get("source_id")
            apple_cid = int(source_id) if source_id is not None else None
            slug = f"{d['slug']}/{cslug}"
            if only_slug and slug != only_slug:
                continue
            total += mig.migrate_chat(slug, dev_ids[d["slug"]], apple_cid,
                                      import_messages=import_msgs)
    con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version','1')")
    con.commit()
    con.close()
    print(f"\nFertig: {total} Nachrichten -> {DB}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    fresh = "--fresh" in sys.argv
    build_db(only_slug=args[0] if args else None, fresh=fresh)
