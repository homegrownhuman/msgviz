# -*- coding: utf-8 -*-
"""
msgviz.cli — Typer-based command line.

The central CLI for msgviz. Invoke as:
    msgviz <subcommand> ...
    python -m msgviz <subcommand> ...

Subcommand groups are organized by domain:
    msgviz init                          -- create config + empty DB
    msgviz status                        -- DB stats / health
    msgviz serve                         -- start the local server

    msgviz device {add|list|remove}      -- manage devices
    msgviz chat   {add|list|remove}      -- manage chats
    msgviz person {add|list|merge}       -- manage persons
    msgviz import {imessage|whatsapp}    -- import data
    msgviz delete {chat|device|all}      -- delete data
    msgviz transcribe                    -- audio transcription
    msgviz ocr                           -- image OCR

Tip: `msgviz --install-completion` enables shell completion.
"""
from .main import app

__all__ = ["app"]
