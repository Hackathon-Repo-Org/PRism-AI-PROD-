"""
T4 tests: orchestrator.agent_io

Acceptance criteria (from spec):
- begin writes <agent>.started.json with startedAt
- finish (valid findings) → <agent>-result.json with correct stamps
- finish (invalid findings, first attempt) → NEEDS_REPAIR, no result file written
- finish (invalid findings, repair attempt) → ERROR, error result written with
  status "error" and statusReason starting with "SCHEMA_INVALID:"
- finish (valid findings after one NEEDS_REPAIR) → OK (the repair path)
- No .tmp files left after any successful write
- Missing context.json → ERROR immediately
- begin not called → finish still works (startedAt falls back gracefully)
- --status / --reason override applied correctly
- durationMs is non-negative integer
- runId and snapshotId are stamped from context, not from findings file
- CLI: begin exits 0, finish exits 0/2/1 correctly
"""

import json
import os
import pathlib
import sys

import pytest

_REPO = pathlib.Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO))

from orchestrator.agent_io import (
    begin,
    finish,
    FinishResult,
    FinishStatus,
    _main,
)

# ── shared test data ─────────────────────────────────────────────────────────

_RUN_ID    = "run-20260926-101500-a1b2c3d"
_SNAPSHOT  = "aabbccdd11223344556677889900aabbccdd1122"
_BASE_SHA  = "0011223344556677889900aabbccdd1122334455"

_CONTEXT = {
    "schemaVersion": "1.0",
    "runId": _RUN_ID,
    "snapshotId": _SNAPSHOT,
    "baseCommit": _BASE_SHA,
    "baseRef": "baseline-clean",
    "candidateRef": "demo-bad",
    "intent": "test intent",
    "scopePath": "sample-project",
    "snapshotDir": f"runs/{_RUN_ID}/snapshot",
    "diffPath": f"runs/{_RUN_ID}/diff.patch",
    "project": {
        "language": "Python",
        "framework": "FastAPI",
        "testCommand": "python -m pytest -q",
        "testWorkingDirectory": "sample-project",
    },
    "changedFiles": [],
    "relevantSource": [],
    "relevantTests": [],
    "relevantDocs": [],
    "exclusions": [],
    "scopeLimitations": ["Only files under sample-project/ were analysed."],
    "createdAt": "2026-09-26T10:15:00.000Z",
}

_VALID_FINDINGS = {
    "findings": [
        {
            "id": "CODE-001",
            "key": "NULL_HANDLING|sample-project/app/service.py|create_item|priority",
            "severity": "HIGH",
            "category": "NULL_HANDLING",
            "title": "Null dereference before check",
            "file": "sample-project/app/service.py",
            "line": 42,
            "symbol": "create_item",
            "description": "Priority field is dereferenced before null check.",
            "evidence": "data.priority.lower()  # may be None",
            "evidenceType": "source-analysis",
            "recommendation": "Add null guard before dereference.",
            "relatedFiles": [],
        }
    ],
    "limitations": ["Third-party code not analysed."],
}

_INVALID_FINDINGS = {
    "findings": [
        {
            "id": "CODE-001",
            "key": "NULL_HANDLING|sample-project/app/service.py|create_item|priority",
            "severity": "BLOCKER",          # ← invalid: not in enum
            "category": "NULL_HANDLING",
            "title": "Null dereference",
            "file": "sample-project/app/service.py",
            "line": 42,
            "symbol": "create_item",
            "description": "desc",
            # ← missing: evidence, evidenceType, recommendation, relatedFiles
        }
    ],
    "limitations": [],
}

_REPAIRED_FINDINGS = {
    "findings": [
        {
            "id": "CODE-001",
            "key": "NULL_HANDLING|sample-project/app/service.py|create_item|priority",
            "severity": "HIGH",
            "category": "NULL_HANDLING",
            "title": "Null dereference",
            "file": "sample-project/app/service.py",
            "line": 42,
            "symbol": "create_item",
            "description": "desc",
            "evidence": "data.priority.lower()",
            "evidenceType": "source-analysis",
            "recommendation": "Add null guard.",
            "relatedFiles": [],
        }
    ],
    "limitations": [],
}


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def run_dir(tmp_path) -> pathlib.Path:
    """Create a minimal run directory with context.json."""
    rdir = tmp_path / _RUN_ID
    rdir.mkdir()
    (rdir / "context.json").write_text(
        json.dumps(_CONTEXT), encoding="utf-8"
    )
    return rdir


@pytest.fixture()
def runs_dir(run_dir) -> pathlib.Path:
    return run_dir.parent


