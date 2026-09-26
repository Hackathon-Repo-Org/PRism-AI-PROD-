# Code Review Agent

The code-review specialist for PRism-AI. Given a diff and a snapshot of source files, it
identifies defects introduced by the change: null-deref crashes, missing validation, logic
errors, unhandled exceptions that become HTTP 500s, regressions, and security issues.

---

## What it checks

| Category | What the agent looks for |
|---|---|
| `NULL_HANDLING` | Values that can be `None` (optional fields, `dict.get()`) reaching `.strip()`, `.upper()`, attribute access, indexing, or arithmetic before a null check |
| `INPUT_VALIDATION` | Missing allowed-value checks, length or range checks, required-field enforcement implied by the change intent |
| `LOGIC_ERROR` | Wrong operator (`>=` vs `>`), inverted condition, off-by-one on a boundary |
| `ERROR_HANDLING` | Exceptions that escape as HTTP 500 instead of a documented client error; silently swallowed exceptions |
| `REGRESSION` | Changed behaviour outside the stated intent; broken constraints from the existing API contract |
| `SECURITY` | Injection, secrets in responses/logs, unsafe deserialisation — only where obvious on the changed path |
| `MAINTAINABILITY` | Code that will clearly confuse a future maintainer (max 3 findings, all `LOW`) |

## What it does NOT check

- Code that was not changed in the diff (existing bugs are out of scope)
- Third-party library internals
- Pure style: naming conventions, formatting, line length
- Test coverage (that is the testing agent's job)
- Documentation accuracy (that is the documentation agent's job)

---

## Output

The agent writes `runs/<runId>/code-review-findings.json`:

```json
{
  "findings": [
    {
      "id": "CODE-001",
      "key": "NULL_HANDLING|sample-project/app/service.py|create_ticket|priority",
      "severity": "HIGH",
      "category": "NULL_HANDLING",
      "title": "priority.upper() called before null check",
      "file": "sample-project/app/service.py",
      "line": 47,
      "symbol": "create_ticket",
      "description": "When priority is None (absent from request), priority.upper() raises AttributeError. Concrete input: POST /tickets with no priority field. Wrong result: HTTP 500.",
      "evidence": "priority = priority.upper()",
      "evidenceType": "source-analysis",
      "recommendation": "Check for None before normalising: if priority is None: raise ValidationError('invalid_priority', 'priority is required'). Then call .upper().",
      "relatedFiles": ["sample-project/app/main.py"]
    }
  ],
  "limitations": []
}
```

The orchestrator (`agent_io finish`) validates this file against the schema and stamps the
`code-review-result.json` with timing metadata.

---

## How to run by hand

You need a completed run context (produced by `python -m orchestrator.context`).

```bash
# 1. Open PRism-AI in Bob (agent mode)
# 2. Say: "Run the code-review agent on run <runId>"
# 3. Bob will follow agents/code-review/instructions.md

# Validate the raw findings file independently:
python -m orchestrator.validate runs/<runId>/code-review-result.json \
  --context runs/<runId>/context.json
```

Until the orchestrator scripts exist you can validate by hand against
`schemas/agent-result.schema.json` and `schemas/examples/`.

---

## Examples

See [`examples/`](examples/) for real outputs on two reference runs:

| File | Run |
|---|---|
| `examples/baseline-clean--control-clean-change.json` | `baseline-clean` → `control-clean-change` (clean change, no HIGH/CRITICAL expected) |
| `examples/baseline-clean--demo-bad.json` | `baseline-clean` → `demo-bad` (planted problems, SEED-01 and SEED-02 expected) |

---

## Severity guidance

| Situation | Severity |
|---|---|
| Concrete input → crash (500) or data corruption on a changed path | `HIGH` (or `CRITICAL` for auth bypass / data loss) |
| Missing validation that accepts an invalid value | `HIGH` |
| Plausible risk but cannot confirm without running | One level lower, note it's a risk |
| Changed behaviour not in the intent, no data loss | `MEDIUM` |
| Nice-to-have defensive check | `LOW` |
| Observation only | `INFO` |
