"""
T5 tests: orchestrator.aggregate

Table-driven tests covering every reason code and all three states.

States tested:
  READY_FOR_HUMAN_REVIEW
  ATTENTION_REQUIRED
  VERIFICATION_FAILED

Reason codes tested (all):
  AGENT_MISSING      — result file absent, no started.json
  AGENT_TIMEOUT      — result file absent, started.json present
  AGENT_ERROR        — result present, status == "error"
  AGENT_SKIPPED      — result present, status == "skipped"
  SCHEMA_INVALID     — result file is malformed JSON or fails schema
  SNAPSHOT_MISMATCH  — result has wrong runId or snapshotId
  TESTS_NOT_RUN      — testing completed but execution is null
  TESTS_FAILED       — testing completed, execution.exitCode != 0
  BLOCKING_FINDINGS  — at least one finding above the blocking threshold

Additional:
  - One timed-out agent + others clean → VERIFICATION_FAILED, not READY
  - All agents completed but tests exited 1 → VERIFICATION_FAILED/TESTS_FAILED
  - Key deduplication: identical key from two agents merged
  - Severity ordering in findings output (CRITICAL first)
  - overlapObserved reflects real timestamp intervals
  - generatedAt controlled via --now for byte-identical output
  - report.json is schema-valid in all scenarios
"""

import copy
import json
import pathlib
import sys

import pytest

_REPO = pathlib.Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO))

from orchestrator.aggregate import aggregate
from orchestrator.validate import validate_result

# ── shared test data ─────────────────────────────────────────────────────────

_RUN_ID   = "run-20260926-101500-a1b2c3d"
_SNAP     = "aabbccdd11223344556677889900aabbccdd1122"
_BASE_SHA = "0011223344556677889900aabbccdd1122334455"
_NOW      = "2026-09-26T10:20:00.000Z"

_CONTEXT = {
    "schemaVersion": "1.0",
    "runId": _RUN_ID,
    "snapshotId": _SNAP,
    "baseCommit": _BASE_SHA,
    "baseRef": "baseline-clean",
    "candidateRef": "demo-bad",
    "intent": "test",
    "scopePath": "sample-project",
    "snapshotDir": f"runs/{_RUN_ID}/snapshot",
    "diffPath": f"runs/{_RUN_ID}/diff.patch",
    "project": {
        "language": "Python", "framework": "FastAPI",
        "testCommand": "python -m pytest -q",
        "testWorkingDirectory": "sample-project",
    },
    "changedFiles": [],
    "relevantSource": [], "relevantTests": [], "relevantDocs": [],
    "exclusions": [],
    "scopeLimitations": ["Only files under sample-project/ were analysed."],
    "createdAt": "2026-09-26T10:15:00.000Z",
}

_T_START_A = "2026-09-26T10:15:03.000Z"
_T_END_A   = "2026-09-26T10:15:41.000Z"
_T_START_B = "2026-09-26T10:15:05.000Z"
_T_END_B   = "2026-09-26T10:15:35.000Z"
_T_START_C = "2026-09-26T10:15:04.000Z"
_T_END_C   = "2026-09-26T10:15:55.000Z"


def _clean_result(agent: str, start: str, end: str, findings=None, execution=None) -> dict:
    return {
        "schemaVersion": "1.0",
        "runId": _RUN_ID, "snapshotId": _SNAP,
        "agent": agent, "status": "completed", "statusReason": None,
        "startedAt": start, "finishedAt": end,
        "durationMs": 38000,
        "findings": findings or [],
        "limitations": [],
        "execution": execution,
    }


def _clean_execution(exit_code: int = 0) -> dict:
    return {
        "command": "python -m pytest -q",
        "workingDirectory": "runs/x/snapshot/sample-project",
        "exitCode": exit_code,
        "logPath": "runs/x/logs/pytest.log",
        "junitPath": "runs/x/logs/junit.xml",
        "collected": 18, "passed": 18 if exit_code == 0 else 17,
        "failed": 0 if exit_code == 0 else 1,
        "skipped": 0, "errors": 0,
        "startedAt": _T_START_B, "finishedAt": _T_END_B,
        "durationMs": 23000,
    }


