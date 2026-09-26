"""
T6 tests: orchestrator.report

Tests cover:
- Status banner contains correct state string and reasons for all three states
- Run identity section contains base/candidate refs and SHA prefixes
- Agent table rows present for all three agents
- Test execution section present and shows exit code / counts (null → "unknown")
- Blocking findings section groups by agent, shows severity/id/title/evidence/recommendation
- Non-blocking findings appear in separate section
- Limitations section present
- Next steps section present iff there are blocking findings
- Fixed footer is always present
- No HTML in output (readable as a GitHub PR comment)
- No .tmp files after write
- prefix parameter prepended (used by T10)
"""

import json
import pathlib
import sys

import pytest

_REPO = pathlib.Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO))

from orchestrator.report import render, write_report

# ── shared report dicts ───────────────────────────────────────────────────────

_RUN_ID  = "run-20260926-101500-a1b2c3d"
_SNAP    = "aabbccdd11223344556677889900aabbccdd1122"
_BASE    = "0011223344556677889900aabbccdd1122334455"

_AGENTS = [
    {"agent": "code-review",    "status": "completed",
     "startedAt": "2026-09-26T10:15:03.000Z",
     "finishedAt": "2026-09-26T10:15:41.000Z", "durationMs": 38000},
    {"agent": "testing",        "status": "completed",
     "startedAt": "2026-09-26T10:15:05.000Z",
     "finishedAt": "2026-09-26T10:15:35.000Z", "durationMs": 30000},
    {"agent": "documentation",  "status": "completed",
     "startedAt": "2026-09-26T10:15:04.000Z",
     "finishedAt": "2026-09-26T10:15:55.000Z", "durationMs": 51000},
]

_EXECUTION = {
    "command": "python -m pytest -q",
    "workingDirectory": "runs/x/snapshot/sample-project",
    "exitCode": 0,
    "logPath": "runs/x/logs/pytest.log",
    "junitPath": "runs/x/logs/junit.xml",
    "collected": 18, "passed": 18, "failed": 0, "skipped": 0, "errors": 0,
    "startedAt": "2026-09-26T10:15:07.000Z",
    "finishedAt": "2026-09-26T10:15:32.000Z", "durationMs": 25000,
}

_BLOCKING_FINDING = {
    "id": "CODE-001",
    "key": "NULL_HANDLING|sample-project/app/service.py|create_item|priority",
    "severity": "HIGH", "category": "NULL_HANDLING",
    "title": "Null dereference before check",
    "file": "sample-project/app/service.py", "line": 42, "symbol": "create_item",
    "description": "Priority field accessed before null check.",
    "evidence": "data.priority.lower()  # may be None",
    "evidenceType": "source-analysis",
    "recommendation": "Add null guard: if data.priority is not None.",
    "relatedFiles": [], "agents": ["code-review"], "blocking": True,
}

_NON_BLOCKING_FINDING = {
    "id": "CODE-002",
    "key": "MAINTAINABILITY|sample-project/app/service.py|create_item|name_length",
    "severity": "LOW", "category": "MAINTAINABILITY",
    "title": "Long function name",
    "file": "sample-project/app/service.py", "line": 10, "symbol": "create_item",
    "description": "Function name is very long.",
    "evidence": "def create_item_with_priority_and_status(...):",
    "evidenceType": "source-analysis",
    "recommendation": "Shorten the name.",
    "relatedFiles": [], "agents": ["code-review"], "blocking": False,
}


def _report(
    state: str,
    reasons: list,
    findings: list | None = None,
    execution: dict | None = None,
    agents: list | None = None,
    limitations: list | None = None,
) -> dict:
    return {
        "schemaVersion": "1.0",
        "runId": _RUN_ID, "snapshotId": _SNAP,
        "baseCommit": _BASE, "baseRef": "baseline-clean",
        "candidateRef": "demo-bad",
        "generatedAt": "2026-09-26T10:20:00.000Z",
        "policyVersion": "1.0",
        "readiness": {"state": state, "reasons": reasons},
        "agents": agents if agents is not None else _AGENTS,
        "execution": execution,
        "findings": findings if findings is not None else [],
        "counts": {
            "total": len(findings) if findings else 0,
            "blocking": sum(1 for f in (findings or []) if f.get("blocking")),
            "byAgent": {},
        },
        "timeline": {
            "wallClockMs": 52000, "sumOfAgentMs": 119000,
            "overlapObserved": True,
        },
        "limitations": limitations if limitations is not None else [
            "Only files under sample-project/ were analysed."
        ],
    }


# ── status banner ─────────────────────────────────────────────────────────────

