#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Standalone entry for the msgviz server.

This file exists so the legacy uvicorn invocation
    uvicorn msgviz.server.app:app
keeps working (Start_Message_Visualizer.command, msgviz serve, tests).

Actual app construction happens via create_app() in factory.py.
If you want to embed the server, import directly from there:

    from msgviz.server.factory import create_app
    from msgviz.config import MVConfig
    mv = create_app(MVConfig(data_dir="/var/lib/mv", ...))
    main_app.mount("/messages", mv)
"""
from msgviz.server.factory import create_app

app = create_app()


if __name__ == "__main__":
    import os
    import uvicorn

    uvicorn.run(
        "msgviz.server.app:app",
        host="127.0.0.1",
        port=int(os.environ.get("MV_PORT", "8753")),
    )
