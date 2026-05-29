# -*- coding: utf-8 -*-
"""
msgviz.paths — central path resolution.

Earlier each module computed `ROOT = parent.parent.parent(__file__)`. That
was fragile (every module move broke every ROOT) and not configurable
(for `create_app(config)` we need exactly this: paths from a config, not
from `__file__`).

This module encapsulates the resolution:

  * `project_root()` returns the project root (the directory containing
    `msgviz/`, `data/`, `media/`, `app/`, `config/`).
  * `data_dir()`, `media_root()`, `originals_root()`, `app_dir()`,
    `config_dir()` build on top of `project_root()`.
  * The env variable `MSGVIZ_HOME` overrides the root — important for
    embedded setups and tests.

`MVConfig` takes these as defaults; the helpers here stay as fallbacks for
CLI/standalone calls.
"""
from __future__ import annotations

import os
from pathlib import Path

_THIS = Path(__file__).resolve()


def project_root() -> Path:
    """Project root directory.

    Priority:
      1. env `MSGVIZ_HOME` (absolute path) if set
      2. two levels above this file (`msgviz/paths.py` -> `<repo>/`)
    """
    env = os.environ.get("MSGVIZ_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return _THIS.parent.parent


def data_dir() -> Path:
    return project_root() / "data"


def media_root() -> Path:
    return project_root() / "media"


def originals_root() -> Path:
    return project_root() / "originals"


def app_dir() -> Path:
    return project_root() / "app"


def config_dir() -> Path:
    return project_root() / "config"


def db_file() -> Path:
    return data_dir() / "visualizer.db"


def schema_sql() -> Path:
    return _THIS.parent / "core" / "schema.sql"
