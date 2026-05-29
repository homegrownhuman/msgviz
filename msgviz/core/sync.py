#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Incremental sync: reads live from Apple's chat.db and writes
new/edited messages into data/visualizer.db. NO media processing
(that's the media worker) — here we only detect whether a message has
attachments (-> media_status='pending').

Only mac_live sources are synced (iPad backups are static snapshots).

Usage:
  python3 -m core.sync            # one sync pass
  python3 -m core.sync --report   # only show what would change
"""
import os, sys, json, sqlite3

from msgviz.paths import project_root as _project_root
ROOT = str(_project_root())
os.environ.setdefault("FAST", "1")
from msgviz.legacy import export_data as ex
from msgviz.core.migrate import Migrator
from msgviz.core.person_resolver import PersonResolver

DB = os.path.join(ROOT, "data", "visualizer.db")


def msg_payload(m):
    """Build the DB fields for one Apple message row (as get_messages
    yields) — text incl. attributedBody fallback, edits, apps.
    Returns None if the row isn't a real message (tapback etc.)."""
    amt = m["associated_message_type"] or 0
    # Tapbacks are handled separately (by the caller).
    if amt in ex.TAPBACKS or 3000 <= amt <= 3005:
        return None
    text = ex.clean_text(m["text"])
    if not text and amt == 0:
        text = ex.decode_attributed_body(m["attributedBody"])
    has_att = bool(m["cache_has_attachments"])
    # App-/Balloon-Nachricht ohne Text
    apps = []
    if not text and not has_att and m["balloon_bundle_id"]:
        lbl = ex.balloon_label(m["balloon_bundle_id"])
        if lbl:
            apps.append(lbl)
    if amt != 0 and not text and not has_att and not apps:
        return None
    if not text and not has_att and not apps:
        return None
    # extract_edit_history liefert seit Schritt 4c list[Edit] (Dataclass).
    # Wir konvertieren auf Dicts, weil die message.edits-Spalte JSON-Dicts
    # erwartet (Frontend liest direkt aus dem JSON).
    edits_raw = ex.extract_edit_history(m["message_summary_info"], text)
    edits = [{"text": e.text, "ts": e.ts} for e in edits_raw] if edits_raw else None
    return {
        "text": text or None,
        "has_att": has_att,
        "apps": apps or None,
        "edits": edits,
        "retracted": bool(m["date_retracted"]),
    }


def _platform_supports_live_imessage() -> bool:
    """True if this system can in principle read Apple's chat.db."""
    return sys.platform == "darwin"


def _chatdb_path(dev) -> str:
    """Path of Apple's chat.db for a mac_live device (config override or default)."""
    return dev.get("db") or os.path.expanduser("~/Library/Messages/chat.db")


def sync(report_only=False):
    if not os.path.exists(DB):
        sys.exit("visualizer.db fehlt – erst migrate.py laufen lassen.")
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    res = PersonResolver(con)

    stats = {"new": 0, "updated": 0, "chats": 0, "skipped_devices": 0}

    if not _platform_supports_live_imessage():
        # On Linux/Windows Apple's chat.db isn't reachable. Instead of
        # crashing: silent feedback, sync is skipped. (Backup imports
        # don't go through sync() anyway, they go through dedicated
        # importer tools.)
        for dev in ex.CONFIG["devices"]:
            if dev.get("type") == "mac_live":
                stats["skipped_devices"] += 1
        if stats["skipped_devices"]:
            print(
                f"[sync] {stats['skipped_devices']} mac_live device(s) skipped — "
                f"live iMessage sync only works on macOS.",
                file=sys.stderr,
            )
        con.close()
        return stats

    for dev in ex.CONFIG["devices"]:
        if dev.get("type") != "mac_live":
            continue
        chatdb = _chatdb_path(dev)
        if not os.path.isfile(chatdb):
            # macOS ohne aktivierte iMessage-App (oder Pfad-Override falsch).
            stats["skipped_devices"] += 1
            print(
                f"[sync] device '{dev.get('slug', '?')}' skipped — "
                f"chat.db fehlt unter {chatdb}.",
                file=sys.stderr,
            )
            continue
        ex.BACKEND = ex.make_backend(dev); ex.BACKEND.open()
        owner_name = Migrator.OWNER_ALIAS.get(dev.get("me_name", "Me"), dev.get("me_name", "Me"))
        owner_pid = res.resolve_name(owner_name)
        # source value for this source instance (Mac+ChatDB).
        src_tag = f"imessage_live:{dev['slug']}"

        for c in dev["chats"]:
            # Current object format.
            cslug = c["slug"]
            source_id = c.get("source_id")
            slug = f"{dev['slug']}/{cslug}"
            if source_id is None:
                continue  # bulk import without Apple chat.db link -> skip sync
            apple_cid = int(source_id)
            row = con.execute("SELECT id FROM chat WHERE slug=?", (slug,)).fetchone()
            if not row:
                continue  # chat not migrated yet -> skip
            chat_id = row["id"]
            stats["chats"] += 1

            # Known external_ids of this chat via source_ref:
            # `source` includes the device slug so that overlapping Apple
            # ROWIDs across devices don't collide.
            known = {}
            for r in con.execute(
                """SELECT m.id, sr.external_id, m.text, m.retracted, m.edits
                   FROM message m
                   JOIN source_ref sr ON sr.message_id = m.id
                   WHERE m.chat_id = ? AND sr.source = ?""",
                (chat_id, src_tag)):
                # external_id is TEXT, sync uses int -> Apple ROWID
                known[int(r["external_id"])] = r
            # Initial fill (chat was empty)? -> inserts as 'published'
            # (no spurious "just arrived" on the first browser load).
            # Otherwise: real live arrivals -> 'new'.
            first_fill = (len(known) == 0)
            insert_state = "published" if first_fill else "new"

            rows = ex.get_messages(apple_cid)
            # Collect tapbacks (reactions) -> guid of the target message.
            reactions = {}   # target_guid -> {amt: {emoji,label,sender}}
            guid_to_rowid = {}
            for m in rows:
                if m["guid"]:
                    guid_to_rowid[m["guid"]] = m["rowid"]

            for m in rows:
                amt = m["associated_message_type"] or 0
                # Tapback added
                if amt in ex.TAPBACKS:
                    amg = m["associated_message_guid"]
                    tg = amg.split("/")[-1] if amg else None
                    emoji, label = ex.TAPBACKS[amt]
                    sender = owner_name if m["is_from_me"] else ex.person_name(m["sender_handle"])
                    rdt = ex.apple_dt(m["date"])
                    rts = int(rdt.timestamp()) if rdt else None
                    if tg:
                        reactions.setdefault(tg, {})[amt] = {"emoji": emoji, "label": label, "sender": sender, "ts": rts}
                    continue
                if 3000 <= amt <= 3005:
                    amg = m["associated_message_guid"]
                    tg = amg.split("/")[-1] if amg else None
                    if tg in reactions:
                        reactions[tg].pop(amt - 1000, None)
                    continue

                pl = msg_payload(m)
                if pl is None:
                    continue

                dt = ex.apple_dt(m["date"])
                if dt is None:
                    continue
                ts = int(dt.timestamp())
                is_me = bool(m["is_from_me"])
                sender_pid = owner_pid if is_me else res.resolve_handle(m["sender_handle"])
                rowid = m["rowid"]

                edits_json = json.dumps(pl["edits"], ensure_ascii=False) if pl["edits"] else None
                apps_json = json.dumps(pl["apps"], ensure_ascii=False) if pl["apps"] else None
                media_status = "pending" if pl["has_att"] else "none"

                if rowid in known:
                    # known -> update only if text/edit/retract changed
                    prev = known[rowid]
                    changed = (prev["text"] or None) != pl["text"] \
                        or bool(prev["retracted"]) != pl["retracted"] \
                        or (prev["edits"] or None) != edits_json
                    if changed and not report_only:
                        con.execute(
                            """UPDATE message SET text=?, retracted=?, edits=?, apps=?,
                               sync_state='new' WHERE id=?""",
                            (pl["text"], 1 if pl["retracted"] else 0, edits_json, apps_json, prev["id"]))
                    if changed:
                        stats["updated"] += 1
                else:
                    # new -> insert (media_status pending if attachments)
                    stats["new"] += 1
                    if not report_only:
                        cur = con.execute(
                            """INSERT INTO message(chat_id,sender_person_id,ts,is_me,
                                   text,retracted,edits,apps,media_status,sync_state)
                               VALUES(?,?,?,?,?,?,?,?,?,?)""",
                            (chat_id, sender_pid, ts, 1 if is_me else 0,
                             pl["text"], 1 if pl["retracted"] else 0, edits_json, apps_json,
                             media_status, insert_state))
                        # source_ref anchor for incremental dedup on the next run
                        con.execute(
                            """INSERT OR IGNORE INTO source_ref(message_id, source, external_id)
                               VALUES(?, ?, ?)""",
                            (cur.lastrowid, src_tag, str(rowid)))

            # Apply reactions to known messages (via guid->rowid->message).
            if not report_only:
                for tg, reacts in reactions.items():
                    target_rowid = guid_to_rowid.get(tg)
                    if not target_rowid:
                        continue
                    mrow = con.execute(
                        """SELECT m.id FROM message m
                           JOIN source_ref sr ON sr.message_id = m.id
                           WHERE m.chat_id = ? AND sr.source = ?
                             AND sr.external_id = ?""",
                        (chat_id, src_tag, str(target_rowid))).fetchone()
                    if not mrow or not reacts:
                        continue
                    rlist = [{"emoji": r["emoji"], "label": r["label"], "sender": r["sender"], "ts": r.get("ts")}
                             for _, r in sorted(reacts.items())]
                    con.execute("UPDATE message SET reactions=? WHERE id=?",
                                (json.dumps(rlist, ensure_ascii=False), mrow["id"]))

    if not report_only:
        con.commit()
    con.close()
    print(f"Sync: {stats['new']} new, {stats['updated']} updated, across {stats['chats']} chats"
          + (" (REPORT, nothing written)" if report_only else ""))
    return stats


if __name__ == "__main__":
    sync(report_only="--report" in sys.argv)
