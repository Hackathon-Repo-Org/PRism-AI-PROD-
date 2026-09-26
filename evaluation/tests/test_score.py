"""Unit tests for evaluation/score.py.

Uses hand-made report.json and seeded-issues.json fixtures.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from evaluation.score import _latency_ms, _matches_seed, _best_matching, score_run, summarise


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_seed(**overrides):
    base = {
        "id": "SEED-01",
        "agent": "testing",
        "acceptedCategories": ["MISSING_TEST"],
        "file": "sample-project/app/service.py",
        "symbols": ["create_ticket"],
        "subjectHints": ["null_priority"],
        "expected": "A test that sends null priority and asserts 422.",
        "reproduction": "POST /tickets with priority=null.",
        "presentIn": ["demo-bad"],
        "fixedIn": "demo-fixed",
    }
    return {**base, **overrides}


def _make_finding(**overrides):
    base = {
        "id": "TEST-001",
        "agent": "testing",
        "category": "MISSING_TEST",
        "file": "sample-project/app/service.py",
        "symbol": "create_ticket",
        "key": "MISSING_TEST|sample-project/app/service.py|create_ticket|null_priority",
        "severity": "MEDIUM",
        "title": "No test for null priority",
        "description": "...",
        "evidence": "...",
        "evidenceType": "source-analysis",
        "recommendation": "...",
        "relatedFiles": [],
        "line": None,
    }
    return {**base, **overrides}


def _make_report(findings, agents=None, generated_at="2026-01-01T00:01:00.000Z"):
    agents = agents or [
        {"agent": "testing", "status": "completed",
         "startedAt": "2026-01-01T00:00:00.000Z",
         "finishedAt": "2026-01-01T00:01:00.000Z",
         "durationMs": 60000}
    ]
    return {
        "schemaVersion": "1.0",
        "runId": "run-test",
        "snapshotId": "abc",
        "baseCommit": "base",
        "baseRef": "baseline-clean",
        "candidateRef": "demo-bad",
        "generatedAt": generated_at,
        "policyVersion": "1.0",
        "readiness": {"state": "ATTENTION_REQUIRED", "reasons": []},
        "agents": agents,
        "execution": None,
        "findings": [{**f, "agents": [f.get("agent", "testing")], "blocking": True}
                     for f in findings],
        "counts": {"total": len(findings)},
        "timeline": {},
        "limitations": [],
    }


def _setup_run(tmp_path, report, seeds):
    """Write report.json and seeded-issues.json into tmp_path structure."""
    run_dir = tmp_path / "runs" / "run-test"
    run_dir.mkdir(parents=True)
    (run_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    eval_dir = tmp_path / "evaluation"
    eval_dir.mkdir()
    raw_dir = eval_dir / "raw-results"
    raw_dir.mkdir()
    (eval_dir / "seeded-issues.json").write_text(json.dumps(seeds), encoding="utf-8")
    return tmp_path


def _patch_paths(monkeypatch, tmp_path):
    """Redirect module-level paths to tmp_path."""
    import evaluation.score as mod
    monkeypatch.setattr(mod, "RUNS_DIR",   tmp_path / "runs")
    monkeypatch.setattr(mod, "SEEDS_PATH", tmp_path / "evaluation" / "seeded-issues.json")
    monkeypatch.setattr(mod, "RAW_DIR",    tmp_path / "evaluation" / "raw-results")
    monkeypatch.setattr(mod, "RESULTS_MD", tmp_path / "evaluation" / "results.md")
    return mod


# ---------------------------------------------------------------------------
# _latency_ms
# ---------------------------------------------------------------------------

class TestLatency:
    def test_ignores_synthetic_epoch_start(self):
        agents = [
            {"agent": "testing", "status": "completed",
             "startedAt": "2026-01-01T00:00:00.000Z",
             "finishedAt": "2026-01-01T00:00:30.000Z", "durationMs": 30000},
            {"agent": "code-review", "status": "timeout",
             "startedAt": "1970-01-01T00:00:00.000Z",
             "finishedAt": "1970-01-01T00:00:00.000Z", "durationMs": 0},
        ]
        assert _latency_ms(_make_report([], agents=agents)) == 60000

    def test_only_synthetic_rows_returns_none(self):
        agents = [{"agent": "testing", "status": "timeout",
                   "startedAt": "1970-01-01T00:00:00.000Z",
                   "finishedAt": "1970-01-01T00:00:00.000Z", "durationMs": 0}]
        assert _latency_ms(_make_report([], agents=agents)) is None


# ---------------------------------------------------------------------------
# _matches_seed
# ---------------------------------------------------------------------------

class TestMatchesSeed:
    def test_exact_match(self):
        assert _matches_seed(_make_finding(), _make_seed())

    def test_class_qualified_symbol_matches(self):
        assert _matches_seed(_make_finding(symbol="TicketService.create_ticket"), _make_seed())

    def test_different_qualified_symbol_does_not_match(self):
        assert not _matches_seed(_make_finding(symbol="TicketService.get_ticket"),
                                 _make_seed())

    def test_wrong_agent(self):
        assert not _matches_seed(_make_finding(agent="testing"),
                                 _make_seed(agent="code-review"))

    def test_wrong_category(self):
        assert not _matches_seed(_make_finding(category="MISSING_TEST"),
                                 _make_seed(acceptedCategories=["TEST_FAILURE"]))

    def test_wrong_file(self):
        assert not _matches_seed(
            _make_finding(file="sample-project/app/service.py"),
            _make_seed(file="sample-project/app/main.py"),
        )

    def test_wrong_symbol(self):
        assert not _matches_seed(_make_finding(symbol="create_ticket"),
                                 _make_seed(symbols=["update_status"]))

    def test_empty_symbols_skips_check(self):
        assert _matches_seed(_make_finding(symbol="anything"), _make_seed(symbols=[]))

    def test_hint_not_in_key(self):
        assert not _matches_seed(
            _make_finding(key="MISSING_TEST|file|sym|null_priority"),
            _make_seed(subjectHints=["unsupported_status"]),
        )

    def test_empty_hints_skips_check(self):
        assert _matches_seed(
            _make_finding(key="MISSING_TEST|file|sym|anything"),
            _make_seed(subjectHints=[]),
        )


# ---------------------------------------------------------------------------
# _best_matching — now returns (matched, ambiguous_ids, unmatched)
# ---------------------------------------------------------------------------

class TestBestMatching:
    def test_single_match(self):
        matched, amb_seeds, amb_findings, unmatched = _best_matching([_make_finding()], [_make_seed()])
        assert matched == {"SEED-01": "TEST-001"}
        assert amb_seeds == []
        assert amb_findings == []
        assert unmatched == []

    def test_no_match(self):
        matched, amb_seeds, amb_findings, unmatched = _best_matching(
            [_make_finding(agent="testing")],
            [_make_seed(agent="code-review")],
        )
        assert matched == {}
        assert len(unmatched) == 1

    def test_extra_finding_is_unmatched(self):
        seeds = [_make_seed()]
        f1    = _make_finding(id="TEST-001")
        f2    = _make_finding(id="TEST-002")
        matched, _, _, unmatched = _best_matching([f1, f2], seeds)
        assert len(matched) == 1
        assert len(unmatched) == 1

    def test_multiple_seeds_multiple_findings(self):
        s1 = _make_seed(id="SEED-01", symbols=["f1"], subjectHints=["a"], file="file1.py")
        s2 = _make_seed(id="SEED-02", symbols=["f2"], subjectHints=["b"], file="file2.py")
        f1 = _make_finding(id="T-01", symbol="f1", key="X|file1.py|f1|a", file="file1.py")
        f2 = _make_finding(id="T-02", symbol="f2", key="X|file2.py|f2|b", file="file2.py")
        matched, amb_seeds, amb_findings, unmatched = _best_matching([f1, f2], [s1, s2])
        assert len(matched) == 2
        assert unmatched == []
        assert amb_findings == []

    # ── Ambiguity tests (Item 3) ───────────────────────────────────────────

    def test_ambiguous_seed_flagged(self):
        """Two findings both match the same seed → ambiguousSeeds contains that seed."""
        seed = _make_seed()
        f1   = _make_finding(id="TEST-001")
        f2   = _make_finding(id="TEST-002")  # same criteria as f1
        matched, amb_seeds, amb_findings, unmatched = _best_matching([f1, f2], [seed])
        assert "SEED-01" in amb_seeds
        assert len(matched) == 1
        assert len(unmatched) == 1

    def test_no_ambiguity_when_one_candidate(self):
        """Single compatible finding → no ambiguity on either side."""
        matched, amb_seeds, amb_findings, unmatched = _best_matching([_make_finding()], [_make_seed()])
        assert amb_seeds == []
        assert amb_findings == []

    def test_ambiguity_does_not_reduce_recall(self):
        """Two seeds each matching a different finding → no ambiguity, full recall."""
        s1 = _make_seed(id="SEED-01", subjectHints=["alpha"], key_suffix="alpha")
        s2 = _make_seed(id="SEED-02", subjectHints=["beta"],
                        file="sample-project/app/main.py", symbols=["other"])
        f1 = _make_finding(id="F-01",
                           key="MISSING_TEST|sample-project/app/service.py|create_ticket|alpha")
        f2 = _make_finding(id="F-02", file="sample-project/app/main.py", symbol="other",
                           key="MISSING_TEST|sample-project/app/main.py|other|beta")
        matched, amb_seeds, amb_findings, unmatched = _best_matching([f1, f2], [s1, s2])
        assert len(matched) == 2
        assert amb_seeds == []
        assert amb_findings == []

    def test_one_finding_compatible_with_two_seeds_flagged_as_ambiguous_finding(self):
        """A10: 'If ONE FINDING could match SEVERAL SEEDS, flag the ambiguity.'
        One finding compatible with two seeds must appear in ambiguousFindings.

        BEFORE THE FIX: _best_matching only returns 3 values, so this test
        fails with a ValueError (not enough values to unpack) — proving the
        signal does not exist yet.
        AFTER THE FIX: returns 4 values; F-01 is in ambiguous_findings.
        """
        s1 = _make_seed(id="SEED-01", symbols=[])  # symbols=[] → skip symbol check
        s2 = _make_seed(id="SEED-02", symbols=[])  # same file/agent/category/hints
        f  = _make_finding(id="F-01")
        # After the fix _best_matching returns (matched, ambiguous_seeds,
        # ambiguous_findings, unmatched).  Unpack all four — this line itself
        # fails on the current code because only 3 values are returned.
        matched, ambiguous_seeds, ambiguous_findings, unmatched = _best_matching([f], [s1, s2])
        assert "F-01" in ambiguous_findings, (
            "Finding F-01 is compatible with two seeds and must be in ambiguous_findings"
        )
        # Seed-side: each seed has exactly 1 candidate → ambiguous_seeds is empty
        assert ambiguous_seeds == []


# ---------------------------------------------------------------------------
# score_run (integration — uses tmp_path)
# ---------------------------------------------------------------------------

class TestScoreRun:
    def test_perfect_recall(self, tmp_path, monkeypatch):
        seeds = [_make_seed()]
        findings = [_make_finding()]
        _setup_run(tmp_path, _make_report(findings), seeds)
        _patch_paths(monkeypatch, tmp_path)
        result = score_run("run-test", "demo-bad")
        assert result["seedsMatched"] == 1
        assert result["seedsPresent"] == 1
        assert result["overallRecall"] == 1.0

    def test_zero_recall(self, tmp_path, monkeypatch):
        seeds = [_make_seed()]
        findings = [_make_finding(agent="code-review", category="LOGIC_ERROR")]
        _setup_run(tmp_path, _make_report(findings), seeds)
        _patch_paths(monkeypatch, tmp_path)
        result = score_run("run-test", "demo-bad")
        assert result["seedsMatched"] == 0
        assert result["overallRecall"] == 0.0

    def test_ref_filter(self, tmp_path, monkeypatch):
        """Seeds not present in the given ref should not be scored."""
        seeds = [
            _make_seed(id="SEED-01", presentIn=["demo-bad"]),
            _make_seed(id="SEED-02", presentIn=["heldout-variation"],
                       symbols=["other_func"], file="sample-project/app/main.py"),
        ]
        _setup_run(tmp_path, _make_report([_make_finding()]), seeds)
        _patch_paths(monkeypatch, tmp_path)
        result = score_run("run-test", "demo-bad")
        assert result["seedsPresent"] == 1  # only SEED-01 is present in demo-bad

    def test_writes_raw_json_and_csv(self, tmp_path, monkeypatch):
        seeds = [_make_seed()]
        _setup_run(tmp_path, _make_report([_make_finding()]), seeds)
        mod = _patch_paths(monkeypatch, tmp_path)
        score_run("run-test", "demo-bad")
        assert (mod.RAW_DIR / "run-test.json").exists()
        assert (mod.RAW_DIR / "run-test-adjudication.csv").exists()

    def test_unmatched_finding_in_csv(self, tmp_path, monkeypatch):
        """An unmatched finding should appear as UNMATCHED row in adjudication CSV."""
        seeds = []  # No seeds → finding is unmatched
        _setup_run(tmp_path, _make_report([_make_finding()]), seeds)
        mod = _patch_paths(monkeypatch, tmp_path)
        score_run("run-test", "demo-bad")
        csv_path = mod.RAW_DIR / "run-test-adjudication.csv"
        text = csv_path.read_text()
        assert "TEST-001" in text
        assert "UNMATCHED" in text

    def test_ambiguous_seed_in_raw_json_and_csv(self, tmp_path, monkeypatch):
        """Seed-side ambiguity: two findings match the same seed.
        ambiguousSeeds is populated; non-chosen finding appears as AMBIGUOUS-SEED row."""
        seed = _make_seed()
        f1 = _make_finding(id="TEST-001")
        f2 = _make_finding(id="TEST-002")  # same criteria → seed-side ambiguous
        _setup_run(tmp_path, _make_report([f1, f2]), [seed])
        mod = _patch_paths(monkeypatch, tmp_path)
        result = score_run("run-test", "demo-bad")

        assert "SEED-01" in result["ambiguousSeeds"]
        csv_text = (mod.RAW_DIR / "run-test-adjudication.csv").read_text()
        assert "AMBIGUOUS-SEED" in csv_text

    def test_ambiguous_finding_in_raw_json_and_csv(self, tmp_path, monkeypatch):
        """Finding-side ambiguity (A10 primary case): one finding compatible with two seeds.
        ambiguousFindings is populated; finding appears as AMBIGUOUS-FINDING row with
        semicolon-separated compatibleSeeds."""
        # Two seeds that both accept the same finding (symbols=[] skips symbol check)
        s1 = _make_seed(id="SEED-01", symbols=[])
        s2 = _make_seed(id="SEED-02", symbols=[])
        f  = _make_finding(id="TEST-001")
        _setup_run(tmp_path, _make_report([f]), [s1, s2])
        mod = _patch_paths(monkeypatch, tmp_path)
        result = score_run("run-test", "demo-bad")

        # ambiguousFindings must be populated
        assert "TEST-001" in result["ambiguousFindings"], (
            f"Expected TEST-001 in ambiguousFindings, got: {result['ambiguousFindings']}"
        )
        # ambiguousSeeds must be empty (each seed has only 1 compatible finding)
        assert result["ambiguousSeeds"] == []

        # CSV must contain AMBIGUOUS-FINDING row with both seed IDs
        csv_text = (mod.RAW_DIR / "run-test-adjudication.csv").read_text()
        assert "AMBIGUOUS-FINDING" in csv_text
        # compatibleSeeds must list both seed IDs (semicolon-separated)
        assert "SEED-01" in csv_text
        assert "SEED-02" in csv_text

    def test_ambiguous_seed_empty_when_no_ambiguity(self, tmp_path, monkeypatch):
        """No ambiguity when each seed has exactly one compatible finding."""
        seeds = [_make_seed()]
        _setup_run(tmp_path, _make_report([_make_finding()]), seeds)
        _patch_paths(monkeypatch, tmp_path)
        result = score_run("run-test", "demo-bad")
        assert result["ambiguousSeeds"] == []
        assert result["ambiguousFindings"] == []

    def test_placeholder_seeds_ignored(self, tmp_path, monkeypatch):
        """Seeds with _comment key (placeholder entries) must not be scored."""
        seeds = [
            {"_comment": "placeholder", "_status": "TBD",
             "id": "SEED-PLACEHOLDER", "presentIn": ["demo-bad"]},
            _make_seed(),
        ]
        _setup_run(tmp_path, _make_report([_make_finding()]), seeds)
        _patch_paths(monkeypatch, tmp_path)
        result = score_run("run-test", "demo-bad")
        # Only the real seed should be counted
        assert result["seedsPresent"] == 1


# ---------------------------------------------------------------------------
# summarise() — output structure tests (Fix 4)
# ---------------------------------------------------------------------------

class TestSummarise:
    def _run_summarise(self, tmp_path, monkeypatch, raw_results):
        """Write raw result files, run summarise(), return results.md text."""
        eval_dir = tmp_path / "evaluation"
        raw_dir  = eval_dir / "raw-results"
        raw_dir.mkdir(parents=True)
        results_md = eval_dir / "results.md"

        for r in raw_results:
            (raw_dir / f"{r['runId']}.json").write_text(json.dumps(r), encoding="utf-8")

        mod = _patch_paths(monkeypatch, tmp_path)
        summarise()
        return results_md.read_text(encoding="utf-8") if results_md.exists() else ""

    def _make_raw(self, run_id="run-01", ref="demo-bad", recall=1.0, latency=5000,
                  readiness="ATTENTION_REQUIRED"):
        return {
            "schemaVersion":    "1.0",
            "runId":            run_id,
            "ref":              ref,
            "scoredAt":         "2026-01-01T00:00:00.000Z",
            "seedsPresent":     1,
            "seedsMatched":     1 if recall == 1.0 else 0,
            "overallRecall":    recall,
            "recallPerAgent":   {},
            "seedHits":         {"SEED-01": "TEST-001" if recall == 1.0 else None},
            "ambiguousSeeds":   [],
            "latencyMs":        latency,
            "agentStatuses":    {"testing": "completed"},
            "readinessState":   readiness,
            "totalFindings":    1,
            "unmatchedFindingCount": 0,
        }

    def test_recall_shown_with_n(self, tmp_path, monkeypatch):
        raw = [self._make_raw(run_id="r1"), self._make_raw(run_id="r2")]
        md = self._run_summarise(tmp_path, monkeypatch, raw)
        assert "n=2" in md
        assert "§3.1 Seed recall" in md

    def test_latency_shown_with_n(self, tmp_path, monkeypatch):
        raw = [self._make_raw(run_id="r1", latency=3000),
               self._make_raw(run_id="r2", latency=5000)]
        md = self._run_summarise(tmp_path, monkeypatch, raw)
        assert "§3.6 Latency" in md
        assert "n=2" in md

    def test_precision_na_when_no_adjudication(self, tmp_path, monkeypatch):
        """Precision must show N/A when NO adjudication CSV exists at all,
        even when seedsMatched > 0.  seedsMatched alone must never yield a
        percentage — only filled adjudication verdicts count.
        Regression test for the fabricated-100%-precision bug.
        """
        # seedsMatched=3, no CSV on disk → must NOT emit a percentage
        raw = [self._make_raw(run_id="r1")]  # _make_raw has seedsMatched=1 by default
        # Override to 3 matched seeds to make the bug clearer
        raw[0]["seedsMatched"] = 3
        raw[0]["overallRecall"] = 1.0
        md = self._run_summarise(tmp_path, monkeypatch, raw)
        # Must find the §3.2 heading
        assert "§3.2 Precision" in md
        # The §3.2 line specifically must say N/A
        precision_line = next(
            (l for l in md.splitlines() if "§3.2 Precision" in l), ""
        )
        assert "N/A" in precision_line, (
            f"Expected 'N/A' in §3.2 line, got: {precision_line!r}"
        )
        # Must NOT contain a percentage on the §3.2 line
        assert "%" not in precision_line, (
            f"Found fabricated percentage in §3.2 line: {precision_line!r}"
        )

    def test_parallel_speedup_na_when_missing(self, tmp_path, monkeypatch):
        raw = [self._make_raw()]
        md = self._run_summarise(tmp_path, monkeypatch, raw)
        assert "§3.7 Parallel speedup" in md
        assert "N/A" in md

    def test_parallel_speedup_computed_when_present(self, tmp_path, monkeypatch):
        raw = [
            self._make_raw(run_id="seq-run", ref="timing-sequential", latency=10000),
            self._make_raw(run_id="par-run", ref="timing-parallel",   latency=5000),
        ]
        md = self._run_summarise(tmp_path, monkeypatch, raw)
        assert "§3.7 Parallel speedup" in md
        assert "2.00×" in md
        assert "10000 ms" in md
        assert "5000 ms" in md

    def test_action_quality_na_when_no_action_results(self, tmp_path, monkeypatch):
        raw = [self._make_raw()]
        md = self._run_summarise(tmp_path, monkeypatch, raw)
        assert "§3.9 Action quality" in md
        assert "N/A" in md

    def test_action_quality_shown_when_present(self, tmp_path, monkeypatch):
        raw = [self._make_raw()]
        # Write a fake action-result.json
        runs_dir = tmp_path / "runs" / "run-01"
        runs_dir.mkdir(parents=True, exist_ok=True)
        ar = {
            "schemaVersion": "1.0", "runId": "run-01",
            "generated": 2, "executed": 2, "passed": 2, "failed": 2,
            "skipped": 0, "patchPath": "runs/run-01/patches/missing-tests.patch",
            "notes": "New tests fail on buggy candidate as expected.",
        }
        (runs_dir / "action-result.json").write_text(json.dumps(ar))
        mod = _patch_paths(monkeypatch, tmp_path)
        # Also need RUNS_DIR to see the action-result files
        monkeypatch.setattr(mod, "RUNS_DIR", tmp_path / "runs")
        # Write raw result files
        raw_dir = tmp_path / "evaluation" / "raw-results"
        raw_dir.mkdir(parents=True, exist_ok=True)
        for r in raw:
            (raw_dir / f"{r['runId']}.json").write_text(json.dumps(r))
        summarise()
        md = (tmp_path / "evaluation" / "results.md").read_text(encoding="utf-8")
        assert "§3.9 Action quality" in md
        assert "proof of detection" in md
        assert "run-01" in md

    def test_action_quality_real_n_not_hardcoded(self, tmp_path, monkeypatch):
        """§3.9 must show n=<real count>, never a hard-coded 'n=1' when >1 result exists.
        Regression test for Item 2(c)."""
        raw = [self._make_raw()]
        runs_dir = tmp_path / "runs"
        # Write TWO action-result files
        for rid in ("run-a", "run-b"):
            d = runs_dir / rid
            d.mkdir(parents=True, exist_ok=True)
            ar = {"schemaVersion": "1.0", "runId": rid,
                  "generated": 1, "executed": 1, "passed": 1, "failed": 1,
                  "skipped": 0, "patchPath": f"runs/{rid}/patches/p.patch", "notes": ""}
            (d / "action-result.json").write_text(json.dumps(ar))
        mod = _patch_paths(monkeypatch, tmp_path)
        monkeypatch.setattr(mod, "RUNS_DIR", runs_dir)
        raw_dir = tmp_path / "evaluation" / "raw-results"
        raw_dir.mkdir(parents=True, exist_ok=True)
        for r in raw:
            (raw_dir / f"{r['runId']}.json").write_text(json.dumps(r))
        summarise()
        md = (tmp_path / "evaluation" / "results.md").read_text(encoding="utf-8")
        # Must say n=2, not n=1
        assert "n=2" in md
        # The hard-coded "n=1" string must not appear in the action quality section
        action_section_start = md.find("§3.9 Action quality")
        action_section = md[action_section_start:] if action_section_start != -1 else ""
        assert "n=1 —" not in action_section, (
            "Hard-coded 'n=1' appeared in §3.9 section even though 2 results exist"
        )

    def test_fp_control_section_shown_for_control_ref(self, tmp_path, monkeypatch):
        raw = [self._make_raw(ref="control-clean-change", readiness="READY_FOR_HUMAN_REVIEW")]
        md = self._run_summarise(tmp_path, monkeypatch, raw)
        assert "§3.3 False positives on control" in md

    def test_fp_control_missing_report_excluded_not_zero(self, tmp_path, monkeypatch):
        """When report.json is absent, §3.3 must exclude that run, not count it as 0.
        Regression test for Item 2(a)."""
        # Two control runs: r1 has no report.json, r2 has a clean report (0 MEDIUM+ findings)
        raw = [
            self._make_raw(run_id="r1", ref="control-clean-change"),
            self._make_raw(run_id="r2", ref="control-clean-change"),
        ]
        # Write report.json only for r2 (0 MEDIUM+ findings)
        runs_dir = tmp_path / "runs"
        r2_dir = runs_dir / "r2"
        r2_dir.mkdir(parents=True, exist_ok=True)
        report = {
            "schemaVersion": "1.0", "runId": "r2", "snapshotId": "abc",
            "generatedAt": "2026-01-01T00:01:00.000Z",
            "readiness": {"state": "READY_FOR_HUMAN_REVIEW", "reasons": []},
            "agents": [], "execution": None, "findings": [],
            "counts": {"total": 0}, "timeline": {}, "limitations": [],
        }
        (r2_dir / "report.json").write_text(json.dumps(report))
        # r1 has no report.json at all
        mod = _patch_paths(monkeypatch, tmp_path)
        monkeypatch.setattr(mod, "RUNS_DIR", runs_dir)
        md = self._run_summarise(tmp_path, monkeypatch, raw)

        # §3.3 must appear (r2 is measurable)
        assert "§3.3 False positives on control" in md
        # Must say n=1 (only r2 contributes), not n=2
        fp_line = next(
            (l for l in md.splitlines() if "§3.3 False positives" in l), ""
        )
        assert "n=1" in fp_line, (
            f"Expected n=1 (only 1 measurable run), got: {fp_line!r}"
        )
        # Must mention r1 was excluded
        assert "r1" in md, "Expected r1 to be named as excluded run"

    def test_fp_control_section_not_shown_for_demo_bad(self, tmp_path, monkeypatch):
        raw = [self._make_raw(ref="demo-bad")]
        md = self._run_summarise(tmp_path, monkeypatch, raw)
        assert "§3.3 False positives on control" not in md

    def test_reliability_shown_with_n(self, tmp_path, monkeypatch):
        raw = [self._make_raw(run_id="r1"), self._make_raw(run_id="r2")]
        md = self._run_summarise(tmp_path, monkeypatch, raw)
        assert "§3.8 Reliability" in md
        assert "n=2" in md

    # ── §3.4 Held-out recall tests (Item 4) ──────────────────────────────

    def test_heldout_recall_na_when_no_heldout_runs(self, tmp_path, monkeypatch):
        """When no heldout-variation runs are scored, §3.4 must emit N/A with reason."""
        raw = [self._make_raw(ref="demo-bad")]
        md = self._run_summarise(tmp_path, monkeypatch, raw)
        assert "§3.4 Held-out recall" in md
        heldout_line = next(
            (l for l in md.splitlines() if "§3.4 Held-out" in l or "N/A" in l and "heldout" in l.lower()),
            ""
        )
        # The N/A section must appear somewhere after the §3.4 heading
        heldout_start = md.find("§3.4 Held-out recall")
        assert heldout_start != -1
        heldout_section = md[heldout_start : heldout_start + 300]
        assert "N/A" in heldout_section

    def test_heldout_recall_computed_when_runs_present(self, tmp_path, monkeypatch):
        """When heldout-variation runs are present, §3.4 shows count/n and seed breakdown."""
        # 2 of 3 runs found the held-out seed
        raw = [
            self._make_raw(run_id="h1", ref="heldout-variation", recall=1.0),
            self._make_raw(run_id="h2", ref="heldout-variation", recall=0.0),
            self._make_raw(run_id="h3", ref="heldout-variation", recall=1.0),
        ]
        md = self._run_summarise(tmp_path, monkeypatch, raw)
        assert "§3.4 Held-out recall" in md
        heldout_line = next(
            (l for l in md.splitlines() if "§3.4 Held-out recall" in l), ""
        )
        # 2 out of 3 runs had seedsMatched > 0
        assert "2/3" in heldout_line, (
            f"Expected '2/3' in §3.4 line, got: {heldout_line!r}"
        )
        assert "n=3" in heldout_line
