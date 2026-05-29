#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Media worker.

Picks up messages with media_status='pending', processes their
attachments (HEIC->JPG, mov->mp4, audio->m4a, …) into a dedicated media
folder with stable, order-independent filenames
(att_<attachment_rowid>.*), inserts the media rows in the DB and sets
media_status='ready'.

A message only becomes 'ready' when ALL its attachments are done — only
then may the display layer render it (rule: media ready before display).

Reuses export_data.process_asset (redirected to our media folder) — no
duplication of the conversion logic.

Usage:
  python3 -m msgviz.workers.media_worker                  # process all pending
  python3 -m msgviz.workers.media_worker --limit 50       # first 50 only
  python3 -m msgviz.workers.media_worker --chat my_mac/bob
"""
import os, sqlite3

from msgviz.paths import project_root as _project_root
ROOT = str(_project_root())
from msgviz.legacy import export_data as ex

DB = os.path.join(ROOT, "data", "visualizer.db")

ex.MEDIA_ROOT = "media"
ex.ORIG_ROOT  = "originals"
ex.FAST = False   # really reprocess (don't reuse old files)

# Open the backend per device once (attachment resolution needs the active backend).
_backends = {}
def backend_for(device_slug):
    if device_slug not in _backends:
        dev = next(d for d in ex.CONFIG["devices"] if d["slug"] == device_slug)
        b = ex.make_backend(dev); b.open()
        _backends[device_slug] = (b, dev)
    return _backends[device_slug]


def process_message(con, msg_row):
    """Process every attachment of ONE message. Returns True if the
    message is 'ready' afterwards (every attachment done or nothing to do)."""
    chat_slug = msg_row["chat_slug"]
    device_slug = chat_slug.split("/")[0]
    backend, dev = backend_for(device_slug)
    ex.BACKEND = backend
    ex.ME_NAME = dev.get("me_name", "Me")

    # Apple rowid comes from source_ref (kept up to date by the sync).
    # Bulk imports without an Apple link have no anchor -> nothing to do.
    apple_rowid = msg_row["apple_rowid"]
    if apple_rowid is None:
        con.execute("UPDATE message SET media_status='ready' WHERE id=?", (msg_row["id"],))
        return True

    atts = ex.get_attachments(int(apple_rowid))
    is_me = bool(msg_row["is_me"])
    st = ex.new_stats()  # process_asset expects a stats dict
    ex.ensure_dirs(chat_slug)   # create media subfolder if missing
    all_ok = True
    missing = 0
    media_rows = []

    for att in atts:
        if ex.is_plugin_payload(att):
            continue  # app payload, not a real media item
        srcfile = ex.resolve_attachment(att["filename"])
        if not srcfile:
            # Source file missing (often a deleted macOS temp file) ->
            # record a 'media' row with src=NULL so the message doesn't
            # stay pending forever.
            media_rows.append((msg_row["id"], "other", None, None, 0, 0))
            missing += 1
            continue
        # Stable index = attachment.ROWID (unique, order-independent).
        idx = att["att_rowid"]
        try:
            rel, typ = ex.process_asset(
                srcfile, idx, att["mime_type"], att["transfer_name"],
                att["is_sticker"] == 1, chat_slug, st, is_me)
        except Exception as e:
            print(f"    error on att {idx}: {e}")
            all_ok = False
            continue
        # Determine category/portrait the same way as the export.
        cat = None
        if typ == "image":
            if (att["emoji_desc"] or "") == "Emojis": cat = "emoji"
            elif att["is_sticker"] == 1: cat = "sticker"
            else: cat = "photo"
        portrait = 0
        if (typ == "image" and cat == "photo") or typ == "video":
            if ex.is_portrait(rel, typ): portrait = 1
        # Capture file size of the finished web file (for storage stats).
        try: nbytes = os.path.getsize(os.path.join(ROOT, rel))
        except OSError: nbytes = 0
        media_rows.append((msg_row["id"], typ, rel, cat, portrait, nbytes))

    # Write media rows (delete old rows for this message first -> idempotent).
    con.execute("DELETE FROM media WHERE message_id=?", (msg_row["id"],))
    for mid, kind, rel, cat, portrait, nbytes in media_rows:
        con.execute(
            "INSERT INTO media(message_id,kind,src,cat,portrait,done,bytes) VALUES(?,?,?,?,?,1,?)",
            (mid, kind, rel, cat, portrait, nbytes))

    new_status = "ready" if all_ok else "pending"
    con.execute("UPDATE message SET media_status=? WHERE id=?", (new_status, msg_row["id"]))
    return all_ok


def run(limit=None, chat=None):
    if not os.path.exists(DB):
        sys.exit("visualizer.db missing — run migrate.py + sync.py first.")
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    # Apple rowid via source_ref. `source` includes the device slug
    # ('imessage_live:<device>') — we filter via LIKE because the worker
    # is source-agnostic and serves every Apple source. LEFT JOIN
    # because bulk imports without an anchor may also be in the list.
    sql = """SELECT m.id,
                    sr.external_id AS apple_rowid,
                    m.is_me, c.slug AS chat_slug
             FROM message m
             JOIN chat c ON c.id = m.chat_id
             LEFT JOIN source_ref sr
               ON sr.message_id = m.id
              AND sr.source LIKE 'imessage_live:%'
             WHERE m.media_status='pending'"""
    params = []
    if chat:
        sql += " AND c.slug=?"; params.append(chat)
    sql += " ORDER BY m.ts ASC"
    if limit:
        sql += f" LIMIT {int(limit)}"

    pending = con.execute(sql, params).fetchall()
    total = len(pending)
    print(f"Media worker: {total} pending messages" + (f" in {chat}" if chat else ""))
    ready = 0
    for i, row in enumerate(pending, 1):
        ok = process_message(con, row)
        if ok: ready += 1
        if i % 50 == 0 or i == total:
            con.commit()
            print(f"  {i}/{total} processed ({ready} ready)")
    con.commit()
    con.close()
    print(f"Done: {ready}/{total} messages ready.")


if __name__ == "__main__":
    args = sys.argv[1:]
    limit = None; chat = None
    if "--limit" in args: limit = int(args[args.index("--limit")+1])
    if "--chat" in args:  chat = args[args.index("--chat")+1]
    run(limit=limit, chat=chat)
