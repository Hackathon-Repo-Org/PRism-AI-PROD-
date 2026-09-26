"""
orchestrator.report — produce report.md from report.json.

CLI:
    python -m orchestrator.report --run <runId>
    → writes runs/<runId>/report.md, prints the path.

Layout (per spec T6):
  1. Status banner
  2. Run identity
  3. Agent table + text timeline
  4. Build/test evidence
  5. Findings (blocking then non-blocking, grouped by agent)
  6. Limitations and scope
  7. Next steps + fixed footer

Importable:
    from orchestrator.report import render, write_report
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from typing import Any

_REPO     = pathlib.Path(__file__).parent.parent
_RUNS_DIR = _REPO / "runs"

_SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]

# Map internal state names to display strings
_STATE_DISPLAY = {
    "READY_FOR_HUMAN_REVIEW": "✅ READY FOR HUMAN REVIEW",
    "ATTENTION_REQUIRED":     "⚠️  ATTENTION REQUIRED",
    "VERIFICATION_FAILED":    "❌ VERIFICATION FAILED",
}

_REASON_DISPLAY = {
    "AGENT_MISSING":      "Agent missing",
    "AGENT_ERROR":        "Agent error",
    "AGENT_TIMEOUT":      "Agent timeout",
    "AGENT_SKIPPED":      "Agent skipped",
    "SCHEMA_INVALID":     "Schema invalid",
    "SNAPSHOT_MISMATCH":  "Snapshot mismatch",
    "TESTS_NOT_RUN":      "Tests did not run",
    "TESTS_FAILED":       "Tests failed",
    "BLOCKING_FINDINGS":  "Blocking findings",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_null(val: Any, fallback: str = "unknown") -> str:
    if val is None:
        return fallback
    return str(val)


def _short_ts(iso: str) -> str:
    """2026-09-26T10:15:03.000Z → 10:15:03Z"""
    if not iso or iso == "1970-01-01T00:00:00.000Z":
        return "—"
    try:
        return iso[11:19] + "Z"
    except Exception:
        return iso


def _fmt_duration(ms: int | None) -> str:
    if ms is None:
        return "unknown"
    s = ms // 1000
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m {s % 60}s"


def _timeline_bar(agents: list[dict]) -> str:
    """
    Render a tiny text timeline like:
      code-review  |====|
      testing          |===|
      documentation  |======|
    based on startedAt/finishedAt relative positions.
    """
    import datetime

    def _parse(ts: str):
        for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                return datetime.datetime.strptime(ts, fmt)
            except (ValueError, TypeError):
                pass
        return None

    rows = []
    epoch = "1970-01-01T00:00:00.000Z"
    for a in agents:
        s = _parse(a.get("startedAt", epoch))
        e = _parse(a.get("finishedAt", epoch))
        rows.append((a["agent"], s, e))

    valid = [(name, s, e) for name, s, e in rows if s and e and s.year > 1970]
    if not valid:
        return ""

    t_min = min(s for _, s, _ in valid)
    t_max = max(e for _, _, e in valid)
    total_s = (t_max - t_min).total_seconds()
    if total_s <= 0:
        total_s = 1

    width = 40
    lines = ["```"]
    label_w = max(len(name) for name, _, _ in valid) + 2
    for name, s, e in valid:
        if s and e and s.year > 1970:
            start_pos = int(((s - t_min).total_seconds() / total_s) * width)
            end_pos   = max(start_pos + 1,
                            int(((e - t_min).total_seconds() / total_s) * width))
            bar = " " * start_pos + "=" * (end_pos - start_pos)
            bar = bar.ljust(width)
        else:
            bar = "?" * width
        lines.append(f"{name:<{label_w}} |{bar}|")
    lines.append("```")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core renderer (importable)
# ---------------------------------------------------------------------------

def render(report: dict) -> str:
    """Return the full Markdown string for report.md."""
    lines: list[str] = []

    state   = report["readiness"]["state"]
    reasons = report["readiness"]["reasons"]

    # ── 1. Status banner ──────────────────────────────────────────────────
    lines.append(f"# {_STATE_DISPLAY.get(state, state)}")
    lines.append("")
    if reasons:
        for r in reasons:
            label  = _REASON_DISPLAY.get(r["code"], r["code"])
            detail = r.get("detail", "")
            lines.append(f"- **{label}:** {detail}")
        lines.append("")

    # ── 2. Run identity ───────────────────────────────────────────────────
    lines.append("## Run identity")
    lines.append("")
    lines.append(f"| Field | Value |")
    lines.append(f"|---|---|")
    lines.append(f"| Run ID | `{report['runId']}` |")
    lines.append(f"| Base | `{report['baseRef']}` (`{report['baseCommit'][:12]}`) |")
    lines.append(f"| Candidate | `{report['candidateRef']}` (`{report['snapshotId'][:12]}`) |")
    lines.append(f"| Generated | {report['generatedAt']} |")
    lines.append(f"| Policy | v{report['policyVersion']} |")
    lines.append("")

    # ── 3. Agent table + timeline ─────────────────────────────────────────
    lines.append("## Agents")
    lines.append("")
    lines.append("| Agent | Status | Start | End | Duration |")
    lines.append("|---|---|---|---|---|")
    for a in report["agents"]:
        status_icon = {"completed": "✅", "error": "❌",
                       "timeout": "⏱", "skipped": "⏭"}.get(a["status"], "?")
        lines.append(
            f"| {a['agent']} "
            f"| {status_icon} {a['status']} "
            f"| {_short_ts(a['startedAt'])} "
            f"| {_short_ts(a['finishedAt'])} "
            f"| {_fmt_duration(a.get('durationMs'))} |"
        )
    lines.append("")

    tl = report.get("timeline", {})
    overlap_note = " (overlapping)" if tl.get("overlapObserved") else " (sequential)"
    lines.append(
        f"Wall clock: **{_fmt_duration(tl.get('wallClockMs'))}**{overlap_note}  "
        f"· Sum of agent time: {_fmt_duration(tl.get('sumOfAgentMs'))}"
    )
    lines.append("")

    bar = _timeline_bar(report["agents"])
    if bar:
        lines.append(bar)
        lines.append("")

    # ── 4. Build/test evidence ────────────────────────────────────────────
    execution = report.get("execution")
    if execution:
        lines.append("## Test execution")
        lines.append("")
        cmd  = execution.get("command", "unknown")
        code = _fmt_null(execution.get("exitCode"))
        coll = _fmt_null(execution.get("collected"))
        passed  = _fmt_null(execution.get("passed"))
        failed  = _fmt_null(execution.get("failed"))
        skipped = _fmt_null(execution.get("skipped"))
        errs    = _fmt_null(execution.get("errors"))
        log_p   = execution.get("logPath", "")

        lines.append(f"**Command:** `{cmd}`  ")
        lines.append(f"**Exit code:** `{code}`  ")
        lines.append(
            f"**Results:** collected {coll} · "
            f"passed {passed} · failed {failed} · "
            f"skipped {skipped} · errors {errs}"
        )
        if log_p:
            lines.append(f"**Log:** `{log_p}`")
        lines.append("")

    # ── 5. Findings ───────────────────────────────────────────────────────
    findings = report.get("findings", [])
    blocking     = [f for f in findings if f.get("blocking")]
    non_blocking = [f for f in findings if not f.get("blocking")]

    def _render_finding_group(group: list[dict], header: str) -> None:
        if not group:
            return
        lines.append(f"## {header}")
        lines.append("")
        # group by first agent
        by_agent: dict[str, list[dict]] = {}
        for f in group:
            a = f["agents"][0] if f.get("agents") else "unknown"
            by_agent.setdefault(a, []).append(f)
        for agent, afindings in sorted(by_agent.items()):
            lines.append(f"### {agent}")
            lines.append("")
            for f in afindings:
                loc = f.get("file", "")
                if f.get("line"):
                    loc += f":{f['line']}"
                lines.append(
                    f"**[{f['severity']}] {f['id']} — {f['title']}**  "
                )
                lines.append(f"`{loc}`  ")
                if len(f.get("agents", [])) > 1:
                    lines.append(f"_Also reported by: {', '.join(f['agents'][1:])}_  ")
                lines.append("")
                lines.append(f.get("description", ""))
                lines.append("")
                ev = f.get("evidence", "")
                if ev:
                    lines.append("```")
                    lines.append(ev)
                    lines.append("```")
                    lines.append("")
                rec = f.get("recommendation", "")
                if rec:
                    lines.append(f"> **Recommendation:** {rec}")
                    lines.append("")

    _render_finding_group(blocking,     "Blocking findings")
    _render_finding_group(non_blocking, "Non-blocking findings")

    if not findings:
        lines.append("## Findings")
        lines.append("")
        lines.append("No findings.")
        lines.append("")

    # ── 6. Limitations and scope ──────────────────────────────────────────
    limitations = report.get("limitations", [])
    if limitations:
        lines.append("## Limitations and scope")
        lines.append("")
        for lim in limitations:
            lines.append(f"- {lim}")
        lines.append("")

    # ── 7. Next steps + footer ────────────────────────────────────────────
    if blocking:
        lines.append("## Next steps")
        lines.append("")
        for f in blocking:
            rec = f.get("recommendation", "")
            if rec:
                lines.append(f"- **{f['id']}:** {rec}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "_This is a scoped pre-review check, not approval to merge._"
    )
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# write_report (importable)
# ---------------------------------------------------------------------------

def write_report(
    run_id: str,
    *,
    runs_dir: pathlib.Path | None = None,
    prefix: str = "",
) -> pathlib.Path:
    """
    Read report.json for *run_id*, render report.md, write it atomically.

    *prefix* is prepended before the rendered content (used by T10 for the
    delta section).

    Returns the path to report.md.
    """
    rdir = (runs_dir or _RUNS_DIR) / run_id
    report_json_path = rdir / "report.json"

    if not report_json_path.exists():
        print(f"ERROR: report.json not found at {report_json_path}", file=sys.stderr)
        sys.exit(1)

    report = json.loads(report_json_path.read_text(encoding="utf-8"))
    md     = prefix + render(report)

    out_path = rdir / "report.md"
    tmp      = pathlib.Path(str(out_path) + ".tmp")
    tmp.write_text(md, encoding="utf-8")
    os.replace(str(tmp), str(out_path))
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.report",
        description="Render report.md from report.json.",
    )
    parser.add_argument("--run", required=True, help="runId")
    args = parser.parse_args(argv)

    path = write_report(args.run)
    print(f"report.md → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