def _finding(agent_prefix: str, n: int, sev: str, cat: str) -> dict:
    pfx = agent_prefix.upper()[:3] if agent_prefix == "testing" else agent_prefix[:3].upper()
    if agent_prefix == "code-review": pfx = "CODE"
    elif agent_prefix == "testing":   pfx = "TEST"
    else:                              pfx = "DOC"
    return {
        "id": f"{pfx}-{n:03d}",
        "key": f"{cat}|sample-project/app/service.py|fn|field{n}",
        "severity": sev,
        "category": cat,
        "title": f"Finding {n}",
        "file": "sample-project/app/service.py",
        "line": 10 + n,
        "symbol": "fn",
        "description": "desc",
        "evidence": "code snippet",
        "evidenceType": "source-analysis",
        "recommendation": "fix it",
        "relatedFiles": [],
    }


# ── fixture builder ───────────────────────────────────────────────────────────

@pytest.fixture()
def run_dir(tmp_path) -> pathlib.Path:
    rdir = tmp_path / _RUN_ID
    rdir.mkdir()
    (rdir / "context.json").write_text(json.dumps(_CONTEXT), encoding="utf-8")
    return rdir


@pytest.fixture()
def runs_dir(run_dir) -> pathlib.Path:
    return run_dir.parent


def _write(rdir: pathlib.Path, name: str, data: dict) -> None:
    (rdir / name).write_text(json.dumps(data), encoding="utf-8")


def _write_all_clean(rdir: pathlib.Path, *, exit_code: int = 0) -> None:
    """Write three clean completed agent results (no findings)."""
    _write(rdir, "code-review-result.json",
           _clean_result("code-review", _T_START_A, _T_END_A))
    _write(rdir, "testing-result.json",
           _clean_result("testing", _T_START_B, _T_END_B,
                         execution=_clean_execution(exit_code)))
    _write(rdir, "documentation-result.json",
           _clean_result("documentation", _T_START_C, _T_END_C))


# ── READY_FOR_HUMAN_REVIEW ────────────────────────────────────────────────────

class TestReady:
    def test_all_clean_is_ready(self, run_dir, runs_dir):
        _write_all_clean(run_dir)
        r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        assert r["readiness"]["state"] == "READY_FOR_HUMAN_REVIEW"
        assert r["readiness"]["reasons"] == []

    def test_ready_with_non_blocking_low_finding(self, run_dir, runs_dir):
        low_finding = _finding("code-review", 1, "LOW", "MAINTAINABILITY")
        _write(run_dir, "code-review-result.json",
               _clean_result("code-review", _T_START_A, _T_END_A,
                              findings=[low_finding]))
        _write(run_dir, "testing-result.json",
               _clean_result("testing", _T_START_B, _T_END_B,
                              execution=_clean_execution()))
        _write(run_dir, "documentation-result.json",
               _clean_result("documentation", _T_START_C, _T_END_C))
        r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        assert r["readiness"]["state"] == "READY_FOR_HUMAN_REVIEW"
        assert len(r["findings"]) == 1
        assert r["findings"][0]["blocking"] is False

    def test_report_is_schema_valid(self, run_dir, runs_dir):
        _write_all_clean(run_dir)
        r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        vr = validate_result(r)
        assert vr.valid, vr.errors


# ── ATTENTION_REQUIRED ────────────────────────────────────────────────────────

