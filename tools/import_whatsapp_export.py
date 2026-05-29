#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Importer for WhatsApp chat EXPORTS (folders with `_chat.txt` + media files).

NOT the ChatStorage.sqlite path — this is the export produced by WhatsApp
itself ("Export chat -> include media"). Upside: every media file is in
the folder. Format per message:

    [DD.MM.YY, HH:MM:SS] Sender Name: text
    ...continuation lines belong to the same message...

Attachments: `‎<attached: 00000032-PHOTO-2016-03-07-09-04-15.jpg>`
Special cases: "This message was deleted." / "You deleted this message." -> retracted,
"Messages and calls are end-to-end encrypted." -> system (ignored),
"Missed voice call" etc. -> kept as app/system marker.

Writes additively into data/visualizer.db (idempotent: deletes the target
chat first). Media is processed via mediakit.process.process_asset into
media/ (opus/m4a -> m4a, photos -> JPG/PNG scaled down, video -> mp4).

Usage:
  python3 tools/import_whatsapp_export.py "<export folder>" \
      --device my_mac --chat-slug wa_bob \
      [--me "Owner"] [--limit N] [--no-media]
"""
import os, re, json, sqlite3, unicodedata, datetime, argparse

from msgviz.legacy import export_data as ex

# msgviz paths — honour MSGVIZ_HOME so the importer writes into the
# right environment (live / dev / demo).
from msgviz.paths import project_root as _project_root, db_file as _db_file
ROOT = str(_project_root())
_HOME = ROOT

# Medien-Verzeichnisse (hash-basiert seit Schritt 1)
ex.MEDIA_ROOT = "media"
ex.ORIG_ROOT  = "originals"
ex.FAST = False
ex.OUT = _HOME  # mediakit.process writes its files relative to OUT

DB = str(_db_file())

# Eine Nachrichtenzeile beginnt mit  [DD.MM.YY, HH:MM:SS] Sender: rest
# (führende LTR-/Unsichtbar-Marken werden vorher entfernt)
MSG_RE = re.compile(
    r"^\[(\d{2})\.(\d{2})\.(\d{2}),\s*(\d{2}):(\d{2}):(\d{2})\]\s([^:]+?):\s?(.*)$"
)
ATTACH_RE = re.compile(r"<attached:\s*([^>]+)>")

# Zeilen, die als System-/Statusmeldung gelten (kein echter Gesprächsinhalt)
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
# Anruf-/Status-Marker: als kurzer App-Hinweis behalten (kein Medium, kein Text)
APP_MARKERS = (
    "Missed voice call", "Missed video call",
    "Verpasster Sprachanruf", "Verpasster Videoanruf",
    "Voice call", "Video call",
)

def strip_invisible(s):
    """LTR/RTL/Bidi-Steuerzeichen entfernen, die WhatsApp einstreut (‎ etc.)."""
    return "".join(ch for ch in s if unicodedata.category(ch) != "Cf")

def parse_ts(dd, mm, yy, h, mi, s):
    year = 2000 + int(yy)
    dt = datetime.datetime(year, int(mm), int(dd), int(h), int(mi), int(s))
    return int(dt.timestamp())

def classify_attachment(fname):
    """(kind, cat) anhand der Export-Dateiendung/Typ-Markierung."""
    low = fname.lower()
    ext = os.path.splitext(low)[1]
    if "-sticker" in low or ext == ".webp":
        return "image", "sticker"
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".heic"):
        return "image", "foto"
    if ext in (".mp4", ".mov", ".m4v"):
        return "video", None
    if ext in (".opus", ".m4a", ".mp3", ".aac", ".ogg", ".wav"):
        return "audio", None
    return "other", None

MIME_BY_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".heic": "image/heic", ".webp": "image/webp",
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".m4v": "video/mp4",
    ".opus": "audio/ogg", ".m4a": "audio/mp4", ".mp3": "audio/mpeg",
    ".aac": "audio/aac", ".ogg": "audio/ogg", ".wav": "audio/wav",
    ".pdf": "application/pdf", ".vcf": "text/vcard",
}

def parse_chat(txt_path):
    """Liest _chat.txt und liefert eine Liste von Nachrichten-Dicts."""
    msgs = []
    cur = None
    with open(txt_path, encoding="utf-8") as f:
        for raw in f:
            line = strip_invisible(raw.rstrip("\n"))
            m = MSG_RE.match(line)
            if m:
                if cur is not None:
                    msgs.append(cur)
                dd, mm, yy, h, mi, s, sender, rest = m.groups()
                cur = {
                    "ts": parse_ts(dd, mm, yy, h, mi, s),
                    "sender": sender.strip(),
                    "text_lines": [rest],
                }
            else:
                # Folgezeile einer mehrzeiligen Nachricht
                if cur is not None:
                    cur["text_lines"].append(line)
    if cur is not None:
        msgs.append(cur)

    # Nachbearbeiten: Anhänge erkennen, Text säubern, Sonderfälle markieren
    out = []
    for mrec in msgs:
        full = "\n".join(mrec["text_lines"]).strip()
        rec = {"ts": mrec["ts"], "sender": mrec["sender"],
               "text": None, "retracted": False, "attachments": [], "apps": None,
               "system": False}

        if any(mark in full for mark in SYSTEM_MARKERS):
            rec["system"] = True
            out.append(rec)
            continue
        if any(full.strip() == mark or full.strip().startswith(mark) for mark in DELETED_MARKERS):
            rec["retracted"] = True
            out.append(rec)
            continue

        att = ATTACH_RE.search(full)
        if att:
            rec["attachments"].append(att.group(1).strip())
            # Begleittext (z.B. "Gestern <attached: ...>") behalten, Marker entfernen
            cleaned = ATTACH_RE.sub("", full).strip()
            rec["text"] = cleaned or None
        else:
            stripped = full.strip()
            if any(stripped == mark for mark in APP_MARKERS):
                rec["apps"] = [stripped]
            else:
                rec["text"] = full or None
        out.append(rec)
    return out


def import_export(export_dir, device_slug, chat_slug, me_name=None,
                  limit=None, with_media=True, reporter=None):
    txt = os.path.join(export_dir, "_chat.txt")
    if not os.path.isfile(txt):
        sys.exit(f"_chat.txt nicht gefunden in {export_dir}")
    if not os.path.exists(DB):
        sys.exit("visualizer.db fehlt – erst migrate.py laufen lassen.")

    if reporter is None:
        from msgviz.core.progress import make_reporter
        reporter = make_reporter("null")

    # --- Phase 1: Quelle parsen --------------------------------------------
    with reporter.phase("Quelle parsen") as ph_parse:
        ex.load_config_if_needed = None  # noop
        # sources.json is optional: if the device is registered via the
        # DB (`msgviz device add`) and not declared in sources.json,
        # we proceed with sensible defaults.
        from msgviz.core.sources import load_sources
        from msgviz.paths import config_dir as _cfg_dir
        sources_path = _cfg_dir() / "sources.json"
        dev = None
        if sources_path.is_file():
            try:
                sources = load_sources(sources_path)
                dev = next((d for d in sources.devices if d.slug == device_slug), None)
            except Exception:
                dev = None
        if me_name is None and dev is not None:
            from importlib import import_module
            mig = import_module("msgviz.core.migrate")
            me_name = mig.Migrator.OWNER_ALIAS.get(dev.me_name, dev.me_name)
        elif me_name is None:
            me_name = "Me"

        slug = f"{device_slug}/{chat_slug}"
        cmeta = None
        if dev is not None:
            cmeta = next((c for c in dev.chats if c.slug == chat_slug), None)
        title = cmeta.title if cmeta else chat_slug
        subtitle = cmeta.subtitle if cmeta else None
        origin = cmeta.origin if cmeta else "whatsapp"

        ph_parse.note(f"Slug: {slug} · me={me_name}")

        from msgviz.adapters.whatsapp_export import WhatsAppExportAdapter
        adapter = WhatsAppExportAdapter(
            export_dir=export_dir, slug=slug, title=title,
            subtitle=subtitle, is_group=False, me_name=me_name,
        )
        chat_spec = next(iter(adapter.list_chats()))
        msgs_canonical = list(adapter.iter_messages(chat_spec))
        ph_parse.note(f"{len(msgs_canonical)} echte Nachrichten")
        ph_parse.set_total(len(msgs_canonical))
        ph_parse.tick(len(msgs_canonical))

    # --- Phase 2: DB-Vorbereitung ------------------------------------------
    with reporter.phase("DB-Vorbereitung") as ph_db:
        con = sqlite3.connect(DB)
        con.row_factory = sqlite3.Row
        dev_row = con.execute("SELECT id, owner_person_id FROM device WHERE slug=?",
                              (device_slug,)).fetchone()
        if dev_row is None:
            con.close()
            sys.exit(f"Device-Zeile '{device_slug}' fehlt in der DB")
        device_id, owner_pid = dev_row["id"], dev_row["owner_person_id"]
        from msgviz.core.person_resolver import PersonResolver
        resolver = PersonResolver(con)
        ph_db.note(f"Device id={device_id}, Owner pid={owner_pid}")

        old = con.execute("SELECT id FROM chat WHERE slug=?", (slug,)).fetchone()
        if old:
            oid = old["id"]
            con.execute("DELETE FROM media WHERE message_id IN (SELECT id FROM message WHERE chat_id=?)", (oid,))
            con.execute("DELETE FROM message WHERE chat_id=?", (oid,))
            con.execute("DELETE FROM chat_participant WHERE chat_id=?", (oid,))
            con.execute("DELETE FROM chat WHERE id=?", (oid,))
            ph_db.note(f"alter Chat #{oid} entfernt (Re-Import)")

        chat_id = con.execute(
            """INSERT INTO chat(slug,device_id,title,subtitle,is_group,origin)
               VALUES(?,?,?,?,0,?)""",
            (slug, device_id, title, subtitle, origin)).lastrowid

    # --- Phase 3: Nachrichten + Medien schreiben ---------------------------
    if with_media:
        ex.ensure_dirs(slug)
    st = ex.new_stats()
    participants = {owner_pid}
    n_media = 0
    use = msgs_canonical if limit is None else msgs_canonical[:limit]

    with reporter.phase("Nachrichten + Medien schreiben", total=len(use)) as ph_write:
        for cm in use:
            sender_pid = owner_pid if cm.is_me else resolver.resolve_name(cm.sender_raw)
            participants.add(sender_pid)
            attachments = cm.attachments if with_media else []
            has_media = bool(attachments)
            media_status = "ready" if has_media else "none"

            msg_id = con.execute(
                """INSERT INTO message(chat_id,sender_person_id,ts,is_me,
                       text,retracted,edits,reactions,apps,media_status,sync_state)
                   VALUES(?,?,?,?,?,?,?,?,?,?,'published')""",
                (chat_id, sender_pid, cm.ts, 1 if cm.is_me else 0,
                 cm.text, 1 if cm.retracted else 0,
                 None, None,
                 json.dumps(cm.apps, ensure_ascii=False) if cm.apps else None,
                 media_status)).lastrowid

            for att in attachments:
                src_path = adapter.resolve_attachment(att.source_ref)
                if src_path is None:
                    con.execute("INSERT INTO media(message_id,kind,src,cat,portrait,done,bytes) VALUES(?,?,?,?,0,0,0)",
                                (msg_id, "other", None, None))
                    continue
                try:
                    rel, typ = ex.process_asset(
                        str(src_path), 0, att.mime, att.filename, att.is_sticker,
                        slug, st, cm.is_me)
                except Exception as e:
                    ph_write.note(f"Fehler bei {att.filename}: {e}")
                    con.execute("INSERT INTO media(message_id,kind,src,cat,portrait,done,bytes) VALUES(?,?,?,?,0,0,0)",
                                (msg_id, "other", None, None))
                    continue
                _, cat = classify_attachment(att.filename)
                if typ != "image":
                    cat = None
                portrait = 1 if (typ in ("image", "video") and ex.is_portrait(rel, typ)) else 0
                try: nbytes = os.path.getsize(os.path.join(ROOT, rel))
                except OSError: nbytes = 0
                con.execute(
                    "INSERT INTO media(message_id,kind,src,cat,portrait,done,bytes) VALUES(?,?,?,?,?,1,?)",
                    (msg_id, typ, rel, cat, portrait, nbytes))
                n_media += 1
                if n_media % 50 == 0:
                    ph_write.note(f"{n_media} Medien")

            ph_write.tick()
            if ph_write._state.current % 1000 == 0:
                con.commit()

        ph_write.note(f"{ph_write._state.current} Nachrichten · {n_media} Medien")

    for pid in participants:
        con.execute("INSERT OR IGNORE INTO chat_participant(chat_id,person_id) VALUES(?,?)",
                    (chat_id, pid))
    con.commit()
    con.close()
    return slug


def transcribe_chat(slug, reporter=None):
    """Ruft die inkrementelle Transkription für genau diesen Chat auf."""
    try:
        from importlib import import_module
        tr = import_module("workers.transcribe")
    except Exception as e:
        if reporter:
            with reporter.phase("Transkription"):
                reporter._stack[-1].notes.append(f"übersprungen: {e}")
        else:
            print(f"  (Transkription übersprungen: {e})")
        return
    if reporter:
        with reporter.phase("Transkription") as ph:
            ph.note(f"chat={slug}")
            try:
                tr.run(chat=slug, reporter_phase=ph)
            except TypeError:
                # älterer worker ohne reporter-arg
                tr.run(chat=slug)
            except Exception as e:
                ph.note(f"Fehler: {e}")
    else:
        print(f"\nTranskribiere Sprachnachrichten in {slug} …")
        try: tr.run(chat=slug)
        except Exception as e: print(f"  Fehler: {e}")


def ocr_chat(slug, reporter=None):
    """OCR über alle Bilder dieses Chats."""
    try:
        from importlib import import_module
        oc = import_module("workers.ocr_images")
    except Exception as e:
        if reporter:
            with reporter.phase("OCR Bilder") as ph:
                ph.note(f"übersprungen: {e}")
        return
    if reporter:
        with reporter.phase("OCR Bilder") as ph:
            ph.note(f"chat={slug}")
            try:
                oc.run(chat=slug, reporter_phase=ph)
            except TypeError:
                oc.run(chat=slug)
            except Exception as e:
                ph.note(f"Fehler: {e}")
    else:
        try: oc.run(chat=slug)
        except Exception as e: print(f"  Fehler: {e}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("export_dir")
    ap.add_argument("--device", default="mac_christian")
    ap.add_argument("--chat-slug", default="wa_angela")
    ap.add_argument("--me", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-media", action="store_true")
    ap.add_argument("--no-transcribe", action="store_true",
                    help="Transkription nach dem Import NICHT starten")
    ap.add_argument("--no-ocr", action="store_true",
                    help="OCR über Bilder NICHT starten")
    ap.add_argument("--progress", choices=("terminal","events","null"),
                    default="terminal",
                    help="Progress-Anzeige (Default: terminal mit Rich)")
    ap.add_argument("--events-path", default=None,
                    help="Pfad für JSONL-Events bei --progress=events")
    a = ap.parse_args()

    from msgviz.core.progress import make_reporter
    import datetime as _dt
    if a.progress == "events" and not a.events_path:
        a.events_path = f"data/imports/{_dt.datetime.now():%Y%m%d-%H%M%S}.jsonl"
    reporter = make_reporter(a.progress, events_path=a.events_path)
    try:
        slug = f"{a.device}/{a.chat_slug}"
        import_export(a.export_dir, a.device, a.chat_slug, me_name=a.me,
                      limit=a.limit, with_media=not a.no_media,
                      reporter=reporter)
        if not a.no_media and not a.no_transcribe:
            transcribe_chat(slug, reporter=reporter)
        if not a.no_media and not a.no_ocr:
            ocr_chat(slug, reporter=reporter)
    finally:
        reporter.close()
