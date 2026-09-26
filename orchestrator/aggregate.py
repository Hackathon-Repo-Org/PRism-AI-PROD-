"""
orchestrator.aggregate — deterministic readiness decision.

CLI:
    python -m orchestrator.aggregate --run <runId> [--now <iso-timestamp>]
    → writes runs/<runId>/report.json, prints the readiness state.

Importable:
    from orchestrator.aggregate import aggregate, AggregateResult

Rules (from A7):
    1. VERIFICATION_FAILED if any required agent is missing/error/timeout/skipped/
       schema-invalid/id-mismatched, OR tests did not run or exited non-zero.
    2. ATTENTION_REQUIRED if all checks ran and there is ≥1 blocking finding.
    3. READY_FOR_HUMAN_REVIEW otherwise.

Determinism:
    - Findings sorted by severity (CRITICAL→INFO), then agent name, then id.
    - generatedAt is the only time field injected here; pass --now for tests.
    - All other time fields are copied from agent results.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys
from typing import Any

from orchestrator import safe_run_id

_REPO       = pathlib.Path(__file__).parent.parent
_RUNS_DIR   = _REPO / "runs"
_CONFIG_DIR = _REPO / "config"

# Severity ordering: higher index = higher severity
_SEVERITY_ORDER = ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _severity_rank(sev: str) -> int:
    try:
        return _SEVERITY_ORDER.index(sev)
    except ValueError:
        return -1


def _is_blocking(finding: dict, policy: dict) -> bool:
    """Return True when the finding's severity meets the blocking threshold."""
    category = finding.get("category", "")
    severity  = finding.get("severity", "")
    thresholds = policy["blockingMinSeverity"]
    min_sev = thresholds.get(category, thresholds["default"])
    return _severity_rank(severity) >= _severity_rank(min_sev)


def _parse_iso(ts: str) -> datetime.datetime | None:
    """Parse ISO-8601 UTC string; return None on failure."""
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.datetime.strptime(ts, fmt)
        except (ValueError, TypeError):
            pass
    return None


def _intervals_overlap(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    """True when two [start, end] intervals genuinely overlap (not just touch)."""
    t0a, t1a = _parse_iso(a_start), _parse_iso(a_end)
    t0b, t1b = _parse_iso(b_start), _parse_iso(b_end)
    if None in (t0a, t1a, t0b, t1b):
        return False
    return t0a < t1b and t0b < t1a


def _wall_clock_ms(results: list[dict]) -> int:
    """last finishedAt − first startedAt across all completed agents."""
    starts = [_parse_iso(r["startedAt"])  for r in results]
    ends   = [_parse_iso(r["finishedAt"]) for r in results]
    starts = [t for t in starts if t]
    ends   = [t for t in ends   if t]
    if not starts or not ends:
        return 0
    delta = max(ends) - min(starts)
    return max(0, int(delta.total_seconds() * 1000))


def _sum_agent_ms(results: list[dict]) -> int:
    return sum(r.get("durationMs", 0) or 0 for r in results)


def _overlap_observed(results: list[dict]) -> bool:
    """True if any two agent intervals genuinely overlap."""
    for i in range(len(results)):
        for j in range(i + 1, len(results)):
            a, b = results[i], results[j]
            if _intervals_overlap(
                a["startedAt"], a["finishedAt"],
                b["startedAt"], b["finishedAt"],
            ):
                return True
    return False


def _now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )


# ---------------------------------------------------------------------------
# Synthetic agent result for missing/timeout agents
# ---------------------------------------------------------------------------

_EPOCH = "1970-01-01T00:00:00.000Z"

def _synthetic_result(agent: str, status: str, reason: str, context: dict) -> dict:
    return {
        "schemaVersion": "1.0",
        "runId":         context["runId"],
        "snapshotId":    context["snapshotId"],
        "agent":         agent,
        "status":        status,
        "statusReason":  reason,
        "startedAt":     _EPOCH,
        "finishedAt":    _EPOCH,
        "durationMs":    0,
        "findings":      [],
        "limitations":   [],
        "execution":     None,
    }


# ---------------------------------------------------------------------------
# Core aggregate logic (importable)
# ---------------------------------------------------------------------------

