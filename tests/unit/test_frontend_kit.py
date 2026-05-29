# -*- coding: utf-8 -*-
"""
Regression guard for the frontend kit decoupling.

Verifies:
1. Standalone: HTML contains absolute /app/ paths (no sub-mount prefix).
2. Sub-mount: HTML contains prefixed paths (/messages/app/...).
3. The `{{base}}` placeholder never appears in rendered HTML.
4. window.MSGVIZ.base is set correctly — empty in standalone,
   "/messages" under sub-mount.
5. msgviz-base.js is referenced in the HTML (before index.js / chat.js).
6. Static assets are reachable under the sub-mount
   (/messages/app/msgviz-base.js, /messages/app/chat.css).
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from msgviz.config import MVConfig
from msgviz.server.factory import create_app


@pytest.fixture
def standalone_client(tmp_visualizer_db, visualizer_db_path):
    """Standalone msgviz pointing at an isolated tmp DB (see submount_client)."""
    tmp_visualizer_db.close()
    app = create_app(MVConfig(enable_watcher=False, db_file=visualizer_db_path))
    return TestClient(app)


@pytest.fixture
def submount_client(tmp_visualizer_db, visualizer_db_path):
    """Build a sub-mounted msgviz against an isolated tmp DB.

    Without this isolation the test would hit the live data/visualizer.db
    (whose schema may differ from the code's current expectations).
    """
    tmp_visualizer_db.close()
    host = FastAPI()
    mv = create_app(MVConfig(enable_watcher=False, db_file=visualizer_db_path))
    host.mount("/messages", mv)
    return TestClient(host)


# ---------------------------------------------------------------------------
#  Standalone
# ---------------------------------------------------------------------------
def test_standalone_html_uses_root_paths(standalone_client):
    r = standalone_client.get("/")
    assert r.status_code == 200
    body = r.text
    assert '/app/index.js' in body
    assert '/app/msgviz-base.js' in body
    assert '{{base}}' not in body


def test_standalone_msgviz_base_is_empty(standalone_client):
    r = standalone_client.get("/")
    assert 'window.MSGVIZ = { base: "" }' in r.text


def test_standalone_chat_template_renders(standalone_client):
    r = standalone_client.get("/chat/my_mac/bob")
    assert r.status_code == 200
    body = r.text
    assert '<base href="/">' in body
    assert '/app/chat.js' in body
    assert '{{base}}' not in body


# ---------------------------------------------------------------------------
#  Sub-mount
# ---------------------------------------------------------------------------
def test_submount_html_uses_prefixed_paths(submount_client):
    r = submount_client.get("/messages/")
    assert r.status_code == 200
    body = r.text
    assert '/messages/app/index.js' in body
    assert '/messages/app/msgviz-base.js' in body
    # No unprefixed asset path in the body.
    assert '"/app/index.js"' not in body
    assert '{{base}}' not in body


def test_submount_msgviz_base_is_prefixed(submount_client):
    r = submount_client.get("/messages/")
    assert 'window.MSGVIZ = { base: "/messages" }' in r.text


def test_submount_chat_template_base_href_is_prefixed(submount_client):
    r = submount_client.get("/messages/chat/my_mac/bob")
    assert r.status_code == 200
    assert '<base href="/messages/">' in r.text


# ---------------------------------------------------------------------------
#  Static assets under sub-mount
# ---------------------------------------------------------------------------
def test_submount_static_assets_reachable(submount_client):
    """Key smoke test: msgviz-base.js is requested by the browser at
    /messages/app/msgviz-base.js — and must deliver there."""
    r = submount_client.get("/messages/app/msgviz-base.js")
    assert r.status_code == 200
    assert "window.MSGVIZ" in r.text
    assert "mvUrl" in r.text


def test_submount_api_reachable(submount_client):
    """Sub-mount under /messages reaches every API route with the prefix."""
    r = submount_client.get("/messages/api/index")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
#  msgviz-base.js contents
# ---------------------------------------------------------------------------
def test_msgviz_base_js_exports_helpers(standalone_client):
    r = standalone_client.get("/app/msgviz-base.js")
    assert r.status_code == 200
    src = r.text
    # Helper API — JS uses 'var W = window' and then W.mvUrl etc.
    assert "mvUrl" in src
    assert "mvApi" in src
    assert "MSGVIZ" in src
