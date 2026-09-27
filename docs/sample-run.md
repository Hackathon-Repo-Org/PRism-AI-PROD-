# Sample run — commit check (input, process, output)

A real, unedited run of `python prism.py check` on a one-line change to `sample-project/`,
using DeepSeek (`deepseek-chat`). Every file below is copied from
`runs/run-20260926-084322-ccb07af/`; long files are shortened (marked `...`).

> The terminal output shown here is from before the findings table and recommendation list
> were added; the files and the decision are unchanged. For a large-folder scan and a chat
> session, see the *Sample run* section of the README.

```
 INPUT                    PROCESS                                   OUTPUT
 ─────                    ───────                                   ──────
 a git commit      ──►  1 snapshot + context  (Python)        ──►  context.json, diff.patch
                        2 three reviewers in parallel               <agent>-findings.json
                          ├ code review   (AI)                      testing-execution.json
                          ├ testing       (Python runs pytest + AI)  testing-behaviour-map.md
                          └ documentation (Python reads API + AI)    api-surface.md
                        3 validate every answer (Python + schemas)  <agent>-result.json
                        4 decide with fixed rules (Python)          report.json
                        5 write the report (Python)                 report.md  + terminal summary
```

---

## 1. Input — what the developer does

The developer raises the ticket title limit but forgets the tests and the API docs.

`sample-project/app/service.py`, line 15:

```python
MAX_TITLE_LENGTH = 100   # before
MAX_TITLE_LENGTH = 200   # after
```

```bash
git checkout -b judge-test
# edit the line above
git commit -am "Raise title limit"
python prism.py check --base main
```

That is the only input: **a committed change** and **which branch to compare it with**.
The AI key comes from `DEEPSEEK_API_KEY` (or `python prism.py setup`).

---

## 2. Process

### Step 1 — Snapshot and context (Python, no AI)

PRism-AI takes a frozen copy of the candidate commit (`git archive`) into
`runs/<runId>/snapshot/`, so every reviewer sees exactly the same code, and writes the diff:

**`diff.patch`**
```diff
--- a/sample-project/app/service.py
+++ b/sample-project/app/service.py
@@ -12,7 +12,7 @@ from typing import Optional
 from .store import store

 ALLOWED_STATUSES = {"OPEN", "IN_PROGRESS", "CLOSED"}
-MAX_TITLE_LENGTH = 100
+MAX_TITLE_LENGTH = 200
```

It then works out **which files matter** by following Python imports. The test file imports
`app.main`, which imports `app.service`, so the test file is picked even though it did not change:

**`context.json`** (shortened)
```json
{
  "runId": "run-20260926-084322-ccb07af",
  "baseRef": "main",
  "candidateRef": "HEAD",
  "intent": "Raise title limit",
  "changedFiles": [
    { "path": "sample-project/app/service.py", "status": "M", "additions": 1, "deletions": 1 }
  ],
  "relevantSource": ["sample-project/app/__init__.py", "sample-project/app/main.py",
                     "sample-project/app/service.py", "sample-project/app/store.py"],
  "relevantTests":  ["sample-project/tests/test_tickets.py"],
  "relevantDocs":   ["sample-project/README.md", "sample-project/docs/api.md"]
}
```

### Step 2 — Three reviewers run in parallel

Each reviewer gets **its own instructions** (`agents/<name>/instructions.md`) plus **only the
files it is allowed to read**, with line numbers. It must answer with JSON findings.

| Reviewer | Reads | Deterministic helper run first |
|---|---|---|
| code review | diff, changed + related source | — |
| testing | diff, source, tests, test results | `agents/testing/runner.py` runs pytest |
| documentation | diff, docs, API surface | `agents/documentation/extract_api.py` reads OpenAPI |

**Testing helper — the real test run** (`testing-execution.json`). The pass/fail counts come
from pytest's JUnit XML, never from the AI:

```json
{
  "command": "python -m pytest -q --junitxml=../../logs/junit.xml",
  "exitCode": 1,
  "collected": 21, "passed": 20, "failed": 1, "skipped": 0, "errors": 0,
  "durationMs": 2018
}
```

