# -*- coding: utf-8 -*-
"""
msgviz.server.factory — create_app(config) for an embeddable FastAPI app.

server/app.py used to be a singleton with module globals (DB path, config
paths, watcher state). That blocked embedding (own server process, own
domain, own auth) and made tests fragile.

This factory:
  * accepts an MVConfig,
  * builds a FastAPI app with every route + static mount,
  * encapsulates state (DB path, watcher, hub) in a ServerState closure,
  * can be called as often as needed with different configs
    (e.g. multi-tenant in one process).

Standalone calls still go through `msgviz.server.app:app`, which wraps
this factory with `default_config()`.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from msgviz.config import MVConfig
from msgviz.core.sources import load_sources


# ---------------------------------------------------------------------------
#  Internal state — everything the routes need.
# ---------------------------------------------------------------------------
@dataclass
class ServerState:
    """Encapsulates every piece of mutable server state instead of module globals."""

    config: MVConfig
    db_online: bool = True
    hub: "Hub" = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.hub is None:
            self.hub = Hub()

    def db(self) -> sqlite3.Connection:
        """Read-only DB connection."""
        con = sqlite3.connect(f"file:{self.config.db_file}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con

    def db_write(self) -> sqlite3.Connection:
        """Writable DB connection (for /seen and similar endpoints)."""
        con = sqlite3.connect(str(self.config.db_file))
        con.row_factory = sqlite3.Row
        return con

    def configured_device_order(self) -> list[str]:
        try:
            src = load_sources(self.config.sources_json)
            return [d.slug for d in src.devices]
        except Exception:
            return []

    def configured_chat_order(self) -> list[str]:
        try:
            src = load_sources(self.config.sources_json)
            return [f"{d.slug}/{c.slug}" for d in src.devices for c in d.chats]
        except Exception:
            return []

    def device_types(self) -> dict[str, str]:
        try:
            src = load_sources(self.config.sources_json)
            return {d.slug: d.type for d in src.devices}
        except Exception:
            return {}


class Hub:
    """WebSocket-Broadcast-Hub."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def join(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def leave(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def broadcast(self, msg: dict) -> None:
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.leave(ws)


# ---------------------------------------------------------------------------
#  Message rendering helpers — pure functions, kein State.
# ---------------------------------------------------------------------------
_BASE_SELECT = """SELECT m.id, m.ts, m.is_me, m.text, m.edits, m.reactions, m.apps, m.retracted,
                         COALESCE(p.display_name, '?') AS sender_name,
                         p.avatar_src AS sender_avatar
                  FROM message m
                  JOIN chat c ON c.id = m.chat_id
                  LEFT JOIN person p ON p.id = m.sender_person_id
                  WHERE c.slug = ? AND m.media_status IN ('ready','none')"""


def _chat_id_for(con: sqlite3.Connection, slug: str) -> int | None:
    r = con.execute("SELECT id FROM chat WHERE slug=?", (slug,)).fetchone()
    return r["id"] if r else None


