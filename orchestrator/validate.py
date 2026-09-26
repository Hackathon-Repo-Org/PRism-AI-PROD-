"""
orchestrator.validate — JSON artifact validator.

CLI:
    python -m orchestrator.validate <file.json> [--context <context.json>]

Importable:
    from orchestrator.validate import validate_result, ValidationResult

Detects artifact kind by inspecting top-level keys, validates against the
matching JSON Schema (draft 2020-12), and with --context also checks:
  - runId and snapshotId match context.json
  - every finding's file path starts with scopePath and contains no '..'
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Any

import jsonschema
from jsonschema import Draft202012Validator

_REPO = pathlib.Path(__file__).parent.parent
_SCHEMAS = _REPO / "schemas"


# ---------------------------------------------------------------------------
# Schema cache (loaded once per process)
# ---------------------------------------------------------------------------

def _load_schema(name: str) -> dict:
    return json.loads((_SCHEMAS / name).read_text(encoding="utf-8"))


_CONTEXT_SCHEMA      = _load_schema("context.schema.json")
_AGENT_RESULT_SCHEMA = _load_schema("agent-result.schema.json")
_REPORT_SCHEMA       = _load_schema("report.schema.json")


# ---------------------------------------------------------------------------
# Kind detection
# ---------------------------------------------------------------------------

def _detect_kind(data: dict) -> str:
    """Return 'context', 'agent-result', or 'report'. Raises ValueError if unknown."""
    if not isinstance(data, dict):
        raise ValueError("Artifact must be a JSON object.")
    if "agent" in data and "findings" in data:
        return "agent-result"
    if "readiness" in data and "agents" in data:
        return "report"
    if "changedFiles" in data and "scopePath" in data:
        return "context"
    raise ValueError(
        "Cannot determine artifact kind — missing discriminating fields. "
        "Expected one of: context.json (changedFiles+scopePath), "
        "agent result (agent+findings), report (readiness+agents)."
    )


# ---------------------------------------------------------------------------
# Scope check helpers
# ---------------------------------------------------------------------------

def _is_safe_path(file_path: str, scope_path: str) -> bool:
    """
    Return True when file_path is safely inside scope_path.
    Rejects:
      - any component equal to '..'
      - paths that do not start with scope_path/
    """
    if not isinstance(file_path, str) or not isinstance(scope_path, str) or not scope_path:
        return False
    normalized = file_path.replace("\\", "/")
    scope = scope_path.replace("\\", "/").rstrip("/")
    if pathlib.PureWindowsPath(file_path).drive or normalized.startswith("/"):
        return False
    if ".." in normalized.split("/"):
        return False
    return normalized.startswith(scope + "/") or normalized == scope



# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    valid: bool
    kind: str | None = None
    errors: list[str] = field(default_factory=list)

    def print_errors(self) -> None:
        for msg in self.errors:
            print(f"  ERROR: {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Core validator (importable)
# ---------------------------------------------------------------------------

def validate_result(
    data: dict,
    context: dict | None = None,
) -> ValidationResult:
    """
    Validate *data* against the appropriate schema.

    If *context* is provided (a parsed context.json dict), also performs:
      - runId / snapshotId identity check
      - scope check on every finding's file path

    Returns a ValidationResult; never raises.
    """
    # --- kind detection ---
    try:
        kind = _detect_kind(data)
    except ValueError as exc:
        return ValidationResult(valid=False, errors=[str(exc)])

    schema_map = {
        "context":      _CONTEXT_SCHEMA,
        "agent-result": _AGENT_RESULT_SCHEMA,
        "report":       _REPORT_SCHEMA,
    }
    validator = Draft202012Validator(schema_map[kind])
    schema_errors = [e.message for e in validator.iter_errors(data)]

    extra_errors: list[str] = []

    if context is not None and not isinstance(context, dict):
        return ValidationResult(False, kind, schema_errors + ["Context must be a JSON object."])

    if context is not None:
        # identity checks
        if data.get("runId") != context.get("runId"):
            extra_errors.append(
                f"runId mismatch: artifact has {data.get('runId')!r}, "
                f"context has {context.get('runId')!r}"
            )
        if data.get("snapshotId") != context.get("snapshotId"):
            extra_errors.append(
                f"snapshotId mismatch: artifact has {data.get('snapshotId')!r}, "
                f"context has {context.get('snapshotId')!r}"
            )

        # scope check on findings
        scope_path = context.get("scopePath", "")
        findings = data.get("findings", [])
        for finding in findings if isinstance(findings, list) else []:
            if not isinstance(finding, dict):
                continue
            file_path = finding.get("file", "")
            key_parts = str(finding.get("key", "")).split("|")
            if len(key_parts) == 4 and (key_parts[0] != finding.get("category")
                                        or key_parts[1] != file_path):
                extra_errors.append(
                    f"Finding {finding.get('id', '?')!r}: key {finding.get('key')!r} must "
                    f"start with its category {finding.get('category')!r} and file "
                    f"{file_path!r} (<CATEGORY>|<file>|<symbol or ->|<subject>)."
                )
            if not _is_safe_path(file_path, scope_path):
                extra_errors.append(
                    f"Finding {finding.get('id', '?')!r}: file {file_path!r} is "
                    f"outside scope {scope_path!r} or contains path traversal."
                )
            related_files = finding.get("relatedFiles", [])
            for related in related_files if isinstance(related_files, list) else []:
                if not _is_safe_path(related, scope_path):
                    extra_errors.append(
                        f"Finding {finding.get('id', '?')!r}: relatedFile {related!r} is "
                        f"outside scope {scope_path!r} or contains path traversal."
                    )

        if kind == "context":
            paths = []
            for name in ("relevantSource", "relevantTests", "relevantDocs"):
                values = data.get(name, [])
                if isinstance(values, list):
                    paths.extend(values)
            changed = data.get("changedFiles", [])
            if isinstance(changed, list):
                paths.extend(item.get("path") for item in changed if isinstance(item, dict))
            for path in paths:
                if not _is_safe_path(path, scope_path):
                    extra_errors.append(f"Context path {path!r} is outside scope or contains path traversal.")

    all_errors = schema_errors + extra_errors
    return ValidationResult(
        valid=len(all_errors) == 0,
        kind=kind,
        errors=all_errors,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.validate",
        description="Validate a PRism-AI JSON artifact against its schema.",
    )
    parser.add_argument("file", help="Path to the JSON file to validate.")
    parser.add_argument(
        "--context",
        metavar="CONTEXT_JSON",
        help="Path to context.json for runId/snapshotId and scope checks.",
    )
    args = parser.parse_args(argv)

    try:
        data = json.loads(pathlib.Path(args.file).read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"ERROR: cannot read {args.file!r}: {exc}", file=sys.stderr)
        return 1

    context: dict | None = None
    if args.context:
        try:
            context = json.loads(pathlib.Path(args.context).read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"ERROR: cannot read context file {args.context!r}: {exc}", file=sys.stderr)
            return 1

    result = validate_result(data, context=context)

    if result.valid:
        print(f"OK  {args.file}  [{result.kind}]")
        return 0
    else:
        print(f"INVALID  {args.file}  [{result.kind}]", file=sys.stderr)
        result.print_errors()
        return 1


if __name__ == "__main__":
    sys.exit(_main())