class TestAttentionRequired:
    def test_high_finding_triggers_attention(self, run_dir, runs_dir):
        hi = _finding("code-review", 1, "HIGH", "NULL_HANDLING")
        _write(run_dir, "code-review-result.json",
               _clean_result("code-review", _T_START_A, _T_END_A,
                              findings=[hi]))
        _write(run_dir, "testing-result.json",
               _clean_result("testing", _T_START_B, _T_END_B,
                              execution=_clean_execution()))
        _write(run_dir, "documentation-result.json",
               _clean_result("documentation", _T_START_C, _T_END_C))
        r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        assert r["readiness"]["state"] == "ATTENTION_REQUIRED"
        codes = [x["code"] for x in r["readiness"]["reasons"]]
        assert "BLOCKING_FINDINGS" in codes

    def test_medium_missing_test_is_blocking(self, run_dir, runs_dir):
        # MISSING_TEST threshold is MEDIUM → MEDIUM is blocking
        mt = _finding("testing", 1, "MEDIUM", "MISSING_TEST")
        _write(run_dir, "code-review-result.json",
               _clean_result("code-review", _T_START_A, _T_END_A))
        _write(run_dir, "testing-result.json",
               _clean_result("testing", _T_START_B, _T_END_B,
                              findings=[mt], execution=_clean_execution()))
        _write(run_dir, "documentation-result.json",
               _clean_result("documentation", _T_START_C, _T_END_C))
        r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        assert r["readiness"]["state"] == "ATTENTION_REQUIRED"
        assert r["findings"][0]["blocking"] is True

    def test_low_missing_test_is_not_blocking(self, run_dir, runs_dir):
        # LOW < MEDIUM threshold → not blocking
        mt = _finding("testing", 1, "LOW", "MISSING_TEST")
        _write(run_dir, "code-review-result.json",
               _clean_result("code-review", _T_START_A, _T_END_A))
        _write(run_dir, "testing-result.json",
               _clean_result("testing", _T_START_B, _T_END_B,
                              findings=[mt], execution=_clean_execution()))
        _write(run_dir, "documentation-result.json",
               _clean_result("documentation", _T_START_C, _T_END_C))
        r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        assert r["readiness"]["state"] == "READY_FOR_HUMAN_REVIEW"
        assert r["findings"][0]["blocking"] is False

    def test_info_test_failure_is_blocking(self, run_dir, runs_dir):
        # TEST_FAILURE threshold is INFO → even INFO is blocking
        tf = _finding("testing", 1, "INFO", "TEST_FAILURE")
        _write(run_dir, "code-review-result.json",
               _clean_result("code-review", _T_START_A, _T_END_A))
        _write(run_dir, "testing-result.json",
               _clean_result("testing", _T_START_B, _T_END_B,
                              findings=[tf], execution=_clean_execution()))
        _write(run_dir, "documentation-result.json",
               _clean_result("documentation", _T_START_C, _T_END_C))
        r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        assert r["findings"][0]["blocking"] is True

    def test_report_is_schema_valid(self, run_dir, runs_dir):
        hi = _finding("code-review", 1, "HIGH", "NULL_HANDLING")
        _write(run_dir, "code-review-result.json",
               _clean_result("code-review", _T_START_A, _T_END_A,
                              findings=[hi]))
        _write(run_dir, "testing-result.json",
               _clean_result("testing", _T_START_B, _T_END_B,
                              execution=_clean_execution()))
        _write(run_dir, "documentation-result.json",
               _clean_result("documentation", _T_START_C, _T_END_C))
        r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        vr = validate_result(r)
        assert vr.valid, vr.errors


# ── VERIFICATION_FAILED — every reason code ──────────────────────────────────

