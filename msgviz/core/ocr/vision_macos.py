# -*- coding: utf-8 -*-
"""
msgviz.core.ocr.vision_macos — macOS Vision OCR via Swift binary.

Uses the Swift tool `tools/ocr/ocr` shipped with the repo. The binary
only exists on macOS (Apple Vision API). On Linux this class is never
loaded — `get_engine()` filters it out first.

Build:
    swiftc -O tools/ocr/ocr.swift -o tools/ocr/ocr
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from msgviz.paths import project_root


class VisionEngine:
    name = "vision"

    def __init__(self, binary: Path | None = None) -> None:
        self.binary = Path(binary) if binary else project_root() / "tools" / "ocr" / "ocr"

    def is_available(self) -> bool:
        return sys.platform == "darwin" and self.binary.is_file()

    def recognize(self, path: Path | str) -> tuple[str, int]:
        if not self.is_available():
            raise RuntimeError(
                f"vision: binary missing at {self.binary}. "
                "Build with: swiftc -O tools/ocr/ocr.swift -o tools/ocr/ocr"
            )
        try:
            out = subprocess.check_output(
                [str(self.binary), str(path)], stderr=subprocess.DEVNULL
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"vision: subprocess fail ({e.returncode})")
        try:
            data = json.loads(out.decode("utf-8"))
        except Exception as e:
            raise RuntimeError(f"vision: invalid JSON ({e})")
        if "error" in data:
            raise RuntimeError(f"vision: {data['error']}")
        return data.get("text", "") or "", int(data.get("lines", 0))