def _write_findings(tmp_path: pathlib.Path, data: dict, name="findings.json") -> pathlib.Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


# ── begin ─────────────────────────────────────────────────────────────────────

class TestBegin:
    def test_writes_started_json(self, run_dir, runs_dir):
        path = begin(_RUN_ID, "code-review", runs_dir=runs_dir)
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["agent"] == "code-review"
        assert "startedAt" in data
        assert data["startedAt"].endswith("Z")

    def test_started_json_name(self, run_dir, runs_dir):
        path = begin(_RUN_ID, "testing", runs_dir=runs_dir)
        assert path.name == "testing.started.json"

    def test_no_tmp_leftover(self, run_dir, runs_dir):
        begin(_RUN_ID, "documentation", runs_dir=runs_dir)
        tmps = list(run_dir.glob("*.tmp"))
        assert tmps == []


# ── finish — valid findings ───────────────────────────────────────────────────

class TestFinishValid:
    def test_result_written(self, run_dir, runs_dir, tmp_path):
        begin(_RUN_ID, "code-review", runs_dir=runs_dir)
        fp = _write_findings(tmp_path, _VALID_FINDINGS)
        result = finish(_RUN_ID, "code-review", fp, runs_dir=runs_dir)
        assert result.status == FinishStatus.OK
        assert result.result_path is not None
        assert result.result_path.exists()
        assert result.result_path.name == "code-review-result.json"

    def test_stamps_schema_version(self, run_dir, runs_dir, tmp_path):
        begin(_RUN_ID, "code-review", runs_dir=runs_dir)
        fp = _write_findings(tmp_path, _VALID_FINDINGS)
        finish(_RUN_ID, "code-review", fp, runs_dir=runs_dir)
        data = json.loads((run_dir / "code-review-result.json").read_text())
        assert data["schemaVersion"] == "1.0"

    def test_stamps_run_id_from_context(self, run_dir, runs_dir, tmp_path):
        begin(_RUN_ID, "code-review", runs_dir=runs_dir)
        fp = _write_findings(tmp_path, _VALID_FINDINGS)
        finish(_RUN_ID, "code-review", fp, runs_dir=runs_dir)
        data = json.loads((run_dir / "code-review-result.json").read_text())
        assert data["runId"] == _RUN_ID

    def test_stamps_snapshot_id_from_context(self, run_dir, runs_dir, tmp_path):
        begin(_RUN_ID, "code-review", runs_dir=runs_dir)
        fp = _write_findings(tmp_path, _VALID_FINDINGS)
        finish(_RUN_ID, "code-review", fp, runs_dir=runs_dir)
        data = json.loads((run_dir / "code-review-result.json").read_text())
        assert data["snapshotId"] == _SNAPSHOT

    def test_stamps_started_at_from_begin(self, run_dir, runs_dir, tmp_path):
        begin(_RUN_ID, "code-review", runs_dir=runs_dir)
        started_data = json.loads((run_dir / "code-review.started.json").read_text())
        fp = _write_findings(tmp_path, _VALID_FINDINGS)
        finish(_RUN_ID, "code-review", fp, runs_dir=runs_dir)
        data = json.loads((run_dir / "code-review-result.json").read_text())
        assert data["startedAt"] == started_data["startedAt"]

    def test_duration_ms_non_negative(self, run_dir, runs_dir, tmp_path):
        begin(_RUN_ID, "code-review", runs_dir=runs_dir)
        fp = _write_findings(tmp_path, _VALID_FINDINGS)
        finish(_RUN_ID, "code-review", fp, runs_dir=runs_dir)
        data = json.loads((run_dir / "code-review-result.json").read_text())
        assert isinstance(data["durationMs"], int)
        assert data["durationMs"] >= 0

    def test_status_completed_and_null_reason(self, run_dir, runs_dir, tmp_path):
        begin(_RUN_ID, "code-review", runs_dir=runs_dir)
        fp = _write_findings(tmp_path, _VALID_FINDINGS)
        finish(_RUN_ID, "code-review", fp, runs_dir=runs_dir)
        data = json.loads((run_dir / "code-review-result.json").read_text())
        assert data["status"] == "completed"
        assert data["statusReason"] is None

    def test_findings_and_limitations_copied(self, run_dir, runs_dir, tmp_path):
        begin(_RUN_ID, "code-review", runs_dir=runs_dir)
        fp = _write_findings(tmp_path, _VALID_FINDINGS)
        finish(_RUN_ID, "code-review", fp, runs_dir=runs_dir)
        data = json.loads((run_dir / "code-review-result.json").read_text())
        assert len(data["findings"]) == 1
        assert data["findings"][0]["id"] == "CODE-001"
        assert data["limitations"] == ["Third-party code not analysed."]

    def test_no_tmp_leftover(self, run_dir, runs_dir, tmp_path):
        begin(_RUN_ID, "code-review", runs_dir=runs_dir)
        fp = _write_findings(tmp_path, _VALID_FINDINGS)
        finish(_RUN_ID, "code-review", fp, runs_dir=runs_dir)
        assert list(run_dir.glob("*.tmp")) == []

    def test_status_override_error(self, run_dir, runs_dir, tmp_path):
        begin(_RUN_ID, "code-review", runs_dir=runs_dir)
        # valid empty findings with error status + reason
        fp = _write_findings(tmp_path, {"findings": [], "limitations": []})
        result = finish(
            _RUN_ID, "code-review", fp,
            override_status="error",
            reason="Agent failed to start.",
            runs_dir=runs_dir)
        assert result.status == FinishStatus.OK
        data = json.loads((run_dir / "code-review-result.json").read_text())
        assert data["status"] == "error"
        assert data["statusReason"] == "Agent failed to start."

    def test_begin_not_called_still_works(self, run_dir, runs_dir, tmp_path):
        """finish without begin: startedAt falls back to now; result still written."""
        fp = _write_findings(tmp_path, {"findings": [], "limitations": []})
        result = finish(_RUN_ID, "code-review", fp, runs_dir=runs_dir)
        assert result.status == FinishStatus.OK


