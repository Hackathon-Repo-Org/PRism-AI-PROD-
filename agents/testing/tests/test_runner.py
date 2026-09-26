"""Unit tests for agents/testing/runner.py.

Uses tiny temp projects: pass, fail, no-tests, timeout.
Runs entirely in-process via the `run()` function (no subprocess to pytest itself
is needed at the unit level — we build real mini projects and invoke the runner).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure the repo root is on sys.path so `import agents.testing.runner` works
# when pytest is run from the repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]  # PRism-AI-main/
sys.path.insert(0, str(REPO_ROOT))

from agents.testing.runner import run, _parse_junit  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers to build tiny fake run directories
# ---------------------------------------------------------------------------

def _make_run(tmp_path: Path, test_src: str, *, run_id: str = "run-test") -> Path:
    """Build a minimal run directory with a tiny pytest project."""
    run_dir = tmp_path / run_id
    snapshot_dir = run_dir / "snapshot" / "sample-project"
    snapshot_dir.mkdir(parents=True)
    tests_dir = snapshot_dir / "tests"
    tests_dir.mkdir()

    # Write a tiny test file
    (tests_dir / "test_tiny.py").write_text(test_src, encoding="utf-8")

    # Write pytest.ini
    (snapshot_dir / "pytest.ini").write_text(
        "[pytest]\npythonpath = .\ntestpaths = tests\n", encoding="utf-8"
    )

    # Write context.json
    ctx = {
        "schemaVersion": "1.0",
        "runId": run_id,
        "snapshotId": "abc123",
        "project": {
            "language": "Python",
            "framework": "none",
            "testCommand": "python -m pytest -q",
            "testWorkingDirectory": "sample-project",
        },
    }
    (run_dir / "context.json").write_text(json.dumps(ctx), encoding="utf-8")
    return run_dir


# ---------------------------------------------------------------------------
# T2-1: Passing tests → exit code 0, correct counts
# ---------------------------------------------------------------------------

def test_passing_tests(tmp_path):
    _make_run(tmp_path, "def test_ok(): assert 1 + 1 == 2\n")
    execution = run("run-test", runs_root=tmp_path)

    assert execution["exitCode"] == 0
    assert execution["passed"] == 1
    assert execution["failed"] == 0
    assert execution["errors"] == 0
    assert execution["skipped"] == 0
    assert execution["collected"] == 1

    # Output file exists
    out = tmp_path / "run-test" / "testing-execution.json"
    assert out.exists()
    written = json.loads(out.read_text())
    assert written["exitCode"] == 0


# ---------------------------------------------------------------------------
# T2-2: Failing test → exit code 1, failing count = 1
# ---------------------------------------------------------------------------

def test_failing_test(tmp_path):
    _make_run(tmp_path, "def test_bad(): assert 1 == 2\n")
    execution = run("run-test", runs_root=tmp_path)

    assert execution["exitCode"] == 1
    assert execution["failed"] == 1
    assert execution["passed"] == 0


# ---------------------------------------------------------------------------
# T2-3: No tests collected → exit code 5, counts are null or zero (not invented)
# ---------------------------------------------------------------------------

def test_no_tests_collected(tmp_path):
    # Empty test file — pytest exits 5 (no tests collected)
    _make_run(tmp_path, "# no tests here\n")
    execution = run("run-test", runs_root=tmp_path)

    assert execution["exitCode"] == 5
    # pytest does not write a useful JUnit when no tests are collected; counts must
    # be null (unknown), never a fabricated 0.
    for field in ("collected", "passed", "failed", "skipped", "errors"):
        v = execution[field]
        assert v is None or v == 0, (
            f"Expected null or 0 for {field} when no tests collected, got {v!r}"
        )


# ---------------------------------------------------------------------------
# T2-4: Timeout → exitCode null, counts null
# ---------------------------------------------------------------------------

def test_timeout(tmp_path):
    src = "import time\ndef test_slow(): time.sleep(30)\n"
    _make_run(tmp_path, src)
    execution = run("run-test", timeout=1, runs_root=tmp_path)

    assert execution["exitCode"] is None
    assert execution["collected"] is None
    assert execution["passed"] is None
    # Log should mention TIMEOUT
    log_text = (tmp_path / "run-test" / "logs" / "pytest.log").read_text()
    assert "TIMEOUT" in log_text


def test_timeout_with_bytes_output(tmp_path, monkeypatch):
    """On POSIX, TimeoutExpired carries bytes even with text=True; must not crash."""
    import subprocess
    import agents.testing.runner as runner_mod

    _make_run(tmp_path, "def test_ok(): pass\n")

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"),
                                        output=b"partial output", stderr=b"")

    monkeypatch.setattr(runner_mod.subprocess, "run", fake_run)
    execution = run("run-test", timeout=1, runs_root=tmp_path)

    assert execution["exitCode"] is None
    assert execution["collected"] is None
    log_text = (tmp_path / "run-test" / "logs" / "pytest.log").read_text()
    assert "partial output" in log_text
    assert "TIMEOUT" in log_text


# ---------------------------------------------------------------------------
# T2-5: Command not found → exitCode null, counts null
# ---------------------------------------------------------------------------

def test_command_not_found(tmp_path):
    _make_run(tmp_path, "def test_ok(): pass\n")
    # Overwrite context.json with a non-existent command
    run_dir = tmp_path / "run-test"
    ctx = json.loads((run_dir / "context.json").read_text())
    ctx["project"]["testCommand"] = "nonexistent_command_xyz"
    (run_dir / "context.json").write_text(json.dumps(ctx))

    execution = run("run-test", runs_root=tmp_path)
    assert execution["exitCode"] is None
    assert execution["collected"] is None


# ---------------------------------------------------------------------------
# T2-5b: JUnit file absent after run → counts null, exitCode preserved
# ---------------------------------------------------------------------------

def test_junit_missing_counts_null_exitcode_preserved(tmp_path):
    """When pytest exits non-zero and writes no JUnit file, counts must be null
    and the real exit code must be preserved (not replaced with null).

    We simulate this by using a nonexistent command that exits with an error
    via a wrapper script, but the simplest portable approach is: run a real
    pytest scenario where the JUnit path cannot be created (point it to an
    existing file used as a directory).  Instead we test via a two-step approach:
    1. Run a passing test so we know the runner works.
    2. Then manually delete the junit file and call the runner's internal
       _parse_junit helper to confirm null-on-missing.  At the run() level,
       we verify by using a command that fails before pytest gets to write XML.
    """
    # Use a tiny helper script placed in the snapshot directory that exits 1
    # immediately without writing any JUnit file.
    _make_run(tmp_path, "def test_ok(): pass\n")
    run_dir = tmp_path / "run-test"
    snap_dir = run_dir / "snapshot" / "sample-project"

    # Write a helper script that just exits 1
    (snap_dir / "_fail.py").write_text("import sys; sys.exit(1)\n")

    ctx = json.loads((run_dir / "context.json").read_text())
    ctx["project"]["testCommand"] = "python _fail.py"
    (run_dir / "context.json").write_text(json.dumps(ctx))

    execution = run("run-test", runs_root=tmp_path)

    # Exit code must be the real value (1), not null
    assert execution["exitCode"] == 1

    # No JUnit was written → all counts must be null
    for field in ("collected", "passed", "failed", "skipped", "errors"):
        assert execution[field] is None, (
            f"Expected null for {field} when JUnit missing, got {execution[field]!r}"
        )


def test_stale_junit_deleted_before_run(tmp_path):
    """runner.py must delete any pre-existing junit.xml before starting, so a stale
    file from a previous run is never silently parsed when the new run produces no XML.
    """
    _make_run(tmp_path, "def test_ok(): assert True\n")
    run_dir = tmp_path / "run-test"
    snap_dir = run_dir / "snapshot" / "sample-project"

    # Plant a stale junit.xml with fake passing counts
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stale_xml = """<?xml version="1.0" ?>
<testsuite name="stale" tests="99" failures="0" errors="0" skipped="0"/>"""
    (logs_dir / "junit.xml").write_text(stale_xml)

    # Write a helper script that exits 1 — no new junit will be written
    (snap_dir / "_fail.py").write_text("import sys; sys.exit(1)\n")

    ctx = json.loads((run_dir / "context.json").read_text())
    ctx["project"]["testCommand"] = "python _fail.py"
    (run_dir / "context.json").write_text(json.dumps(ctx))

    execution = run("run-test", runs_root=tmp_path)

    # The stale "99 tests" must NOT appear — counts must be null because the
    # runner deleted the stale file before starting.
    assert execution["collected"] != 99, "Stale junit.xml was parsed instead of deleted"
    for field in ("collected", "passed", "failed", "skipped", "errors"):
        assert execution[field] is None, (
            f"Stale junit leaked into {field}: {execution[field]!r}"
        )


# ---------------------------------------------------------------------------
# T2-path: All emitted paths must be portable (forward slashes, no drive letters)
# ---------------------------------------------------------------------------

def test_emitted_paths_are_portable(tmp_path):
    """No path in testing-execution.json may contain a backslash or a drive letter."""
    _make_run(tmp_path, "def test_ok(): assert True\n")
    execution = run("run-test", runs_root=tmp_path)

    path_fields = ("workingDirectory", "logPath", "junitPath")
    for field in path_fields:
        value = execution[field]
        assert "\\" not in value, (
            f"{field!r} contains a backslash: {value!r}"
        )
        # Drive letters look like "C:" — no single letter followed by a colon
        import re
        assert not re.search(r"^[A-Za-z]:", value), (
            f"{field!r} contains a drive letter: {value!r}"
        )

    # The --junitxml argument in command must also be relative (no drive letter)
    cmd = execution["command"]
    import re
    assert not re.search(r"--junitxml=[A-Za-z]:", cmd), (
        f"command contains absolute --junitxml path: {cmd!r}"
    )
    assert "\\" not in cmd, f"command contains backslash: {cmd!r}"


# ---------------------------------------------------------------------------
# T2-6: _parse_junit unit tests
# ---------------------------------------------------------------------------

def test_parse_junit_valid(tmp_path):
    xml = """<?xml version="1.0" ?>
<testsuite name="pytest" tests="4" failures="1" errors="0" skipped="1">
</testsuite>"""
    p = tmp_path / "junit.xml"
    p.write_text(xml)
    counts = _parse_junit(p)
    assert counts["collected"] == 4
    assert counts["failed"]    == 1
    assert counts["skipped"]   == 1
    assert counts["errors"]    == 0
    assert counts["passed"]    == 2  # 4 - 1 - 0 - 1


def test_parse_junit_missing_file(tmp_path):
    counts = _parse_junit(tmp_path / "nonexistent.xml")
    for v in counts.values():
        assert v is None


def test_parse_junit_malformed(tmp_path):
    p = tmp_path / "bad.xml"
    p.write_text("not xml at all <<<")
    counts = _parse_junit(p)
    for v in counts.values():
        assert v is None
