"""
orchestrator.reverify — compare a new run with a previous run (A8).

CLI:
    python -m orchestrator.reverify --previous <runId> --run <newRunId>
    → writes runs/<newRunId>/delta.json and delta.md, prints a one-line summary.

Importable:
    from orchestrator.reverify import compute_delta, render_delta

Rules (A8):
    resolved    previous key absent from the new run AND the owning agent
                completed in the new run
    persistent  same key in both runs (class-qualified symbols normalised), or
                the same category + file + symbol, or the same category + file
                + exact line (fallbacks for agents naming an issue differently)
    new         key only in the new run
    unverified  previous key absent, but the owning agent did NOT complete in
                the new run — disappearing from an incomplete result is never
                "resolved"
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from typing import Any

from orchestrator import safe_run_id

_REPO     = pathlib.Path(__file__).parent.parent
_RUNS_DIR = _REPO / "runs"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_report(run_id: str, runs_dir: pathlib.Path) -> dict:
    path = runs_dir / safe_run_id(run_id) / "report.json"
    if not path.exists():
        raise FileNotFoundError(
            f"report.json not found for run {run_id!r} at {path}. "
            "Run orchestrator.aggregate first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _owner(finding: dict) -> str:
    agents = finding.get("agents") or []
    return agents[0] if agents else ""


def _norm_symbol(symbol: Any) -> str:
    """'TicketService.create_ticket' and 'create_ticket' name the same function."""
    if not symbol or symbol == "-":
        return "-"
    return str(symbol).rsplit(".", 1)[-1]


def _norm_key(finding: dict) -> str:
    parts = str(finding.get("key", "")).split("|")
    if len(parts) == 4:
        parts[2] = _norm_symbol(parts[2])
    return "|".join(parts)


def _same_symbol(a: dict, b: dict) -> bool:
    sym = _norm_symbol(a.get("symbol"))
    return (sym != "-" and sym == _norm_symbol(b.get("symbol"))
            and a.get("category") == b.get("category") and a.get("file") == b.get("file"))


def _same_line(a: dict, b: dict) -> bool:
    return (a.get("line") is not None and a.get("line") == b.get("line")
            and a.get("category") == b.get("category") and a.get("file") == b.get("file"))


def _entry(finding: dict, previous: dict | None = None) -> dict:
    entry = {
        "id":       finding.get("id"),
        "key":      finding.get("key"),
        "agent":    _owner(finding),
        "severity": finding.get("severity"),
        "blocking": finding.get("blocking", False),
        "title":    finding.get("title"),
        "file":     finding.get("file"),
        "line":     finding.get("line"),
    }
    if previous is not None:
        entry["previousId"] = previous.get("id")
        entry["previousLine"] = previous.get("line")
    return entry


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def compute_delta(previous: dict, current: dict) -> dict:
    """Classify every finding of *previous* and *current* per A8."""
    completed = {
        row["agent"] for row in current.get("agents", [])
        if row.get("status") == "completed"
    }
    new_findings = list(current.get("findings", []))
    matched_new: set[int] = set()  # indices into new_findings

    resolved, persistent, unverified = [], [], []

    # Most specific rule first: key (symbol normalised), then category + file +
    # symbol, then category + file + exact line. Agents name the same issue
    # slightly differently between runs; line alone is never enough.
    # The fallbacks only apply when unambiguous: one agent often reports several
    # different gaps against the same function, and those must not be paired up.
    rules = (
        (lambda a, b: _norm_key(a) == _norm_key(b), False),
        (_same_symbol, True),
        (_same_line, True),
    )

    prev_findings = list(previous.get("findings", []))
    match_for: dict[int, int] = {}  # previous index → new index
    for rule, needs_unique in rules:  # one pass per rule: a weak rule never steals a key match
        for p, prev in enumerate(prev_findings):
            if p in match_for:
                continue
            cands = [i for i, cand in enumerate(new_findings)
                     if i not in matched_new and rule(prev, cand)]
            if not cands:
                continue
            if needs_unique:
                rivals = [q for q, other in enumerate(prev_findings)
                          if q not in match_for and rule(other, new_findings[cands[0]])]
                if len(cands) > 1 or len(rivals) > 1:
                    continue
            match_for[p] = cands[0]
            matched_new.add(cands[0])

    for p, prev in enumerate(prev_findings):
        if p in match_for:
            persistent.append(_entry(new_findings[match_for[p]], previous=prev))
        elif _owner(prev) in completed:
            resolved.append(_entry(prev))
        else:
            unverified.append(_entry(prev))

    new = [_entry(f) for i, f in enumerate(new_findings) if i not in matched_new]

    return {
        "schemaVersion":   "1.0",
        "previousRunId":   previous.get("runId"),
        "runId":           current.get("runId"),
        "previousSnapshotId": previous.get("snapshotId"),
        "snapshotId":      current.get("snapshotId"),
        "previousState":   previous.get("readiness", {}).get("state"),
        "state":           current.get("readiness", {}).get("state"),
        "resolved":        resolved,
        "persistent":      persistent,
        "new":             new,
        "unverified":      unverified,
        "counts": {
            "resolved":   len(resolved),
            "persistent": len(persistent),
            "new":        len(new),
            "unverified": len(unverified),
        },
    }


def render_delta(delta: dict) -> str:
    """Human-readable delta.md."""
    c = delta["counts"]
    lines = [
        "# Changes since previous run",
        "",
        f"- Previous run: `{delta['previousRunId']}` → **{delta['previousState']}**",
        f"- This run: `{delta['runId']}` → **{delta['state']}**",
        f"- Resolved: **{c['resolved']}** · Persistent: **{c['persistent']}** · "
        f"New: **{c['new']}** · Unverified: **{c['unverified']}**",
        "",
    ]

    sections = [
        ("resolved",   "✅ Resolved",
         "The owning check ran on the new snapshot and no longer reports the issue."),
        ("persistent", "🔁 Persistent", "The same issue is still reported."),
        ("new",        "🆕 New", "Reported for the first time in this run."),
        ("unverified", "❔ Unverified",
         "The owning check did not complete, so these cannot be called resolved."),
    ]
    for name, heading, note in sections:
        items = delta[name]
        lines += [f"## {heading} ({len(items)})", "", f"_{note}_", ""]
        if not items:
            lines += ["None.", ""]
            continue
        lines += ["| ID | Agent | Severity | Title | Location |",
                  "|---|---|---|---|---|"]
        for e in items:
            loc = e["file"] or "-"
            if e.get("line") is not None:
                loc += f":{e['line']}"
            ident = e["id"]
            if e.get("previousId") and e["previousId"] != e["id"]:
                ident += f" (was {e['previousId']})"
            lines.append(
                f"| {ident} | {e['agent']} | {e['severity']} | {e['title']} | `{loc}` |")
        lines.append("")

    lines.append("_Resolution is evidence from the scoped checks, not proof that no "
                 "defects remain. Human review is still required._")
    return "\n".join(lines) + "\n"


def reverify(
    previous_run_id: str,
    run_id: str,
    *,
    runs_dir: pathlib.Path | None = None,
) -> dict:
    """Compute the delta and write delta.json + delta.md into the new run dir."""
    rdir_root = runs_dir or _RUNS_DIR
    previous = _load_report(previous_run_id, rdir_root)
    current  = _load_report(run_id, rdir_root)
    delta = compute_delta(previous, current)

    out_dir = rdir_root / run_id
    for name, text in (("delta.json", json.dumps(delta, indent=2)),
                       ("delta.md", render_delta(delta))):
        tmp = out_dir / (name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, out_dir / name)
    return delta


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.reverify",
        description="Compare a new run with a previous run and write delta.json/delta.md.",
    )
    parser.add_argument("--previous", required=True, help="runId of the earlier run")
    parser.add_argument("--run",      required=True, help="runId of the new run")
    args = parser.parse_args(argv)

    try:
        delta = reverify(args.previous, args.run)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    c = delta["counts"]
    print(
        f"{delta['previousState']} → {delta['state']}  |  "
        f"resolved {c['resolved']}, persistent {c['persistent']}, "
        f"new {c['new']}, unverified {c['unverified']}  →  "
        f"{_RUNS_DIR / args.run / 'delta.md'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(_main())
