# -*- coding: utf-8 -*-
"""
Regression guard for msgviz.core.sync — cross-platform behavior.

Verifies:
1. On non-Darwin systems sync() does not crash; it skips mac_live
   devices and reports that in the stats dict ('skipped_devices').
2. On Darwin with an unreachable chat.db, only the affected device is
   skipped — not the whole sync run.
3. The watcher in factory.py checks sys.platform == 'darwin' and no-ops
   otherwise.
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture
def fake_visualizer_db(visualizer_db_path, tmp_visualizer_db, monkeypatch):
    """Schema-compliant empty DB + sync.DB pointing at this DB."""
    tmp_visualizer_db.close()
    monkeypatch.setattr("msgviz.core.sync.DB", str(visualizer_db_path), raising=True)
    return visualizer_db_path


def test_sync_skips_mac_live_on_non_darwin(monkeypatch, fake_visualizer_db):
    """On Linux: mac_live devices are skipped, sync() returns stats."""
    import msgviz.core.sync as sync_mod

    # Force platform to 'linux'.
    monkeypatch.setattr(sys, "platform", "linux")

    # Fake CONFIG with a mac_live device.
    fake_cfg = {
        "devices": [
            {"slug": "mac_test", "type": "mac_live", "me_name": "Tester", "chats": []},
            {"slug": "ipad_test", "type": "ios_backup", "me_name": "T", "chats": []},
        ]
    }
    monkeypatch.setattr(sync_mod.ex, "CONFIG", fake_cfg, raising=True)

    stats = sync_mod.sync()
    assert stats["skipped_devices"] == 1
    assert stats["new"] == 0
    assert stats["updated"] == 0


def test_sync_skips_when_chatdb_missing(monkeypatch, fake_visualizer_db, tmp_path):
    """On Darwin without chat.db: only the affected device is skipped, no crash."""
    import msgviz.core.sync as sync_mod

    # Stay on Darwin.
    monkeypatch.setattr(sys, "platform", "darwin")

    # chat.db points at a non-existent path.
    fake_cfg = {
        "devices": [
            {
                "slug": "mac_test",
                "type": "mac_live",
                "me_name": "Tester",
                "db": str(tmp_path / "nonexistent-chat.db"),
                "chats": [],
            },
        ]
    }
    monkeypatch.setattr(sync_mod.ex, "CONFIG", fake_cfg, raising=True)

    stats = sync_mod.sync()
    assert stats["skipped_devices"] == 1


def test_watcher_loop_returns_immediately_on_non_darwin(monkeypatch):
    """The live watcher in factory.py exits immediately on non-Darwin."""
    import asyncio

    from msgviz.config import MVConfig
    from msgviz.server.factory import ServerState, _watcher_loop

    monkeypatch.setattr(sys, "platform", "linux")

    state = ServerState(config=MVConfig(enable_watcher=False))

    # _watcher_loop is async — on Linux it should return instantly,
    # without entering an endless polling loop.
    async def runner():
        await asyncio.wait_for(_watcher_loop(state), timeout=0.5)

    # If this doesn't return within 500ms, the skip logic is broken.
    asyncio.run(runner())
