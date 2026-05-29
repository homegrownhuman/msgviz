# -*- coding: utf-8 -*-
"""msgviz serve — start the FastAPI server via uvicorn."""
from __future__ import annotations

import os

import typer

from ._helpers import console, die


def serve(
    host: str = typer.Option("127.0.0.1", "--host", "-H", help="Bind host."),
    port: int = typer.Option(8753, "--port", "-p", help="Bind port."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload (development)."),
) -> None:
    """Start `msgviz.server.app:app` via uvicorn."""
    try:
        import uvicorn  # noqa: WPS433
    except ImportError:
        die("uvicorn missing — `pip install uvicorn` or `pip install msgviz[server]`.")

    os.environ.setdefault("MV_PORT", str(port))
    console.print(f"[bold]msgviz serve[/bold] -> http://{host}:{port}")
    uvicorn.run(
        "msgviz.server.app:app",
        host=host,
        port=port,
        reload=reload,
    )
