"""
T10 — orchestrator.reverify: delta between two runs (A8).

Covers:
- resolved: key gone and owning agent completed
- persistent: same key; and fallback match when the key subject changed but
  category + file + symbol are the same (e.g. line moved)
- new: key only in the new run
- unverified: key gone but owning agent timed out — never "resolved"
- delta.json / delta.md written by the CLI-level function
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

_REPO = pathlib.Path(__file__).parent.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from orchestrator.reverify import compute_delta, render_delta, reverify  # noqa: E402


def _f(fid, key, agent, *, cat="NULL_HANDLING", file="sample-project/app/service.py",
       symbol="fn", line=10, sev="HIGH"):
    return {
        "id": fid, "key": key, "category": cat, "file": file, "symbol": symbol,
        "line": line, "severity": sev, "title": f"title {fid}",
        "agents": [agent], "blocking": True,
    }


def _report(run_id, findings, *, statuses=None, state="ATTENTION_REQUIRED"):
    statuses = statuses or {}
    return {
        "runId": run_id, "snapshotId": f"sha-{run_id}",
        "readiness": {"state": state, "reasons": []},
        "agents": [
            {"agent": a, "status": statuses.get(a, "completed")}
            for a in ("code-review", "testing", "documentation")
        ],
        "findings": findings,
    }


K_CODE = "NULL_HANDLING|sample-project/app/service.py|fn|value"
K_TEST = "MISSING_TEST|sample-project/app/service.py|fn|null_value"
K_DOC  = "DOC_MISSING|sample-project/docs/api.md|-|value"


def test_resolved_when_agent_completed():
    prev = _report("r1", [_f("CODE-001", K_CODE, "code-review")])
    cur = _report("r2", [], state="READY_FOR_HUMAN_REVIEW")
    d = compute_delta(prev, cur)
    assert [e["id"] for e in d["resolved"]] == ["CODE-001"]
    assert d["counts"] == {"resolved": 1, "persistent": 0, "new": 0, "unverified": 0}


def test_unverified_when_owning_agent_timed_out():
    prev = _report("r1", [_f("TEST-001", K_TEST, "testing", cat="MISSING_TEST")])
    cur = _report("r2", [], statuses={"testing": "timeout"}, state="VERIFICATION_FAILED")
    d = compute_delta(prev, cur)
    assert d["resolved"] == []
    assert [e["id"] for e in d["unverified"]] == ["TEST-001"]


def test_persistent_same_key_even_if_line_moved():
    prev = _report("r1", [_f("CODE-001", K_CODE, "code-review", line=10)])
    cur = _report("r2", [_f("CODE-003", K_CODE, "code-review", line=25)])
    d = compute_delta(prev, cur)
    assert len(d["persistent"]) == 1
    e = d["persistent"][0]
    assert e["id"] == "CODE-003" and e["previousId"] == "CODE-001"
    assert e["line"] == 25 and e["previousLine"] == 10
    assert d["new"] == [] and d["resolved"] == []


def test_persistent_fallback_on_category_file_symbol():
    prev = _report("r1", [_f("CODE-001", K_CODE, "code-review")])
    renamed = K_CODE.replace("|value", "|value_field")
    cur = _report("r2", [_f("CODE-001", renamed, "code-review")])
    d = compute_delta(prev, cur)
    assert len(d["persistent"]) == 1
    assert d["new"] == []


def test_persistent_when_symbol_is_class_qualified():
    """Real runs named the same test gap 'create_ticket' and 'TicketService.create_ticket'."""
    prev = _report("r1", [_f("TEST-004", K_TEST, "testing", cat="MISSING_TEST")])
    qualified = K_TEST.replace("|fn|", "|Service.fn|")
    cur = _report("r2", [_f("TEST-003", qualified, "testing", cat="MISSING_TEST",
                            symbol="Service.fn", line=49)])
    d = compute_delta(prev, cur)
    assert [e["id"] for e in d["persistent"]] == ["TEST-003"]
    assert d["resolved"] == [] and d["new"] == []


def test_persistent_on_same_line_when_symbols_differ():
    """Real runs named the same README example gap with symbol 'create_ticket' and '-'."""
    prev = _report("r1", [_f("DOC-005", "DOC_EXAMPLE_INVALID|sample-project/README.md|fn|curl",
                             "documentation", cat="DOC_EXAMPLE_INVALID",
                             file="sample-project/README.md", line=80)])
    cur = _report("r2", [_f("DOC-001", "DOC_EXAMPLE_INVALID|sample-project/README.md|-|value",
                            "documentation", cat="DOC_EXAMPLE_INVALID",
                            file="sample-project/README.md", symbol=None, line=80)])
    d = compute_delta(prev, cur)
    assert [e["id"] for e in d["persistent"]] == ["DOC-001"]


def test_symbolless_findings_on_different_lines_do_not_match():
    prev = _report("r1", [_f("DOC-001", "DOC_MISSING|sample-project/docs/api.md|-|a",
                             "documentation", cat="DOC_MISSING",
                             file="sample-project/docs/api.md", symbol=None, line=10)])
    cur = _report("r2", [_f("DOC-001", "DOC_MISSING|sample-project/docs/api.md|-|b",
                            "documentation", cat="DOC_MISSING",
                            file="sample-project/docs/api.md", symbol=None, line=90)])
    d = compute_delta(prev, cur)
    assert len(d["resolved"]) == 1 and len(d["new"]) == 1


def test_ambiguous_symbol_fallback_does_not_pair_different_gaps():
    """Several test gaps on one function: a fixed gap must not be paired with a new one."""
    prev = _report("r1", [
        _f("TEST-001", K_TEST.replace("null_value", "missing_value"), "testing", cat="MISSING_TEST"),
        _f("TEST-002", K_TEST, "testing", cat="MISSING_TEST"),
    ])
    cur = _report("r2", [_f("TEST-001", K_TEST.replace("null_value", "whitespace_value"),
                            "testing", cat="MISSING_TEST", line=48)])
    d = compute_delta(prev, cur)
    assert d["persistent"] == []
    assert sorted(e["id"] for e in d["resolved"]) == ["TEST-001", "TEST-002"]
    assert [e["id"] for e in d["new"]] == ["TEST-001"]


def test_key_match_is_not_stolen_by_weaker_rule():
    """A same-symbol fallback for one finding must not take another finding's exact key match."""
    k_other = K_CODE.replace("|value", "|other")
    prev = _report("r1", [
        _f("CODE-001", k_other, "code-review", line=10),
        _f("CODE-002", K_CODE, "code-review", line=20),
    ])
    cur = _report("r2", [_f("CODE-009", K_CODE, "code-review", line=20)])
    d = compute_delta(prev, cur)
    assert [(e["id"], e["previousId"]) for e in d["persistent"]] == [("CODE-009", "CODE-002")]
    assert [e["id"] for e in d["resolved"]] == ["CODE-001"]


