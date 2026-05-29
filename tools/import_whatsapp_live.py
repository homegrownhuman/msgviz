#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Importer for WhatsApp DESKTOP's live ChatStorage.sqlite (macOS).

NOT the export-folder path — this reads the plaintext SQLite the
WhatsApp Desktop app keeps under
``~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/``,
the same way the iMessage adapters read Apple's chat.db.

Incremental and multi-chat: keyed on
``source_ref(source='whatsapp_live:<device>', external_id=<ZSTANZAID>)``
so re-runs only insert genuinely-new messages (mirrors the iMessage
live sync dedup). Re-importing never duplicates.

Schema-drift aware (proposal §13): the adapter's probe runs first; a
fatal drift (Meta reshaped the DB) aborts the whole import with nothing
written, and every drift event — fatal or warn — is recorded into the
``drift_event`` table so ``msgviz drift`` can surface it.

Usage (via the CLI):
    msgviz import whatsapp-live --device my_mac_wa
    msgviz import whatsapp-live --device my_mac_wa --chat "Alice"
    msgviz import whatsapp-live --device my_mac_wa --dry-run
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Optional

from msgviz.paths import project_root as _project_root, db_file as _db_file
from msgviz.core import drift
from msgviz.core.person_resolver import PersonResolver
from msgviz.adapters.whatsapp_live import WhatsAppLiveAdapter

# Resolved per-call (not at import) so MSGVIZ_HOME changes — and tests
# that point it at a temp dir — are honoured.
def _root() -> str:
    return str(_project_root())


def _db() -> str:
    return str(_db_file())


def _media_kit():
    """Lazily import the legacy media-processing module.

    msgviz.legacy.export_data runs ``CONFIG = load_config()`` at import
    time and sys.exits if config/sources.json is missing — which the
    live importer doesn't need. Importing it lazily (only when media is
    actually processed) keeps media-free / sources.json-free imports
    working. Callers that hit media must have a usable config or accept
    the legacy module's defaults.
    """
    from msgviz.legacy import export_data as ex
    ex.MEDIA_ROOT = "media"
    ex.ORIG_ROOT = "originals"
    ex.FAST = False
    ex.OUT = _root()
    return ex


def _chat_db_slug(device_slug: str, source_id: str) -> str:
    """Stable per-chat slug. Mirrors the adapter's ChatSpec.slug."""
    return f"{device_slug}/chat_{source_id}"