`logs/pytest.log`:
```
....F................                                                    [100%]
____________ TestCreateTicket.test_create_title_101_chars_rejected ____________
    def test_create_title_101_chars_rejected(self, client):
        resp = client.post("/tickets", json={"title": "A" * 101})
>       assert resp.status_code == 400
E       assert 201 == 400
tests\test_tickets.py:73: AssertionError
1 failed, 20 passed in 0.54s
```

**Documentation helper — the real API surface** (`api-surface.md`, shortened):
```
## POST /tickets
**Request body (application/json)**
  - `title` (any, optional)
  - `description` (any, optional)
**Responses**
  - `201` Successful Response
```

**Testing reviewer — AI answer** (`testing-findings.json`, one of three findings):
```json
{
  "id": "TEST-001",
  "key": "TEST_FAILURE|sample-project/tests/test_tickets.py|test_create_title_101_chars_rejected|regression",
  "severity": "HIGH",
  "category": "TEST_FAILURE",
  "title": "Previously passing test test_create_title_101_chars_rejected now fails after the title limit change",
  "file": "sample-project/tests/test_tickets.py",
  "line": 71,
  "description": "The diff raises MAX_TITLE_LENGTH from 100 to 200, so a 101-character title is now accepted. The existing test still asserts that a 101-character title is rejected with 400 ...",
  "evidence": ">   assert resp.status_code == 400\nE   assert 201 == 400",
  "evidenceType": "test-execution",
  "recommendation": "Update the test to the new 200-character limit ... assert 400 and error == \"invalid_title\" for a title of 201 characters instead of 101."
}
```

The testing reviewer also maps each changed behaviour to a test (`testing-behaviour-map.md`):

| Behaviour | Test(s) | Covered? |
|---|---|---|
| create_ticket accepts titles up to the new limit of 200 characters | none | no |
| create_ticket rejects titles longer than the new limit of 200 characters | test_create_title_101_chars_rejected (asserts old 100 limit; now failing) | no |
| create_ticket applies the length limit to the trimmed title | none | no |
| create_ticket still rejects missing title | test_create_missing_title_rejected | yes |
| create_ticket still trims the title | test_create_title_is_trimmed | yes |

**Documentation reviewer — AI answer** (`documentation-findings.json`, one of four findings):
```json
{
  "id": "DOC-001",
  "key": "DOC_MISMATCH|sample-project/docs/api.md|-|title_max_length",
  "severity": "MEDIUM",
  "category": "DOC_MISMATCH",
  "title": "Doc says title max length is 100; code now allows 200",
  "file": "sample-project/docs/api.md",
  "line": 15,
  "evidence": "Code: MAX_TITLE_LENGTH = 200  |  Doc: | `title` | string | 1–100 characters after trimming whitespace |",
  "evidenceType": "doc-comparison",
  "recommendation": "Update the Ticket table to say 1–200 characters after trimming whitespace."
}
```

**Code-review reviewer — AI answer** (`code-review-findings.json`): no findings. It judged the
one-line change itself correct and recorded that in `limitations`. The other two reviewers
caught the consequences — which is why three specialists are used instead of one.

### Step 3 — Validate every answer (Python)

`orchestrator/agent_io.py` checks each AI answer against `schemas/agent-result.schema.json`:
required fields, allowed categories, the `<CATEGORY>|<file>|<symbol>|<subject>` key format,
and that every file path is inside `sample-project/`. An invalid answer gets **one** repair
attempt; if it is still invalid, that reviewer is marked `error` — never silently ignored.
It also stamps the real start/finish time of each reviewer.

### Step 4 — Decide (Python, fixed rules, no AI)

`orchestrator/aggregate.py` applies `config/policy.json`:

| Rule | This run |
|---|---|
| Any test failure → `VERIFICATION_FAILED` | pytest exited 1 → **yes** |
| `HIGH`+ findings block | TEST-001 |
| `MEDIUM`+ missing tests / doc gaps block | TEST-002, DOC-001, DOC-003 |
| `LOW` findings never block | TEST-003 (shown as a suggestion) |

