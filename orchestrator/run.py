"""
orchestrator.run — thin end-to-end pipeline.

CLI modes:
    # Fixture mode (CP1 / safety net — no real agents required):
    python -m orchestrator.run \\
        --base <ref> --candidate <ref> \\
        --fixtures bad|clean|error|tests-failed

    # Agents-done mode (agents ran by hand; just aggregate + report):
    python -m orchestrator.run \\
        --base <ref> --candidate <ref> \\
        --agents-done \\
        [--run <existing-runId>]

Pipeline for --fixtures:
  1. orchestrator.context  → creates run dir, context.json, snapshot, diff
  2. re-stamp each fixture result with the real runId / snapshotId
  3. orchestrator.aggregate → report.json
  4. orchestrator.report    → report.md
  Prints: runId, state, path to report.md.

Pipeline for --agents-done:
  Skips steps 1-2 (agents already wrote their result files).
  Runs aggregate + report only. Accepts --run to target an existing run dir;
  if omitted, runs context first to create a new one.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import pathlib
import sys
from typing import Any

_REPO           = pathlib.Path(__file__).parent.parent
_RUNS_DIR       = _REPO / "runs"
_EXAMPLES_DIR   = _REPO / "schemas" / "examples"
_CONFIG_DIR     = _REPO / "config"

# Map fixture set name → example file suffix
_FIXTURE_SETS = {
    "bad":          "bad",
    "clean":        "clean",
    "error":        "error",
    "tests-failed": "tests-failed",  # testing gets tests-failed, others get clean
}

_AGENTS = ["code-review", "testing", "documentation"]


# ---------------------------------------------------------------------------
# Fixture re-stamping
# ---------------------------------------------------------------------------

def _restamp(
    fixture_data: dict,
    run_id: str,
    snapshot_id: str,
) -> dict:
    """Return a copy of *fixture_data* with runId and snapshotId overwritten."""
    stamped = copy.deepcopy(fixture_data)
    stamped["runId"]      = run_id
    stamped["snapshotId"] = snapshot_id
    return stamped


def _load_fixture(agent: str, suffix: str) -> dict:
    """Load schemas/examples/<agent>-result.<suffix>.json."""
    path = _EXAMPLES_DIR / f"{agent}-result.{suffix}.json"
    if not path.exists():
        print(
            f"ERROR: fixture not found: {path}",
            file=sys.stderr,
        )
        sys.exit(1)
    return json.loads(path.read_text(encoding="utf-8"))


def _write_fixture_results(
    run_dir: pathlib.Path,
    fixture_set: str,
    run_id: str,
    snapshot_id: str,
) -> None:
    """
    Write re-stamped agent result files into *run_dir* based on *fixture_set*.

    For fixture_set "tests-failed":
      - testing gets testing-result.tests-failed.json
      - the other two agents get their *.clean.json
    For all other sets:
      - all three agents get <agent>-result.<fixture_set>.json
      - if that file doesn't exist for an agent, fall back to clean
    """
    for agent in _AGENTS:
        if fixture_set == "tests-failed":
            suffix = "tests-failed" if agent == "testing" else "clean"
        else:
            suffix = fixture_set

        # fallback: if per-agent error file missing, use error for that agent
        candidate = _EXAMPLES_DIR / f"{agent}-result.{suffix}.json"
        if not candidate.exists():
            candidate = _EXAMPLES_DIR / f"{agent}-result.clean.json"

        fixture = json.loads(candidate.read_text(encoding="utf-8"))
        stamped = _restamp(fixture, run_id, snapshot_id)

        out = run_dir / f"{agent}-result.json"
        tmp = pathlib.Path(str(out) + ".tmp")
        tmp.write_text(json.dumps(stamped, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(out))


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------

def _run_context(
    base_ref: str,
    candidate_ref: str,
    intent: str | None,
    repo_dir: pathlib.Path | None,
    runs_dir: pathlib.Path,
) -> tuple[str, pathlib.Path]:
    """Call build_context and return (run_id, run_dir)."""
    from orchestrator.context import build_context
    return build_context(
        base_ref=base_ref,
        candidate_ref=candidate_ref,
        intent=intent,
        repo_dir=repo_dir,
        runs_dir=runs_dir,
    )


def _run_aggregate(
    run_id: str,
    runs_dir: pathlib.Path,
    config_dir: pathlib.Path | None = None,
    now: str | None = None,
) -> dict:
    from orchestrator.aggregate import aggregate
    return aggregate(run_id, runs_dir=runs_dir,
                     config_dir=config_dir, now=now)


def _write_run_report(run_id: str, runs_dir: pathlib.Path, report: dict) -> pathlib.Path:
    rdir = runs_dir / run_id
    report_json = rdir / "report.json"
    tmp = pathlib.Path(str(report_json) + ".tmp")
    tmp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(report_json))
    from orchestrator.report import write_report
    return write_report(run_id, runs_dir=runs_dir)


# ---------------------------------------------------------------------------
# Public pipeline function (importable, testable)
# ---------------------------------------------------------------------------

def run_fixtures(
    base_ref: str,
    candidate_ref: str,
    fixture_set: str,
    *,
    intent: str | None = None,
    repo_dir: pathlib.Path | None = None,
    runs_dir: pathlib.Path | None = None,
    config_dir: pathlib.Path | None = None,
    now: str | None = None,
) -> tuple[str, str, pathlib.Path]:
    """
    Run the full fixture pipeline.

    Returns (run_id, state, report_md_path).
    """
    rdir = runs_dir or _RUNS_DIR

    # step 1 — context
    run_id, run_dir = _run_context(
        base_ref, candidate_ref, intent, repo_dir, rdir
    )

    # step 2 — re-stamped fixtures
    context = json.loads((run_dir / "context.json").read_text(encoding="utf-8"))
    _write_fixture_results(run_dir, fixture_set, run_id, context["snapshotId"])

    # step 3 — aggregate
    report = _run_aggregate(run_id, rdir, config_dir=config_dir, now=now)

    # step 4 — report.md
    report_path = _write_run_report(run_id, rdir, report)

    state = report["readiness"]["state"]
    return run_id, state, report_path


def run_agents_done(
    run_id: str,
    *,
    runs_dir: pathlib.Path | None = None,
    config_dir: pathlib.Path | None = None,
    now: str | None = None,
) -> tuple[str, str, pathlib.Path]:
    """
    Aggregate + report for an existing run (agents wrote their results).

    Returns (run_id, state, report_md_path).
    """
    rdir   = runs_dir or _RUNS_DIR
    report = _run_aggregate(run_id, rdir, config_dir=config_dir, now=now)
    report_path = _write_run_report(run_id, rdir, report)
    state = report["readiness"]["state"]
    return run_id, state, report_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.run",
        description="Run the PRism-AI pipeline (fixture or agents-done mode).",
    )
    parser.add_argument("--base",       required=True)
    parser.add_argument("--candidate",  required=True)
    parser.add_argument("--intent",     default=None)
    parser.add_argument(
        "--fixtures",
        choices=list(_FIXTURE_SETS.keys()),
        default=None,
        help="Use pre-built fixture results instead of real agents.",
    )
    parser.add_argument(
        "--agents-done",
        dest="agents_done",
        action="store_true",
        help="Agents have already run; just aggregate + report.",
    )
    parser.add_argument(
        "--run",
        default=None,
        help="Existing runId (for --agents-done when context already exists).",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Fix generatedAt for reproducible output (tests only).",
    )
    args = parser.parse_args(argv)

    if not args.fixtures and not args.agents_done:
        print(
            "ERROR: specify either --fixtures <set> or --agents-done",
            file=sys.stderr,
        )
        return 1

    if args.fixtures:
        run_id, state, report_path = run_fixtures(
            args.base, args.candidate, args.fixtures,
            intent=args.intent,
            now=args.now,
        )
    else:
        # --agents-done
        if args.run:
            run_id = args.run
        else:
            # create context first
            from orchestrator.context import build_context
            run_id, _ = build_context(
                base_ref=args.base,
                candidate_ref=args.candidate,
                intent=args.intent,
            )
        run_id, state, report_path = run_agents_done(run_id, now=args.now)

    print(f"runId:   {run_id}")
    print(f"state:   {state}")
    print(f"report:  {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
