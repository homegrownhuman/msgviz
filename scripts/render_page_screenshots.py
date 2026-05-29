#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Render PNG screenshots of the live demo pages via headless Chrome's
DevTools protocol. We can't use Chrome's `--screenshot=` flag because
the pages are SPAs: that flag captures the page at DOMContentLoaded —
before any `fetch()` has resolved — so we'd just get the "Loading…"
shell.

Instead: launch Chrome with `--remote-debugging-port`, wait for the
SPA's content to settle, then ask DevTools for a screenshot over a
minimal hand-rolled WebSocket. No new Python dep (uses stdlib only).

Output: docs/screenshots/page-{index,chat,heatmap}.png

Usage:
    .venv/bin/python scripts/render_page_screenshots.py
    .venv/bin/python scripts/render_page_screenshots.py --only chat
    .venv/bin/python scripts/render_page_screenshots.py --base http://localhost:9000

Prerequisites:
    The demo server is running:  ./scripts/msgviz-demo serve --port 8754
    Chrome / Chromium is installed (auto-detected; override with
    MSGVIZ_CHROME=/path/to/chrome).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "screenshots"
DEBUG_PORT = 9223  # arbitrary; avoid Chrome's default 9222 to dodge clashes

CHROME_CANDIDATES = [
    os.environ.get("MSGVIZ_CHROME"),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    shutil.which("google-chrome"),
    shutil.which("google-chrome-stable"),
    shutil.which("chromium"),
    shutil.which("chromium-browser"),
    shutil.which("chrome"),
]


def find_chrome() -> str:
    for c in CHROME_CANDIDATES:
        if c and Path(c).is_file():
            return c
    print(
        "✗ No Chrome/Chromium binary found. Install one of:\n"
        "    macOS:  brew install --cask google-chrome\n"
        "    Linux:  apt install chromium\n"
        "  …or set MSGVIZ_CHROME=/abs/path/to/chrome",
        file=sys.stderr,
    )
    sys.exit(2)


def wait_for(base: str, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base, timeout=1) as r:
                if r.status == 200:
                    return
        except Exception:
            time.sleep(0.3)
    print(f"✗ Server at {base} did not respond within {timeout}s", file=sys.stderr)
    sys.exit(3)


# ---------------------------------------------------------------------------
# Minimal DevTools WebSocket client (stdlib only)
# ---------------------------------------------------------------------------
class DevToolsClient:
    """Just enough WebSocket to send one Page.navigate + one
    Page.captureScreenshot per page. Not a general-purpose impl.

    We follow RFC 6455 for the framing: opening handshake over HTTP,
    then text frames with a masking key (clients MUST mask).
    """

    def __init__(self, ws_url: str):
        # ws_url like 'ws://127.0.0.1:9223/devtools/page/ABC123'
        assert ws_url.startswith("ws://")
        rest = ws_url[len("ws://"):]
        host_port, _, path = rest.partition("/")
        host, _, port = host_port.partition(":")
        self.host = host
        self.port = int(port or "80")
        self.path = "/" + path
        self.sock: socket.socket | None = None
        self._next_id = 1

    def connect(self) -> None:
        s = socket.create_connection((self.host, self.port), timeout=30)
        # Opening handshake. Sec-WebSocket-Key is required and arbitrary.
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        s.sendall(req.encode("ascii"))
        # Read until end of HTTP headers.
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                raise RuntimeError("WebSocket handshake closed early")
            buf += chunk
        if b" 101 " not in buf.split(b"\r\n", 1)[0]:
            raise RuntimeError(f"Bad handshake response: {buf[:200]!r}")
        self.sock = s

    def _send_frame(self, payload: bytes) -> None:
        assert self.sock is not None
        # Single text frame, FIN=1, opcode=0x1, with mask (client requirement).
        header = bytes([0x81])  # FIN + opcode text
        length = len(payload)
        mask_key = os.urandom(4)
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", length)
        header += mask_key
        masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + masked)

    def _recv_exact(self, n: int) -> bytes:
        assert self.sock is not None
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise RuntimeError("WebSocket closed mid-frame")
            buf += chunk
        return buf

    def _recv_frame(self) -> str:
        # Read one server frame and return its text payload.
        # Skips non-text control frames (pings).
        while True:
            head = self._recv_exact(2)
            opcode = head[0] & 0x0F
            mask = head[1] & 0x80
            length = head[1] & 0x7F
            if length == 126:
                (length,) = struct.unpack("!H", self._recv_exact(2))
            elif length == 127:
                (length,) = struct.unpack("!Q", self._recv_exact(8))
            mask_key = self._recv_exact(4) if mask else None
            payload = self._recv_exact(length)
            if mask_key:
                payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
            if opcode == 0x1:  # text
                return payload.decode("utf-8")
            if opcode == 0x9:  # ping → pong
                pong = bytes([0x8A, 0x80 | len(payload)]) + os.urandom(4)
                # send empty pong (servers ignore the payload content)
                self.sock.sendall(pong + b"")  # type: ignore[union-attr]
            # else: continue (binary, pong, close → just ignore for now)

    def call(self, method: str, params: dict | None = None,
             timeout: float = 30.0) -> dict:
        """Send one DevTools method, wait for the matching response."""
        msg_id = self._next_id
        self._next_id += 1
        msg = {"id": msg_id, "method": method}
        if params is not None:
            msg["params"] = params
        self._send_frame(json.dumps(msg).encode("utf-8"))
        deadline = time.time() + timeout
        while time.time() < deadline:
            text = self._recv_frame()
            data = json.loads(text)
            if data.get("id") == msg_id:
                if "error" in data:
                    raise RuntimeError(f"{method} → {data['error']}")
                return data.get("result", {})
            # otherwise it's an event we don't care about — keep reading
        raise TimeoutError(f"{method}: no response in {timeout}s")

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.sendall(bytes([0x88, 0x80]) + os.urandom(4))
            except Exception:
                pass
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None


