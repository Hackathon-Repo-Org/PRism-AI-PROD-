"""PRism-AI Evaluation Scorer.

Usage:
    # Score a single run against a ref tag:
    python evaluation/score.py --run <runId> --ref demo-bad

    # Summarise all scored runs into evaluation/results.md:
    python evaluation/score.py --summarise

The scorer reads evaluation/seeded-issues.json and runs/<runId>/report.json.
It writes:
  - evaluation/raw-results/<runId>.json        (per-run scored result)
  - evaluation/raw-results/<runId>-adjudication.csv  (unmatched + ambiguous findings)

--summarise reads all raw-results/*.json (+ filled adjudication CSVs) and writes
  evaluation/results.md.

Ambiguity (A10):
  When a finding could match more than one seed, the scorer records that seed in
  "ambiguousSeeds" in the raw-results JSON and emits an "AMBIGUOUS" row in the
  adjudication CSV for human adjudication.  The greedy algorithm (fewest-options-
  first) is documented in benchmark.md §3.1 as a heuristic approximation; it
  maximises the number of seeds matched but does not guarantee a globally optimal
  assignment in all edge cases.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT   = Path(__file__).resolve().parent.parent
EVAL_DIR    = REPO_ROOT / "evaluation"
SEEDS_PATH  = EVAL_DIR / "seeded-issues.json"
RAW_DIR     = EVAL_DIR / "raw-results"
RESULTS_MD  = EVAL_DIR / "results.md"
RUNS_DIR    = REPO_ROOT / "runs"


# ---------------------------------------------------------------------------
# Seed matching (A10)
# ---------------------------------------------------------------------------

def _matches_seed(finding: Dict[str, Any], seed: Dict[str, Any]) -> bool:
    """Return True when finding matches seed per A10 rules."""
    if finding.get("agent") != seed.get("agent"):
        return False
    if finding.get("category") not in seed.get("acceptedCategories", []):
        return False
    if finding.get("file") != seed.get("file"):
        return False
    symbols = seed.get("symbols") or []
    symbol = finding.get("symbol") or ""
    # "TicketService.create_ticket" names the same function as "create_ticket".
    if symbols and symbol not in symbols and symbol.rsplit(".", 1)[-1] not in symbols:
        return False
    hints = seed.get("subjectHints") or []
    if hints:
        key = finding.get("key", "")
        parts = key.split("|")
        subject = parts[-1].lower() if parts else ""
        if not any(h.lower() in subject for h in hints):
            return False
    return True


def _best_matching(
    findings: List[Dict[str, Any]],
    seeds: List[Dict[str, Any]],
) -> Tuple[Dict[str, str], List[str], List[str], List[Dict[str, Any]]]:
    """Greedy maximum bipartite matching: seeds × findings.

    Algorithm (see benchmark.md §3.1 — greedy heuristic):
      Build a compatibility matrix.  Process seeds in fewest-compatible-
      findings-first order; assign each seed the first unassigned compatible
      finding.  Maximises recall for tightly-constrained seeds first.

    Returns a 4-tuple:
      matched_map:        {seed_id: finding_id}
      ambiguous_seed_ids: seed IDs where >1 finding was compatible
                          (seed-side ambiguity — non-chosen finding needs review)
      ambiguous_finding_ids: finding IDs compatible with >1 seed
                          (A10 case: "one finding could match several seeds")
      unmatched_findings: findings not assigned to any seed
    """
    # Build compatibility matrices in both directions
    # seed_id   → [compatible finding_ids]
    compat_s: Dict[str, List[str]] = {s["id"]: [] for s in seeds}
    # finding_id → [compatible seed_ids]
    compat_f: Dict[str, List[str]] = {f["id"]: [] for f in findings}

    for seed in seeds:
        for f in findings:
            if _matches_seed(f, seed):
                compat_s[seed["id"]].append(f["id"])
                compat_f[f["id"]].append(seed["id"])

    used_findings: Set[str] = set()
    matched: Dict[str, str] = {}

    # Greedy: seeds with fewest options first
    for seed in sorted(seeds, key=lambda s: len(compat_s[s["id"]])):
        sid = seed["id"]
        for fid in compat_s[sid]:
            if fid not in used_findings:
                matched[sid] = fid
                used_findings.add(fid)
                break

    # Seed-side ambiguity: seeds where >1 finding was compatible
    ambiguous_seed_ids: List[str] = [
        sid for sid, fids in compat_s.items() if len(fids) > 1
    ]

    # Finding-side ambiguity (A10): findings compatible with >1 seed
    ambiguous_finding_ids: List[str] = [
        fid for fid, sids in compat_f.items() if len(sids) > 1
    ]

    unmatched = [f for f in findings if f["id"] not in used_findings]
    return matched, ambiguous_seed_ids, ambiguous_finding_ids, unmatched


# ---------------------------------------------------------------------------
# Latency calculation
# ---------------------------------------------------------------------------

def _latency_ms(report: Dict[str, Any]) -> Optional[int]:
    """Wall-clock latency in ms: earliest agent startedAt → report generatedAt."""
    try:
        generated_at = report.get("generatedAt", "")
        agents = report.get("agents", [])
        # Missing/timed-out agents get a synthetic 1970 startedAt; skip them.
        started_ats = [
            a["startedAt"] for a in agents
            if a.get("startedAt") and not a["startedAt"].startswith("1970-")
        ]
        if not started_ats or not generated_at:
            return None
        earliest = min(started_ats)

        def _parse(s: str) -> datetime:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))

        return int((_parse(generated_at) - _parse(earliest)).total_seconds() * 1000)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Score a single run
# ---------------------------------------------------------------------------

def score_run(run_id: str, ref: str) -> Dict[str, Any]:
    """Score a run against a ref tag. Writes raw-results JSON and adjudication CSV."""
    report_path = RUNS_DIR / run_id / "report.json"
    if not report_path.exists():
        raise FileNotFoundError(f"report.json not found: {report_path}")
    if not SEEDS_PATH.exists():
        raise FileNotFoundError(f"seeded-issues.json not found: {SEEDS_PATH}")

    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    all_seeds: List[Dict] = json.loads(SEEDS_PATH.read_text(encoding="utf-8-sig"))

    # Filter out placeholder/comment entries and filter by ref
    seeds = [
        s for s in all_seeds
        if not s.get("_comment") and ref in s.get("presentIn", [])
    ]

    # Annotate findings with agent (report findings carry an "agents" list)
    findings: List[Dict[str, Any]] = []
    for f in report.get("findings", []):
        agents_list = f.get("agents", [])
        agent = agents_list[0] if agents_list else f.get("agent", "unknown")
        findings.append({**f, "agent": agent})

    # Match — 4-tuple: matched, ambiguous_seeds, ambiguous_findings, unmatched
    matched_map, ambiguous_seed_ids, ambiguous_finding_ids, unmatched = \
        _best_matching(findings, seeds)

    seed_hits: Dict[str, Optional[str]] = {
        seed["id"]: matched_map.get(seed["id"]) for seed in seeds
    }

    # Recall per agent
    agent_seeds: Dict[str, List[str]] = {}
    for seed in seeds:
        agent_seeds.setdefault(seed["agent"], []).append(seed["id"])

    recall_per_agent: Dict[str, Dict[str, Any]] = {}
    for agent, sids in agent_seeds.items():
        matched_count = sum(1 for sid in sids if sid in matched_map)
        recall_per_agent[agent] = {
            "matched": matched_count,
            "total":   len(sids),
            "recall":  matched_count / len(sids) if sids else None,
        }

    total_seeds   = len(seeds)
    total_matched = len(matched_map)
    overall_recall = total_matched / total_seeds if total_seeds else None

    latency        = _latency_ms(report)
    agent_statuses = {a["agent"]: a["status"] for a in report.get("agents", [])}
    readiness      = report.get("readiness", {}).get("state", "unknown")

    raw_result: Dict[str, Any] = {
        "schemaVersion":    "1.0",
        "runId":            run_id,
        "ref":              ref,
        "scoredAt":         datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "seedsPresent":     total_seeds,
        "seedsMatched":     total_matched,
        "overallRecall":    overall_recall,
        "recallPerAgent":   recall_per_agent,
        "seedHits":         seed_hits,
        # A10 seed-side: seeds where >1 finding was compatible
        "ambiguousSeeds":    ambiguous_seed_ids,
        # A10 finding-side: findings compatible with >1 seed (primary A10 case)
        "ambiguousFindings": ambiguous_finding_ids,
        "latencyMs":        latency,
        "agentStatuses":    agent_statuses,
        "readinessState":   readiness,
        "totalFindings":    len(findings),
        "unmatchedFindingCount": len(unmatched),
    }

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RAW_DIR / f"{run_id}.json"
    out_path.write_text(json.dumps(raw_result, indent=2), encoding="utf-8")

    # ---------------------------------------------------------------------------
    # Adjudication CSV
    # Three row types:
    #   UNMATCHED  — finding not assigned to any seed
    #   AMBIGUOUS-SEED    — seed had multiple compatible findings; non-chosen one listed
    #   AMBIGUOUS-FINDING — finding compatible with multiple seeds (A10 primary case)
    # ---------------------------------------------------------------------------
    # Build finding_id → [seed_ids] map (already computed inside _best_matching but
    # we rebuild it here for the CSV, avoiding exposure of internals)
    finding_compat_seeds: Dict[str, List[str]] = {}
    for seed in seeds:
        for f in findings:
            if _matches_seed(f, seed):
                finding_compat_seeds.setdefault(f["id"], []).append(seed["id"])

    csv_path = RAW_DIR / f"{run_id}-adjudication.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["runId", "rowType", "findingId", "findingKey", "agent",
                        "category", "severity", "title",
                        "compatibleSeeds", "verdict"],
        )
        writer.writeheader()

        # UNMATCHED rows: findings not assigned to any seed
        for f in unmatched:
            writer.writerow({
                "runId":           run_id,
                "rowType":         "UNMATCHED",
                "findingId":       f.get("id", ""),
                "findingKey":      f.get("key", ""),
                "agent":           f.get("agent", ""),
                "category":        f.get("category", ""),
                "severity":        f.get("severity", ""),
                "title":           f.get("title", ""),
                "compatibleSeeds": "",
                "verdict":         "",  # human fills: TP-extra / FP / duplicate
            })

        # AMBIGUOUS-SEED rows: for each ambiguous seed, the non-chosen compatible findings
        for sid in ambiguous_seed_ids:
            chosen_fid = matched_map.get(sid)
            seed_obj   = next(s for s in seeds if s["id"] == sid)
            for f in findings:
                if _matches_seed(f, seed_obj) and f["id"] != chosen_fid:
                    writer.writerow({
                        "runId":           run_id,
                        "rowType":         "AMBIGUOUS-SEED",
                        "findingId":       f.get("id", ""),
                        "findingKey":      f.get("key", ""),
                        "agent":           f.get("agent", ""),
                        "category":        f.get("category", ""),
                        "severity":        f.get("severity", ""),
                        "title":           f.get("title", ""),
                        "compatibleSeeds": sid,
                        "verdict":         "",
                    })

        # AMBIGUOUS-FINDING rows: findings compatible with multiple seeds (A10 primary case)
        # One row per such finding; compatibleSeeds lists all matching seed IDs.
        for fid in ambiguous_finding_ids:
            f = next((x for x in findings if x["id"] == fid), None)
            if f is None:
                continue
            seed_ids_str = ";".join(finding_compat_seeds.get(fid, []))
            writer.writerow({
                "runId":           run_id,
                "rowType":         "AMBIGUOUS-FINDING",
                "findingId":       f.get("id", ""),
                "findingKey":      f.get("key", ""),
                "agent":           f.get("agent", ""),
                "category":        f.get("category", ""),
                "severity":        f.get("severity", ""),
                "title":           f.get("title", ""),
                "compatibleSeeds": seed_ids_str,  # semicolon-separated list
                "verdict":         "",
            })

    return raw_result


# ---------------------------------------------------------------------------
# Summarise all runs
# ---------------------------------------------------------------------------

def _read_adjudication(csv_path: Path) -> Dict[str, str]:
    """Returns {findingId: verdict} from a filled adjudication CSV (all row types)."""
    result: Dict[str, str] = {}
    if not csv_path.exists():
        return result
    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            fid     = row.get("findingId", "")
            verdict = row.get("verdict", "").strip()
            if fid and verdict:
                result[fid] = verdict
    return result


def _fmt_n(values: list, fmt_fn) -> str:
    """Format a metric value with n=<sample_size>, or 'N/A' if no data."""
    if not values:
        return "N/A"
    return f"{fmt_fn(values)}, n={len(values)}"


def summarise() -> None:
    """Read all raw-results/*.json (+ adjudication CSVs) and write results.md."""
    raw_files = sorted(RAW_DIR.glob("*.json"))
    if not raw_files:
        print("No raw result files found in evaluation/raw-results/")
        return

    results: List[Dict[str, Any]] = []
    for p in raw_files:
        try:
            results.append(json.loads(p.read_text(encoding="utf-8-sig")))
        except Exception as exc:
            print(f"Warning: skipping {p.name}: {exc}")

    if not results:
        print("No valid result files to summarise.")
        return

    # Group by ref
    by_ref: Dict[str, List[Dict]] = {}
    for r in results:
        by_ref.setdefault(r.get("ref", "unknown"), []).append(r)

    lines: List[str] = ["# PRism-AI Evaluation Results", ""]
    lines.append(f"*Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*")
    lines.append(f"*Total runs scored: {len(results)}*")
    lines.append("")

    for ref, runs in sorted(by_ref.items()):
        lines.append(f"## Ref: `{ref}` ({len(runs)} run(s))")
        lines.append("")

        # ── §3.1 Seed recall ────────────────────────────────────────────────
        recalls = [r["overallRecall"] for r in runs if r.get("overallRecall") is not None]
        if recalls:
            med = statistics.median(recalls)
            lines.append(
                f"**§3.1 Seed recall** (overall): median {med:.0%}, "
                f"range [{min(recalls):.0%}, {max(recalls):.0%}], n={len(recalls)}"
            )
        else:
            lines.append("**§3.1 Seed recall**: N/A")
        lines.append("")

        # ── §3.2 Precision ──────────────────────────────────────────────────
        # Definition (benchmark.md §3.2):
        #   adjudicated true findings / all findings after de-duplication
        #   "true findings" = seedsMatched + TP-extra
        #   "all"           = seedsMatched + TP-extra + FP
        #
        # A run contributes ONLY when its adjudication CSV exists AND contains at
        # least one non-empty verdict cell.  seedsMatched alone is never sufficient
        # (A11 rule 2 — no fabricated numbers).
        precision_vals: List[float] = []
        excluded_run_ids: List[str] = []
        for r in runs:
            run_id_r = r["runId"]
            csv_path = RAW_DIR / f"{run_id_r}-adjudication.csv"
            adj      = _read_adjudication(csv_path)   # {} when file absent or empty
            if not adj:
                # No filled verdicts at all → this run cannot contribute to precision
                excluded_run_ids.append(run_id_r)
                continue
            tp_extra = sum(1 for v in adj.values() if v == "TP-extra")
            fp       = sum(1 for v in adj.values() if v == "FP")
            matched  = r.get("seedsMatched", 0)
            true_pos = matched + tp_extra
            total    = true_pos + fp
            if total > 0:
                precision_vals.append(true_pos / total)
            else:
                excluded_run_ids.append(run_id_r)

        if precision_vals:
            med_p = statistics.median(precision_vals)
            lines.append(
                f"**§3.2 Precision** (true / all adjudicated): "
                f"median {med_p:.0%}, range [{min(precision_vals):.0%}, "
                f"{max(precision_vals):.0%}], n={len(precision_vals)}"
            )
            if excluded_run_ids:
                lines.append(
                    f"  *(Excluded from precision — no adjudication verdicts: "
                    f"{', '.join(excluded_run_ids)})*"
                )
        else:
            lines.append(
                "**§3.2 Precision**: N/A (no adjudication verdicts recorded)"
            )
        lines.append("")

        # ── §3.3 False positives on control ─────────────────────────────────
        # Definition (benchmark.md §3.3): count of MEDIUM+ findings on
        # control-clean-change.  Only meaningful for that ref.
        if ref == "control-clean-change":
            fp_control_counts: List[Optional[int]] = []
            unreadable_runs: List[str] = []
            sev_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
            for r in runs:
                run_id_r    = r["runId"]
                report_path = RUNS_DIR / run_id_r / "report.json"
                if not report_path.exists():
                    # Missing report is unknown, not zero (A11 rule 2)
                    unreadable_runs.append(run_id_r)
                    continue
                try:
                    rpt = json.loads(report_path.read_text(encoding="utf-8-sig"))
                    medium_plus = sum(
                        1 for f in rpt.get("findings", [])
                        if sev_order.get(f.get("severity", "INFO"), 4) <= 2
                    )
                    fp_control_counts.append(medium_plus)
                except Exception:
                    unreadable_runs.append(run_id_r)

            measured = [v for v in fp_control_counts if v is not None]
            if measured:
                lines.append(
                    f"**§3.3 False positives on control** (MEDIUM+ findings): "
                    + _fmt_n(measured,
                             lambda vs: f"median {statistics.median(vs):.0f}, "
                                        f"range [{min(vs)}, {max(vs)}]")
                )
                lines.append("  *(Expected: 0)*")
                if unreadable_runs:
                    lines.append(
                        f"  *({len(unreadable_runs)} run(s) excluded — report.json "
                        f"unreadable: {', '.join(unreadable_runs)})*"
                    )
            else:
                lines.append("**§3.3 False positives on control**: N/A")
                if unreadable_runs:
                    lines.append(
                        f"  *(All runs excluded — report.json unreadable: "
                        f"{', '.join(unreadable_runs)})*"
                    )
            lines.append("")

        # ── §3.4 Held-out recall ─────────────────────────────────────────────
        # Definition (benchmark.md §3.4): did the agents surface the unseen problem
        # in heldout-variation?  Reported as "count / n runs".
        if ref == "heldout-variation":
            planned_runs = 3  # from benchmark.md §2
            found_count  = sum(
                1 for r in runs
                if r.get("seedsMatched", 0) > 0
            )
            n_runs = len(runs)
            # Seed-by-seed breakdown
            seed_ids_heldout = list((runs[0].get("seedHits") or {}).keys()) if runs else []
            lines.append(
                f"**§3.4 Held-out recall**: {found_count}/{n_runs} runs surfaced "
                f"the held-out problem"
                + (f" (planned: {planned_runs})" if n_runs < planned_runs else "")
                + f", n={n_runs}"
            )
            if seed_ids_heldout:
                lines.append("")
                lines.append("  Seed-by-seed breakdown:")
                for sid in seed_ids_heldout:
                    hit_count = sum(
                        1 for r in runs
                        if r.get("seedHits", {}).get(sid) is not None
                    )
                    lines.append(f"  - `{sid}`: found in {hit_count}/{n_runs} runs")
            lines.append("")

        # ── §3.5 Consistency ─────────────────────────────────────────────────
        if len(runs) >= 2 and any(r.get("seedsPresent", 0) > 0 for r in runs):
            seed_ids = list((runs[0].get("seedHits") or {}).keys())
            if seed_ids:
                lines.append("**§3.5 Consistency (per seed):**")
                lines.append("")
                header = "| Seed ID | " + " | ".join(f"Run {i+1}" for i in range(len(runs))) + " |"
                sep    = "|---------|" + "|".join("---" for _ in runs) + "|"
                lines.append(header)
                lines.append(sep)
                for sid in seed_ids:
                    row_str = f"| {sid} | "
                    row_str += " | ".join(
                        "✓" if runs[i].get("seedHits", {}).get(sid) else "✗"
                        for i in range(len(runs))
                    )
                    row_str += " |"
                    lines.append(row_str)
                lines.append("")

        # ── §3.6 Latency ─────────────────────────────────────────────────────
        latencies = [r["latencyMs"] for r in runs if r.get("latencyMs") is not None]
        if latencies:
            med_lat = statistics.median(latencies)
            lines.append(
                f"**§3.6 Latency**: median {med_lat:.0f} ms, "
                f"range [{min(latencies):.0f}, {max(latencies):.0f}] ms, n={len(latencies)}"
            )
        else:
            lines.append("**§3.6 Latency**: N/A")
        lines.append("")

        # ── §3.8 Reliability ──────────────────────────────────────────────────
        total_attempted = len(runs)
        completed = sum(
            1 for r in runs
            if all(v == "completed" for v in r.get("agentStatuses", {}).values())
        )
        lines.append(
            f"**§3.8 Reliability**: {completed}/{total_attempted} runs fully completed, "
            f"n={total_attempted}"
        )
        lines.append("")

        # Readiness state breakdown
        states: Dict[str, int] = {}
        for r in runs:
            s = r.get("readinessState", "unknown")
            states[s] = states.get(s, 0) + 1
        lines.append("**Readiness states:**")
        for state, count in sorted(states.items()):
            lines.append(f"- `{state}`: {count}")
        lines.append("")

    # ── §3.4 Held-out recall — global N/A fallback ────────────────────────
    # The per-ref block above handles the case when heldout runs exist.
    # When no heldout-variation runs have been scored at all, emit a named N/A.
    if "heldout-variation" not in by_ref:
        lines.append("## §3.4 Held-out recall")
        lines.append("")
        lines.append(
            "N/A — no `heldout-variation` runs scored yet. "
            "Score 3 runs with `--ref heldout-variation` to populate this section."
        )
        lines.append("")

    # ── §3.7 Parallel speedup ─────────────────────────────────────────────
    # Stored in raw-results with ref "timing-sequential" and "timing-parallel"
    # (set by the operator when scoring those two special runs)
    seq_run  = next((r for r in results if r.get("ref") == "timing-sequential"), None)
    par_run  = next((r for r in results if r.get("ref") == "timing-parallel"),   None)
    lines.append("## §3.7 Parallel speedup")
    lines.append("")
    if seq_run and par_run and seq_run.get("latencyMs") is not None and par_run.get("latencyMs") is not None:
        seq_ms   = seq_run["latencyMs"]
        par_ms   = par_run["latencyMs"]
        speedup  = seq_ms / par_ms
        lines.append(f"- Sequential wall time:  {seq_ms} ms  (run `{seq_run['runId']}`)")
        lines.append(f"- Parallel wall time:    {par_ms} ms  (run `{par_run['runId']}`)")
        lines.append(f"- Speedup ratio:         {speedup:.2f}×  (point estimate, n=1 each)")
    else:
        lines.append("N/A — timing runs not yet scored (use `--ref timing-sequential` and "
                     "`--ref timing-parallel` when scoring the two timing runs).")
    lines.append("")

    # ── §3.9 Action quality ───────────────────────────────────────────────
    # Stored in runs/<runId>/action-result.json (written by generate-tests workflow)
    lines.append("## §3.9 Action quality")
    lines.append("")
    action_results: List[Dict] = []
    for run_dir in sorted(RUNS_DIR.glob("*/action-result.json")):
        try:
            action_results.append(json.loads(run_dir.read_text(encoding="utf-8-sig")))
        except Exception:
            pass

    if action_results:
        n_action = len(action_results)
        lines.append(
            f"*(n={n_action} — presented as qualitative demonstration only, "
            f"per benchmark.md §7)*"
        )
        lines.append("")
        for ar in action_results:
            lines.append(f"Run: `{ar.get('runId', 'unknown')}`")
            lines.append(f"- Tests generated:    {ar.get('generated', 'N/A')}")
            lines.append(f"- Tests executed:     {ar.get('executed', 'N/A')}")
            lines.append(f"- Failed on buggy:    {ar.get('failed', 'N/A')}  ← proof of detection")
            lines.append(f"- Passed on fixed:    {ar.get('passed', 'N/A')}")
            lines.append(f"- Manual edits needed: {ar.get('manualEdits', 'N/A')}")
            notes = ar.get("notes")
            if notes:
                lines.append(f"- Notes: {notes}")
            lines.append(f"- Patch: `{ar.get('patchPath', 'N/A')}`")
            lines.append("")
    else:
        lines.append("N/A — no `action-result.json` files found under `runs/`.")
        lines.append("Run `agents/testing/generate-tests.md` workflow on a completed run to populate this.")
    lines.append("")

    # ── Limitations ───────────────────────────────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append("## Limitations")
    lines.append("")
    limitations = [
        "One small sample app — results may not generalise to larger or more complex codebases.",
        "Five planted problems that are related (same domain); not a random sample of real defects.",
        "Self-tuned prompts — prompts were written by the same team that designed the evaluation.",
        "Small sample sizes — five runs is insufficient for statistical confidence intervals.",
        "LLM non-determinism — same prompt on same input may produce different findings across runs.",
        "Single model — all agents use the same underlying model.",
        "Action quality sample size is 1 — presented as qualitative demonstration only.",
        "Seed matching uses a greedy heuristic (fewest-options-first); ambiguous cases are "
        "flagged for human adjudication and recorded in ambiguousSeeds.",
    ]
    for lim in limitations:
        lines.append(f"- {lim}")
    lines.append("")

    RESULTS_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"Written: {RESULTS_MD}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> None:
    parser = argparse.ArgumentParser(description="PRism-AI evaluation scorer")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("score", help="Score a single run")
    run_p.add_argument("--run",  required=True)
    run_p.add_argument("--ref",  required=True)

    sub.add_parser("summarise", help="Summarise all scored runs into results.md")

    # Also support flat flags for backwards compat:
    parser.add_argument("--run")
    parser.add_argument("--ref")
    parser.add_argument("--summarise", action="store_true")

    args = parser.parse_args()

    if args.command == "score" or (args.run and args.ref):
        result = score_run(args.run, args.ref)
        print(json.dumps(result, indent=2))
    elif args.command == "summarise" or args.summarise:
        summarise()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    _main()
