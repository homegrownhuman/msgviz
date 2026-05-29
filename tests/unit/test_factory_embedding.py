# -*- coding: utf-8 -*-
"""
Regression guard for msgviz.server.factory.create_app().

Pins the two core contracts:

1. create_app(config) builds an **isolated** app from the provided
   MVConfig, without reading or setting module-level globals.
2. The app is **embeddable**: you can mount it as a sub-app inside a
   foreign FastAPI server, and the /api/... routes work under a prefix.

We use the conftest fixture `visualizer_db_path` (fresh tmp DB) and the
schema from msgviz/core/schema.sql. The watcher is always off.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from msgviz.config import MVConfig
from msgviz.server.factory import create_app


def _seed_minimal(con: sqlite3.Connection) -> None:
    pid = con.execute("INSERT INTO person(display_name) VALUES(?)", ("Alice",)).lastrowid
    did = con.execute(
        "INSERT INTO device(slug,name,type,owner_person_id) VALUES(?,?,?,?)",
        ("dev1", "Test-Device", "static", pid),
    ).lastrowid
    cid = con.execute(
        "INSERT INTO chat(slug,device_id,title,subtitle,is_group,origin) "
        "VALUES(?,?,?,?,0,'apple')",
        ("dev1/x", did, "X-Chat", ""),
    ).lastrowid
    con.execute(
        "INSERT INTO message(chat_id,sender_person_id,ts,is_me,text,media_status,sync_state) "
        "VALUES(?,?,?,0,'hi','none','published')",
        (cid, pid, 1700000000),
    )
    con.commit()


@pytest.fixture
def app_with_seeded_db(tmp_visualizer_db, visualizer_db_path):
    """Fresh DB + small content + create_app() pointing at exactly this DB."""
    _seed_minimal(tmp_visualizer_db)
    tmp_visualizer_db.close()
    cfg = MVConfig(db_file=visualizer_db_path, enable_watcher=False)
    return create_app(cfg)


def test_create_app_uses_custom_db(app_with_seeded_db):
    """create_app(config) reads from config.db_file, not from a default."""
    client = TestClient(app_with_seeded_db)
    r = client.get("/api/index")
    assert r.status_code == 200
    data = r.json()
    chats = data["chats"]
    assert len(chats) == 1
    assert chats[0]["slug"] == "dev1/x"
    assert chats[0]["total"] == 1


def test_app_state_exposes_config_and_state(app_with_seeded_db):
    """For embedding users: app.state.mv_config / mv_state are reachable."""
    assert app_with_seeded_db.state.mv_config is not None
    assert app_with_seeded_db.state.mv_state is not None
    assert app_with_seeded_db.state.mv_config.enable_watcher is False


def test_embedding_under_subprefix(app_with_seeded_db):
    """msgviz mounts cleanly as a sub-app in a foreign FastAPI."""
    host = FastAPI()

    @host.get("/")
    def host_root():
        return {"host": True}

    host.mount("/msgviz", app_with_seeded_db)

    client = TestClient(host)

    # Host route remains reachable.
    r = client.get("/")
    assert r.status_code == 200 and r.json() == {"host": True}

    # Sub-app routes work under the prefix.
    r = client.get("/msgviz/api/index")
    assert r.status_code == 200
    assert len(r.json()["chats"]) == 1


def test_custom_mount_paths():
    """Mount paths in MVConfig propagate to the app."""
    cfg = MVConfig(
        mount_app="/static/app",
        mount_media="/static/media",
        enable_watcher=False,
    )
    app = create_app(cfg)
    paths = [getattr(r, "path", "") for r in app.routes]
    # At least the static-asset mount appears under the custom prefix.
    assert any(p == "/static/app" for p in paths)