**`report.json`** (shortened):
```json
{
  "readiness": {
    "state": "VERIFICATION_FAILED",
    "reasons": [
      { "code": "TESTS_FAILED",      "detail": "pytest exited 1" },
      { "code": "BLOCKING_FINDINGS", "detail": "TEST-001, DOC-001, DOC-003, TEST-002" }
    ]
  },
  "timeline": { "wallClockMs": 7005, "sumOfAgentMs": 13283, "overlapObserved": true }
}
```

`overlapObserved: true` proves the reviewers really ran at the same time (13.3 s of work
finished in 7.0 s).

---

## 3. Output

### Terminal

```
PRism-AI  HEAD (ccb07af) vs main  [DeepSeek: deepseek-chat]
Run run-20260926-084322-ccb07af: 1 changed file(s). Running code review, testing and documentation in parallel...

  code-review    completed  1s
  testing        completed  7s
  documentation  completed  5s
  tests: 20 passed, 1 failed, exit code 1

RESULT: VERIFICATION FAILED
  - TESTS_FAILED: pytest exited 1
  [HIGH] Previously passing test test_create_title_101_chars_rejected now fails after the title limit change
      sample-project/tests/test_tickets.py:71
  [MEDIUM] Doc says title max length is 100; code now allows 200
      sample-project/docs/api.md:15
  [MEDIUM] Error table says invalid_title triggers above 100 chars; code triggers above 200
      sample-project/docs/api.md:59
  [MEDIUM] No test for the new 200-character title boundary
      sample-project/app/service.py:15
  (+1 non-blocking suggestion(s) in the report)

This is a scoped pre-review check, not approval to merge.

Full report: runs\run-20260926-084322-ccb07af\report.md
```

Exit code `2` (so CI scripts can react to it).

### Report — `runs/<runId>/report.md` (shortened)

```markdown
# ❌ VERIFICATION FAILED

- **Tests failed:** pytest exited 1
- **Blocking findings:** TEST-001, DOC-001, DOC-003, TEST-002

## Agents
| Agent         | Status       | Start     | End       | Duration |
|---------------|--------------|-----------|-----------|----------|
| code-review   | ✅ completed | 08:43:24Z | 08:43:25Z | 1s |
| testing       | ✅ completed | 08:43:24Z | 08:43:31Z | 7s |
| documentation | ✅ completed | 08:43:24Z | 08:43:28Z | 4s |

Wall clock: **7s** (overlapping)  · Sum of agent time: 13s

## Test execution
**Results:** collected 21 · passed 20 · failed 1 · skipped 0 · errors 0

## Blocking findings
### testing
**[HIGH] TEST-001 — Previously passing test ... now fails after the title limit change**
`sample-project/tests/test_tickets.py:71`
> **Recommendation:** Update the test to the new 200-character limit ...

### documentation
**[MEDIUM] DOC-001 — Doc says title max length is 100; code now allows 200**
`sample-project/docs/api.md:15`
> **Recommendation:** Update the Ticket table to say 1–200 characters after trimming whitespace.
...
```

---

## 4. After the fix — re-check

The developer restores the limit and re-runs with `--previous last`:

```bash
git commit -am "Restore title limit"
python prism.py check --base main --previous last
```

```
  code-review    completed  1s
  testing        completed  3s
  documentation  completed  2s
  tests: 21 passed, 0 failed, exit code 0

RESULT: READY FOR HUMAN REVIEW

Since run-20260926-084126-d796857: 6 resolved, 0 still open, 0 new, 0 unverified

This is a scoped pre-review check, not approval to merge.
```

`runs/<runId>/delta.md` lists each finding as **resolved**, **still open**, **new** or
**unverified**, matched by its key — so progress is measured, not claimed.

---

## Summary

| | |
|---|---|
| **Input** | One committed change (1 line) + the branch to compare with |
| **Work done** | Snapshot → 3 parallel reviewers → real pytest run → schema validation → rule-based decision |
| **Time** | ~7 seconds |
| **Found** | 1 broken test (HIGH), 1 missing boundary test, 4 outdated doc lines, 1 extra test suggestion |
| **Output** | Terminal verdict + `report.md` / `report.json` with file:line evidence and fixes |
| **After fix** | `READY FOR HUMAN REVIEW`, 6 findings resolved |