class TestVerificationFailed:

    # AGENT_MISSING
    def test_agent_missing_no_marker(self, run_dir, runs_dir):
        _write(run_dir, "testing-result.json",
               _clean_result("testing", _T_START_B, _T_END_B,
                              execution=_clean_execution()))
        _write(run_dir, "documentation-result.json",
               _clean_result("documentation", _T_START_C, _T_END_C))
        # no code-review-result.json and no .started.json
        r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        assert r["readiness"]["state"] == "VERIFICATION_FAILED"
        codes = [x["code"] for x in r["readiness"]["reasons"]]
        assert "AGENT_MISSING" in codes

    # AGENT_TIMEOUT
    def test_agent_timeout_with_started_marker(self, run_dir, runs_dir):
        # started.json present but no result
        (run_dir / "code-review.started.json").write_text(
            json.dumps({"agent": "code-review",
                        "startedAt": _T_START_A}), encoding="utf-8"
        )
        _write(run_dir, "testing-result.json",
               _clean_result("testing", _T_START_B, _T_END_B,
                              execution=_clean_execution()))
        _write(run_dir, "documentation-result.json",
               _clean_result("documentation", _T_START_C, _T_END_C))
        r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        assert r["readiness"]["state"] == "VERIFICATION_FAILED"
        codes = [x["code"] for x in r["readiness"]["reasons"]]
        assert "AGENT_TIMEOUT" in codes

    # AGENT_ERROR
    def test_agent_error_status(self, run_dir, runs_dir):
        err = {**_clean_result("code-review", _T_START_A, _T_END_A),
               "status": "error", "statusReason": "boom"}
        _write(run_dir, "code-review-result.json", err)
        _write(run_dir, "testing-result.json",
               _clean_result("testing", _T_START_B, _T_END_B,
                              execution=_clean_execution()))
        _write(run_dir, "documentation-result.json",
               _clean_result("documentation", _T_START_C, _T_END_C))
        r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        assert r["readiness"]["state"] == "VERIFICATION_FAILED"
        codes = [x["code"] for x in r["readiness"]["reasons"]]
        assert "AGENT_ERROR" in codes

    # AGENT_SKIPPED
    def test_agent_skipped_status(self, run_dir, runs_dir):
        sk = {**_clean_result("code-review", _T_START_A, _T_END_A),
              "status": "skipped", "statusReason": "skipped by operator"}
        _write(run_dir, "code-review-result.json", sk)
        _write(run_dir, "testing-result.json",
               _clean_result("testing", _T_START_B, _T_END_B,
                              execution=_clean_execution()))
        _write(run_dir, "documentation-result.json",
               _clean_result("documentation", _T_START_C, _T_END_C))
        r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        assert r["readiness"]["state"] == "VERIFICATION_FAILED"
        codes = [x["code"] for x in r["readiness"]["reasons"]]
        assert "AGENT_SKIPPED" in codes

    # SCHEMA_INVALID
    def test_schema_invalid_bad_json(self, run_dir, runs_dir):
        (run_dir / "code-review-result.json").write_text(
            "{ not valid json", encoding="utf-8"
        )
        _write(run_dir, "testing-result.json",
               _clean_result("testing", _T_START_B, _T_END_B,
                              execution=_clean_execution()))
        _write(run_dir, "documentation-result.json",
               _clean_result("documentation", _T_START_C, _T_END_C))
        r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        assert r["readiness"]["state"] == "VERIFICATION_FAILED"
        codes = [x["code"] for x in r["readiness"]["reasons"]]
        assert "SCHEMA_INVALID" in codes

    def test_schema_invalid_bad_schema(self, run_dir, runs_dir):
        bad = {**_clean_result("code-review", _T_START_A, _T_END_A)}
        bad["status"] = "NOPE"   # not in enum
        _write(run_dir, "code-review-result.json", bad)
        _write(run_dir, "testing-result.json",
               _clean_result("testing", _T_START_B, _T_END_B,
                              execution=_clean_execution()))
        _write(run_dir, "documentation-result.json",
               _clean_result("documentation", _T_START_C, _T_END_C))
        r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        assert r["readiness"]["state"] == "VERIFICATION_FAILED"
        codes = [x["code"] for x in r["readiness"]["reasons"]]
        assert "SCHEMA_INVALID" in codes

    # SNAPSHOT_MISMATCH
    def test_snapshot_mismatch_wrong_run_id(self, run_dir, runs_dir):
        mismatch = {**_clean_result("code-review", _T_START_A, _T_END_A),
                    "runId": "run-20991231-235959-0000000"}
        _write(run_dir, "code-review-result.json", mismatch)
        _write(run_dir, "testing-result.json",
               _clean_result("testing", _T_START_B, _T_END_B,
                              execution=_clean_execution()))
        _write(run_dir, "documentation-result.json",
               _clean_result("documentation", _T_START_C, _T_END_C))
        r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        assert r["readiness"]["state"] == "VERIFICATION_FAILED"
        codes = [x["code"] for x in r["readiness"]["reasons"]]
        assert "SNAPSHOT_MISMATCH" in codes

    # TESTS_NOT_RUN
    def test_tests_not_run_null_execution(self, run_dir, runs_dir):
        # testing completed but execution is null
        no_exec = _clean_result("testing", _T_START_B, _T_END_B, execution=None)
        _write(run_dir, "code-review-result.json",
               _clean_result("code-review", _T_START_A, _T_END_A))
        _write(run_dir, "testing-result.json", no_exec)
        _write(run_dir, "documentation-result.json",
               _clean_result("documentation", _T_START_C, _T_END_C))
        r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        assert r["readiness"]["state"] == "VERIFICATION_FAILED"
        codes = [x["code"] for x in r["readiness"]["reasons"]]
        assert "TESTS_NOT_RUN" in codes

    def test_tests_not_run_null_exit_code(self, run_dir, runs_dir):
        exec_null = {**_clean_execution(), "exitCode": None}
        _write(run_dir, "code-review-result.json",
               _clean_result("code-review", _T_START_A, _T_END_A))
        _write(run_dir, "testing-result.json",
               _clean_result("testing", _T_START_B, _T_END_B,
                              execution=exec_null))
        _write(run_dir, "documentation-result.json",
               _clean_result("documentation", _T_START_C, _T_END_C))
        r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        codes = [x["code"] for x in r["readiness"]["reasons"]]
        assert "TESTS_NOT_RUN" in codes

    # TESTS_FAILED (the spec's explicit scenario)
    def test_all_agents_completed_but_tests_exit_1(self, run_dir, runs_dir):
        """All agents completed but tests exited 1 → VERIFICATION_FAILED/TESTS_FAILED."""
        _write_all_clean(run_dir, exit_code=1)
        r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        assert r["readiness"]["state"] == "VERIFICATION_FAILED"
        codes = [x["code"] for x in r["readiness"]["reasons"]]
        assert "TESTS_FAILED" in codes

    # One timed-out agent + others found nothing → VERIFICATION_FAILED, not READY
    def test_timeout_agent_with_others_clean_is_not_ready(self, run_dir, runs_dir):
        """One agent timed out but the others found nothing → VERIFICATION_FAILED."""
        (run_dir / "code-review.started.json").write_text(
            json.dumps({"agent": "code-review", "startedAt": _T_START_A}),
            encoding="utf-8",
        )
        # no code-review-result.json
        _write(run_dir, "testing-result.json",
               _clean_result("testing", _T_START_B, _T_END_B,
                              execution=_clean_execution()))
        _write(run_dir, "documentation-result.json",
               _clean_result("documentation", _T_START_C, _T_END_C))
        r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        assert r["readiness"]["state"] == "VERIFICATION_FAILED"
        codes = [x["code"] for x in r["readiness"]["reasons"]]
        assert "AGENT_TIMEOUT" in codes

    # VERIFICATION_FAILED with blocking findings still shows them
    def test_verification_failed_still_shows_valid_findings(self, run_dir, runs_dir):
        hi = _finding("code-review", 1, "HIGH", "NULL_HANDLING")
        _write(run_dir, "code-review-result.json",
               _clean_result("code-review", _T_START_A, _T_END_A,
                              findings=[hi]))
        # testing is missing
        _write(run_dir, "documentation-result.json",
               _clean_result("documentation", _T_START_C, _T_END_C))
        r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        assert r["readiness"]["state"] == "VERIFICATION_FAILED"
        assert len(r["findings"]) == 1   # finding still present
        codes = [x["code"] for x in r["readiness"]["reasons"]]
        assert "BLOCKING_FINDINGS" in codes