# ── finish — invalid findings, first attempt ─────────────────────────────────

class TestFinishInvalidFirst:
    def test_returns_needs_repair(self, run_dir, runs_dir, tmp_path):
        begin(_RUN_ID, "code-review", runs_dir=runs_dir)
        fp = _write_findings(tmp_path, _INVALID_FINDINGS)
        result = finish(_RUN_ID, "code-review", fp, runs_dir=runs_dir)
        assert result.status == FinishStatus.NEEDS_REPAIR

    def test_no_result_file_written(self, run_dir, runs_dir, tmp_path):
        begin(_RUN_ID, "code-review", runs_dir=runs_dir)
        fp = _write_findings(tmp_path, _INVALID_FINDINGS)
        finish(_RUN_ID, "code-review", fp, runs_dir=runs_dir)
        assert not (run_dir / "code-review-result.json").exists()

    def test_validation_errors_reported(self, run_dir, runs_dir, tmp_path):
        begin(_RUN_ID, "code-review", runs_dir=runs_dir)
        fp = _write_findings(tmp_path, _INVALID_FINDINGS)
        result = finish(_RUN_ID, "code-review", fp, runs_dir=runs_dir)
        assert result.validation_errors
        assert len(result.validation_errors) > 0


# ── finish — repair path: invalid then valid ─────────────────────────────────

class TestFinishRepairValid:
    def test_repair_with_valid_findings_succeeds(self, run_dir, runs_dir, tmp_path):
        begin(_RUN_ID, "code-review", runs_dir=runs_dir)
        bad_fp = _write_findings(tmp_path, _INVALID_FINDINGS, "bad.json")
        r1 = finish(_RUN_ID, "code-review", bad_fp, runs_dir=runs_dir)
        assert r1.status == FinishStatus.NEEDS_REPAIR

        good_fp = _write_findings(tmp_path, _REPAIRED_FINDINGS, "good.json")
        r2 = finish(_RUN_ID, "code-review", good_fp, runs_dir=runs_dir)
        assert r2.status == FinishStatus.OK
        assert (run_dir / "code-review-result.json").exists()


# ── finish — repair path: invalid twice ──────────────────────────────────────