def aggregate(
    run_id: str,
    *,
    now: str | None = None,
    runs_dir: pathlib.Path | None = None,
    config_dir: pathlib.Path | None = None,
) -> dict:
    """
    Load agent results for *run_id*, apply the readiness policy, and return
    the report dict (not yet written to disk).

    Parameters
    ----------
    now        : ISO-8601 UTC string to use as generatedAt (for tests)
    runs_dir   : override the default runs/ directory
    config_dir : override the default config/ directory
    """
    from orchestrator.validate import validate_result

    rdir    = (runs_dir   or _RUNS_DIR)   / run_id
    cfgdir  = config_dir  or _CONFIG_DIR
    gen_at  = now or _now_utc()

    # --- load context ---
    ctx_path = rdir / "context.json"
    if not ctx_path.exists():
        print(f"ERROR: context.json not found in {rdir}", file=sys.stderr)
        sys.exit(1)
    context = json.loads(ctx_path.read_text(encoding="utf-8"))

    # --- load policy ---
    policy = json.loads((cfgdir / "policy.json").read_text(encoding="utf-8"))
    required_agents: list[str] = policy["requiredAgents"]

    # --- load / synthesise each agent result ---
    loaded_results: dict[str, dict] = {}   # agent → result dict
    reasons: list[dict] = []               # accumulates VERIFICATION_FAILED reasons

    for agent in required_agents:
        result_path  = rdir / f"{agent}-result.json"
        started_path = rdir / f"{agent}.started.json"

        if not result_path.exists():
            # no result file
            if started_path.exists():
                reason_code = "AGENT_TIMEOUT"
                reason_detail = f"{agent}: started but no result written"
            else:
                reason_code = "AGENT_MISSING"
                reason_detail = f"{agent}: no result and no start marker"
            reasons.append({"code": reason_code, "detail": reason_detail})
            loaded_results[agent] = _synthetic_result(
                agent, "timeout" if started_path.exists() else "skipped",
                reason_detail, context,
            )
            continue

        # file exists — load and validate
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception as exc:
            reasons.append({"code": "SCHEMA_INVALID",
                             "detail": f"{agent}: cannot parse JSON: {exc}"})
            loaded_results[agent] = _synthetic_result(
                agent, "error", str(exc), context)
            continue

        vr = validate_result(result, context=context)

        if vr.kind != "agent-result":
            vr.errors.append("Expected an agent-result artifact.")
        elif result.get("agent") != agent:
            vr.errors.append(f"Expected agent {agent!r}, got {result.get('agent')!r}.")

        if vr.errors:
            # distinguish id mismatch vs. schema error
            id_errors = [e for e in vr.errors if "mismatch" in e]
            if id_errors:
                reasons.append({"code": "SNAPSHOT_MISMATCH",
                                 "detail": f"{agent}: " + "; ".join(id_errors)})
            schema_errors = [e for e in vr.errors if e not in id_errors]
            if schema_errors:
                reasons.append({"code": "SCHEMA_INVALID",
                                 "detail": f"{agent}: " + "; ".join(schema_errors[:2])})
            loaded_results[agent] = _synthetic_result(
                agent, "error", "validation failed", context)
            continue

        # valid result — check status
        status = result.get("status")
        if status == "error":
            reasons.append({"code": "AGENT_ERROR",
                             "detail": f"{agent}: {result.get('statusReason', '')}"})
        elif status == "timeout":
            reasons.append({"code": "AGENT_TIMEOUT",
                             "detail": f"{agent}: {result.get('statusReason', '')}"})
        elif status == "skipped":
            reasons.append({"code": "AGENT_SKIPPED",
                             "detail": f"{agent}: {result.get('statusReason', '')}"})

        loaded_results[agent] = result

    # --- testing execution checks ---
    testing_result = loaded_results.get("testing", {})
    execution = testing_result.get("execution") if isinstance(testing_result, dict) else None

    if testing_result.get("status") == "completed" and execution is None:
        reasons.append({"code": "TESTS_NOT_RUN",
                         "detail": "testing agent completed but execution block is null"})
    elif execution is not None:
        exit_code = execution.get("exitCode")
        if exit_code is None:
            reasons.append({"code": "TESTS_NOT_RUN",
                             "detail": "exitCode is null — tests could not be started"})
        elif exit_code != 0:
            reasons.append({"code": "TESTS_FAILED",
                             "detail": f"pytest exited {exit_code}"})

    # --- collect all valid findings ---
    all_findings: list[dict] = []
    seen_keys: dict[str, int] = {}   # key → index in all_findings

    for agent in required_agents:
        result = loaded_results[agent]
        for f in result.get("findings", []):
            key = f.get("key", "")
            if key in seen_keys:
                # merge: keep the more severe finding, the union of agents,
                # and blocking if either finding blocks
                idx = seen_keys[key]
                existing = all_findings[idx]
                agents = existing["agents"] + (
                    [agent] if agent not in existing["agents"] else [])
                blocking = existing["blocking"] or _is_blocking(f, policy)
                if _severity_rank(f.get("severity", "")) > _severity_rank(
                        existing.get("severity", "")):
                    existing = dict(f)
                existing["agents"] = agents
                existing["blocking"] = blocking
                all_findings[idx] = existing
            else:
                report_finding = dict(f)
                report_finding["agents"]   = [agent]
                report_finding["blocking"] = _is_blocking(f, policy)
                seen_keys[key] = len(all_findings)
                all_findings.append(report_finding)

    # --- sort findings: severity desc, then agent (first), then id ---
    def _sort_key(f: dict) -> tuple:
        sev_rank = -_severity_rank(f.get("severity", ""))  # descending
        agent_0  = f["agents"][0] if f["agents"] else ""
        return (sev_rank, agent_0, f.get("id", ""))

    all_findings.sort(key=_sort_key)

    # --- blocking finding reason ---
    blocking_ids = [f["id"] for f in all_findings if f["blocking"]]
    if blocking_ids and not reasons:
        # only add if no VERIFICATION_FAILED reasons yet (checked below in state logic)
        pass  # added conditionally in state determination

    # --- state determination (A7 order) ---
    if reasons:
        state = "VERIFICATION_FAILED"
        # also append BLOCKING_FINDINGS reason if any
        if blocking_ids:
            reasons.append({"code": "BLOCKING_FINDINGS",
                             "detail": ", ".join(blocking_ids)})
    elif blocking_ids:
        state = "ATTENTION_REQUIRED"
        reasons = [{"code": "BLOCKING_FINDINGS",
                    "detail": ", ".join(blocking_ids)}]
    else:
        state = "READY_FOR_HUMAN_REVIEW"
        reasons = []

    # --- agent summary rows ---
    agent_rows = []
    for agent in required_agents:
        r = loaded_results[agent]
        agent_rows.append({
            "agent":      agent,
            "status":     r.get("status", "error"),
            "startedAt":  r.get("startedAt", _EPOCH),
            "finishedAt": r.get("finishedAt", _EPOCH),
            "durationMs": r.get("durationMs", 0),
        })

    # --- timeline ---
    real_results = [
        loaded_results[a] for a in required_agents
        if loaded_results[a].get("startedAt") != _EPOCH
    ]
    timeline = {
        "wallClockMs":     _wall_clock_ms(real_results) if real_results else 0,
        "sumOfAgentMs":    _sum_agent_ms(list(loaded_results.values())),
        "overlapObserved": _overlap_observed(real_results) if real_results else False,
    }

    # --- counts ---
    by_agent: dict[str, int] = {}
    for f in all_findings:
        for a in f["agents"]:
            by_agent[a] = by_agent.get(a, 0) + 1

    counts = {
        "total":    len(all_findings),
        "blocking": len(blocking_ids),
        "byAgent":  by_agent,
    }

    # --- limitations (union of all agent limitations) ---
    limitations: list[str] = []
    seen_lim: set[str] = set()
    for agent in required_agents:
        for lim in loaded_results[agent].get("limitations", []):
            if lim not in seen_lim:
                limitations.append(lim)
                seen_lim.add(lim)
    limitations += context.get("scopeLimitations", [])

    # --- assemble report ---
    report = {
        "schemaVersion": "1.0",
        "runId":         context["runId"],
        "snapshotId":    context["snapshotId"],
        "baseCommit":    context["baseCommit"],
        "baseRef":       context["baseRef"],
        "candidateRef":  context["candidateRef"],
        "generatedAt":   gen_at,
        "policyVersion": policy["policyVersion"],
        "readiness": {
            "state":   state,
            "reasons": reasons,
        },
        "agents":    agent_rows,
        "execution": execution,
        "findings":  all_findings,
        "counts":    counts,
        "timeline":  timeline,
        "limitations": limitations,
    }

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.aggregate",
        description="Aggregate agent results into report.json.",
    )
    parser.add_argument("--run", required=True, help="runId")
    parser.add_argument(
        "--now", default=None,
        help="Fix generatedAt to this ISO-8601 UTC string (for reproducible tests).",
    )
    args = parser.parse_args(argv)

    report = aggregate(args.run, now=args.now)

    rdir        = _RUNS_DIR / safe_run_id(args.run)
    report_path = rdir / "report.json"
    tmp         = pathlib.Path(str(report_path) + ".tmp")
    tmp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(report_path))

    state = report["readiness"]["state"]
    print(f"{state}  →  {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
