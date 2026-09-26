"""
orchestrator.agent_io — the only way agent results get written.

CLI:
    # Record that an agent has started:
    python -m orchestrator.agent_io begin --run <runId> --agent <name>

    # Stamp and validate the model-written findings, write the final result:
    python -m orchestrator.agent_io finish \\
        --run <runId> --agent <name> \\
        [--findings <path-to-findings.json>] \\
        [--execution <path-to-execution.json>] \\
        [--status completed|error|timeout|skipped] \\
        [--reason "..."]

    --findings is required for a completed result; an agent reporting its own
    failure may pass --status error --reason "..." without one.

Exit codes for `finish`:
    0  — result written successfully
    2  — findings were invalid; errors printed so the agent can repair once
         (just call finish again after fixing the file)
    1  — unrecoverable error (second validation failure, missing context, etc.)

Importable:
    from orchestrator.agent_io import begin, finish, FinishResult
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Any

from orchestrator import safe_run_id

_REPO = pathlib.Path(__file__).parent.parent
_RUNS_DIR = _REPO / "runs"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _ms_between(start_iso: str, end_iso: str) -> int:
    """Return integer milliseconds between two ISO-8601 UTC strings."""
    fmt = "%Y-%m-%dT%H:%M:%S.%fZ"
    try:
        t0 = datetime.datetime.strptime(start_iso, fmt)
        t1 = datetime.datetime.strptime(end_iso, fmt)
        return max(0, int((t1 - t0).total_seconds() * 1000))
    except ValueError:
        return 0


def _atomic_write(path: pathlib.Path, data: dict) -> None:
    tmp = pathlib.Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


def _run_dir(run_id: str) -> pathlib.Path:
    return _RUNS_DIR / safe_run_id(run_id)


# ---------------------------------------------------------------------------
# begin
# ---------------------------------------------------------------------------

def begin(run_id: str, agent: str, runs_dir: pathlib.Path | None = None) -> pathlib.Path:
    """
    Write <agent>.started.json into the run directory.
    Returns the path written.
    """
    rdir = (runs_dir or _RUNS_DIR) / run_id
    rdir.mkdir(parents=True, exist_ok=True)

    started_at = _now_utc()
    started_path = rdir / f"{agent}.started.json"
    _atomic_write(started_path, {"agent": agent, "startedAt": started_at})
    # A new dispatch gets its own repair chance.
    (rdir / f"{agent}.repair.json").unlink(missing_ok=True)
    return started_path


# ---------------------------------------------------------------------------
# FinishResult
# ---------------------------------------------------------------------------

class FinishStatus(str, Enum):
    OK            = "ok"           # result written successfully
    NEEDS_REPAIR  = "needs_repair" # first validation failure; errors printed
    ERROR         = "error"        # second failure or unrecoverable


@dataclass
class FinishResult:
    status: FinishStatus
    result_path: pathlib.Path | None = None
    validation_errors: list[str] | None = None


# ---------------------------------------------------------------------------
# finish (importable core)
# ---------------------------------------------------------------------------

def finish(
    run_id: str,
    agent: str,
    findings_path: pathlib.Path | None,
    execution_path: pathlib.Path | None = None,
    override_status: str | None = None,
    reason: str | None = None,
    *,
    runs_dir: pathlib.Path | None = None,
) -> FinishResult:
    """
    Stamp, validate, and atomically write <agent>-result.json.

    The first validation failure writes <agent>.repair.json and returns
    NEEDS_REPAIR; a failure while that marker exists writes an error result.

    Parameters
    ----------
    run_id, agent    : identify the run and agent
    findings_path    : path to the model-written findings file
                       Format: {"findings": [...], "limitations": [...]}
                       May be None or missing when the status is not
                       "completed" (the agent is reporting its own failure).
    execution_path   : optional path to a testing execution JSON block
    override_status  : override the status field (default: "completed")
    reason           : statusReason (required when status != "completed")
    """
    from orchestrator.validate import validate_result

    rdir = (runs_dir or _RUNS_DIR) / run_id

    # --- load context.json ---
    ctx_path = rdir / "context.json"
    if not ctx_path.exists():
        print(
            f"ERROR: context.json not found at {ctx_path}. "
            "Run orchestrator.context first.",
            file=sys.stderr,
        )
        return FinishResult(status=FinishStatus.ERROR)

    context = json.loads(ctx_path.read_text(encoding="utf-8"))

    # --- load startedAt ---
    started_path = rdir / f"{agent}.started.json"
    if started_path.exists():
        started_data = json.loads(started_path.read_text(encoding="utf-8"))
        started_at = started_data.get("startedAt", _now_utc())
    else:
        # begin was not called; use now as a fallback (not ideal but not fatal)
        started_at = _now_utc()

    finished_at = _now_utc()
    duration_ms = _ms_between(started_at, finished_at)

    status = override_status or "completed"

    # Validate the raw envelope before defaults can hide missing fields.
    raw_errors = []
    raw = None
    if status != "completed" and (findings_path is None or not findings_path.exists()):
        # An agent reporting its own failure may have no findings to hand in.
        raw = {"findings": [], "limitations": []}
    elif findings_path is None:
        raw_errors.append("A findings file is required when the status is completed.")
    else:
        try:
            raw = json.loads(findings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raw_errors.append(f"Cannot read findings JSON: {exc}")
    if not isinstance(raw, dict):
        raw_errors.append("Raw findings must be an object with findings and limitations arrays.")
    elif set(raw) != {"findings", "limitations"}:
        raw_errors.append("Raw findings requires exactly findings and limitations fields.")
    findings = raw.get("findings") if isinstance(raw, dict) else None
    limitations = raw.get("limitations") if isinstance(raw, dict) else None

    # --- load execution block ---
    execution: dict | None = None
    if execution_path is not None:
        try:
            execution = json.loads(execution_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"ERROR: cannot read execution file {execution_path}: {exc}", file=sys.stderr)
            return FinishResult(status=FinishStatus.ERROR)

    # --- determine status reason ---
    status_reason: str | None = reason if status != "completed" else None

    # --- assemble full result ---
    result = {
        "schemaVersion": "1.0",
        "runId":         context["runId"],
        "snapshotId":    context["snapshotId"],
        "agent":         agent,
        "status":        status,
        "statusReason":  status_reason,
        "startedAt":     started_at,
        "finishedAt":    finished_at,
        "durationMs":    duration_ms,
        "findings":      findings,
        "limitations":   limitations,
        "execution":     execution,
    }

    # --- validate ---
    vr = validate_result(result, context=context)

    vr.errors = raw_errors + vr.errors
    # The first failure leaves a marker; a failure while it exists is the
    # failed repair attempt.
    repair_marker = rdir / f"{agent}.repair.json"
    if vr.errors:
        if repair_marker.exists():
            repair_marker.unlink(missing_ok=True)
            # Second failure — write an error result, never silently drop
            print(
                "ERROR: findings still invalid after repair attempt. "
                "Writing error result.",
                file=sys.stderr,
            )
            for e in vr.errors:
                print(f"  {e}", file=sys.stderr)

            error_result = {
                "schemaVersion": "1.0",
                "runId":         context["runId"],
                "snapshotId":    context["snapshotId"],
                "agent":         agent,
                "status":        "error",
                "statusReason":  "SCHEMA_INVALID: " + "; ".join(vr.errors[:3]),
                "startedAt":     started_at,
                "finishedAt":    finished_at,
                "durationMs":    duration_ms,
                "findings":      [],
                "limitations":   [],
                "execution":     None,
            }
            result_path = rdir / f"{agent}-result.json"
            _atomic_write(result_path, error_result)
            return FinishResult(
                status=FinishStatus.ERROR,
                result_path=result_path,
                validation_errors=vr.errors,
            )
        else:
            # First failure — print errors and signal that repair is needed
            _atomic_write(repair_marker, {"agent": agent, "failedAt": finished_at,
                                          "errors": vr.errors})
            source = findings_path.name if findings_path else "findings"
            print(
                f"VALIDATION ERRORS in {source} — "
                "fix these and call finish again:",
                file=sys.stderr,
            )
            for e in vr.errors:
                print(f"  {e}", file=sys.stderr)
            return FinishResult(
                status=FinishStatus.NEEDS_REPAIR,
                validation_errors=vr.errors,
            )

    # --- write result ---
    result_path = rdir / f"{agent}-result.json"
    _atomic_write(result_path, result)
    repair_marker.unlink(missing_ok=True)
    return FinishResult(status=FinishStatus.OK, result_path=result_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.agent_io",
        description="Record agent lifecycle events.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # begin
    bp = sub.add_parser("begin", help="Record that an agent has started.")
    bp.add_argument("--run",   required=True, help="runId")
    bp.add_argument("--agent", required=True,
                    choices=["code-review", "testing", "documentation"],
                    help="Agent name.")

    # finish
    fp = sub.add_parser("finish", help="Stamp and write the agent result.")
    fp.add_argument("--run",       required=True, help="runId")
    fp.add_argument("--agent",     required=True,
                    choices=["code-review", "testing", "documentation"])
    fp.add_argument("--findings",  default=None,
                    help="Path to the model-written findings JSON file "
                         "(optional when --status is not completed).")
    fp.add_argument("--execution", default=None,
                    help="Path to a testing execution JSON block (testing agent only).")
    fp.add_argument("--status",    default=None,
                    choices=["completed", "error", "timeout", "skipped"],
                    help="Override the status (default: completed).")
    fp.add_argument("--reason",    default=None,
                    help="statusReason (required when --status != completed).")
    fp.add_argument("--repair",    action="store_true",
                    help="Deprecated and ignored: repair attempts are detected "
                         "automatically.")

    args = parser.parse_args(argv)

    if args.command == "begin":
        path = begin(args.run, args.agent)
        print(f"started: {path}")
        return 0

    # finish
    findings_path  = pathlib.Path(args.findings) if args.findings else None
    execution_path = pathlib.Path(args.execution) if args.execution else None

    result = finish(
        run_id=args.run,
        agent=args.agent,
        findings_path=findings_path,
        execution_path=execution_path,
        override_status=args.status,
        reason=args.reason,
    )

    if result.status == FinishStatus.OK:
        print(f"OK  {result.result_path}")
        return 0
    elif result.status == FinishStatus.NEEDS_REPAIR:
        # Exit 2: caller should fix the findings file and call finish again
        return 2
    else:
        return 1


if __name__ == "__main__":
    sys.exit(_main())
