#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Progress reporter for imports and worker runs.

Abstraction `ProgressReporter` with two implementations:

  TerminalReporter   – Rich live tree for interactive CLI calls.
  EventReporter      – JSONL file (data/imports/<id>.jsonl) for
                       background / web invocations. A web UI can later
                       read it via fetch/WebSocket.

Phases nest. A reporter supports:

  with reporter.phase("Parse source") as p:
      p.set_total(n_lines)
      for line in lines:
          p.tick()                          # increment by 1
      p.note("dropped: 12")                 # extra info line

  with reporter.phase("Media", total=n_atts) as p:
      for att in atts:
          with p.subtask(att.filename) as sub:
              sub.note("HEIC → JPG")
              ...
          p.tick()

Reporter choice:

  reporter = make_reporter(kind="terminal")     # interactive
  reporter = make_reporter(kind="events",
                           events_path="data/imports/abc.jsonl")
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional


# ---------------------------------------------------------------------------
# Phase handles. Both reporter implementations return the same phase API so
# caller code stays identical.
# ---------------------------------------------------------------------------
@dataclass
class _PhaseState:
    title: str
    total: Optional[int] = None
    current: int = 0
    notes: list[str] = field(default_factory=list)
    children: list["_PhaseState"] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None


class PhaseHandle:
    """Returned by the reporter. Callers invoke `tick`/`set_total`/
    `note`/`subtask`. The implementation delegates to the concrete reporter."""

    def __init__(self, reporter: "ProgressReporter", state: _PhaseState):
        self._reporter = reporter
        self._state = state

    def set_total(self, total: int) -> None:
        self._state.total = total
        self._reporter._on_phase_update(self._state)

    def tick(self, n: int = 1) -> None:
        self._state.current += n
        self._reporter._on_phase_update(self._state)

    def note(self, text: str) -> None:
        self._state.notes.append(text)
        self._reporter._on_phase_note(self._state, text)

    @contextmanager
    def subtask(self, title: str, total: Optional[int] = None) -> Iterator["PhaseHandle"]:
        with self._reporter._phase(title, total=total, parent=self._state) as ph:
            yield ph