class TestFinishInvalidTwice:
    def test_second_failure_writes_error_result(self, run_dir, runs_dir, tmp_path):
        begin(_RUN_ID, "code-review", runs_dir=runs_dir)
        bad_fp = _write_findings(tmp_path, _INVALID_FINDINGS, "bad1.json")
        r1 = finish(_RUN_ID, "code-review", bad_fp, runs_dir=runs_dir)
        assert r1.status == FinishStatus.NEEDS_REPAIR

        bad_fp2 = _write_findings(tmp_path, _INVALID_FINDINGS, "bad2.json")
        r2 = finish(_RUN_ID, "code-review", bad_fp2, runs_dir=runs_dir)
        assert r2.status == FinishStatus.ERROR
        assert r2.result_path is not None
        assert r2.result_path.exists()

    def test_error_result_has_schema_invalid_reason(self, run_dir, runs_dir, tmp_path):
        begin(_RUN_ID, "code-review", runs_dir=runs_dir)
        bad_fp = _write_findings(tmp_path, _INVALID_FINDINGS, "b1.json")
        finish(_RUN_ID, "code-review", bad_fp, runs_dir=runs_dir)
        bad_fp2 = _write_findings(tmp_path, _INVALID_FINDINGS, "b2.json")
        finish(_RUN_ID, "code-review", bad_fp2, runs_dir=runs_dir)
        data = json.loads((run_dir / "code-review-result.json").read_text())
        assert data["status"] == "error"
        assert data["statusReason"].startswith("SCHEMA_INVALID:")

    def test_error_result_has_empty_findings(self, run_dir, runs_dir, tmp_path):
        """Invalid findings are never written to the error result."""
        begin(_RUN_ID, "code-review", runs_dir=runs_dir)
        bad_fp = _write_findings(tmp_path, _INVALID_FINDINGS, "b1.json")
        finish(_RUN_ID, "code-review", bad_fp, runs_dir=runs_dir)
        bad_fp2 = _write_findings(tmp_path, _INVALID_FINDINGS, "b2.json")
        finish(_RUN_ID, "code-review", bad_fp2, runs_dir=runs_dir)
        data = json.loads((run_dir / "code-review-result.json").read_text())
        assert data["findings"] == []

    def test_error_result_is_schema_valid(self, run_dir, runs_dir, tmp_path):
        """The error result itself must pass schema validation."""
        from orchestrator.validate import validate_result
        begin(_RUN_ID, "code-review", runs_dir=runs_dir)
        bad_fp = _write_findings(tmp_path, _INVALID_FINDINGS, "b1.json")
        finish(_RUN_ID, "code-review", bad_fp, runs_dir=runs_dir)
        bad_fp2 = _write_findings(tmp_path, _INVALID_FINDINGS, "b2.json")
        finish(_RUN_ID, "code-review", bad_fp2, runs_dir=runs_dir)
        data = json.loads((run_dir / "code-review-result.json").read_text())
        vr = validate_result(data)
        assert vr.valid, vr.errors

    def test_second_failure_without_repair_flag_writes_error_result(
            self, run_dir, runs_dir, tmp_path):
        """Agents that retry without --repair must still end with an error result."""
        begin(_RUN_ID, "code-review", runs_dir=runs_dir)
        bad_fp = _write_findings(tmp_path, _INVALID_FINDINGS, "b1.json")
        r1 = finish(_RUN_ID, "code-review", bad_fp, runs_dir=runs_dir)
        assert r1.status == FinishStatus.NEEDS_REPAIR
        bad_fp2 = _write_findings(tmp_path, _INVALID_FINDINGS, "b2.json")
        r2 = finish(_RUN_ID, "code-review", bad_fp2, runs_dir=runs_dir)
        assert r2.status == FinishStatus.ERROR
        data = json.loads((run_dir / "code-review-result.json").read_text())
        assert data["status"] == "error"
        assert not (run_dir / "code-review.repair.json").exists()

    def test_repair_marker_cleared_after_success(self, run_dir, runs_dir, tmp_path):
        begin(_RUN_ID, "code-review", runs_dir=runs_dir)
        bad_fp = _write_findings(tmp_path, _INVALID_FINDINGS, "bad.json")
        finish(_RUN_ID, "code-review", bad_fp, runs_dir=runs_dir)
        assert (run_dir / "code-review.repair.json").exists()
        good_fp = _write_findings(tmp_path, _REPAIRED_FINDINGS, "good.json")
        r2 = finish(_RUN_ID, "code-review", good_fp, runs_dir=runs_dir)
        assert r2.status == FinishStatus.OK
        assert not (run_dir / "code-review.repair.json").exists()

    def test_begin_clears_stale_repair_marker(self, run_dir, runs_dir, tmp_path):
        """A re-dispatched agent gets its own repair chance."""
        begin(_RUN_ID, "code-review", runs_dir=runs_dir)
        bad_fp = _write_findings(tmp_path, _INVALID_FINDINGS, "b1.json")
        finish(_RUN_ID, "code-review", bad_fp, runs_dir=runs_dir)
        assert (run_dir / "code-review.repair.json").exists()

        begin(_RUN_ID, "code-review", runs_dir=runs_dir)
        assert not (run_dir / "code-review.repair.json").exists()
        r = finish(_RUN_ID, "code-review", bad_fp, runs_dir=runs_dir)
        assert r.status == FinishStatus.NEEDS_REPAIR