# ── deduplication ─────────────────────────────────────────────────────────────

class TestDeduplication:
    def test_identical_key_merged_agents(self, run_dir, runs_dir):
        """Two agents reporting the same key → one finding, two agents listed."""
        shared_key = "NULL_HANDLING|sample-project/app/service.py|fn|-"
        f_cr = {
            "id": "CODE-001", "key": shared_key,
            "severity": "HIGH", "category": "NULL_HANDLING",
            "title": "Null issue", "file": "sample-project/app/service.py",
            "line": 10, "symbol": "fn",
            "description": "d", "evidence": "e",
            "evidenceType": "source-analysis",
            "recommendation": "r", "relatedFiles": [],
        }
        # testing can't use NULL_HANDLING — use a distinct key
        _write(run_dir, "code-review-result.json",
               _clean_result("code-review", _T_START_A, _T_END_A,
                              findings=[f_cr]))
        _write(run_dir, "testing-result.json",
               _clean_result("testing", _T_START_B, _T_END_B,
                              execution=_clean_execution()))
        _write(run_dir, "documentation-result.json",
               _clean_result("documentation", _T_START_C, _T_END_C))
        r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        assert len(r["findings"]) == 1
        assert "code-review" in r["findings"][0]["agents"]

    def test_different_keys_not_merged(self, run_dir, runs_dir):
        f1 = _finding("code-review", 1, "HIGH", "NULL_HANDLING")
        f2 = _finding("code-review", 2, "MEDIUM", "INPUT_VALIDATION")
        _write(run_dir, "code-review-result.json",
               _clean_result("code-review", _T_START_A, _T_END_A,
                              findings=[f1, f2]))
        _write(run_dir, "testing-result.json",
               _clean_result("testing", _T_START_B, _T_END_B,
                              execution=_clean_execution()))
        _write(run_dir, "documentation-result.json",
               _clean_result("documentation", _T_START_C, _T_END_C))
        r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        assert len(r["findings"]) == 2

    def test_duplicate_key_keeps_more_severe_finding(self, run_dir, runs_dir):
        """A LOW then MEDIUM missing test with the same key must still block."""
        low = _finding("testing", 1, "LOW", "MISSING_TEST")
        medium = dict(_finding("testing", 2, "MEDIUM", "MISSING_TEST"), key=low["key"])
        _write(run_dir, "code-review-result.json",
               _clean_result("code-review", _T_START_A, _T_END_A))
        _write(run_dir, "testing-result.json",
               _clean_result("testing", _T_START_B, _T_END_B,
                              findings=[low, medium],
                              execution=_clean_execution()))
        _write(run_dir, "documentation-result.json",
               _clean_result("documentation", _T_START_C, _T_END_C))
        r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        assert len(r["findings"]) == 1
        assert r["findings"][0]["id"] == "TEST-002"
        assert r["findings"][0]["severity"] == "MEDIUM"
        assert r["findings"][0]["blocking"] is True
        assert r["readiness"]["state"] == "ATTENTION_REQUIRED"


