#!/usr/bin/env python3
"""validate_findings.py — offline hand-validator for *-findings.json files.

Checks a raw findings file (as produced by an agent before orchestrator stamping)
against the A6.3 finding contract and the agent-result schema rules from A6.2.

Usage:
    python agents/validate_findings.py <findings.json> --agent <code-review|documentation>

Exit 0 if valid, 1 if errors found.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ALLOWED_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}
ALLOWED_EVIDENCE_TYPES = {"source-analysis", "tool-output", "test-execution", "doc-comparison"}

ALLOWED_CATEGORIES = {
    "code-review": {
        "NULL_HANDLING", "INPUT_VALIDATION", "LOGIC_ERROR",
        "ERROR_HANDLING", "REGRESSION", "SECURITY", "MAINTAINABILITY",
    },
    "documentation": {
        "DOC_MISMATCH", "DOC_MISSING", "DOC_EXAMPLE_INVALID",
    },
    "testing": {
        "MISSING_TEST", "WEAK_TEST", "TEST_FAILURE",
    },
}

ID_PREFIXES = {
    "code-review": "CODE-",
    "documentation": "DOC-",
    "testing": "TEST-",
}


def validate(path: Path, agent: str) -> list[str]:
    errors: list[str] = []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"Cannot parse JSON: {exc}"]

    # Top-level structure
    if not isinstance(data, dict):
        return ["Root must be a JSON object"]
    if "findings" not in data:
        errors.append("Missing top-level 'findings' key")
    if "limitations" not in data:
        errors.append("Missing top-level 'limitations' key")
    if errors:
        return errors

    if not isinstance(data["findings"], list):
        errors.append("'findings' must be an array")
        return errors
    if not isinstance(data["limitations"], list):
        errors.append("'limitations' must be an array")

    allowed_cats = ALLOWED_CATEGORIES.get(agent, set())
    id_prefix = ID_PREFIXES.get(agent, "")
    seen_ids: set[str] = set()
    seen_keys: set[str] = set()

    for i, f in enumerate(data["findings"]):
        loc = f"findings[{i}]"

        # Required string fields
        for field in ("id", "key", "severity", "category", "title",
                      "file", "description", "evidence", "evidenceType", "recommendation"):
            if field not in f:
                errors.append(f"{loc}: missing required field '{field}'")
            elif not isinstance(f[field], str) or not f[field].strip():
                errors.append(f"{loc}: field '{field}' must be a non-empty string")

        # id uniqueness and prefix
        fid = f.get("id", "")
        if fid:
            if fid in seen_ids:
                errors.append(f"{loc}: duplicate id '{fid}'")
            seen_ids.add(fid)
            if id_prefix and not fid.startswith(id_prefix):
                errors.append(f"{loc}: id '{fid}' must start with '{id_prefix}'")

        # key uniqueness and format (CATEGORY|file|symbol|subject, no line numbers)
        key = f.get("key", "")
        if key:
            if key in seen_keys:
                errors.append(f"{loc}: duplicate key '{key}'")
            seen_keys.add(key)
            parts = key.split("|")
            if len(parts) != 4:
                errors.append(f"{loc}: key '{key}' must have exactly 4 pipe-separated parts")

        # severity
        sev = f.get("severity", "")
        if sev and sev not in ALLOWED_SEVERITIES:
            errors.append(f"{loc}: severity '{sev}' not in {sorted(ALLOWED_SEVERITIES)}")

        # category
        cat = f.get("category", "")
        if cat and allowed_cats and cat not in allowed_cats:
            errors.append(f"{loc}: category '{cat}' not allowed for agent '{agent}'")

        # evidenceType
        et = f.get("evidenceType", "")
        if et and et not in ALLOWED_EVIDENCE_TYPES:
            errors.append(f"{loc}: evidenceType '{et}' not in {sorted(ALLOWED_EVIDENCE_TYPES)}")

        # line: must be int or null
        if "line" in f and f["line"] is not None and not isinstance(f["line"], int):
            errors.append(f"{loc}: 'line' must be an integer or null")

        # symbol: must be string or null
        if "symbol" in f and f["symbol"] is not None and not isinstance(f["symbol"], str):
            errors.append(f"{loc}: 'symbol' must be a string or null")

        # relatedFiles: must be list of strings
        rf = f.get("relatedFiles", [])
        if not isinstance(rf, list):
            errors.append(f"{loc}: 'relatedFiles' must be an array")
        elif any(not isinstance(x, str) for x in rf):
            errors.append(f"{loc}: all 'relatedFiles' entries must be strings")

        # MAINTAINABILITY cap: max 3, all LOW
        if cat == "MAINTAINABILITY":
            if sev and sev != "LOW":
                errors.append(f"{loc}: MAINTAINABILITY findings must be severity LOW, got '{sev}'")

    # MAINTAINABILITY count
    m_count = sum(1 for f in data["findings"] if f.get("category") == "MAINTAINABILITY")
    if m_count > 3:
        errors.append(f"Too many MAINTAINABILITY findings: {m_count} (max 3)")

    return errors


def main() -> None:
    # cp1252 consoles cannot encode the dashes/bullets printed below.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    p = argparse.ArgumentParser(description="Validate a *-findings.json file.")
    p.add_argument("path", type=Path, help="Path to the findings JSON file")
    p.add_argument("--agent", required=True,
                   choices=list(ALLOWED_CATEGORIES.keys()),
                   help="Agent name (code-review | documentation | testing)")
    args = p.parse_args()

    errors = validate(args.path, args.agent)
    if errors:
        print(f"INVALID — {len(errors)} error(s) in {args.path}:")
        for e in errors:
            print(f"  • {e}")
        sys.exit(1)
    else:
        findings_count = len(json.loads(args.path.read_text())["findings"])
        print(f"VALID — {findings_count} finding(s), 0 errors in {args.path}")


if __name__ == "__main__":
    main()