class TestStatusBanner:
    def test_ready_banner(self):
        md = render(_report("READY_FOR_HUMAN_REVIEW", []))
        assert "READY FOR HUMAN REVIEW" in md

    def test_attention_banner(self):
        md = render(_report("ATTENTION_REQUIRED",
                            [{"code": "BLOCKING_FINDINGS", "detail": "CODE-001"}]))
        assert "ATTENTION REQUIRED" in md

    def test_verification_failed_banner(self):
        md = render(_report("VERIFICATION_FAILED",
                            [{"code": "AGENT_MISSING", "detail": "code-review"}]))
        assert "VERIFICATION FAILED" in md

    def test_reason_detail_in_banner(self):
        md = render(_report("ATTENTION_REQUIRED",
                            [{"code": "BLOCKING_FINDINGS", "detail": "CODE-001"}]))
        assert "CODE-001" in md

    def test_all_reason_codes_render(self):
        codes = [
            "AGENT_MISSING", "AGENT_ERROR", "AGENT_TIMEOUT", "AGENT_SKIPPED",
            "SCHEMA_INVALID", "SNAPSHOT_MISMATCH", "TESTS_NOT_RUN",
            "TESTS_FAILED", "BLOCKING_FINDINGS",
        ]
        for code in codes:
            md = render(_report("VERIFICATION_FAILED",
                                [{"code": code, "detail": "detail"}]))
            assert "detail" in md, f"Reason detail missing for {code}"


# ── run identity ──────────────────────────────────────────────────────────────

class TestRunIdentity:
    def test_run_id_present(self):
        md = render(_report("READY_FOR_HUMAN_REVIEW", []))
        assert _RUN_ID in md

    def test_base_ref_present(self):
        md = render(_report("READY_FOR_HUMAN_REVIEW", []))
        assert "baseline-clean" in md

    def test_candidate_ref_present(self):
        md = render(_report("READY_FOR_HUMAN_REVIEW", []))
        assert "demo-bad" in md

    def test_sha_prefix_present(self):
        md = render(_report("READY_FOR_HUMAN_REVIEW", []))
        # first 12 chars of snapshot id
        assert _SNAP[:12] in md


# ── agent table ───────────────────────────────────────────────────────────────

class TestAgentTable:
    def test_all_agents_listed(self):
        md = render(_report("READY_FOR_HUMAN_REVIEW", []))
        assert "code-review" in md
        assert "testing" in md
        assert "documentation" in md

    def test_error_agent_shown(self):
        err_agents = [
            {**_AGENTS[0], "status": "error"},
            _AGENTS[1], _AGENTS[2],
        ]
        md = render(_report("VERIFICATION_FAILED",
                            [{"code": "AGENT_ERROR", "detail": "boom"}],
                            agents=err_agents))
        assert "error" in md

    def test_timeline_bar_present(self):
        md = render(_report("READY_FOR_HUMAN_REVIEW", []))
        # timeline bar uses ``` fences
        assert "```" in md


# ── test execution ────────────────────────────────────────────────────────────

class TestExecution:
    def test_execution_section_present(self):
        md = render(_report("READY_FOR_HUMAN_REVIEW", [],
                            execution=_EXECUTION))
        assert "Test execution" in md

    def test_exit_code_shown(self):
        md = render(_report("READY_FOR_HUMAN_REVIEW", [],
                            execution=_EXECUTION))
        assert "Exit code" in md
        assert "`0`" in md

    def test_null_counts_shown_as_unknown(self):
        null_exec = {**_EXECUTION,
                     "collected": None, "passed": None, "failed": None}
        md = render(_report("VERIFICATION_FAILED",
                            [{"code": "TESTS_NOT_RUN", "detail": "x"}],
                            execution=null_exec))
        assert "unknown" in md

    def test_no_execution_section_when_null(self):
        md = render(_report("READY_FOR_HUMAN_REVIEW", [], execution=None))
        assert "Test execution" not in md


# ── findings ──────────────────────────────────────────────────────────────────

