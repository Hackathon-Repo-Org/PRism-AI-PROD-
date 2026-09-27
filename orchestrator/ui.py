"""
orchestrator.ui — live progress while PRism-AI works.

In a real terminal (and with the `rich` package installed) it shows an animated panel:

    🔮 PRism-AI  checking scan of my-project
      ✓ [1/4] Preparing code                         0.4s
      ⠹ [2/4] Reviewing: 3 specialists in parallel  12s
          🔍 code-review     ⠹ reading the code…      12s
          🧪 testing         ✓ completed               7s
          📚 documentation   ⠹ comparing docs…         12s
      · [3/4] Deciding
      · [4/4] Writing report

Anywhere else (redirected to a file, CI, watch logs, PRISM_PLAIN=1, NO_COLOR) it
prints the same steps as plain lines, so logs stay readable.
"""

from __future__ import annotations

import os
import sys
import threading
import time

VIOLET, PINK, GREEN, RED, DIM = "#a78bfa", "#f472b6", "#4ade80", "#f87171", "grey58"
AGENT_ICON = {"code-review": "🔍", "testing": "🧪", "documentation": "📚"}
AGENT_VERB = {"code-review": "reading the code", "testing": "running & reading tests",
              "documentation": "comparing docs with code"}
DONE_OK = {"completed"}


def _fancy_possible() -> bool:
    if os.environ.get("PRISM_PLAIN") or os.environ.get("NO_COLOR"):
        return False
    if not (hasattr(sys.stdout, "isatty") and sys.stdout.isatty()):
        return False
    try:
        from rich.console import Console
    except ImportError:
        return False
    # Old Windows consoles (conhost without VT support) cannot draw emoji, boxes or
    # live updates; plain lines are safer there.
    return not Console().legacy_windows


class Progress:
    """Thread-safe progress display. Use as a context manager."""

    def __init__(self, title: str, steps: list[str], fancy: bool | None = None):
        self.title = title
        self.steps = steps
        self.fancy = _fancy_possible() if fancy is None else fancy
        self._lock = threading.Lock()
        self._current = -1
        self._step_start: dict[int, float] = {}
        self._step_secs: dict[int, float] = {}
        self._detail = ""
        self._agents: dict[str, dict] = {}
        self._live = None

    # --- lifecycle ----------------------------------------------------------
    def __enter__(self) -> "Progress":
        if self.fancy:
            from rich.console import Console
            from rich.live import Live
            self._console = Console(highlight=False)
            self._live = Live(get_renderable=self._render, console=self._console,
                              refresh_per_second=10, transient=False)
            self._live.start()
        else:
            print(f"PRism-AI  {self.title}")
        return self

    def __exit__(self, *exc) -> None:
        with self._lock:
            if 0 <= self._current < len(self.steps) and self._current not in self._step_secs:
                self._step_secs[self._current] = time.monotonic() - self._step_start[self._current]
        if self._live:
            self._live.stop()

    # --- events -------------------------------------------------------------
    def step(self, index: int, detail: str = "") -> None:
        """Start step `index` (0-based); the previous step is marked done."""
        with self._lock:
            now = time.monotonic()
            if 0 <= self._current != index and self._current not in self._step_secs:
                self._step_secs[self._current] = now - self._step_start[self._current]
            self._current = index
            self._step_start[index] = now
            self._detail = detail
        if not self.fancy:
            print(f"[{index + 1}/{len(self.steps)}] {self.steps[index]}"
                  + (f" - {detail}" if detail else "") + "...", flush=True)

    def detail(self, text: str) -> None:
        """Update the current step's detail, e.g. 'summarising 12/40 files'."""
        with self._lock:
            changed = text != self._detail
            self._detail = text
        if not self.fancy and changed and text:
            print(f"      {text}", flush=True)

    def note(self, text: str) -> None:
        """A permanent line above the live panel (or a plain print)."""
        if self._live:
            self._console.print(f"[{DIM}]{text}[/]")
        else:
            print(text, flush=True)

    def agent_start(self, agent: str) -> None:
        with self._lock:
            self._agents[agent] = {"start": time.monotonic(), "status": None, "secs": None}

    def agent_done(self, agent: str, status: str) -> None:
        with self._lock:
            a = self._agents.setdefault(agent, {"start": time.monotonic()})
            a["status"] = status
            a["secs"] = time.monotonic() - a["start"]
        if not self.fancy:
            print(f"      {agent:<14} {status:<10} {a['secs']:.0f}s", flush=True)

    # --- drawing ------------------------------------------------------------
    def _render(self):
        from rich.padding import Padding
        from rich.spinner import Spinner
        from rich.table import Table
        from rich.text import Text

        with self._lock:
            grid = Table.grid(padding=(0, 1))
            grid.add_column(width=2)
            grid.add_column()
            grid.add_column(justify="right", style=DIM)
            grid.add_row(Text("🔮"), Text.assemble(("PRism-AI  ", f"bold {VIOLET}"),
                                                    (self.title, PINK)), "")
            now = time.monotonic()
            for i, name in enumerate(self.steps):
                label = f"[{i + 1}/{len(self.steps)}] {name}"
                if i in self._step_secs:
                    grid.add_row(Text("✓", GREEN), Text(label), f"{self._step_secs[i]:.1f}s")
                elif i == self._current:
                    body = Text.assemble((label, f"bold {VIOLET}"),
                                         (f"  {self._detail}" if self._detail else "", PINK))
                    grid.add_row(Spinner("dots", style=PINK), body,
                                 f"{now - self._step_start[i]:.0f}s")
                else:
                    grid.add_row(Text("·", DIM), Text(label, DIM), "")
                if name.startswith("Reviewing") and self._agents and (
                        i == self._current or i in self._step_secs):
                    for agent, a in self._agents.items():
                        icon = AGENT_ICON.get(agent, "•")
                        if a.get("status") is None:
                            words = Text(f"{icon} {agent:<14} "
                                         f"{AGENT_VERB.get(agent, 'working')}…", VIOLET)
                            cell = Padding(Spinner("dots", text=words, style=VIOLET), (0, 0, 0, 3))
                            secs = f"{now - a['start']:.0f}s"
                        else:
                            ok = a["status"] in DONE_OK
                            cell = Text.assemble(("   ✓ " if ok else "   ✗ ", GREEN if ok else RED),
                                                 f"{icon} {agent:<14} ",
                                                 (a["status"], "default" if ok else RED))
                            secs = f"{a['secs']:.0f}s"
                        grid.add_row("", cell, secs)
            return grid