# ---------------------------------------------------------------------------
# Reporter-Basis
# ---------------------------------------------------------------------------
class ProgressReporter:
    """Gemeinsame Basis. Subklassen implementieren die `_on_*`-Hooks."""

    def __init__(self):
        self._root_phases: list[_PhaseState] = []
        self._stack: list[_PhaseState] = []

    @contextmanager
    def phase(self, title: str, total: Optional[int] = None) -> Iterator[PhaseHandle]:
        with self._phase(title, total=total, parent=None) as ph:
            yield ph

    @contextmanager
    def _phase(self, title: str, total: Optional[int],
               parent: Optional[_PhaseState]) -> Iterator[PhaseHandle]:
        state = _PhaseState(title=title, total=total)
        if parent is None:
            self._root_phases.append(state)
        else:
            parent.children.append(state)
        self._stack.append(state)
        self._on_phase_start(state, parent)
        try:
            yield PhaseHandle(self, state)
        except Exception as e:
            state.finished_at = time.time()
            self._on_phase_error(state, e)
            raise
        else:
            state.finished_at = time.time()
            self._on_phase_done(state)
        finally:
            self._stack.pop()

    # --- Hooks (subclassed) -------------------------------------------------
    def _on_phase_start(self, state: _PhaseState, parent: Optional[_PhaseState]) -> None:
        pass

    def _on_phase_update(self, state: _PhaseState) -> None:
        pass

    def _on_phase_note(self, state: _PhaseState, text: str) -> None:
        pass

    def _on_phase_done(self, state: _PhaseState) -> None:
        pass

    def _on_phase_error(self, state: _PhaseState, err: BaseException) -> None:
        pass

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# TerminalReporter — Rich Live-Tree
# ---------------------------------------------------------------------------
class TerminalReporter(ProgressReporter):
    """Live tree in the terminal. Active phase with spinner, subtasks
    indented, finished phases marked with ✓ + duration."""

    def __init__(self):
        super().__init__()
        from rich.console import Console
        from rich.live import Live
        from rich.tree import Tree
        from rich.spinner import Spinner
        self._Console = Console
        self._Live = Live
        self._Tree = Tree
        self._Spinner = Spinner
        self._console = Console()
        self._live: Optional[Live] = None

    def _ensure_live(self) -> None:
        if self._live is None:
            self._live = self._Live(self._render(), console=self._console,
                                    refresh_per_second=10, transient=False)
            self._live.start()

    def _render(self):
        Tree = self._Tree
        Spinner = self._Spinner
        root = Tree("[bold]Message Visualizer · Import[/bold]")
        for st in self._root_phases:
            self._render_phase(root, st)
        return root

    def _phase_label(self, st: _PhaseState) -> str:
        # Status-Symbol + Titel + Counter/Dauer + letzte Note
        if st.finished_at is not None:
            dur = st.finished_at - st.started_at
            cnt = ""
            if st.total is not None:
                cnt = f" [{st.current}/{st.total}]"
            elif st.current:
                cnt = f" [{st.current}]"
            line = f"[green]✓[/green] {st.title}{cnt} [dim]({dur:.1f}s)[/dim]"
        else:
            cnt = ""
            if st.total is not None and st.total > 0:
                pct = (st.current / st.total) * 100
                cnt = f" [{st.current}/{st.total} · {pct:.0f}%]"
            elif st.current:
                cnt = f" [{st.current}]"
            line = f"[cyan]●[/cyan] {st.title}{cnt}"
        if st.notes:
            line += f"  [dim]{st.notes[-1]}[/dim]"
        return line

    def _render_phase(self, parent_tree, st: _PhaseState) -> None:
        node = parent_tree.add(self._phase_label(st))
        for ch in st.children:
            self._render_phase(node, ch)

    def _refresh(self) -> None:
        if self._live is not None:
            self._live.update(self._render())

    def _on_phase_start(self, state, parent):
        self._ensure_live()
        self._refresh()

    def _on_phase_update(self, state):
        self._refresh()

    def _on_phase_note(self, state, text):
        self._refresh()

    def _on_phase_done(self, state):
        self._refresh()

    def _on_phase_error(self, state, err):
        self._refresh()

    def close(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None


# ---------------------------------------------------------------------------
# EventReporter — JSONL stream for background/web calls
# ---------------------------------------------------------------------------
class EventReporter(ProgressReporter):
    """Writes every phase change as a JSON line into a file.
    Per line: {ts, kind, path, ...}.

    Optionally an on_event callback (e.g. a WebSocket push) is invoked
    for every event.
    """

    def __init__(self, events_path: str | Path,
                 on_event: Optional[callable] = None):
        super().__init__()
        self.events_path = Path(events_path)
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.events_path, "a", encoding="utf-8")
        self._on_event = on_event
        self._phase_paths: dict[int, list[str]] = {}

    def _path_of(self, state: _PhaseState) -> list[str]:
        # Reconstruct ID path from the current stack. Simpler: title path.
        # The stack may no longer contain the phase (e.g. _on_phase_done
        # after pop), so we use the stored path.
        return self._phase_paths.get(id(state), [state.title])

    def _emit(self, kind: str, state: _PhaseState, **extra) -> None:
        rec = {
            "ts": time.time(),
            "kind": kind,
            "path": self._path_of(state),
            "title": state.title,
            "current": state.current,
            "total": state.total,
            **extra,
        }
        if kind in ("phase_done", "phase_error"):
            rec["duration"] = (state.finished_at or time.time()) - state.started_at
        line = json.dumps(rec, ensure_ascii=False)
        self._file.write(line + "\n")
        self._file.flush()
        if self._on_event:
            try:
                self._on_event(rec)
            except Exception:
                pass

    def _on_phase_start(self, state, parent):
        parent_path = self._phase_paths.get(id(parent), []) if parent else []
        self._phase_paths[id(state)] = parent_path + [state.title]
        self._emit("phase_start", state)

    def _on_phase_update(self, state):
        self._emit("phase_update", state)

    def _on_phase_note(self, state, text):
        self._emit("phase_note", state, note=text)

    def _on_phase_done(self, state):
        self._emit("phase_done", state)

    def _on_phase_error(self, state, err):
        self._emit("phase_error", state, error=str(err))

    def close(self) -> None:
        try:
            self._file.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Null-Reporter (Default, kein Output)
# ---------------------------------------------------------------------------
class NullReporter(ProgressReporter):
    pass


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def make_reporter(kind: str = "terminal",
                  events_path: Optional[str] = None) -> ProgressReporter:
    """Erzeugt einen Reporter. kind ∈ {'terminal','events','null'}.
    Bei kind='events' MUSS events_path gesetzt sein."""
    if kind == "terminal":
        return TerminalReporter()
    if kind == "events":
        if not events_path:
            raise ValueError("EventReporter needs events_path")
        return EventReporter(events_path)
    if kind == "null":
        return NullReporter()
    raise ValueError(f"unknown reporter kind: {kind}")