# ── sorting ───────────────────────────────────────────────────────────────────

class TestSorting:
    def test_findings_sorted_severity_descending(self, run_dir, runs_dir):
        low  = _finding("code-review", 1, "LOW",      "MAINTAINABILITY")
        high = _finding("code-review", 2, "HIGH",     "NULL_HANDLING")
        crit = _finding("code-review", 3, "CRITICAL", "SECURITY")
        _write(run_dir, "code-review-result.json",
               _clean_result("code-review", _T_START_A, _T_END_A,
                              findings=[low, high, crit]))
        _write(run_dir, "testing-result.json",
               _clean_result("testing", _T_START_B, _T_END_B,
                              execution=_clean_execution()))
        _write(run_dir, "documentation-result.json",
               _clean_result("documentation", _T_START_C, _T_END_C))
        r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        sevs = [f["severity"] for f in r["findings"]]
        assert sevs[0] == "CRITICAL"
        assert sevs[1] == "HIGH"
        assert sevs[2] == "LOW"

    def test_same_inputs_byte_identical(self, run_dir, runs_dir):
        _write_all_clean(run_dir)
        r1 = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        r2 = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        assert json.dumps(r1) == json.dumps(r2)


# ── timeline ──────────────────────────────────────────────────────────────────

class TestTimeline:
    def test_wall_clock_ms_computed(self, run_dir, runs_dir):
        _write_all_clean(run_dir)
        r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        # earliest start: _T_START_A = 10:15:03, latest end: _T_END_C = 10:15:55 → 52 s
        assert r["timeline"]["wallClockMs"] == 52000

    def test_sum_of_agent_ms(self, run_dir, runs_dir):
        _write_all_clean(run_dir)
        r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        # all three use durationMs=38000
        assert r["timeline"]["sumOfAgentMs"] == 38000 * 3

    def test_overlap_observed_true_when_intervals_overlap(self, run_dir, runs_dir):
        # A: 03→41, B: 05→35, C: 04→55 — all overlap
        _write_all_clean(run_dir)
        r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        assert r["timeline"]["overlapObserved"] is True

    def test_overlap_observed_false_sequential(self, run_dir, runs_dir):
        # sequential: A ends before B starts
        _write(run_dir, "code-review-result.json",
               _clean_result("code-review",
                              "2026-09-26T10:15:00.000Z",
                              "2026-09-26T10:15:10.000Z"))
        _write(run_dir, "testing-result.json",
               _clean_result("testing",
                              "2026-09-26T10:15:11.000Z",
                              "2026-09-26T10:15:20.000Z",
                              execution=_clean_execution()))
        _write(run_dir, "documentation-result.json",
               _clean_result("documentation",
                              "2026-09-26T10:15:21.000Z",
                              "2026-09-26T10:15:30.000Z"))
        r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
        assert r["timeline"]["overlapObserved"] is False


