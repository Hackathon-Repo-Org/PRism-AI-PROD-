# Testing Agent

The Testing specialist for PRism-AI. Owned by Member 3.

## Files

| File | Purpose |
|------|---------|
| `instructions.md` | Runtime prompt — Bob follows this when the orchestrator dispatches the testing agent |
| `runner.py` | Deterministic test runner (no AI) — executes pytest, captures JUnit XML, writes `testing-execution.json` |
| `generate-tests.md` | Developer-triggered corrective action — generates missing tests as a patch |
| `tests/test_runner.py` | Unit tests for `runner.py` |
| `examples/` | Example outputs for reference |

## Runner usage

```bash
python agents/testing/runner.py --run <runId> [--timeout 300] [--runs-root runs]
```

Reads `runs/<runId>/context.json` for the test command and working directory, executes pytest,
and writes `runs/<runId>/testing-execution.json`.

## Running runner unit tests

From the repo root:

```bash
python -m pytest agents/testing/tests/ -v
```

## What the testing agent produces

For each run, the agent writes:

- `runs/<runId>/testing-execution.json` — exit code, counts, timing (A6.4 shape, written by runner.py)
- `runs/<runId>/testing-behaviour-map.md` — table of changed behaviours vs. asserting tests
- `runs/<runId>/testing-findings.json` — findings array + limitations
- `runs/<runId>/testing-result.json` — final validated result (written by orchestrator.agent_io)

## Finding categories

| Category | When raised |
|----------|-------------|
| `MISSING_TEST` | A changed behaviour has no test that asserts its result |
| `TEST_FAILURE` | An existing or new test fails |
| `WEAK_TEST` | A test calls changed code but makes no meaningful assertion |

## Severity guidance

- `TEST_FAILURE` → `HIGH` (regression) or `MEDIUM` (new test, newly failing)
- `MISSING_TEST` for directly changed behaviour → `MEDIUM`
- `MISSING_TEST` for nice-to-have scenario → `LOW`
- `WEAK_TEST` → `LOW`

Per policy, `TEST_FAILURE` at any severity is **blocking** (blocks `READY_FOR_HUMAN_REVIEW`).
`MISSING_TEST` at `MEDIUM`+ is blocking.