def test_new_finding():
    prev = _report("r1", [])
    cur = _report("r2", [_f("DOC-001", K_DOC, "documentation", cat="DOC_MISSING",
                            file="sample-project/docs/api.md", symbol=None, sev="MEDIUM")])
    d = compute_delta(prev, cur)
    assert [e["id"] for e in d["new"]] == ["DOC-001"]


def test_mixed_states():
    prev = _report("r1", [
        _f("CODE-001", K_CODE, "code-review"),
        _f("TEST-001", K_TEST, "testing", cat="MISSING_TEST"),
        _f("DOC-001", K_DOC, "documentation", cat="DOC_MISSING",
           file="sample-project/docs/api.md", symbol=None),
    ])
    k_new = "LOGIC_ERROR|sample-project/app/service.py|other|limit"
    cur = _report("r2", [
        _f("TEST-004", K_TEST, "testing", cat="MISSING_TEST"),
        _f("CODE-001", k_new, "code-review", cat="LOGIC_ERROR", symbol="other"),
    ], statuses={"documentation": "error"})
    d = compute_delta(prev, cur)
    assert [e["id"] for e in d["resolved"]] == ["CODE-001"]
    assert [e["id"] for e in d["persistent"]] == ["TEST-004"]
    assert [e["key"] for e in d["new"]] == [k_new]
    assert [e["id"] for e in d["unverified"]] == ["DOC-001"]


def test_reverify_writes_files(tmp_path):
    for rid, rep in (("r1", _report("r1", [_f("CODE-001", K_CODE, "code-review")])),
                     ("r2", _report("r2", [], state="READY_FOR_HUMAN_REVIEW"))):
        (tmp_path / rid).mkdir()
        (tmp_path / rid / "report.json").write_text(json.dumps(rep), encoding="utf-8")
    d = reverify("r1", "r2", runs_dir=tmp_path)
    saved = json.loads((tmp_path / "r2" / "delta.json").read_text(encoding="utf-8"))
    assert saved == d
    md = (tmp_path / "r2" / "delta.md").read_text(encoding="utf-8")
    assert "Resolved (1)" in md and "CODE-001" in md
    assert "READY_FOR_HUMAN_REVIEW" in md


def test_missing_report_raises(tmp_path):
    (tmp_path / "r1").mkdir()
    with pytest.raises(FileNotFoundError):
        reverify("r1", "r2", runs_dir=tmp_path)


def test_render_handles_empty_delta():
    d = compute_delta(_report("r1", []), _report("r2", [], state="READY_FOR_HUMAN_REVIEW"))
    md = render_delta(d)
    assert md.count("None.") == 4
