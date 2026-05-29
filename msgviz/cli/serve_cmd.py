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
    root_path: str = typer.Option(
        "",
        "--root-path",
        help=(
            "ASGI root_path for sub-mount deployments. Example: --root-path /dev "
            "when this server is reverse-proxied behind https://example.com/dev/. "
            "All template paths and API URLs are prefixed automatically."
        ),
    ),
) -> None:
    """Start `msgviz.server.app:app` via uvicorn."""
    try:
        import uvicorn  # noqa: WPS433
    except ImportError:
        die("uvicorn missing — `pip install uvicorn` or `pip install msgviz[server]`.")

    os.environ.setdefault("MV_PORT", str(port))
    rp_suffix = f"  (root_path={root_path})" if root_path else ""
    console.print(f"[bold]msgviz serve[/bold] -> http://{host}:{port}{rp_suffix}")
    uvicorn.run(
        "msgviz.server.app:app",
        host=host,
        port=port,
        reload=reload,
        root_path=root_path,
    )
