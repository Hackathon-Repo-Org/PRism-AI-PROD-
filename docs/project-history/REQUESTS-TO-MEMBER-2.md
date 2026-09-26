# Requests to Member 2 — `evaluation/seeded-issues.json`

> **From:** Member 3 (Testing agent + evaluation)
> **Rule:** A11.1 — Member 3 must not write `evaluation/seeded-issues.*`; that file belongs to
> Member 2 (see B0 and A10). This document records exactly what Member 3 needs.

---

## What the file is

`evaluation/seeded-issues.json` is the ground-truth answer key used **only** by Member 3's
scorer (`evaluation/score.py`). Runtime agents never read it.

---

## Schema (A10)

Each element of the top-level JSON array must conform to:

```json
{
  "id":                 "SEED-NN",
  "agent":              "code-review" | "testing" | "documentation",
  "acceptedCategories": ["<category>", ...],
  "file":               "sample-project/app/<file>.py",
  "symbols":            ["<function_name>"],
  "subjectHints":       ["<lowercase_snake_case_slug>"],
  "expected":           "Plain-English description of what a correct finding must say.",
  "reproduction":       "Concrete input and the wrong result it produces.",
  "presentIn":          ["demo-bad"],
  "fixedIn":            "demo-fixed"
}
```

Field rules:
- `id`: unique string, e.g. `"SEED-01"`, `"SEED-02"`, …
- `agent`: must match one of the three agent names exactly.
- `acceptedCategories`: list of allowed `category` values from A6.3 for the named agent.
  Multiple values mean the agent may report the same defect under any of those categories.
- `file`: repo-relative path with forward slashes, e.g. `"sample-project/app/service.py"`.
- `symbols`: list of function/method names under test. Scorer skips this check if the list is
  empty, so only omit it when the defect is not tied to a specific function.
- `subjectHints`: list of lowercase slug strings that must appear in the finding's `key` subject
  segment (the last `|`-separated part). Scorer skips this check if empty.
- `presentIn`: list of git ref names where this defect is present. Usually `["demo-bad"]`.
  Add `"heldout-variation"` if the same defect appears there too.
- `fixedIn`: the ref where the defect is resolved. Usually `"demo-fixed"`.

---

## What Member 3 needs (one seed per planted defect)

Member 3 needs **one seed entry for every planted defect** in `demo-bad` that one of the three
agents should find. Specifically:

1. **One seed for the testing agent** — for the defect that is introduced in `demo-bad` and
   is expected to be reported as a `MISSING_TEST` (or `TEST_FAILURE` if a test exercises the
   broken code path). Fill in the exact `file`, `symbols`, and `subjectHints` that match the
   planted defect so the scorer can determine whether the testing agent found it.

2. **Seeds for the code-review and documentation agents** as appropriate for the other planted
   defects — Member 2 owns those.

### Minimum required for Member 3's scorer to run

At minimum, provide the seeds whose `agent` is `"testing"`. Without them, `score.py` reports
`seedsPresent: 0` for the testing agent and cannot compute testing recall.

### Example (fill in actual values):

```json
[
  {
    "id": "SEED-01",
    "agent": "testing",
    "acceptedCategories": ["MISSING_TEST"],
    "file": "sample-project/app/service.py",
    "symbols": ["create_ticket"],
    "subjectHints": ["null_priority"],
    "expected": "The testing agent should report that no test asserts the behaviour when priority is null.",
    "reproduction": "POST /tickets with a body that omits priority; the API silently accepts or crashes.",
    "presentIn": ["demo-bad"],
    "fixedIn": "demo-fixed"
  }
]
```

---

## When Member 3 needs this

- Before **CP2** (sequential end-to-end): at least the testing seeds must exist so
  `python evaluation/score.py --run <runId> --ref demo-bad` produces meaningful output.
- Before **CP4** (evaluation): all seeds for all agents must be present and final, because
  `evaluation/benchmark.md` is frozen and no re-runs are performed.

---

## Important constraints

- Do **not** put this file on `feature/testing-*` or `feature/eval-*` branches — it belongs
  on Member 2's branch (`feature/code-*` or `fixture/*`) and must not be seen by the testing
  agent before it runs (A11 rule 5).
- The scorer reads `subjectHints` case-insensitively but `file` and `agent` are exact-match.
  Use the exact repo-relative path as it appears in the snapshot.
