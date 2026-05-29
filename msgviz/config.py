# -*- coding: utf-8 -*-
"""
msgviz.config — runtime configuration for create_app().

`MVConfig` bundles everything that used to be a module global in
server/app.py or scattered across os.environ:

* paths (data, media, originals, app assets, sources.json)
* DB location
* API knobs (default/max page size, websocket polling)
* mount points (important for sub-app embedding)
* caching behavior

Without overrides you get sensible defaults derived from
`msgviz.paths.project_root()` (-> env `MSGVIZ_HOME` or repo layout).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from msgviz.paths import (
    app_dir,
    config_dir,
    data_dir,
    db_file,
    media_root,
    originals_root,
    project_root,
)


@dataclass
class MVConfig:
    """Configuration object for create_app().

    Path fields accept Path or str; normalized in __post_init__.
    """

    # --- Paths ---
    project_root: Path = field(default_factory=project_root)
    data_dir: Path = field(default_factory=data_dir)
    media_root: Path = field(default_factory=media_root)
    originals_root: Path = field(default_factory=originals_root)
    app_dir: Path = field(default_factory=app_dir)
    config_dir: Path = field(default_factory=config_dir)
    db_file: Path = field(default_factory=db_file)
    sources_json: Path | None = None  # default: config_dir / sources.json

    # --- HTML templates (historically in project_root) ---
    index_html: Path | None = None
    chat_template_html: Path | None = None
    favicon_path: Path | None = None

    # --- API ---
    default_page_limit: int = 50
    max_page_limit: int = 500

    # --- Live watcher ---
    watcher_poll_seconds: float = field(
        default_factory=lambda: float(os.environ.get("MV_POLL", "2.0"))
    )
    enable_watcher: bool = True

    # --- Mounts (relative to the app, typically "/app" etc.) ---
    # If the app is mounted at /messages/ inside a foreign server, these
    # are the paths UNDER /messages/.
    mount_app: str = "/app"
    mount_data: str = "/data"
    mount_media: str = "/media"
    mount_originals: str = "/originals"

    # --- Caching ---
    # Never cache app assets (CSS/JS) and HTML so frontend changes show
    # up immediately. Media is hash-based -> can be cached aggressively.
    nocache_app_files: bool = True

    # --- FastAPI metadata ---
    title: str = "msgviz"
    description: str = "Local, source-agnostic chat archive visualizer."

    def __post_init__(self) -> None:
        # Normalize everything to Path.
        self.project_root = Path(self.project_root)
        self.data_dir = Path(self.data_dir)
        self.media_root = Path(self.media_root)
        self.originals_root = Path(self.originals_root)
        self.app_dir = Path(self.app_dir)
        self.config_dir = Path(self.config_dir)
        self.db_file = Path(self.db_file)

        # Frontend assets (HTML templates, app/) live in the CODE repo,
        # not under MSGVIZ_HOME. When MSGVIZ_HOME differs from the code
        # repo (e.g. demo/ or dev/), the static-content paths fall back
        # to the code repo.
        code_repo = Path(__file__).resolve().parent.parent

        if self.sources_json is None:
            self.sources_json = self.config_dir / "sources.json"
        else:
            self.sources_json = Path(self.sources_json)

        # Templates live under app/templates/. We check the per-environment
        # project_root first (so MSGVIZ_HOME=demo or dev can override with
        # custom templates) and fall back to the code repo's bundled ones.
        if self.index_html is None:
            cand = self.project_root / "app" / "templates" / "index.html"
            self.index_html = (
                cand if cand.is_file()
                else (code_repo / "app" / "templates" / "index.html")
            )
        else:
            self.index_html = Path(self.index_html)

        if self.chat_template_html is None:
            cand = self.project_root / "app" / "templates" / "chat.template.html"
            self.chat_template_html = (
                cand if cand.is_file()
                else (code_repo / "app" / "templates" / "chat.template.html")
            )
        else:
            self.chat_template_html = Path(self.chat_template_html)

        # app_dir falls back to code_repo too if it doesn't exist under
        # project_root (so static mounts still work in demo/dev mode).
        if not self.app_dir.is_dir():
            self.app_dir = code_repo / "app"

        if self.favicon_path is None:
            self.favicon_path = self.app_dir / "icons" / "favicon.ico"
        else:
            self.favicon_path = Path(self.favicon_path)


def default_config() -> MVConfig:
    """Default config from MSGVIZ_HOME / repo defaults — for standalone calls."""
    return MVConfig()
