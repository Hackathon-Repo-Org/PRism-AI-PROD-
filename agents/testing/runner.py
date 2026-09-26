"""Test runner for PRism-AI — deterministic, no AI.

Usage:
    python agents/testing/runner.py --run <runId> [--timeout 300]

Reads runs/<runId>/context.json, executes the project test command inside
runs/<runId>/snapshot/<testWorkingDirectory>, captures output and JUnit XML,
and writes runs/<runId>/testing-execution.json (A6.4 shape).

Path portability (A6.4):
  - All paths stored in testing-execution.json use forward slashes and are
    repo-relative (e.g. "runs/<runId>/logs/junit.xml"), never absolute.
  - The --junitxml argument passed to pytest is expressed as a relative path
    from the test working directory (e.g. "../../logs/junit.xml").
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Optional
import xml.etree.ElementTree as ET


def _to_fwd(path: Path, relative_to: Path) -> str:
    """Return *path* as a forward-slash string relative to *relative_to*.

    Uses os.path.relpath so it works even when path is not under relative_to,
    then converts the OS-native separators to forward slashes via Path.as_posix().
    Example: _to_fwd(Path('/a/b/c'), Path('/a')) -> 'b/c'
    """
    return Path(os.path.relpath(path.resolve(), relative_to.resolve())).as_posix()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _as_text(value: Any) -> str:
    """Captured output as str; TimeoutExpired carries bytes on POSIX even with text=True."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _parse_junit(junit_path: Path) -> Dict[str, Optional[int]]:
    """Parse a JUnit XML file and return collected/passed/failed/skipped/errors.

    Returns all-None dict on any parse error.
    """
    null_counts: Dict[str, Optional[int]] = {
        "collected": None, "passed": None,
        "failed": None, "skipped": None, "errors": None,
    }
    try:
        tree = ET.parse(junit_path)
        root = tree.getroot()
        # JUnit XML produced by pytest: <testsuite tests="..." failures="..." errors="..." skipped="...">
        suite = root if root.tag == "testsuite" else root.find("testsuite")
        if suite is None:
            return null_counts
        tests   = int(suite.attrib.get("tests",    0))
        failed  = int(suite.attrib.get("failures", 0))
        errors  = int(suite.attrib.get("errors",   0))
        skipped = int(suite.attrib.get("skipped",  0))
        passed  = tests - failed - errors - skipped
        return {
            "collected": tests,
            "passed":    max(0, passed),
            "failed":    failed,
            "skipped":   skipped,
            "errors":    errors,
        }
    except Exception:
        return null_counts


def _write_execution(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main runner logic
# ---------------------------------------------------------------------------

def run(run_id: str, timeout: int = 300, runs_root: Path = Path("runs")) -> Dict[str, Any]:
    """Execute tests for a run and return the execution dict (A6.4 shape).

    All paths in the returned dict are repo-relative with forward slashes (A6.4).
    The --junitxml argument is expressed relative to the test working directory.
    """
    # Resolve runs_root to an absolute path so all relative computations are stable.
    runs_root  = runs_root.resolve()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", run_id or ""):
        raise ValueError(f"invalid run id {run_id!r}")
    run_dir    = runs_root / run_id
    ctx_path   = run_dir / "context.json"
    logs_dir   = run_dir / "logs"
    log_path   = logs_dir / "pytest.log"
    junit_path = logs_dir / "junit.xml"
    exec_path  = run_dir / "testing-execution.json"

    # Repo root = the parent of runs_root (i.e. the directory that contains "runs/")
    repo_root = runs_root.parent

    logs_dir.mkdir(parents=True, exist_ok=True)

    # Delete any pre-existing junit.xml so a stale file is never parsed on failure.
    if junit_path.exists():
        junit_path.unlink()

    # Read context.json (use utf-8-sig to tolerate a BOM if present)
    ctx = json.loads(ctx_path.read_text(encoding="utf-8-sig"))
    project      = ctx.get("project", {})
    test_cmd     = project.get("testCommand", "python -m pytest -q")
    test_cwd_rel = project.get("testWorkingDirectory", "sample-project")

    snapshot_dir = run_dir / "snapshot"
    work_dir     = snapshot_dir / test_cwd_rel

    # --junitxml: path relative to work_dir, forward slashes only (no drive letters)
    junit_rel = Path(
        os.path.relpath(junit_path.resolve(), work_dir.resolve())
    ).as_posix()

    base_args = test_cmd.split()
    cmd = base_args + [f"--junitxml={junit_rel}"]

    # Repo-relative forward-slash paths for the execution record
    command_str      = " ".join(cmd)
    working_dir_str  = _to_fwd(work_dir,  repo_root)
    log_path_str     = _to_fwd(log_path,  repo_root)
    junit_path_str   = _to_fwd(junit_path, repo_root)

    started_at = _now_iso()
    start_ts   = time.monotonic()
    exit_code: Optional[int] = None
    timed_out       = False
    could_not_start = False

    try:
        result = subprocess.run(
            cmd,
            cwd=work_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
        exit_code  = result.returncode
        log_content = result.stdout + result.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out   = True
        log_content = _as_text(exc.stdout) + _as_text(exc.stderr) + "\nTIMEOUT\n"
    except FileNotFoundError as exc:
        could_not_start = True
        log_content = f"COMMAND NOT FOUND: {exc}\n"
    except Exception as exc:
        could_not_start = True
        log_content = f"FAILED TO START: {exc}\n"

    finished_at = _now_iso()
    duration_ms = int((time.monotonic() - start_ts) * 1000)

    # Write log
    log_path.write_text(log_content, encoding="utf-8")

    # Parse JUnit — null counts when runner could not produce the file
    if timed_out or could_not_start or not junit_path.exists():
        counts: Dict[str, Optional[int]] = {
            "collected": None, "passed": None,
            "failed": None, "skipped": None, "errors": None,
        }
    else:
        counts = _parse_junit(junit_path)

    execution: Dict[str, Any] = {
        "command":          command_str,
        "workingDirectory": working_dir_str,
        "exitCode":         exit_code,   # None (→ null) on timeout or start failure
        "logPath":          log_path_str,
        "junitPath":        junit_path_str,
        **counts,
        "startedAt":  started_at,
        "finishedAt": finished_at,
        "durationMs": duration_ms,
    }

    _write_execution(exec_path, execution)
    return execution


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _main() -> None:
    parser = argparse.ArgumentParser(description="PRism-AI test runner")
    parser.add_argument("--run",     required=True, help="Run ID, e.g. run-20260926-101500-a1b2c3d")
    parser.add_argument("--timeout", type=int, default=300, help="Subprocess timeout in seconds")
    parser.add_argument("--runs-root", default="runs", help="Directory containing run folders")
    args = parser.parse_args()

    execution = run(
        run_id=args.run,
        timeout=args.timeout,
        runs_root=Path(args.runs_root),
    )
    print(json.dumps(execution, indent=2))
    # Mirror pytest's exit code so callers can check it
    code = execution.get("exitCode")
    sys.exit(code if isinstance(code, int) else 1)


if __name__ == "__main__":
    _main()
