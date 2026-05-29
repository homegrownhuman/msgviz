# -*- coding: utf-8 -*-
"""
msgviz check — selftest / dependency audit.

Probes Python version, optional Python packages, system binaries, the
Whisper model, OCR engines, and the configured MSGVIZ_HOME paths. For
each feature reports whether it works on this machine and — when it
doesn't — what the consequence is and how to fix it.

Exit code:
    0  baseline OK (serve + status work, even if optional features missing)
    1  baseline broken (server can't start, e.g. missing FastAPI)

Designed to be safe to run anywhere — no DB writes, no network calls.
"""
from __future__ import annotations

import os
import platform
import shutil
import sys
from dataclasses import dataclass, field
from typing import Callable

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from msgviz.paths import data_dir, db_file, media_root, originals_root, project_root

# Mute Rich's auto-detection — we want consistent output even in CI.
console = Console()


# ---------------------------------------------------------------------------
# Per-feature probe data model
# ---------------------------------------------------------------------------
OK = "ok"
DEGRADED = "degraded"
MISSING = "missing"


@dataclass
class Probe:
    """One feature being checked."""

    feature: str
    status: str  # OK / DEGRADED / MISSING
    detail: str
    consequence: str = ""
    fix: str = ""

    def style(self) -> str:
        return {
            OK: "green",
            DEGRADED: "yellow",
            MISSING: "red",
        }[self.status]

    def glyph(self) -> str:
        return {OK: "✓", DEGRADED: "~", MISSING: "✗"}[self.status]


@dataclass
class Report:
    probes: list[Probe] = field(default_factory=list)

    def add(self, p: Probe) -> None:
        self.probes.append(p)

    def has_missing(self) -> bool:
        return any(p.status == MISSING for p in self.probes)

    def has_degraded(self) -> bool:
        return any(p.status == DEGRADED for p in self.probes)

    def baseline_ok(self) -> bool:
        """True if the server can at least start and serve.

        We treat a missing Python version or missing core import as
        baseline-broken; everything else is "degraded": msgviz runs but
        the corresponding feature won't work.
        """
        for p in self.probes:
            if p.feature in {"Python version", "FastAPI / uvicorn"} and p.status == MISSING:
                return False
        return True


# ---------------------------------------------------------------------------
# Individual probes
# ---------------------------------------------------------------------------
def _probe_python() -> Probe:
    major, minor = sys.version_info[:2]
    version = f"{major}.{minor}.{sys.version_info[2]}"
    if (major, minor) < (3, 10):
        return Probe(
            "Python version", MISSING, version,
            consequence="msgviz declares >=3.10 — install will fail or "
                        "imports will break at runtime.",
            fix="Install Python 3.10+ (macOS: brew install python@3.12; "
                "Linux: apt install python3.12 python3.12-venv).",
        )
    return Probe("Python version", OK, version)


