"""
orchestrator.memory — what PRism-AI remembers about a project between checks.

Stored in <project>/.prism/ (never scanned, never sent to the AI):

    summaries.json   M1  content-hash -> AI summary of that file (purpose, risk)
    last-check.json  L6  fingerprint of every file + settings at the last check,
                         and where that check's report is

Summaries are keyed by the file's content hash, so a file that did not change is
never summarised twice, even if it moves or the project is copied.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import tempfile
import threading
from typing import Any

MEMORY_DIR = ".prism"
_VERSION = 1


def file_hash(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_write(path: pathlib.Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def _read(path: pathlib.Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("version") == _VERSION else {}
    except (OSError, ValueError):
        return {}  # missing or corrupt memory is simply rebuilt


class Memory:
    """Summary cache + last-check record for one project folder. Thread-safe."""

    def __init__(self, project: pathlib.Path | None):
        self.dir = (project / MEMORY_DIR) if project else None
        self._lock = threading.Lock()
        self._summaries: dict[str, dict] = {}
        self._dirty = False
        if self.dir:
            self._summaries = _read(self.dir / "summaries.json").get("files", {})

    # --- M1: summaries ------------------------------------------------------
    def summary(self, content_hash: str) -> dict | None:
        with self._lock:
            return self._summaries.get(content_hash)

    def remember_summary(self, content_hash: str, summary: dict) -> None:
        with self._lock:
            self._summaries[content_hash] = summary
            self._dirty = True

    def save(self) -> None:
        if not self.dir or not self._dirty:
            return
        with self._lock:
            _atomic_write(self.dir / "summaries.json",
                          {"version": _VERSION, "files": self._summaries})
            self._dirty = False

    # --- L6: what changed since the last check ------------------------------
    def last_check(self) -> dict:
        return _read(self.dir / "last-check.json") if self.dir else {}

    def changed_since_last(self, hashes: dict[str, str]) -> set[str]:
        """Files that are new or different since the last check (all files the first time)."""
        before = self.last_check().get("files", {})
        return {rel for rel, h in hashes.items() if before.get(rel) != h}

    def reusable_report(self, fingerprint: str) -> pathlib.Path | None:
        """The previous report, if nothing (code or settings) changed since it was made."""
        last = self.last_check()
        report = pathlib.Path(last.get("report", ""))
        if last.get("fingerprint") == fingerprint and report.is_file():
            return report
        return None

    def remember_check(self, hashes: dict[str, str], fingerprint: str,
                       report: pathlib.Path) -> None:
        if self.dir:
            _atomic_write(self.dir / "last-check.json",
                          {"version": _VERSION, "fingerprint": fingerprint,
                           "report": str(report), "files": hashes})


def fingerprint(hashes: dict[str, str], *settings: str) -> str:
    """One hash for 'these exact files, checked with these exact settings'."""
    h = hashlib.sha256()
    for rel in sorted(hashes):
        h.update(f"{rel}\0{hashes[rel]}\n".encode())
    for s in settings:
        h.update(f"setting\0{s}\n".encode())
    return h.hexdigest()