# ---------------------------------------------------------------------------
# High-level Chrome lifecycle
# ---------------------------------------------------------------------------
def launch_chrome(chrome: str, width: int, height: int) -> tuple[subprocess.Popen, str]:
    """Launch headless Chrome with remote-debugging-port. Returns (proc, ws_url).

    The ws_url is the page-target WebSocket of the initial about:blank tab —
    that's the one we drive through Page.navigate.
    """
    tmp = tempfile.mkdtemp(prefix="mv_chrome_dt_")
    cmd = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--hide-scrollbars",
        f"--user-data-dir={tmp}",
        f"--window-size={width},{height}",
        f"--remote-debugging-port={DEBUG_PORT}",
        "about:blank",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Wait for DevTools to be up.
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{DEBUG_PORT}/json/version", timeout=1
            ):
                break
        except Exception:
            time.sleep(0.2)
    else:
        proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError("Chrome DevTools didn't come up in time")
    # Find the page target's WebSocket URL.
    with urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json/list") as r:
        targets = json.loads(r.read())
    page_targets = [t for t in targets if t.get("type") == "page"]
    if not page_targets:
        proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError("No page target in Chrome")
    return proc, page_targets[0]["webSocketDebuggerUrl"]


def capture(chrome: str, url: str, out: Path, *,
            width: int = 1400, height: int = 900,
            settle_seconds: float = 4.0,
            full_page: bool = False) -> None:
    """Open `url` in headless Chrome, wait for the SPA to render, save PNG.

    full_page=True captures the entire scrollable document, not just the
    initial viewport. The PNG width is `width` regardless; height is the
    rendered document height.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    proc, ws_url = launch_chrome(chrome, width, height)
    try:
        client = DevToolsClient(ws_url)
        client.connect()
        try:
            client.call("Page.enable")
            # Force a precise viewport (no Retina DPR scaling) so layout
            # is reproducible across machines and the PNG is exactly the
            # requested width.
            client.call(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": width,
                    "height": height,
                    "deviceScaleFactor": 1,
                    "mobile": False,
                },
            )
            client.call("Page.navigate", {"url": url})
            time.sleep(settle_seconds)
            params: dict = {"format": "png", "fromSurface": True,
                            "captureBeyondViewport": full_page}
            if full_page:
                # Probe document height, then clip to it so we don't get
                # extra whitespace from any default body padding.
                size = client.call(
                    "Page.getLayoutMetrics", timeout=10,
                )["contentSize"]
                doc_h = int(size["height"])
                params["clip"] = {
                    "x": 0, "y": 0, "width": width, "height": doc_h,
                    "scale": 1,
                }
            result = client.call("Page.captureScreenshot", params, timeout=30)
            data = base64.b64decode(result["data"])
            out.write_bytes(data)
            size_kb = len(data) // 1024
            print(f"  → {out.relative_to(ROOT)}  ({size_kb} KB, "
                  f"{width}×{'auto' if full_page else height})")
        finally:
            client.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


# ---------------------------------------------------------------------------
# Per-page settings
# ---------------------------------------------------------------------------
def render_index(chrome: str, base: str) -> None:
    """Overview page — the demo's 6 cards. The CSS centers them with
    large side margins on wide screens, so we use a viewport closer to
    the actual content width to crop out the empty black bars."""
    capture(chrome, f"{base}/", OUT_DIR / "page-index.png",
            width=1100, height=850, settle_seconds=4.0)


def render_chat(chrome: str, base: str) -> None:
    """Chat page including the right-hand calendar heatmap.

    Width is generous (1600) so the chat-bubble column and the heatmap
    column both have room. Height is fixed (not full-page) so the PNG
    stays a reasonable size — visitors who want to scroll the real
    chat can click through to the demo.
    """
    capture(chrome, f"{base}/chat/my_mac/bob", OUT_DIR / "page-chat.png",
            width=1600, height=1000, settle_seconds=6.0)


RENDERERS = {
    "index": render_index,
    "chat": render_chat,
}


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--base", default="http://127.0.0.1:8754",
                   help="Base URL of the running demo server.")
    p.add_argument("--only", nargs="+", choices=list(RENDERERS),
                   help="Render only these (default: all).")
    args = p.parse_args()

    chrome = find_chrome()
    print(f"✓ chrome: {chrome}")
    wait_for(args.base)
    print(f"✓ server: {args.base}")

    names = args.only or list(RENDERERS)
    print(f"Rendering {len(names)} screenshot(s) to {OUT_DIR.relative_to(ROOT)}/")
    for name in names:
        RENDERERS[name](chrome, args.base)


if __name__ == "__main__":
    main()