def _probe_core_imports() -> Probe:
    missing: list[str] = []
    for mod in ("fastapi", "uvicorn", "typer", "rich"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        return Probe(
            "FastAPI / uvicorn", MISSING,
            f"missing: {', '.join(missing)}",
            consequence="The server (`msgviz serve`) can't start.",
            fix="Re-install msgviz: pip install -e .",
        )
    import fastapi  # noqa
    import uvicorn  # noqa
    return Probe(
        "FastAPI / uvicorn", OK,
        f"fastapi {fastapi.__version__}, uvicorn {uvicorn.__version__}",
    )


def _probe_pillow() -> Probe:
    try:
        import PIL  # noqa: F401
        return Probe("Pillow (image processing)", OK, PIL.__version__)
    except ImportError:
        return Probe(
            "Pillow (image processing)", DEGRADED, "not installed",
            consequence="Image-thumbnail generation in the media kit will "
                        "skip resizing — originals are served as-is. "
                        "Demo asset generation (scripts/) needs this.",
            fix="pip install Pillow  (or: pip install -e '.[dev]')",
        )


def _probe_ffmpeg() -> Probe:
    path = shutil.which("ffmpeg")
    if not path:
        # Also check msgviz's own fallback search.
        try:
            from msgviz.core.whisper import find_ffmpeg
            p = find_ffmpeg()
            if p:
                path = str(p)
        except Exception:
            pass
    if path:
        return Probe("ffmpeg", OK, path)
    return Probe(
        "ffmpeg", DEGRADED, "not found on PATH",
        consequence="Voice-message conversion (m4a→wav for Whisper) won't "
                    "work; `msgviz transcribe` will skip those files.",
        fix="macOS: brew install ffmpeg   |   Linux: apt install ffmpeg",
    )


def _probe_whisper_cli() -> Probe:
    try:
        from msgviz.core.whisper import find_whisper_cli
        p = find_whisper_cli()
    except Exception as e:
        return Probe("whisper-cli", DEGRADED, f"detection failed: {e}",
                     consequence="Audio transcription disabled.")
    if p:
        return Probe("whisper-cli", OK, str(p))
    return Probe(
        "whisper-cli", DEGRADED, "not installed",
        consequence="`msgviz transcribe` is disabled. Voice messages are "
                    "still stored and playable; just not transcribed.",
        fix="macOS: brew install whisper-cpp   |   "
            "Linux: build whisper.cpp from source (see docs/CLI.md).",
    )


def _probe_whisper_model() -> Probe:
    try:
        from msgviz.core.whisper import find_model, default_model_name, model_search_paths
        p = find_model()
    except Exception as e:
        return Probe("Whisper model", DEGRADED, f"detection failed: {e}")
    if p:
        size_mb = p.stat().st_size // (1024 * 1024)
        return Probe("Whisper model", OK, f"{p.name} ({size_mb} MB) at {p.parent}")
    paths = ", ".join(str(x) for x in model_search_paths())
    return Probe(
        "Whisper model", DEGRADED,
        f"{default_model_name()} not found in: {paths}",
        consequence="`msgviz transcribe` will refuse to run without a model.",
        fix="Download the Whisper large-v3 model (~3 GB):\n"
            "  mkdir -p ~/.whisper-models && \\\n"
            "  curl -L -o ~/.whisper-models/ggml-large-v3.bin \\\n"
            "    https://huggingface.co/ggerganov/whisper.cpp/"
            "resolve/main/ggml-large-v3.bin",
    )


def _probe_ocr_engine() -> Probe:
    try:
        from msgviz.core.ocr import get_engine, reset_cache
        reset_cache()
        engine = get_engine()
    except Exception as e:
        return Probe("OCR engine", DEGRADED, f"detection failed: {e}",
                     consequence="Screenshot OCR disabled.")
    if engine.name == "vision":
        return Probe("OCR engine", OK, "macOS Vision (highest quality)")
    if engine.name == "tesseract":
        try:
            import pytesseract
            ver = pytesseract.get_tesseract_version()
            return Probe("OCR engine", OK, f"Tesseract {ver}")
        except Exception:
            return Probe("OCR engine", OK, "Tesseract (version unknown)")
    # Null engine.
    plat = "macOS" if sys.platform == "darwin" else "Linux"
    if plat == "macOS":
        fix = ("Build the Vision binary:\n"
               "  swiftc -O tools/ocr/ocr.swift -o tools/ocr/ocr")
    else:
        fix = ("Install Tesseract:\n"
               "  apt install tesseract-ocr tesseract-ocr-eng "
               "tesseract-ocr-deu\n"
               "  pip install 'msgviz[ocr-tesseract]'")
    return Probe(
        "OCR engine", DEGRADED, "none available — null engine active",
        consequence="`msgviz ocr` skips images instead of crashing — "
                    "screenshots have no searchable text.",
        fix=fix,
    )


def _probe_imessage_live() -> Probe:
    """macOS-only feature; reports unavailable on Linux."""
    if sys.platform != "darwin":
        return Probe(
            "Live iMessage sync", MISSING, "macOS-only",
            consequence="Linux can ingest iMessage backups but not the "
                        "live ~/Library/Messages/chat.db. WhatsApp and "
                        "iMessage backups still work.",
            fix="No fix on Linux — this is a macOS feature.",
        )
    chat_db = os.path.expanduser("~/Library/Messages/chat.db")
    if not os.path.isfile(chat_db):
        return Probe(
            "Live iMessage sync", DEGRADED,
            "~/Library/Messages/chat.db not found",
            consequence="No live iMessage to sync.",
            fix="Open Messages.app at least once on this Mac.",
        )
    # Try to actually read it — Full Disk Access?
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{chat_db}?mode=ro", uri=True)
        con.execute("SELECT 1 FROM message LIMIT 1")
        con.close()
        return Probe("Live iMessage sync", OK, chat_db)
    except sqlite3.OperationalError as e:
        return Probe(
            "Live iMessage sync", DEGRADED, f"can't read chat.db: {e}",
            consequence="Live sync will fail. Backup imports still work.",
            fix="Grant Full-Disk-Access to your terminal: System "
                "Settings → Privacy & Security → Full Disk Access.",
        )


def _probe_msgviz_home() -> Probe:
    home = project_root()
    pieces = []
    pieces.append(f"home={home}")
    pieces.append(f"db={'exists' if db_file().is_file() else 'missing'}")
    pieces.append(f"media={'exists' if media_root().is_dir() else 'missing'}")
    return Probe("MSGVIZ_HOME paths", OK, " · ".join(pieces))


# ---------------------------------------------------------------------------
# Top-level: feature matrix
# ---------------------------------------------------------------------------
PROBES: list[Callable[[], Probe]] = [
    _probe_python,
    _probe_core_imports,
    _probe_msgviz_home,
    _probe_pillow,
    _probe_ffmpeg,
    _probe_whisper_cli,
    _probe_whisper_model,
    _probe_ocr_engine,
    _probe_imessage_live,
]


def _feature_matrix(report: Report) -> Table:
    """High-level: which msgviz features work, which don't."""
    status_by_feature = {p.feature: p for p in report.probes}

    def st(*names: str) -> tuple[str, str]:
        """Worst-case status across the named probes + a one-line reason."""
        present = [status_by_feature[n] for n in names if n in status_by_feature]
        if not present:
            return "?", ""
        if any(p.status == MISSING for p in present):
            missing = [p.feature for p in present if p.status == MISSING]
            return "✗ not available", f"missing: {', '.join(missing)}"
        if any(p.status == DEGRADED for p in present):
            degraded = [p.feature for p in present if p.status == DEGRADED]
            return "~ degraded", f"degraded: {', '.join(degraded)}"
        return "✓ ready", ""

    rows = [
        ("Web UI / API server",        st("Python version", "FastAPI / uvicorn")),
        ("Import: WhatsApp export",    st("Python version")),
        ("Import: iMessage backup",    st("Python version")),
        ("Live iMessage sync (macOS)", st("Live iMessage sync")),
        ("Audio transcription",        st("ffmpeg", "whisper-cli", "Whisper model")),
        ("Screenshot OCR",             st("OCR engine")),
        ("Image thumbnails",           st("Pillow (image processing)")),
    ]

    tbl = Table(title="Feature matrix", show_lines=False, header_style="bold")
    tbl.add_column("Feature", style="bold")
    tbl.add_column("Status")
    tbl.add_column("Why", style="dim")
    for label, (status, why) in rows:
        style = ("green" if status.startswith("✓")
                 else "yellow" if status.startswith("~")
                 else "red")
        tbl.add_row(label, Text(status, style=style), why)
    return tbl


def _probe_table(report: Report) -> Table:
    tbl = Table(title="Probes", show_lines=False, header_style="bold")
    tbl.add_column("Check", style="bold")
    tbl.add_column("Status", justify="center")
    tbl.add_column("Detail")
    for p in report.probes:
        tbl.add_row(p.feature, Text(f"{p.glyph()} {p.status}", style=p.style()), p.detail)
    return tbl


def _fix_panel(report: Report) -> Panel | None:
    needs_fix = [p for p in report.probes if p.status in (DEGRADED, MISSING) and p.fix]
    if not needs_fix:
        return None
    lines: list[str] = []
    for p in needs_fix:
        lines.append(f"[bold]{p.feature}[/bold] — [dim]{p.detail}[/dim]")
        if p.consequence:
            lines.append(f"  consequence: {p.consequence}")
        lines.append(f"  fix:")
        for fl in p.fix.splitlines():
            lines.append(f"    {fl}")
        lines.append("")
    return Panel(
        "\n".join(lines).rstrip(),
        title="How to fix",
        border_style="yellow",
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def check(
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Show the per-probe table in addition to the feature matrix.",
    ),
    json_out: bool = typer.Option(
        False, "--json",
        help="Emit a machine-readable JSON report instead of the tables.",
    ),
) -> None:
    """Run a selftest: which features work on this machine, which don't."""
    report = Report()
    for fn in PROBES:
        try:
            report.add(fn())
        except Exception as e:
            report.add(Probe(
                feature=fn.__name__.replace("_probe_", ""),
                status=DEGRADED,
                detail=f"probe crashed: {e}",
            ))

    if json_out:
        import json
        payload = {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "baseline_ok": report.baseline_ok(),
            "probes": [
                {"feature": p.feature, "status": p.status,
                 "detail": p.detail, "consequence": p.consequence,
                 "fix": p.fix}
                for p in report.probes
            ],
        }
        console.print_json(data=payload)
    else:
        console.print(f"[bold]Platform:[/bold] {platform.platform()}")
        console.print(f"[bold]Python:[/bold]   {sys.version.split()[0]}")
        console.print()
        console.print(_feature_matrix(report))
        if verbose:
            console.print()
            console.print(_probe_table(report))
        panel = _fix_panel(report)
        if panel is not None:
            console.print()
            console.print(panel)
        console.print()
        if not report.baseline_ok():
            console.print("[bold red]✗ baseline broken — msgviz can't run.[/bold red]")
        elif report.has_degraded() or report.has_missing():
            console.print(
                "[bold yellow]~ baseline OK — some optional features "
                "unavailable (see above).[/bold yellow]"
            )
        else:
            console.print("[bold green]✓ all checks passed.[/bold green]")

    if not report.baseline_ok():
        raise typer.Exit(code=1)
