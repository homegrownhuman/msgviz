#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Render a real `msgviz import whatsapp` progress capture as SVG.

The previous docs/screenshots/import.svg was a hand-faked Rich
`Progress` frame — wrong widget (the real importer uses a Live-updating
Tree) and not from an actual run. This script:

  1. Builds a small synthetic WhatsApp export under a sandbox MSGVIZ_HOME
  2. Patches msgviz.core.progress.TerminalReporter to use a
     Console(record=True) (so Rich output ends up in a recording)
  3. Patches it again to render the tree once at the end via
     console.print(), bypassing the Live widget (Live's in-place
     updates don't capture meaningfully into a single static SVG)
  4. Runs the importer with --limit so we get a partial state — a
     few phases marked ✓ done, the rest still in flight
  5. Saves the captured output to docs/screenshots/import.svg

Output: docs/screenshots/import.svg

Usage:
    .venv/bin/python scripts/render_import_screenshot.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots" / "import.svg"


def main() -> None:
    # Set up an isolated MSGVIZ_HOME. We use a temp dir so the run
    # doesn't pollute the developer's live data/.
    sandbox = Path(tempfile.mkdtemp(prefix="mv_import_capture_"))
    msgviz_home = sandbox / "home"
    msgviz_home.mkdir()
    os.environ["MSGVIZ_HOME"] = str(msgviz_home)

    # Import after MSGVIZ_HOME is set so paths.py picks it up.
    sys.path.insert(0, str(ROOT))
    from rich.console import Console
    from msgviz.core import progress as progress_module

    # --- 1. Initialize the sandbox DB + a synthetic WhatsApp export ----------
    from typer.testing import CliRunner
    from msgviz.cli.main import app as msgviz_app
    runner = CliRunner()

    print("→ initializing sandbox DB …", flush=True)
    res = runner.invoke(msgviz_app, ["init"])
    assert res.exit_code == 0, f"init failed: {res.output}"

    res = runner.invoke(msgviz_app, [
        "person", "add", "Alice Chen",
        "--handles", "alice@example.com,+491701234567",
    ])
    assert res.exit_code == 0, f"person alice: {res.output}"
    res = runner.invoke(msgviz_app, [
        "person", "add", "Bob Smith",
        "--handles", "bob@example.com,+491709876543",
    ])
    assert res.exit_code == 0, f"person bob: {res.output}"
    res = runner.invoke(msgviz_app, [
        "device", "add", "wa_archive",
        "--name", "iPhone 14 (WhatsApp backup)",
        "--type", "static", "--owner", "Alice Chen",
    ])
    assert res.exit_code == 0, f"device add: {res.output}"
    res = runner.invoke(msgviz_app, [
        "chat", "add", "wa_archive",
        "--slug", "bob", "--title", "Bob Smith",
        "--subtitle", "WhatsApp export", "--origin", "whatsapp",
    ])
    assert res.exit_code == 0, f"chat add: {res.output}"

    # --- 2. Build a small synthetic WhatsApp export --------------------------
    # We write the minimum the importer needs: a _chat.txt in the
    # `[DD.MM.YY, HH:MM:SS] Sender: text` format with an Alice/Bob
    # dialogue. No media (keeps the script short; the progress tree
    # still shows the same phases).
    import random as _random
    from datetime import datetime, timedelta

    export_dir = sandbox / "whatsapp_bob"
    export_dir.mkdir()

    rnd = _random.Random(42)
    n_messages = 2400  # enough that --limit=1200 sits clearly mid-flight

    alice_lines = [
        "hey", "how's it going?", "got a sec?",
        "saw the news today — wild stuff",
        "lunch tomorrow?", "1pm at the usual place?",
        "Just got off a call with the client and they loved the redesign.",
        "ok cool", "sounds good", "thx",
        "running 5 minutes late", "be there soon",
        "did you see the article I sent?",
        "what time does the movie start?",
        "+1 to all of this", "lol",
        "back from the gym, totally wrecked",
        "any update on the migration?",
    ]
    bob_lines = [
        "morning!", "all good — you?", "for sure",
        "haha that's wild", "sure, where?",
        "works for me", "👍", "k", "noted",
        "no worries, take your time",
        "yeah it was great",
        "around 7:30 I think",
        "haven't yet, let me read it now",
        "let's do it", "perfect",
        "deployment is live",
    ]

    end = datetime(2026, 5, 28, 21, 0)
    start = end - timedelta(days=int(2.5 * 365))
    span = (end - start).total_seconds()

    lines = []
    cur = start
    while len(lines) < n_messages and cur <= end:
        # Loose bursts of 1–4 messages, then a gap of 2–12 hours.
        burst = rnd.randint(1, 4)
        for _ in range(burst):
            if len(lines) >= n_messages or cur > end:
                break
            sender = "Alice Chen" if rnd.random() < 0.48 else "Bob Smith"
            text = rnd.choice(
                alice_lines if sender == "Alice Chen" else bob_lines
            )
            lines.append(
                f"[{cur.strftime('%d.%m.%y, %H:%M:%S')}] {sender}: {text}"
            )
            cur += timedelta(seconds=rnd.randint(20, 180))
        # Gap before the next burst.
        cur += timedelta(hours=rnd.uniform(2, 12))

    (export_dir / "_chat.txt").write_text("\n".join(lines) + "\n",
                                          encoding="utf-8")
    print(f"→ generated synthetic WhatsApp export ({len(lines)} msgs) …",
          flush=True)

    # --- 3. Patch TerminalReporter to record + emit a one-shot frame ---------
    # Each progress event will refresh into the recording. At the end of
    # the run we save the SVG.
    recording_console = Console(
        record=True,
        width=100,
        force_terminal=True,
    )

    _orig_init = progress_module.TerminalReporter.__init__

    # We keep a reference to the live reporter instance so we can
    # render its tree manually after the import returns. import_cmd
    # doesn't call reporter.close() — the reporter just goes out of
    # scope when the command function returns — so we can't rely on a
    # patched close() to fire.
    captured_reporter: dict = {"instance": None}

    def _patched_init(self):
        _orig_init(self)
        self._console = recording_console
        # Disable the Live widget — Live's in-place redraws don't
        # capture into a single static SVG. We'll render() once after
        # the command returns and let the recorder hold the result.
        self._live = None
        captured_reporter["instance"] = self

    def _patched_ensure_live(self):
        # No-op: don't start the Live widget at all.
        pass

    def _patched_refresh(self):
        # No-op during the run; we capture the final frame after the
        # importer's command function returns.
        pass

    progress_module.TerminalReporter.__init__ = _patched_init
    progress_module.TerminalReporter._ensure_live = _patched_ensure_live
    progress_module.TerminalReporter._refresh = _patched_refresh

    # --- 4. Run the real importer (partial, via --limit) --------------------
    # Print a few intro lines to the recording console so the screenshot
    # also shows the invocation, matching what a real user would see.
    recording_console.print(
        "[bold]$ msgviz import whatsapp[/bold] "
        "--device wa_archive --folder ./exports/Bob \\\n"
        "                          --slug bob --me 'Alice Chen'"
    )
    recording_console.print()

    print("→ running real msgviz import whatsapp (limit=1200) …", flush=True)
    res = runner.invoke(msgviz_app, [
        "import", "whatsapp",
        "--device", "wa_archive",
        "--folder", str(export_dir),
        "--slug", "bob",
        "--me", "Alice Chen",
        "--limit", "1200",         # mid-flight stop
    ])
    print(f"   exit={res.exit_code}", flush=True)
    if res.exit_code != 0:
        # Show the importer's output so the failure is debuggable.
        print(res.output[-3000:])

    # --- 5. Render the captured reporter's tree once -----------------------
    reporter = captured_reporter["instance"]
    if reporter is not None and reporter._root_phases:
        recording_console.print(reporter._render())
    else:
        recording_console.print(
            "[dim]no reporter captured — patch may not have taken effect[/dim]"
        )

    # --- 6. Save SVG --------------------------------------------------------
    OUT.parent.mkdir(parents=True, exist_ok=True)
    recording_console.save_svg(str(OUT), title="msgviz import whatsapp")
    size_kb = OUT.stat().st_size // 1024
    print(f"✓ wrote {OUT.relative_to(ROOT)} ({size_kb} KB)")

    # Clean up the sandbox.
    shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    main()