def import_live(
    device_slug: str,
    db_path: Optional[str] = None,
    me_name: Optional[str] = None,
    chat_filter: Optional[str] = None,
    with_media: bool = True,
    report_only: bool = False,
    reporter=None,
) -> dict:
    """Incrementally ingest WhatsApp Desktop chats into visualizer.db.

    Args:
        device_slug: the device this WhatsApp install is attached to
            (must already exist via ``msgviz device add``).
        db_path: override ChatStorage.sqlite location (default: macOS
            container path via the adapter).
        me_name: display name for is_me rows; falls back to the device
            owner / "Me".
        chat_filter: if set, only chats whose title or JID contains this
            substring (case-insensitive) are imported.
        with_media: resolve + process attachments.
        report_only: dry-run — count new messages, write nothing.
        reporter: optional progress reporter.

    Returns a stats dict.
    """
    db = _db()
    if not os.path.exists(db):
        raise SystemExit("visualizer.db not found — run `msgviz init` first.")

    if reporter is None:
        from msgviz.core.progress import make_reporter
        reporter = make_reporter("null")

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")

    # Ensure the drift table exists so on_drift has somewhere to write.
    drift.ensure_drift_event_table(con)

    # The drift sink: persist every event the adapter surfaces. Commit
    # immediately so warn events survive even if a later phase fails.
    def on_drift(event: drift.DriftEvent) -> None:
        drift.record_report(
            con, drift.SchemaReport(schema_version=0, events=(event,))
        )
        con.commit()

    dev_row = con.execute(
        "SELECT id, owner_person_id FROM device WHERE slug=?", (device_slug,)
    ).fetchone()
    if dev_row is None:
        con.close()
        raise SystemExit(f"device '{device_slug}' not found in the DB")
    device_id, owner_pid = dev_row["id"], dev_row["owner_person_id"]

    if me_name is None:
        me_name = "Me"

    resolver = PersonResolver(con)
    src_tag = f"whatsapp_live:{device_slug}"

    stats = {
        "chats": 0, "new": 0, "skipped_existing": 0,
        "media": 0, "drift_fatal": 0, "drift_warn": 0,
    }

    adapter = WhatsAppLiveAdapter(
        device_slug=device_slug,
        db_path=db_path,
        me_name=me_name,
        on_drift=on_drift,
    )

    # --- Phase 1: probe the source schema ----------------------------------
    with reporter.phase("Probe WhatsApp schema") as ph_probe:
        try:
            report = adapter.open()
        except drift.SchemaDriftError as e:
            stats["drift_fatal"] = e.report.fatal_count
            con.commit()
            con.close()
            adapter.close()
            raise SystemExit(
                f"WhatsApp schema drift (fatal) — import aborted, nothing "
                f"written. Run `msgviz drift --explain whatsapp_live`. "
                f"({e})"
            )
        stats["drift_warn"] = report.warn_count
        ph_probe.note(
            f"schema OK · {report.warn_count} warning(s)"
            if report.warn_count else "schema OK"
        )

    # --- Phase 2: discover chats -------------------------------------------
    with reporter.phase("List chats") as ph_list:
        all_chats = list(adapter.list_chats())
        if chat_filter:
            needle = chat_filter.lower()
            chats = [
                c for c in all_chats
                if needle in (c.title or "").lower()
                or needle in (c.subtitle or "").lower()
            ]
        else:
            chats = all_chats
        ph_list.note(f"{len(chats)} chat(s) to sync (of {len(all_chats)})")

    # --- Phase 3: ingest ----------------------------------------------------
    with reporter.phase("Ingest messages", total=len(chats)) as ph_ing:
        for chat in chats:
            stats["chats"] += 1
            chat_id = _upsert_chat(con, chat, device_id, report_only)

            # Known stanza ids already imported for this source.
            known: set[str] = set()
            if chat_id is not None:
                for r in con.execute(
                    """SELECT sr.external_id FROM source_ref sr
                       JOIN message m ON m.id = sr.message_id
                       WHERE m.chat_id = ? AND sr.source = ?""",
                    (chat_id, src_tag),
                ):
                    known.add(r["external_id"])

            participants = {owner_pid}
            for cm in adapter.iter_messages(chat):
                ext = cm.external_id or ""
                if ext in known:
                    stats["skipped_existing"] += 1
                    continue
                if report_only:
                    stats["new"] += 1
                    continue

                sender_pid = (
                    owner_pid if cm.is_me
                    else resolver.resolve_name(cm.sender_raw)
                )
                participants.add(sender_pid)
                attachments = cm.attachments if with_media else []
                has_media = bool(attachments)

                msg_id = con.execute(
                    """INSERT INTO message(chat_id,sender_person_id,ts,is_me,
                           text,retracted,edits,reactions,apps,media_status,
                           sync_state)
                       VALUES(?,?,?,?,?,?,?,?,?,?,'published')""",
                    (
                        chat_id, sender_pid, cm.ts, 1 if cm.is_me else 0,
                        cm.text, 1 if cm.retracted else 0, None, None,
                        json.dumps(cm.apps, ensure_ascii=False) if cm.apps else None,
                        "ready" if has_media else "none",
                    ),
                ).lastrowid

                con.execute(
                    "INSERT OR IGNORE INTO source_ref(message_id,source,external_id) "
                    "VALUES(?,?,?)",
                    (msg_id, src_tag, ext),
                )

                if has_media:
                    stats["media"] += _write_media(
                        con, adapter, chat.slug, cm, msg_id, ph_ing
                    )

                stats["new"] += 1
                if stats["new"] % 500 == 0:
                    con.commit()
                    ph_ing.note(f"{stats['new']} new")

            if not report_only and chat_id is not None:
                for pid in participants:
                    con.execute(
                        "INSERT OR IGNORE INTO chat_participant(chat_id,person_id) "
                        "VALUES(?,?)",
                        (chat_id, pid),
                    )
            ph_ing.tick()

        con.commit()

    con.close()
    adapter.close()
    return stats


def _upsert_chat(con, chat, device_id, report_only) -> Optional[int]:
    """Find or create the chat row; return its id (None in dry-run when
    the chat doesn't exist yet)."""
    row = con.execute("SELECT id FROM chat WHERE slug=?", (chat.slug,)).fetchone()
    if row:
        return row["id"]
    if report_only:
        return None
    return con.execute(
        """INSERT INTO chat(slug,device_id,title,subtitle,is_group,origin)
           VALUES(?,?,?,?,?,?)""",
        (
            chat.slug, device_id, chat.title, chat.subtitle,
            1 if chat.is_group else 0, chat.origin or "whatsapp",
        ),
    ).lastrowid


def _write_media(con, adapter, slug, cm, msg_id, ph) -> int:
    """Process + insert a message's attachments. Returns count written."""
    ex = _media_kit()
    ex.ensure_dirs(slug)
    n = 0
    for att in cm.attachments:
        src_path = adapter.resolve_attachment(att.source_ref)
        if src_path is None:
            con.execute(
                "INSERT INTO media(message_id,kind,src,cat,portrait,done,bytes) "
                "VALUES(?,?,?,?,0,0,0)",
                (msg_id, "other", None, None),
            )
            continue
        try:
            rel, typ = ex.process_asset(
                str(src_path), 0, att.mime, att.filename, att.is_sticker,
                slug, ex.new_stats(), cm.is_me,
            )
        except Exception as e:
            ph.note(f"media error {att.filename}: {e}")
            con.execute(
                "INSERT INTO media(message_id,kind,src,cat,portrait,done,bytes) "
                "VALUES(?,?,?,?,0,0,0)",
                (msg_id, "other", None, None),
            )
            continue
        portrait = 1 if (typ in ("image", "video") and ex.is_portrait(rel, typ)) else 0
        try:
            nbytes = os.path.getsize(os.path.join(_root(), rel))
        except OSError:
            nbytes = 0
        con.execute(
            "INSERT INTO media(message_id,kind,src,cat,portrait,done,bytes) "
            "VALUES(?,?,?,?,?,1,?)",
            (msg_id, typ, rel, None, portrait, nbytes),
        )
        n += 1
    return n
