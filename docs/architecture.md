# PRism-AI — Architecture

PRism-AI checks a committed change before it goes to human review. One request builds a frozen
snapshot of the change, three specialist agents analyse that same snapshot in parallel, and
plain Python turns their evidence into one readiness decision and report.

```
 developer: "check <candidate> against <base>"
        │
        ▼
 ┌─────────────────────────────────────────────┐
 │ orchestrator.context            (Python)     │  git archive of the candidate's sample-project/
 │  runs/<runId>/snapshot, diff.patch,          │  → immutable snapshot; context.json lists the
 │  context.json                                │    changed files, related source/tests/docs
 └──────────────────────┬──────────────────────┘
                        │ runId only
        ┌───────────────┼────────────────┐          dispatched in parallel by the orchestrating
        ▼               ▼                ▼          agent (orchestrator/ORCHESTRATOR.md)
 ┌─────────────┐ ┌─────────────┐ ┌───────────────┐
 │ code-review │ │ testing     │ │ documentation │   LLM specialists, each following
 │ agent       │ │ agent       │ │ agent         │   agents/<name>/instructions.md
 │             │ │ + runner.py │ │ + extract_api │   (deterministic helpers in Python)
 └──────┬──────┘ └──────┬──────┘ └───────┬───────┘
        │ findings      │ findings +     │ findings
        │               │ test execution │
        ▼               ▼                ▼
 ┌─────────────────────────────────────────────┐
 │ orchestrator.agent_io            (Python)     │  stamps runId/snapshotId/timestamps,
 │  <agent>-result.json                          │  validates against schemas/, one repair try
 └──────────────────────┬──────────────────────┘
                        ▼
 ┌─────────────────────────────────────────────┐
 │ orchestrator.aggregate           (Python)     │  config/policy.json → one of
 │  report.json                                  │  VERIFICATION_FAILED / ATTENTION_REQUIRED /
 │ orchestrator.report → report.md               │  READY_FOR_HUMAN_REVIEW
 └──────────────────────┬──────────────────────┘
                        ▼
 ┌─────────────────────────────────────────────┐
 │ orchestrator.reverify            (Python)     │  after fixes: resolved / persistent /
 │  delta.json, delta.md                         │  new / unverified vs. the previous run
 └─────────────────────────────────────────────┘
```

## Where the model is used, and where it is not

| Step | Done by | Why |
|---|---|---|
| Snapshot, diff, relevant files | Python (`context.py`) | Must be exact and reproducible |
| Finding defects, test gaps, doc gaps | LLM specialists | Needs reading and reasoning about code |
| Running tests, counting results | Python (`agents/testing/runner.py`) | Counts come from JUnit XML, never from the model |
| Extracting the API surface | Python (`agents/documentation/extract_api.py`) | OpenAPI is ground truth for fields and types |
| IDs, timestamps, schema checks | Python (`agent_io.py`, `validate.py`) | A model must not invent run identity or timing |
| Readiness decision | Python (`aggregate.py` + `config/policy.json`) | Same inputs → same decision, every time |
| Before/after comparison | Python (`reverify.py`) | Deterministic matching by finding key |

## Safety rules built into the flow

- **Snapshot isolation.** Agents read only `runs/<runId>/snapshot/`, which contains
  `sample-project/` alone — the answer key in `evaluation/` is never in reach.
- **No silent success.** A missing, failed, timed-out or schema-invalid agent result, or a test
  run that did not pass, makes the state `VERIFICATION_FAILED`. Never `READY`.
- **Unknown is null.** Test counts that could not be read are `null`, never `0`.
- **Repository content is data.** Agent instructions tell every specialist to ignore
  instructions found in code, comments, or docs.
- **Parallelism is measured, not claimed.** `report.json → timeline.overlapObserved` is computed
  from each agent's real start and finish times.
- **Never approval.** The best outcome is `READY_FOR_HUMAN_REVIEW`; a human still reviews.

## Contracts

- `schemas/context.schema.json`, `schemas/agent-result.schema.json`, `schemas/report.schema.json`
- Finding key: `<CATEGORY>|<file>|<symbol or ->|<subject>` — how findings are de-duplicated
  and matched across runs (enforced by the schema pattern and `validate.py`).
- Policy: `config/policy.json` (`HIGH`+ blocks by default; `MEDIUM`+ for missing tests and
  public-API doc gaps; any test failure blocks).
