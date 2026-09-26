# Handoff note — M1 → M2 and M3: Contracts, examples, config, validator

**Date:** 2026-09-26  
**From:** Member 1 (Orchestration)  
**To:** Members 2 and 3  
**Status:** Local implementation; not committed or merged

---

## What is available now

Git inspected on 2026-09-26: local `main` and `origin/main` point to
`969fae3` (project structure only). The M1 implementation, schemas, config, and
handoffs are untracked working-tree files. No merge or publication is established.
Run `python -m pytest orchestrator/tests/` to verify the local implementation;
fixture acceptance is separate from real integration.

### 1. JSON Schema contracts (`schemas/`)

Three schemas, draft 2020-12, all `additionalProperties: false`:

| File | What it validates |
|---|---|
| `schemas/context.schema.json` | `context.json` produced by M1's context step |
| `schemas/agent-result.schema.json` | Final result written by **M1 via agent_io finish** |
| `schemas/report.schema.json` | `report.json` (M1 internal — for reference) |

Agents write raw `{"findings": [...], "limitations": [...]}` only. Both arrays
are required; raw findings are not an agent-result artifact. M1 calls `begin`
and `finish`, stamps metadata, attaches M3's execution block, and validates the
final `<agent>-result.json`. Do not send raw findings to `orchestrator.validate`.

**Key constraints on the final `agent-result.schema.json`:**

- `schemaVersion`: always `"1.0"`
- `agent`: exactly `"code-review"`, `"testing"`, or `"documentation"`
- `status`: `"completed"` | `"error"` | `"timeout"` | `"skipped"`
- `statusReason`: **must be a non-empty string when status ≠ `"completed"`**; `null` when completed
- `execution`: **must be `null` for `code-review` and `documentation`**; only `testing` fills it
- `findings[].id`: pattern `^(CODE|TEST|DOC)-\d{3}$`
- `findings[].category`: **only categories for your agent are valid**
  - `code-review`: `NULL_HANDLING` `INPUT_VALIDATION` `LOGIC_ERROR` `ERROR_HANDLING` `REGRESSION` `SECURITY` `MAINTAINABILITY`
  - `testing`: `MISSING_TEST` `WEAK_TEST` `TEST_FAILURE`
  - `documentation`: `DOC_MISMATCH` `DOC_MISSING` `DOC_EXAMPLE_INVALID`
- `findings[].file`: must start with `sample-project/` (no `..`)
- `findings[].evidence`: required, non-empty — quote actual source text, tool output, or doc text
- `findings[].evidenceType`: `"source-analysis"` | `"tool-output"` | `"test-execution"` | `"doc-comparison"`

### 2. Example fixtures (`schemas/examples/`)

Hand-written, schema-valid examples for every agent and every status:

| File | Purpose |
|---|---|
| `context.example.json` | Reference context structure |
| `code-review-result.clean.json` | Completed, no findings |
| `code-review-result.bad.json` | Completed, 2 generic findings (HIGH + MEDIUM) |
| `code-review-result.error.json` | status: error |
| `testing-result.clean.json` | Completed, tests pass, no findings |
| `testing-result.bad.json` | Completed, tests pass (exitCode 0), 1 MISSING_TEST finding |
| `testing-result.tests-failed.json` | Completed, exitCode 1 — for VERIFICATION_FAILED path |
| `testing-result.error.json` | status: error, null counts |
| `documentation-result.{clean,bad,error}.json` | Same pattern |

Use these to understand the exact shape expected and to test your agent's output
before integration.

### 3. Config (`config/`)

- `config/project.json` — `scopePath`, `testCommand`, `testWorkingDirectory`, `language`, `framework`
- `config/policy.json` — readiness thresholds (blocking severity per category)

Your agents **read** `context.json` (written by M1); they never read `config/`.
Context already includes all project fields.

### 4. Validator (`orchestrator/validate.py`)

```bash
# Validate a result file on its own (schema only):
python -m orchestrator.validate <agent>-result.json

# Validate with context (schema + runId/snapshotId match + scope check):
python -m orchestrator.validate <agent>-result.json \
    --context runs/<runId>/context.json
```

**Exit codes:** `0` = valid, `1` = invalid (errors printed to stderr).

**Importable** for use inside your agent runner:

```python
from orchestrator.validate import validate_result

vr = validate_result(data, context=context_dict)
if not vr.valid:
    for e in vr.errors:
        print(e)
```

The validator enforces: schema, `runId`/`snapshotId` match, all `file` paths
start with `scopePath` and contain no `..`.

---

## What you need to do

**Member 2:** Push `baseline-clean`, `demo-bad`, `demo-fixed`, `control-clean-change`
git tags to the shared repo so M1 can run the real end-to-end pipeline (CP2).
M1 is blocked on those tags for T8/CP2.

**Member 3:** Your `agents/testing/runner.py` must produce a findings file
(`{"findings": [...], "limitations": [...]}`) and a separate execution JSON block,
then hand both files to M1, who calls `agent_io finish` (see the agent_io handoff).

Runtime analysis inputs are `context.json`, the diff at `diffPath`, and only
context-listed files (`changedFiles[].path`, `relevantSource`, `relevantTests`,
`relevantDocs`) read under `snapshotDir`. Skip deleted or excluded files that
are absent. Snapshot membership alone does not authorize a read. Never read
live working-tree source or `evaluation/`.

Real integration remains pending M2's agent instructions, documentation helper,
sample app and required tags, and M3's testing agent and runner. Local Git has
no tags and the teammate directories contain only placeholders. T7 fixture
acceptance does not establish CP2 or real test/agent execution.

## Review verification (2026-09-26)

The complete orchestrator suite passed: **245 passed in 19.65s** (186 existing
cases plus 59 added regression cases). The T7 assertions also verify that bad
fixtures have blocking findings with exitCode 0 and tests-failed fixtures carry
TESTS_FAILED. Outcomes: bad = ATTENTION_REQUIRED; clean =
READY_FOR_HUMAN_REVIEW; error and tests-failed = VERIFICATION_FAILED.
These are fixture results, not evidence of real specialist or sample-app runs.

Command: `python -m pytest orchestrator/tests -q -p no:cacheprovider`.
In the Codex Windows sandbox, pytest's mode-0700 temporary-directory creation
caused permission errors (initial run: 91 passed, 95 setup errors). The successful
run used an external test-only launcher that maps mkdir mode 0700 to inherited
0777 permissions, with a fresh workspace `--basetemp`. No repository tests or
assertions were skipped, and no production behavior was patched by that launcher.

Git HEAD remains `969fae389e25ad12b0868b9bd167cfa0ba340c86`; no commits,
pushes, merges, tags, or teammate-file edits were made. T8 and later work remains
out of scope. Real integration remains blocked on teammates' agents, runner,
sample app, and tags listed above.
