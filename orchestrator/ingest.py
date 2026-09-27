"""
orchestrator.ingest — check code that is not a commit in this repository.

Three inputs, no git needed from the user:
    from_scan(folder)            the whole folder, reviewed as if every file were new
    from_folders(before, after)  two copies of the code, before and after a change
    from_patch(folder, patch)    a folder plus a .patch/.diff file describing the change

Each one becomes the same thing: a throwaway git repository under
runs/workspaces/ with tag `base` (before) and tag `candidate` (after), the code
placed under `project/`. The existing pipeline then runs on it unchanged.

Secrets, binaries and files over 200 KB are never copied, so they never reach the AI.
"""

from __future__ import annotations

import datetime
import os
import pathlib
import shutil
import subprocess

from orchestrator.context import _is_safe_excluded

SCOPE = "project"
REPORT_NAME = "PRISM-REPORT.md"
MAX_SCAN_BYTES = int(os.environ.get("PRISM_MAX_SCAN_BYTES") or 5 * 1024 * 1024)
# Above this, even the two-pass review (map + summaries) gets slow and costly.

# Folders and files that are never part of the code under review.
_SKIP = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", "__pycache__",
         ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", "runs", ".idea", ".vscode",
         REPORT_NAME,   # never feed an old report back to the AI
         ".prism"}      # PRism-AI's own memory (summaries, project map)

_GIT_ID = ["-c", "user.name=PRism-AI", "-c", "user.email=prism@localhost",
           "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false"]


class IngestError(Exception):
    """The input could not be turned into a workspace."""


def _git(*args: str, cwd: pathlib.Path) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    proc = subprocess.run(["git", *_GIT_ID, *args], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)
    if proc.returncode != 0:
        raise IngestError(f"git {args[0]} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout.strip()


def _excluded(path: pathlib.Path) -> bool:
    if path.name in _SKIP:
        return True
    return path.is_file() and _is_safe_excluded(path.name, path.stat().st_size)[0]


def _copy_tree(src: pathlib.Path, dst: pathlib.Path) -> None:
    shutil.copytree(src, dst, dirs_exist_ok=True,
                    ignore=lambda d, names: [n for n in names if _excluded(pathlib.Path(d) / n)])


def _included_files(src: pathlib.Path) -> list[pathlib.Path]:
    files = []
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if not _excluded(pathlib.Path(dirpath) / d)]
        files += [pathlib.Path(dirpath) / f for f in filenames
                  if not _excluded(pathlib.Path(dirpath) / f)]
    return files


def _remove(path: pathlib.Path) -> None:
    """rmtree that also removes git's read-only object files on Windows."""
    def clear_readonly(func, target, _exc):
        os.chmod(target, 0o700)
        func(target)
    shutil.rmtree(path, onerror=clear_readonly)


def _folder(path: str, what: str) -> pathlib.Path:
    folder = pathlib.Path(path).expanduser().resolve()
    if not folder.is_dir():
        raise IngestError(f"{what} {folder} is not a folder")
    return folder


def _workspace(runs: pathlib.Path, fill_base, fill_candidate, message: str) -> pathlib.Path:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    ws = runs / "workspaces" / f"ws-{stamp}"
    code = ws / SCOPE
    code.mkdir(parents=True)
    try:
        _git("init", "-q", cwd=ws)
        fill_base(code)
        _git("add", "-A", cwd=ws)
        _git("commit", "-q", "--allow-empty", "-m", "base", cwd=ws)
        _git("tag", "base", cwd=ws)
        shutil.rmtree(code)
        code.mkdir()
        fill_candidate(code)
        _git("add", "-A", cwd=ws)
        _git("commit", "-q", "--allow-empty", "-m", message, cwd=ws)
        _git("tag", "candidate", cwd=ws)
    except Exception:
        _remove(ws)
        raise
    return ws


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

def from_scan(runs: pathlib.Path, folder: str) -> pathlib.Path:
    """Review a whole folder as if every file were new."""
    src = _folder(folder, "scan folder")
    files = _included_files(src)
    if not files:
        raise IngestError(f"no reviewable files in {src}")
    total = sum(f.stat().st_size for f in files)
    if total > MAX_SCAN_BYTES:
        raise IngestError(f"{total // 1024} KB of code is too much for one scan "
                          f"(limit {MAX_SCAN_BYTES // 1024} KB) - scan a subfolder instead, "
                          "or raise PRISM_MAX_SCAN_BYTES")
    return _workspace(runs, lambda d: None, lambda d: _copy_tree(src, d),
                      f"full scan of {src.name}")


def from_folders(runs: pathlib.Path, before: str, after: str) -> pathlib.Path:
    """Two plain folders: the code before and after the change."""
    b, a = _folder(before, "--before"), _folder(after, "--after")
    return _workspace(runs, lambda d: _copy_tree(b, d), lambda d: _copy_tree(a, d),
                      f"changes from {b.name} to {a.name}")


def from_patch(runs: pathlib.Path, folder: str, patch: str) -> pathlib.Path:
    """A folder plus a .patch/.diff file (git diff or unified diff) describing the change."""
    src = _folder(folder, "--folder")
    diff = pathlib.Path(patch).expanduser().resolve()
    if not diff.is_file():
        raise IngestError(f"patch file {diff} not found")

    def apply(code: pathlib.Path) -> None:
        _copy_tree(src, code)
        # Run from the workspace root so the patch's paths land under project/.
        proc = subprocess.run(["git", "apply", "--whitespace=nowarn", "--recount",
                               f"--directory={code.name}", str(diff)],
                              cwd=code.parent, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            raise IngestError(f"patch does not apply to {src}: {proc.stderr.strip()[:300]}")

    return _workspace(runs, lambda d: _copy_tree(src, d), apply, f"patch {diff.name}")


def detect_project(code: pathlib.Path, test_command: str | None = None) -> dict:
    """Project settings for the workspace: pytest for Python code unless told otherwise."""
    py_files = [p for p in code.rglob("*.py")]
    is_fastapi = any("fastapi" in p.read_text(encoding="utf-8", errors="ignore").lower()
                     for p in py_files)
    return {
        "scopePath": SCOPE,
        "testCommand": test_command or ("python -m pytest -q" if py_files
                                        else "python -c \"print('no test command set')\""),
        "testWorkingDirectory": SCOPE,
        "language": "Python" if py_files else "Unknown",
        "framework": "FastAPI" if is_fastapi else "Unknown",
    }
