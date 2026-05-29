#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Data export from the iOS backup -> JSON + media.
Produces NO HTML/CSS/JS. The central app (app/chat.css, app/chat.js,
app/index.js + chat.html / index.html) handles presentation.

Output:
  data/index.json            overview of every chat
  data/<slug>.json           one chat (messages + media paths + stats)
  media/<slug>/{images,videos,voice_notes,other,text}/
  originals/<slug>/

Fast mode: FAST=1  -> reuse existing media files.
"""
import os, sqlite3, shutil, datetime, subprocess, re, json, plistlib, tempfile

# Media path / conversion lives in msgviz.mediakit.process. We re-export
# the symbols here so existing callers (sync, media_worker, WhatsApp
# import) keep working without changes.
import msgviz.mediakit.process as _mp
from msgviz.mediakit.process import (
    # Constants
    MEDIA_ROOT, ORIG_ROOT, MAX_DIM, HASH_LEN, FFMPEG, FFPROBE, FAST,
    SUB_IMG, SUB_VID, SUB_AUD, SUB_OTH, SUB_TXT,
    MIME_EXT, HEIC_MIMES, IMAGE_EXTS, VIDEO_EXTS, AUDIO_EXTS,
    # Functions
    classify, has_alpha, is_portrait, content_hash,
    ensure_dirs, process_asset, new_stats,
)

# Apple DB reading + tapback mapping moves into adapters/imessage_db.py.
# We re-export the helpers so core/sync.py etc. keep working without
# changes. The table-reading functions (get_messages / get_attachments)
# stay bound to the legacy global `sms` connection during the
# transition — they will be cleaned up in a later step.
from msgviz.adapters.imessage_db import (
    TAPBACKS, apple_dt, clean_text, decode_attributed_body,
    extract_edit_history, balloon_label, is_plugin_payload,
)

HOME = os.path.expanduser("~")
# Default OUT to MSGVIZ_HOME (or the live data dir). Callers that need
# a per-run override still do `ex.OUT = some_path` before invoking us
# (e.g. tools/import_whatsapp_export.py).
# Historical note: this used to resolve to the directory containing
# export_data.py, which only worked because the file lived at the
# project root. After moving it under msgviz/legacy/ we resolve via
# msgviz.paths.project_root() instead.
from msgviz.paths import project_root as _project_root
OUT = str(_project_root())
CONFIG_FILE = os.path.join(OUT, "config", "sources.json")
DATA_ROOT = "data"
CHATS_ROOT = "data/chats"          # data/chats/<device>/<chat>.json
APPLE_EPOCH = 978307200
MOBILESYNC = os.path.join(HOME, "Library/Application Support/MobileSync/Backup")
# MEDIA_ROOT/ORIG_ROOT/FFMPEG/FAST/MAX_DIM/SUB_* sind aus mediakit.process
# re-exportiert (siehe Import-Block oben). Mediakit nutzt OUT als
# Projekt-Wurzel – wir spiegeln das Modul-Attribut, damit beide
# Module konsistent zeigen.
_mp.OUT = OUT

# Wird je Quelle gesetzt (global, da von vielen Funktionen genutzt)
ME_NAME = "Owner"    # Besitzer der aktuellen Quelle (is_from_me); pro Lauf überschrieben
sms = None           # Nachrichten-DB der aktuellen Quelle (sms.db / chat.db)
BACKEND = None       # aktuelles Backend (für Anhang-Auflösung)

# Konfiguration laden (Geräte/Quellen + Personen-Map)
def load_config():
    if not os.path.isfile(CONFIG_FILE):
        raise SystemExit(f"Konfiguration fehlt: {CONFIG_FILE}")
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)
CONFIG = load_config()
PERSON_BY_HANDLE = CONFIG.get("people", {})
def person_name(h): return PERSON_BY_HANDLE.get(h, h) if h else "Unbekannt"

# ============================================================================
# BACKENDS: kapseln NUR die quellenspezifischen Teile (DB öffnen + Anhang
# auflösen). Die gesamte Verarbeitung darunter ist quellen-unabhängig.
# ============================================================================
class Backend:
    """Basis-Schnittstelle für eine Nachrichtenquelle."""
    def open(self):
        """Öffnet die Nachrichten-DB und setzt die globale 'sms'-Verbindung."""
        raise NotImplementedError
    def resolve(self, filename):
        """Anhang-'filename' (aus attachment.filename) -> echter Dateipfad oder None."""
        raise NotImplementedError

class IOSBackupBackend(Backend):
    """iOS/iPadOS-Gerätebackup (MobileSync). Anhänge via Manifest.db-Hash-Mapping.
       Auch für iPhone-Backups – nur anderer Ordner."""
    def __init__(self, backup_folder):
        self.folder = backup_folder if os.path.isabs(backup_folder) else os.path.join(MOBILESYNC, backup_folder)
        self.man = None
    def _path(self, fid): return os.path.join(self.folder, fid[:2], fid)
    def _fileid(self, rel, domain=None):
        cur = self.man.cursor()
        if domain: cur.execute("SELECT fileID FROM Files WHERE domain=? AND relativePath=?", (domain, rel))
        else: cur.execute("SELECT fileID FROM Files WHERE relativePath=?", (rel,))
        r = cur.fetchone(); return r["fileID"] if r else None
    def open(self):
        global sms
        if not os.path.isdir(self.folder):
            raise SystemExit(f"Backup-Ordner nicht gefunden: {self.folder}")
        self.man = sqlite3.connect(os.path.join(self.folder, "Manifest.db")); self.man.row_factory = sqlite3.Row
        sms_db = self._path(self._fileid("Library/SMS/sms.db","HomeDomain"))
        sms = sqlite3.connect(sms_db); sms.row_factory = sqlite3.Row
    def resolve(self, fn):
        if not fn: return None
        rel = fn[2:] if fn.startswith("~/") else fn
        fid = self._fileid(rel,"MediaDomain") or self._fileid(rel)
        if not fid: return None
        p = self._path(fid); return p if os.path.exists(p) else None

class MacLiveBackend(Backend):
    """Laufende macOS-Messages-App. DB: ~/Library/Messages/chat.db.
       attachment.filename ist bereits der echte Pfad (nur '~' expandieren)."""
    def __init__(self, db_path=None):
        self.db_path = db_path or os.path.join(HOME, "Library/Messages/chat.db")
    def open(self):
        global sms
        if not os.path.isfile(self.db_path):
            raise SystemExit(f"Mac-Messages-DB nicht gefunden: {self.db_path}")
        sms = sqlite3.connect(self.db_path); sms.row_factory = sqlite3.Row
    def resolve(self, fn):
        if not fn: return None
        p = os.path.expanduser(fn) if fn.startswith("~") else fn
        return p if os.path.exists(p) else None

def make_backend(spec):
    """Erzeugt das passende Backend aus der Quellen-Konfig ('type')."""
    t = spec.get("type","ios_backup")
    if t == "ios_backup":  return IOSBackupBackend(spec["backup"])
    if t == "iphone_backup": return IOSBackupBackend(spec["backup"])   # gleiche Mechanik
    if t == "mac_live":    return MacLiveBackend(spec.get("db"))
    raise SystemExit(f"Unbekannter Quellentyp: {t}")

def resolve_attachment(fn):
    """Quellen-unabhängige Anhang-Auflösung über das aktive Backend."""
    return BACKEND.resolve(fn) if BACKEND else None

def get_messages(cid):
    cur = sms.cursor()
    cur.execute("""SELECT m.ROWID AS rowid,m.guid,m.text,m.attributedBody,m.is_from_me,m.date,
        m.date_edited,m.date_retracted,m.message_summary_info,
        m.cache_has_attachments,m.associated_message_type,m.associated_message_guid,
        m.balloon_bundle_id, h.id AS sender_handle
        FROM message m JOIN chat_message_join cmj ON cmj.message_id=m.ROWID
        LEFT JOIN handle h ON h.ROWID=m.handle_id
        WHERE cmj.chat_id=? ORDER BY m.date ASC,m.ROWID ASC""",(cid,))
    return cur.fetchall()
def get_attachments(rowid):
    cur = sms.cursor()
    cur.execute("""SELECT a.ROWID AS att_rowid,a.filename,a.mime_type,a.transfer_name,a.is_sticker,a.uti,
        a.emoji_image_short_description AS emoji_desc
        FROM attachment a JOIN message_attachment_join maj ON maj.attachment_id=a.ROWID
        WHERE maj.message_id=?""",(rowid,))
    return cur.fetchall()

def tapback_label(t): return " ".join(TAPBACKS[t]) if t in TAPBACKS else ""

# TAPBACKS, clean_text, decode_attributed_body, extract_edit_history,
# balloon_label, is_plugin_payload sind als Re-Export aus
# adapters/imessage_db.py oben verfügbar.
# MIME_EXT, HEIC_MIMES, IMAGE_EXTS, VIDEO_EXTS, AUDIO_EXTS, classify
# sind aus mediakit/process.py (siehe Header).

# Medien-Verarbeitung (has_alpha, is_portrait, content_hash, _hash_web_rel,
#  _hash_orig_rel, _abspath, _ensure_parent, ensure_dirs, web_rel,
#  process_asset) ist nach mediakit/process.py umgezogen (Schritt 4a).
#  Die Symbole sind via Re-Export im Header noch unter export_data.<name>
#  erreichbar.

# --- Klartext-Helfer ----------------------------------------------------------
WEEKDAYS=["Montag","Dienstag","Mittwoch","Donnerstag","Freitag","Samstag","Sonntag"]
def fmt_full(d): return f"{WEEKDAYS[d.weekday()]}, {d.day:02d}.{d.month:02d}.{d.year} {d.strftime('%H:%M:%S')}"

def new_stats():
    return {"msgs_total":0,"msgs_me":0,"msgs_them":0,
            "media":{t:{"me":0,"them":0} for t in ("image","video","audio","other")},
            "bytes":{t:0 for t in ("image","video","audio","other")},   # Speicher je Typ (Web-Dateien)
            "bytes_orig":0,                                              # Originalbilder (media_orig)
            "first":None,"last":None}

def export_chat(cid, title, subtitle, slug, asset_counter, is_group=False, device=None, origin="apple"):
    ensure_dirs(slug)
    msgs = get_messages(cid)
    st = new_stats()
    out_msgs = []           # JSON-Nachrichten
    dialog_lines = []
    by_guid = {}            # guid -> out_msg (für Tapback-Zuordnung)
    reactions = {}          # ziel-guid -> { typ: {emoji,label,sender,ts} }  (nur aktive)

    def target_guid(amg):
        # associated_message_guid hat Format 'p:0/<GUID>' oder 'bp:<GUID>'
        if not amg: return None
        return amg.split('/')[-1]

    for m in msgs:
        dt = apple_dt(m["date"])
        if dt is None: continue
        amt = m["associated_message_type"] or 0
        text = clean_text(m["text"])
        if not text and amt == 0:        # neuere macOS legen den Text nur in attributedBody ab
            text = decode_attributed_body(m["attributedBody"])
        is_me = bool(m["is_from_me"])
        sender = ME_NAME if is_me else person_name(m["sender_handle"])
        ts = int(dt.timestamp())

        # Tapback HINZUGEFÜGT (2000-2005) -> der Ziel-Nachricht zuordnen
        if amt in TAPBACKS:
            tg = target_guid(m["associated_message_guid"])
            emoji,label = TAPBACKS[amt]
            if tg:
                reactions.setdefault(tg, {})[amt] = {
                    "emoji":emoji,"label":label,"sender":sender,"ts":ts}
            dialog_lines.append(f"[{fmt_full(dt)}] {sender}: (Reaktion {emoji} {label})")
            continue
        # Tapback ENTFERNT (3000-3005) -> Reaktion wieder löschen
        if 3000 <= amt <= 3005:
            tg = target_guid(m["associated_message_guid"])
            if tg and tg in reactions:
                reactions[tg].pop(amt-1000, None)
            continue
        if amt!=0 and not text and not m["cache_has_attachments"]:
            continue

        atts = get_attachments(m["rowid"]) if m["cache_has_attachments"] else []
        media = []          # [{kind:image/video/audio/other, src, sticker?}]
        appchips = []
        dialog_tags = []
        for att in atts:
            if is_plugin_payload(att):
                if not text:
                    lbl = balloon_label(m["balloon_bundle_id"]) or "🔗 Geteilter Inhalt"
                    appchips.append(lbl); dialog_tags.append(f"[{lbl}]")
                continue
            src = resolve_attachment(att["filename"])
            if not src:
                appchips.append("📎 Anhang fehlt"); dialog_tags.append("[Anhang fehlt]"); continue
            asset_counter[0]+=1
            rel,typ = process_asset(src,asset_counter[0],att["mime_type"],
                                    att["transfer_name"],att["is_sticker"]==1,slug,st,is_me)
            # Speicherverbrauch der finalen Web-Datei zentral erfassen
            try:
                st["bytes"][typ] += os.path.getsize(os.path.join(OUT, rel))
            except OSError:
                pass
            item={"kind":typ,"src":rel}
            if typ=="image":
                # Unterscheidung: emoji (große Apple-Emojis) vs. sticker vs. foto
                if (att["emoji_desc"] or "") == "Emojis":
                    item["cat"]="emoji"
                elif att["is_sticker"]==1:
                    item["cat"]="sticker"
                else:
                    item["cat"]="foto"
            # Hochformat-Flag (nicht für Sticker/Emoji – die sind klein genug)
            if (typ=="image" and item.get("cat")=="foto") or typ=="video":
                if is_portrait(rel, typ):
                    item["portrait"]=True
            media.append(item)
            dtag={"image":"[Bild]","video":"[Video]","audio":"[Sprachnachricht]"}.get(typ,"[Datei]")
            if typ=="image" and item.get("cat")=="emoji": dtag="[Emoji]"
            elif typ=="image" and item.get("cat")=="sticker": dtag="[Sticker]"
            dialog_tags.append(dtag)

        if not text:
            for c in appchips: pass  # appchips already collected
        # App-Nachricht ohne alles -> Label aus balloon
        if not media and not appchips and not text and m["balloon_bundle_id"]:
            lbl=balloon_label(m["balloon_bundle_id"])
            if lbl: appchips.append(lbl); dialog_tags.append(f"[{lbl}]")

        if not media and not appchips and not text:
            continue

        if st["first"] is None: st["first"]=dt
        st["last"]=dt
        st["msgs_total"]+=1
        st["msgs_me" if is_me else "msgs_them"]+=1

        msg_obj={"t":"msg","ts":ts,"me":is_me,"sender":sender,
                 "text":text or None,"media":media or None,"apps":appchips or None}
        # Bearbeitungs-History (iMessage „Bearbeitet")
        edits = extract_edit_history(m["message_summary_info"], text)
        if edits:
            msg_obj["edits"] = edits
        # Zurückgezogene Nachricht („Senden rückgängig")
        if m["date_retracted"]:
            msg_obj["retracted"] = True
        out_msgs.append(msg_obj)
        if m["guid"]: by_guid[m["guid"]] = msg_obj

        parts=[]
        if dialog_tags: parts.append(" ".join(dialog_tags))
        if text: parts.append(text)
        dialog_lines.append(f"[{fmt_full(dt)}] {sender}: " + " ".join(parts).strip())

    # Reaktionen (Tapbacks) den Ziel-Nachrichten anhängen
    for tg, reacts in reactions.items():
        tgt = by_guid.get(tg)
        if tgt and reacts:
            # nach Zeit sortiert als Liste {emoji,label,sender}
            tgt["reactions"] = [
                {"emoji":r["emoji"],"label":r["label"],"sender":r["sender"]}
                for _,r in sorted(reacts.items())
            ]

    # dialog.txt
    txt_path=os.path.join(OUT,MEDIA_ROOT,slug,SUB_TXT,"dialog.txt")
    hdr=(f"Chat: {title}\n{subtitle}\n"
         f"Zeitraum: {fmt_full(st['first']) if st['first'] else '-'} bis {fmt_full(st['last']) if st['last'] else '-'}\n"
         f"Nachrichten: {st['msgs_total']} ({ME_NAME}: {st['msgs_me']}, andere: {st['msgs_them']})\n"+"="*70+"\n\n")
    with open(txt_path,"w",encoding="utf-8") as f:
        f.write(hdr+"\n".join(dialog_lines)+"\n")

    chat_obj = {
        "slug":slug,"title":title,"subtitle":subtitle,"is_group":is_group,
        "me_name":ME_NAME,"origin":origin,
        "device":(device or {}).get("name"),
        "device_id":(device or {}).get("id"),
        "stats":{
            "total":st["msgs_total"],"me":st["msgs_me"],"them":st["msgs_them"],
            "media":st["media"],
            "bytes":st["bytes"],
            "bytes_total":sum(st["bytes"].values()),
            "bytes_orig":st["bytes_orig"],
            "first":int(st["first"].timestamp()) if st["first"] else None,
            "last":int(st["last"].timestamp()) if st["last"] else None,
        },
        "messages":out_msgs,
    }
    # JSON unter data/chats/<device>/<chat>.json (slug = "<device>/<chat>")
    json_path=os.path.join(OUT,CHATS_ROOT,f"{slug}.json")
    os.makedirs(os.path.dirname(json_path),exist_ok=True)
    with open(json_path,"w",encoding="utf-8") as f:
        json.dump(chat_obj,f,ensure_ascii=False,separators=(",",":"))
    return chat_obj

# --- Run ----------------------------------------------------------------------
# Quellen kommen aus config/sources.json (CONFIG["devices"]).
SOURCES = CONFIG["devices"]

def run_full_export():
    """Vollständiger Export aller Quellen -> JSON + Medien + index.json.
    Gekapselt, damit der Live-Server export_chat()/make_backend() importieren
    kann, ohne den kompletten Export beim Import auszulösen."""
    global ME_NAME, BACKEND
    os.makedirs(OUT,exist_ok=True)
    os.makedirs(os.path.join(OUT,CHATS_ROOT),exist_ok=True)
    if not FFMPEG: print("HINWEIS: ffmpeg nicht gefunden – Audio-Fallback über afconvert.")
    counter=[0]
    index=[]

    for dev in SOURCES:
        dslug = dev["slug"]
        print(f"\n=== Quelle: {dev['name']} [{dev['type']}] ===")
        ME_NAME = dev.get("me_name","Ich")
        BACKEND = make_backend(dev); BACKEND.open()
        device_info = {"id":dev["id"],"name":dev["name"],"slug":dslug}
        for chat in dev["chats"]:
            cid,title,sub,cslug,grp = chat[:5]
            origin = chat[5] if len(chat) > 5 else "apple"   # Messaging-Dienst (Logo)
            key = f"{dslug}/{cslug}"                       # kombinierter Verzeichnis-/JSON-Key
            obj=export_chat(cid,title,sub,key,counter,is_group=grp,device=device_info,origin=origin)
            s=obj["stats"]
            index.append({"slug":key,"title":title,"subtitle":sub,"is_group":grp,"origin":origin,
                          "device":dev["name"],"device_id":dev["id"],"device_slug":dslug,"me_name":ME_NAME,
                          "total":s["total"],"me":s["me"],"them":s["them"],
                          "media":s["media"],"bytes":s["bytes"],"bytes_total":s["bytes_total"],
                          "bytes_orig":s["bytes_orig"],
                          "first":s["first"],"last":s["last"]})
            print(f"  {title}: {s['total']} Nachr. ({ME_NAME} {s['me']}/{s['them']} andere), "
                  f"Bilder {s['media']['image']['me']}↑/{s['media']['image']['them']}↓, "
                  f"Videos {s['media']['video']['me']}↑/{s['media']['video']['them']}↓, "
                  f"Audio {s['media']['audio']['me']}↑/{s['media']['audio']['them']}↓")

    # Quellen-/Geräteliste für den Index (für Gruppierung in der Übersicht)
    devices_meta=[{"id":d["id"],"name":d["name"],"slug":d["slug"],"me_name":d.get("me_name","Ich")} for d in SOURCES]
    with open(os.path.join(OUT,DATA_ROOT,"index.json"),"w",encoding="utf-8") as f:
        json.dump({"devices":devices_meta,"chats":index},f,ensure_ascii=False,indent=0)

    print(f"\nAnhänge verarbeitet: {counter[0]}")
    print(f"JSON + Medien -> {OUT}/{DATA_ROOT}")

if __name__ == "__main__":
    run_full_export()