def _msg_to_json(con: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    m: dict[str, Any] = {
        "t": "msg",
        "ts": row["ts"],
        "me": bool(row["is_me"]),
        "sender": row["sender_name"],
        "text": row["text"],
    }
    # sender_avatar is included only when present; absent → frontend renders initials.
    try:
        if row["sender_avatar"]:
            m["sender_avatar"] = row["sender_avatar"]
    except (IndexError, KeyError):
        pass
    if row["edits"]:
        m["edits"] = json.loads(row["edits"])
    if row["reactions"]:
        m["reactions"] = json.loads(row["reactions"])
    if row["apps"]:
        m["apps"] = json.loads(row["apps"])
    if row["retracted"]:
        m["retracted"] = True
    media = []
    for md in con.execute(
        "SELECT kind,src,cat,portrait FROM media WHERE message_id=? ORDER BY id",
        (row["id"],),
    ):
        if md["src"] is None:
            m.setdefault("apps", []).append("📎 Anhang fehlt")
            continue
        it: dict[str, Any] = {"kind": md["kind"], "src": md["src"]}
        if md["cat"]:
            it["cat"] = md["cat"]
        if md["portrait"]:
            it["portrait"] = True
        media.append(it)
    if media:
        m["media"] = media
    return m


def _media_stats(
    con: sqlite3.Connection, chat_id: int
) -> tuple[dict, dict, int]:
    media = {t: {"me": 0, "them": 0} for t in ("image", "video", "audio", "other")}
    byts = {t: 0 for t in ("image", "video", "audio", "other")}
    for r in con.execute(
        """SELECT md.kind, m.is_me, COUNT(*) n, COALESCE(SUM(md.bytes),0) b FROM media md
           JOIN message m ON m.id=md.message_id
           WHERE m.chat_id=? AND md.src IS NOT NULL GROUP BY md.kind, m.is_me""",
        (chat_id,),
    ):
        k = r["kind"] if r["kind"] in media else "other"
        media[k]["me" if r["is_me"] else "them"] += r["n"]
        byts[k] += r["b"] or 0
    return media, byts, sum(byts.values())


# ---------------------------------------------------------------------------
#  Factory.
# ---------------------------------------------------------------------------
def create_app(config: MVConfig | None = None) -> FastAPI:
    """Baut eine FastAPI-Instanz auf Basis der gegebenen Config.

    Wenn config=None: Default aus msgviz.paths (Repo-Layout / MSGVIZ_HOME).
    """
    if config is None:
        from msgviz.config import default_config

        config = default_config()

    state = ServerState(config=config)
    app = FastAPI(
        title=config.title,
        description=config.description,
    )

    _register_middleware(app, state)
    _register_api_routes(app, state)
    _register_websocket(app, state)
    _register_html_routes(app, state)
    _register_static_mounts(app, state)
    _register_startup(app, state)

    # Expose state to tests & embedding.
    app.state.mv_state = state
    app.state.mv_config = config

    return app


# ---------------------------------------------------------------------------
#  Registration — one function per area. Keeps create_app() readable.
# ---------------------------------------------------------------------------
def _register_middleware(app: FastAPI, state: ServerState) -> None:
    if not state.config.nocache_app_files:
        return

    @app.middleware("http")
    async def no_cache_app_files(request: Request, call_next):
        resp = await call_next(request)
        p = request.url.path
        if p.startswith(f"{state.config.mount_app}/") or p.endswith(".html") or p == "/":
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp


def _register_api_routes(app: FastAPI, state: ServerState) -> None:
    cfg = state.config

    def _clamp(limit: int) -> int:
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = cfg.default_page_limit
        return max(1, min(limit, cfg.max_page_limit))

    @app.get("/api/index")
    def api_index():
        con = state.db()
        devices = []
        for d in con.execute("SELECT id,slug,name FROM device"):
            owner = con.execute(
                "SELECT p.display_name, p.avatar_src "
                "FROM device dv JOIN person p ON p.id=dv.owner_person_id "
                "WHERE dv.id=?",
                (d["id"],),
            ).fetchone()
            dev_entry = {
                "id": d["id"],
                "slug": d["slug"],
                "name": d["name"],
                "me_name": owner["display_name"] if owner else "Me",
            }
            if owner and owner["avatar_src"]:
                dev_entry["owner_avatar"] = owner["avatar_src"]
            devices.append(dev_entry)
        dorder = state.configured_device_order()
        devices.sort(
            key=lambda dv: (
                dorder.index(dv["slug"]) if dv["slug"] in dorder else len(dorder)
            )
        )

        by_slug = {}
        dtypes = state.device_types()
        has_imessage_source = {
            r[0]
            for r in con.execute(
                "SELECT chat_id FROM chat_source WHERE source LIKE 'imessage_live:%'"
            )
        }
        for c in con.execute(
            "SELECT id,slug,title,subtitle,is_group,origin,device_id FROM chat"
        ):
            agg = con.execute(
                """SELECT COUNT(*) total, SUM(CASE WHEN is_me=1 THEN 1 ELSE 0 END) me,
                          MIN(ts) first, MAX(ts) last,
                          SUM(CASE WHEN sync_state='new' THEN 1 ELSE 0 END) new_count
                   FROM message m WHERE m.chat_id=? AND m.media_status IN ('ready','none')""",
                (c["id"],),
            ).fetchone()
            dev = con.execute(
                """SELECT dv.name, dv.slug, p.display_name AS me_name
                   FROM device dv LEFT JOIN person p ON p.id=dv.owner_person_id
                   WHERE dv.id=?""",
                (c["device_id"],),
            ).fetchone()
            total = agg["total"] or 0
            me = agg["me"] or 0
            media, byts, btotal = _media_stats(con, c["id"])
            dslug = dev["slug"] if dev else None

            # Counterpart avatar: for 1:1 chats use the avatar of the
            # most-frequent non-me sender (typically the only other person).
            chat_avatar = None
            if not c["is_group"]:
                top = con.execute(
                    """SELECT p.avatar_src
                       FROM message m
                       JOIN person p ON p.id = m.sender_person_id
                       WHERE m.chat_id = ? AND m.is_me = 0
                         AND p.avatar_src IS NOT NULL
                       GROUP BY p.id
                       ORDER BY COUNT(*) DESC
                       LIMIT 1""",
                    (c["id"],),
                ).fetchone()
                if top:
                    chat_avatar = top["avatar_src"]

            entry = {
                "slug": c["slug"],
                "title": c["title"],
                "subtitle": c["subtitle"],
                "is_group": bool(c["is_group"]),
                "origin": c["origin"],
                "me_name": (dev["me_name"] if dev and dev["me_name"] else "Me"),
                "device": dev["name"] if dev else None,
                "device_slug": dslug,
                "live": dtypes.get(dslug) == "mac_live"
                and c["id"] in has_imessage_source,
                "total": total,
                "me": me,
                "them": total - me,
                "new_count": agg["new_count"] or 0,
                "media": media,
                "first": agg["first"],
                "last": agg["last"],
                "bytes": byts,
                "bytes_total": btotal,
                "bytes_orig": 0,
            }
            if chat_avatar:
                entry["chat_avatar"] = chat_avatar
            by_slug[c["slug"]] = entry
        order = state.configured_chat_order()
        chats = [by_slug[s] for s in order if s in by_slug]
        for s in by_slug:
            if s not in order:
                chats.append(by_slug[s])
        con.close()
        return JSONResponse({"devices": devices, "chats": chats})

    @app.get("/api/chat/{slug:path}/meta")
    def api_chat_meta(slug: str):
        con = state.db()
        c = con.execute(
            """SELECT c.slug,c.title,c.subtitle,c.is_group,c.origin,d.name device,
                      p.display_name me_name, p.avatar_src owner_avatar
               FROM chat c JOIN device d ON d.id=c.device_id
               JOIN person p ON p.id=d.owner_person_id WHERE c.slug=?""",
            (slug,),
        ).fetchone()
        if not c:
            con.close()
            return JSONResponse({"error": "unknown chat"}, status_code=404)
        cid = _chat_id_for(con, slug)
        agg = con.execute(
            """SELECT COUNT(*) total, SUM(CASE WHEN is_me=1 THEN 1 ELSE 0 END) me,
                      MIN(ts) first, MAX(ts) last
               FROM message WHERE chat_id=? AND media_status IN ('ready','none')""",
            (cid,),
        ).fetchone()
        total = agg["total"] or 0
        me = agg["me"] or 0
        media, byts, btotal = _media_stats(con, cid)

        # Counterpart avatar for 1:1 chats.
        chat_avatar = None
        if not c["is_group"]:
            top = con.execute(
                """SELECT p.avatar_src
                   FROM message m
                   JOIN person p ON p.id = m.sender_person_id
                   WHERE m.chat_id = ? AND m.is_me = 0
                     AND p.avatar_src IS NOT NULL
                   GROUP BY p.id
                   ORDER BY COUNT(*) DESC
                   LIMIT 1""",
                (cid,),
            ).fetchone()
            if top:
                chat_avatar = top["avatar_src"]

        out = {
            "slug": c["slug"],
            "title": c["title"],
            "subtitle": c["subtitle"],
            "is_group": bool(c["is_group"]),
            "origin": c["origin"],
            "device": c["device"],
            "me_name": c["me_name"],
            "stats": {
                "total": total,
                "me": me,
                "them": total - me,
                "first": agg["first"],
                "last": agg["last"],
                "media": media,
                "bytes": byts,
                "bytes_total": btotal,
                "bytes_orig": 0,
            },
        }
        if c["owner_avatar"]:
            out["owner_avatar"] = c["owner_avatar"]
        if chat_avatar:
            out["chat_avatar"] = chat_avatar
        con.close()
        return JSONResponse(out)

    @app.get("/api/chat/{slug:path}/latest")
    def api_latest(slug: str, limit: int = cfg.default_page_limit):
        limit = _clamp(limit)
        con = state.db()
        rows = con.execute(
            _BASE_SELECT + " ORDER BY m.ts DESC, m.id DESC LIMIT ?", (slug, limit)
        ).fetchall()
        msgs = [_msg_to_json(con, r) for r in reversed(rows)]
        con.close()
        return JSONResponse({"messages": msgs, "has_more": len(rows) == limit})

    @app.get("/api/chat/{slug:path}/before/{ts:int}")
    def api_before(slug: str, ts: int, limit: int = cfg.default_page_limit):
        limit = _clamp(limit)
        con = state.db()
        rows = con.execute(
            _BASE_SELECT + " AND m.ts < ? ORDER BY m.ts DESC, m.id DESC LIMIT ?",
            (slug, ts, limit),
        ).fetchall()
        msgs = [_msg_to_json(con, r) for r in reversed(rows)]
        con.close()
        return JSONResponse({"messages": msgs, "has_more": len(rows) == limit})

    @app.get("/api/chat/{slug:path}/since/{ts:int}")
    def api_since(slug: str, ts: int, limit: int = cfg.max_page_limit):
        limit = _clamp(limit)
        con = state.db()
        rows = con.execute(
            _BASE_SELECT + " AND m.ts > ? ORDER BY m.ts ASC, m.id ASC LIMIT ?",
            (slug, ts, limit),
        ).fetchall()
        msgs = [_msg_to_json(con, r) for r in rows]
        con.close()
        return JSONResponse({"messages": msgs})

    @app.get("/api/chat/{slug:path}/edited")
    def api_edited(slug: str):
        con = state.db()
        rows = con.execute(
            _BASE_SELECT + " AND m.edits IS NOT NULL ORDER BY m.ts ASC, m.id ASC",
            (slug,),
        ).fetchall()
        msgs = [_msg_to_json(con, r) for r in rows]
        total = con.execute(
            """SELECT COUNT(*) FROM message m JOIN chat c ON c.id=m.chat_id
               WHERE c.slug=? AND m.edits IS NOT NULL
                 AND m.media_status IN ('ready','none')""",
            (slug,),
        ).fetchone()[0]
        con.close()
        return JSONResponse({"messages": msgs, "total": total})

    @app.post("/api/chat/{slug:path}/seen")
    def api_chat_seen(slug: str):
        con_w = state.db_write()
        cid = _chat_id_for(con_w, slug)
        if cid is None:
            con_w.close()
            return JSONResponse({"error": "unknown chat"}, status_code=404)
        n = con_w.execute(
            "UPDATE message SET sync_state='published' "
            "WHERE chat_id=? AND sync_state='new'",
            (cid,),
        ).rowcount
        con_w.commit()
        con_w.close()
        return JSONResponse({"ok": True, "marked_seen": n})

    @app.get("/api/chat/{slug:path}/media")
    def api_media(slug: str):
        con = state.db()
        cid = _chat_id_for(con, slug)
        if cid is None:
            con.close()
            return JSONResponse({"error": "unknown chat"}, status_code=404)
        rows = con.execute(
            """SELECT md.kind, md.src, md.cat, md.portrait,
                      m.is_me, m.ts, m.text,
                      COALESCE(p.display_name,'?') AS sender_name
               FROM media md
               JOIN message m ON m.id = md.message_id
               LEFT JOIN person p ON p.id = m.sender_person_id
               WHERE m.chat_id = ? AND md.src IS NOT NULL
                 AND m.media_status IN ('ready','none')
               ORDER BY m.ts ASC, m.id ASC, md.id ASC""",
            (cid,),
        ).fetchall()
        items = []
        for r in rows:
            it: dict[str, Any] = {
                "kind": r["kind"],
                "src": r["src"],
                "me": bool(r["is_me"]),
                "sender": r["sender_name"],
                "ts": r["ts"],
                "cap": r["text"],
            }
            if r["cat"]:
                it["cat"] = r["cat"]
            if r["portrait"]:
                it["portrait"] = True
            items.append(it)
        con.close()
        return JSONResponse({"media": items})

    @app.get("/api/chat/{slug:path}/days")
    def api_days(slug: str):
        con = state.db()
        cid = _chat_id_for(con, slug)
        if cid is None:
            con.close()
            return JSONResponse({"error": "unknown chat"}, status_code=404)
        rows = con.execute(
            """SELECT strftime('%Y-%m-%d', m.ts, 'unixepoch', 'localtime') AS day,
                      COUNT(*) AS n
               FROM message m
               WHERE m.chat_id=? AND m.media_status IN ('ready','none')
               GROUP BY day ORDER BY day""",
            (cid,),
        ).fetchall()
        days = {r["day"]: r["n"] for r in rows}
        con.close()
        return JSONResponse({"days": days})

    @app.get("/api/chat/{slug:path}/around/{ts:int}")
    def api_around(slug: str, ts: int, before: int = 40, after: int = 80):
        before = max(0, min(int(before), cfg.max_page_limit))
        after = max(1, min(int(after), cfg.max_page_limit))
        con = state.db()
        older = con.execute(
            _BASE_SELECT + " AND m.ts < ? ORDER BY m.ts DESC, m.id DESC LIMIT ?",
            (slug, ts, before),
        ).fetchall()
        newer = con.execute(
            _BASE_SELECT + " AND m.ts >= ? ORDER BY m.ts ASC, m.id ASC LIMIT ?",
            (slug, ts, after),
        ).fetchall()
        msgs = [_msg_to_json(con, r) for r in reversed(older)] + [
            _msg_to_json(con, r) for r in newer
        ]
        con.close()
        return JSONResponse({"messages": msgs})


# ---------------------------------------------------------------------------
#  WebSocket + Watcher (live iMessage)
# ---------------------------------------------------------------------------
def _register_websocket(app: FastAPI, state: ServerState) -> None:
    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await state.hub.join(ws)
        try:
            await ws.send_json({"type": "dbstatus", "online": state.db_online})
        except Exception:
            pass
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            state.hub.leave(ws)
        except Exception:
            state.hub.leave(ws)


def _register_startup(app: FastAPI, state: ServerState) -> None:
    if not state.config.enable_watcher:
        return

    @app.on_event("startup")
    async def _startup():
        asyncio.create_task(_watcher_loop(state))


def _chatdb_path() -> Path:
    """Apple's live chat.db. Only exists on macOS."""
    return Path(os.path.expanduser("~/Library/Messages/chat.db"))


def _chatdb_fingerprint() -> str:
    cdb = _chatdb_path()
    parts = []
    for p in (cdb, Path(str(cdb) + "-wal")):
        try:
            st = p.stat()
            parts.append(f"{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            parts.append("-")
    return hashlib.sha1("|".join(parts).encode()).hexdigest()


def _chatdb_online() -> bool:
    try:
        c = sqlite3.connect(f"file:{_chatdb_path()}?mode=ro", uri=True)
        c.execute("SELECT 1 FROM message LIMIT 1").fetchone()
        c.close()
        return True
    except Exception:
        return False


def _run_sync_and_media(state: ServerState) -> list[dict]:
    """Sync (chat.db -> visualizer.db) + Medien-Worker."""
    import importlib

    sync_mod = importlib.import_module("msgviz.core.sync")
    media_mod = importlib.import_module("msgviz.workers.media_worker")
    stats = sync_mod.sync(report_only=False)
    if stats.get("new", 0) or stats.get("updated", 0):
        media_mod.run()
    con = sqlite3.connect(str(state.config.db_file))
    con.row_factory = sqlite3.Row
    changed = []
    for r in con.execute(
        """SELECT c.slug, COUNT(*) n, MAX(m.ts) last FROM message m
           JOIN chat c ON c.id=m.chat_id WHERE m.sync_state='new' GROUP BY c.slug"""
    ):
        changed.append({"slug": r["slug"], "new": r["n"], "last": r["last"]})
    con.execute("UPDATE message SET sync_state='published' WHERE sync_state='new'")
    con.commit()
    con.close()
    return changed


async def _watcher_loop(state: ServerState) -> None:
    """Live-Watcher: pollt chat.db-Fingerprint, sync't bei Änderung, broadcastet."""
    import sys

    if sys.platform != "darwin":
        return
    if not _chatdb_path().is_file():
        return

    last_fp = _chatdb_fingerprint()
    while True:
        await asyncio.sleep(state.config.watcher_poll_seconds)
        online = await asyncio.to_thread(_chatdb_online)
        if online != state.db_online:
            state.db_online = online
            await state.hub.broadcast({"type": "dbstatus", "online": online})
        if not online:
            continue
        fp = _chatdb_fingerprint()
        if fp == last_fp:
            continue
        last_fp = fp
        try:
            changed = await asyncio.to_thread(_run_sync_and_media, state)
        except Exception as e:
            print("watcher-Fehler:", e)
            continue
        if changed:
            await state.hub.broadcast({"type": "update", "chats": changed})


# ---------------------------------------------------------------------------
#  HTML + static mounts
# ---------------------------------------------------------------------------
def _render_html(template_path: Path, base: str) -> str:
    """Load an HTML template and replace the {{base}} placeholder.

    `base` is the app's mount prefix without a trailing slash, e.g.
    "/messages" or "" (standalone). We do no real templating engine —
    only a single placeholder is replaced; keeps the dep list small.
    """
    txt = template_path.read_text(encoding="utf-8")
    return txt.replace("{{base}}", base)


def _detect_mount_base(request: Request) -> str:
    """Return the mount prefix the sub-app is hanging under.

    If `host.mount('/messages', mv)` was used, Starlette sets
    `request.scope['root_path']` to '/messages'. Standalone: '' (empty).
    A trailing slash is stripped.
    """
    base = (request.scope.get("root_path") or "").rstrip("/")
    return base


def _register_html_routes(app: FastAPI, state: ServerState) -> None:
    cfg = state.config

    @app.get("/", response_class=HTMLResponse)
    def root_index(request: Request):
        return HTMLResponse(_render_html(cfg.index_html, _detect_mount_base(request)))

    @app.get("/chat/{slug:path}", response_class=HTMLResponse)
    def chat_page(slug: str, request: Request):
        return HTMLResponse(
            _render_html(cfg.chat_template_html, _detect_mount_base(request))
        )

    @app.get("/favicon.ico")
    def favicon():
        return FileResponse(str(cfg.favicon_path))


def _register_static_mounts(app: FastAPI, state: ServerState) -> None:
    cfg = state.config

    if cfg.app_dir.is_dir():
        app.mount(cfg.mount_app, StaticFiles(directory=str(cfg.app_dir)), name="app")
    if cfg.data_dir.is_dir():
        app.mount(cfg.mount_data, StaticFiles(directory=str(cfg.data_dir)), name="data")
    if cfg.media_root.is_dir():
        app.mount(
            cfg.mount_media,
            StaticFiles(directory=str(cfg.media_root)),
            name="media",
        )
    if cfg.originals_root.is_dir():
        app.mount(
            cfg.mount_originals,
            StaticFiles(directory=str(cfg.originals_root)),
            name="originals",
        )