# ── finish — agent reports its own failure ────────────────────────────────────

class TestFinishSelfReportedError:
    def test_status_error_without_findings_file(self, run_dir, runs_dir, tmp_path):
        begin(_RUN_ID, "documentation", runs_dir=runs_dir)
        r = finish(_RUN_ID, "documentation", None, override_status="error",
                   reason="extract_api.py failed to import the app",
                   runs_dir=runs_dir)
        assert r.status == FinishStatus.OK
        data = json.loads((run_dir / "documentation-result.json").read_text())
        assert data["status"] == "error"
        assert data["statusReason"] == "extract_api.py failed to import the app"
        assert data["findings"] == []

    def test_status_error_with_missing_findings_path(self, run_dir, runs_dir, tmp_path):
        begin(_RUN_ID, "documentation", runs_dir=runs_dir)
        r = finish(_RUN_ID, "documentation", tmp_path / "never-written.json",
                   override_status="error", reason="agent crashed",
                   runs_dir=runs_dir)
        assert r.status == FinishStatus.OK

    def test_completed_without_findings_file_needs_repair(self, run_dir, runs_dir):
        begin(_RUN_ID, "documentation", runs_dir=runs_dir)
        r = finish(_RUN_ID, "documentation", None, runs_dir=runs_dir)
        assert r.status == FinishStatus.NEEDS_REPAIR


# ── missing context ───────────────────────────────────────────────────────────

def test_finish_missing_context_returns_error(tmp_path):
    runs = tmp_path / "runs"
    rdir = runs / _RUN_ID
    rdir.mkdir(parents=True)
    # no context.json
    fp = _write_findings(tmp_path, _VALID_FINDINGS)
    result = finish(_RUN_ID, "code-review", fp, runs_dir=runs)
    assert result.status == FinishStatus.ERROR


# ── CLI smoke tests ───────────────────────────────────────────────────────────

class TestCLI:
    def test_begin_exits_zero(self, run_dir, runs_dir):
        # patch _RUNS_DIR via env is complex; use the importable directly
        # but we can call _main by monkeypatching the default runs dir
        import orchestrator.agent_io as aio
        orig = aio._RUNS_DIR
        aio._RUNS_DIR = runs_dir
        try:
            rc = _main(["begin", "--run", _RUN_ID, "--agent", "code-review"])
        finally:
            aio._RUNS_DIR = orig
        assert rc == 0

    def test_finish_exits_zero_valid(self, run_dir, runs_dir, tmp_path):
        import orchestrator.agent_io as aio
        orig = aio._RUNS_DIR
        aio._RUNS_DIR = runs_dir
        try:
            _main(["begin", "--run", _RUN_ID, "--agent", "code-review"])
            fp = _write_findings(tmp_path, _VALID_FINDINGS)
            rc = _main([
                "finish", "--run", _RUN_ID, "--agent", "code-review",
                "--findings", str(fp),
            ])
        finally:
            aio._RUNS_DIR = orig
        assert rc == 0

    def test_finish_exits_2_on_invalid(self, run_dir, runs_dir, tmp_path):
        import orchestrator.agent_io as aio
        orig = aio._RUNS_DIR
        aio._RUNS_DIR = runs_dir
        try:
            _main(["begin", "--run", _RUN_ID, "--agent", "code-review"])
            fp = _write_findings(tmp_path, _INVALID_FINDINGS)
            rc = _main([
                "finish", "--run", _RUN_ID, "--agent", "code-review",
                "--findings", str(fp),
            ])
        finally:
            aio._RUNS_DIR = orig
        assert rc == 2

    def test_finish_exits_1_on_second_invalid(self, run_dir, runs_dir, tmp_path):
        import orchestrator.agent_io as aio
        orig = aio._RUNS_DIR
        aio._RUNS_DIR = runs_dir
        try:
            _main(["begin", "--run", _RUN_ID, "--agent", "code-review"])
            fp = _write_findings(tmp_path, _INVALID_FINDINGS, "b1.json")
            _main(["finish", "--run", _RUN_ID, "--agent", "code-review",
                   "--findings", str(fp)])
            fp2 = _write_findings(tmp_path, _INVALID_FINDINGS, "b2.json")
            rc = _main([
                "finish", "--run", _RUN_ID, "--agent", "code-review",
                "--findings", str(fp2), "--repair",
            ])
        finally:
            aio._RUNS_DIR = orig
        assert rc == 1