class TestFindings:
    def test_blocking_section_present(self):
        md = render(_report("ATTENTION_REQUIRED",
                            [{"code": "BLOCKING_FINDINGS", "detail": "CODE-001"}],
                            findings=[_BLOCKING_FINDING],
                            execution=_EXECUTION))
        assert "Blocking findings" in md

    def test_finding_id_in_output(self):
        md = render(_report("ATTENTION_REQUIRED",
                            [{"code": "BLOCKING_FINDINGS", "detail": "CODE-001"}],
                            findings=[_BLOCKING_FINDING],
                            execution=_EXECUTION))
        assert "CODE-001" in md

    def test_finding_severity_in_output(self):
        md = render(_report("ATTENTION_REQUIRED",
                            [{"code": "BLOCKING_FINDINGS", "detail": "CODE-001"}],
                            findings=[_BLOCKING_FINDING],
                            execution=_EXECUTION))
        assert "HIGH" in md

    def test_evidence_in_code_block(self):
        md = render(_report("ATTENTION_REQUIRED",
                            [{"code": "BLOCKING_FINDINGS", "detail": "CODE-001"}],
                            findings=[_BLOCKING_FINDING],
                            execution=_EXECUTION))
        assert "data.priority.lower()" in md

    def test_recommendation_in_output(self):
        md = render(_report("ATTENTION_REQUIRED",
                            [{"code": "BLOCKING_FINDINGS", "detail": "CODE-001"}],
                            findings=[_BLOCKING_FINDING],
                            execution=_EXECUTION))
        assert "null guard" in md

    def test_non_blocking_section_present(self):
        md = render(_report("READY_FOR_HUMAN_REVIEW", [],
                            findings=[_NON_BLOCKING_FINDING],
                            execution=_EXECUTION))
        assert "Non-blocking findings" in md

    def test_no_findings_message(self):
        md = render(_report("READY_FOR_HUMAN_REVIEW", [],
                            findings=[], execution=_EXECUTION))
        assert "No findings" in md

    def test_blocking_and_non_blocking_both_shown(self):
        md = render(_report("ATTENTION_REQUIRED",
                            [{"code": "BLOCKING_FINDINGS", "detail": "CODE-001"}],
                            findings=[_BLOCKING_FINDING, _NON_BLOCKING_FINDING],
                            execution=_EXECUTION))
        assert "Blocking findings" in md
        assert "Non-blocking findings" in md


# ── limitations ───────────────────────────────────────────────────────────────

def test_limitations_section_present():
    md = render(_report("READY_FOR_HUMAN_REVIEW", [],
                        limitations=["Only sample-project/ analysed."]))
    assert "Limitations" in md
    assert "Only sample-project/ analysed." in md


# ── next steps ────────────────────────────────────────────────────────────────

class TestNextSteps:
    def test_next_steps_present_when_blocking(self):
        md = render(_report("ATTENTION_REQUIRED",
                            [{"code": "BLOCKING_FINDINGS", "detail": "CODE-001"}],
                            findings=[_BLOCKING_FINDING],
                            execution=_EXECUTION))
        assert "Next steps" in md
        assert "null guard" in md  # from recommendation

    def test_next_steps_absent_when_no_blocking(self):
        md = render(_report("READY_FOR_HUMAN_REVIEW", []))
        assert "Next steps" not in md


# ── footer ────────────────────────────────────────────────────────────────────

def test_fixed_footer_always_present():
    for state in ["READY_FOR_HUMAN_REVIEW", "ATTENTION_REQUIRED", "VERIFICATION_FAILED"]:
        md = render(_report(state, []))
        assert "not approval to merge" in md, f"Footer missing for state {state}"


# ── no HTML ───────────────────────────────────────────────────────────────────

def test_no_html_tags():
    md = render(_report("ATTENTION_REQUIRED",
                        [{"code": "BLOCKING_FINDINGS", "detail": "CODE-001"}],
                        findings=[_BLOCKING_FINDING],
                        execution=_EXECUTION))
    assert "<div" not in md
    assert "<table" not in md
    assert "<br>" not in md


# ── write_report ─────────────────────────────────────────────────────────────

class TestWriteReport:
    def test_writes_report_md(self, tmp_path):
        rdir = tmp_path / _RUN_ID
        rdir.mkdir()
        report = _report("READY_FOR_HUMAN_REVIEW", [], execution=_EXECUTION)
        (rdir / "report.json").write_text(json.dumps(report), encoding="utf-8")
        path = write_report(_RUN_ID, runs_dir=tmp_path)
        assert path.exists()
        assert path.name == "report.md"

    def test_no_tmp_leftover(self, tmp_path):
        rdir = tmp_path / _RUN_ID
        rdir.mkdir()
        report = _report("READY_FOR_HUMAN_REVIEW", [])
        (rdir / "report.json").write_text(json.dumps(report), encoding="utf-8")
        write_report(_RUN_ID, runs_dir=tmp_path)
        assert list(rdir.glob("*.tmp")) == []

    def test_prefix_prepended(self, tmp_path):
        rdir = tmp_path / _RUN_ID
        rdir.mkdir()
        report = _report("READY_FOR_HUMAN_REVIEW", [])
        (rdir / "report.json").write_text(json.dumps(report), encoding="utf-8")
        write_report(_RUN_ID, runs_dir=tmp_path,
                     prefix="## Changes since previous run\n\nfoo\n\n---\n\n")
        content = (rdir / "report.md").read_text(encoding="utf-8")
        assert content.startswith("## Changes since previous run")
        assert "READY FOR HUMAN REVIEW" in content