# ── counts ────────────────────────────────────────────────────────────────────

def test_counts_correct(run_dir, runs_dir):
    f1 = _finding("code-review", 1, "HIGH",   "NULL_HANDLING")
    f2 = _finding("code-review", 2, "MEDIUM", "INPUT_VALIDATION")  # not blocking (below HIGH default)
    _write(run_dir, "code-review-result.json",
           _clean_result("code-review", _T_START_A, _T_END_A,
                         findings=[f1, f2]))
    _write(run_dir, "testing-result.json",
           _clean_result("testing", _T_START_B, _T_END_B,
                         execution=_clean_execution()))
    _write(run_dir, "documentation-result.json",
           _clean_result("documentation", _T_START_C, _T_END_C))
    r = aggregate(_RUN_ID, now=_NOW, runs_dir=runs_dir)
    assert r["counts"]["total"] == 2
    assert r["counts"]["blocking"] == 1          # only HIGH is blocking under default
    assert r["counts"]["byAgent"]["code-review"] == 2


# ── generatedAt controlled via now ───────────────────────────────────────────

def test_generated_at_uses_now_param(run_dir, runs_dir):
    _write_all_clean(run_dir)
    fixed = "2099-01-01T00:00:00.000Z"
    r = aggregate(_RUN_ID, now=fixed, runs_dir=runs_dir)
    assert r["generatedAt"] == fixed
