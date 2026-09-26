# ⚠️  ATTENTION REQUIRED

- **Blocking findings:** TEST-001, TEST-002

## Run identity

| Field | Value |
|---|---|
| Run ID | `run-20260926-071649-85e2b7e` |
| Base | `baseline-clean` (`76aedbfa2efa`) |
| Candidate | `demo-fixed` (`85e2b7e80e59`) |
| Generated | 2026-09-26T07:19:23.000Z |
| Policy | v1.0 |

## Agents

| Agent | Status | Start | End | Duration |
|---|---|---|---|---|
| code-review | ✅ completed | 07:17:42Z | 07:18:22Z | 39s |
| testing | ✅ completed | 07:17:45Z | 07:19:10Z | 1m 24s |
| documentation | ✅ completed | 07:17:46Z | 07:18:31Z | 44s |

Wall clock: **1m 28s** (overlapping)  · Sum of agent time: 2m 49s

```
code-review     |==================                      |
testing         | =======================================|
documentation   | =====================                  |
```

## Test execution

**Command:** `python -m pytest -q --junitxml=../../logs/junit.xml`  
**Exit code:** `0`  
**Results:** collected 26 · passed 26 · failed 0 · skipped 0 · errors 0
**Log:** `runs/run-20260926-071649-85e2b7e/logs/pytest.log`

## Blocking findings

### testing

**[MEDIUM] TEST-001 — No test asserts that surrounding whitespace in priority is trimmed**  
`sample-project/app/service.py:48`  

The diff normalises priority with `priority.strip().upper()`, so a value such as " high " should be accepted and stored as "HIGH". Existing tests only cover case normalisation ("high", "medium") and never send a whitespace-padded priority, so the trimming half of the normalisation is unasserted.

```
service.py:48 (added in diff): `normalised = priority.strip().upper()`; tests only post "high" (test_create_with_priority_is_normalised) and "medium" (test_create_lowercase_priority_is_normalised).
```

> **Recommendation:** Add `test_create_priority_is_trimmed` in sample-project/tests/test_tickets.py: `resp = client.post("/tickets", json={"title": "T", "priority": "  high  "})`; assert `resp.status_code == 201` and `resp.json()["priority"] == "HIGH"`.

**[MEDIUM] TEST-002 — No test asserts priority is returned when a ticket is retrieved or listed**  
`sample-project/app/models.py:25`  

The diff adds a required `priority: str` field to `TicketResponse`, which is the response model for GET /tickets, GET /tickets/{id} and PATCH /tickets/{id}/status, and stores `"priority": normalised` on the ticket. No test checks that the stored priority round-trips through retrieval endpoints; test_get_existing and test_list_returns_all assert only `title`.

```
models.py:25 (added): `priority: str`; service.py:61 (added): `"priority": normalised,`; test_get_existing asserts only `resp.json()["title"] == "Retrieve me"`.
```

> **Recommendation:** Add `test_get_returns_priority` in sample-project/tests/test_tickets.py: create with `client.post("/tickets", json={"title": "T", "priority": "low"})`, then `resp = client.get("/tickets/1")`; assert `resp.status_code == 200` and `resp.json()["priority"] == "LOW"`. Optionally assert the same field in `client.get("/tickets").json()[0]["priority"]`.

## Non-blocking findings

### documentation

**[MEDIUM] DOC-001 — README create-ticket curl example omits the now-required priority field**  
`sample-project/README.md:80`  

The README smoke-test example creates a ticket with only a title. priority is now required on POST /tickets, so this example returns 400 invalid_priority instead of creating a ticket. The later steps in the same smoke test (get ticket 1, update status) then fail with 404.

```
Code: sample-project/app/service.py:46-47 `if priority is None:` / `raise ValidationError("invalid_priority", "priority is required")`  |  Doc: sample-project/README.md:80 `-d '{"title": "Fix login bug"}' | python -m json.tool`
```

> **Recommendation:** Add a priority to the README example body, e.g. `-d '{"title": "Fix login bug", "priority": "HIGH"}'`.

**[LOW] DOC-002 — README ticket overview does not mention the new priority field**  
`sample-project/README.md:9`  

The README's 'What it is' section lists the ticket attributes (title, description, status, creation timestamp) but not priority, which is now a required input and is returned on every ticket.

```
Code: sample-project/app/models.py:25 `priority: str`  |  Doc: sample-project/README.md:9-10 `Tickets have a title, an optional description, a status (`OPEN` → `IN_PROGRESS` → `CLOSED`), and a creation timestamp.`
```

> **Recommendation:** Add priority (`LOW`, `MEDIUM`, `HIGH`, required) to the ticket overview sentence in the README.

**[LOW] DOC-003 — Validation rules summary table has no priority rules**  
`sample-project/docs/api.md:210`  

The 'Validation rules summary' section lists title and status rules but omits the new priority rules (required, allowed values LOW/MEDIUM/HIGH, case-insensitive with surrounding whitespace trimmed, normalised to upper case). The rules are described in the POST /tickets table, so the summary is incomplete rather than contradictory.

```
Code: sample-project/app/service.py:48-49 `normalised = priority.strip().upper()` / `if normalised not in ALLOWED_PRIORITIES:`  |  Doc: sample-project/docs/api.md:210 `| Status allowed values | `OPEN`, `IN_PROGRESS`, `CLOSED` — exact case, no other values accepted |` (last row; no priority row)
```

> **Recommendation:** Add 'Priority required' and 'Priority allowed values' rows: `LOW`, `MEDIUM`, `HIGH`, case-insensitive, whitespace trimmed, stored upper case.

### testing

**[LOW] TEST-003 — No test for empty or whitespace-only priority**  
`sample-project/app/service.py:49`  

An empty string or whitespace-only priority passes the `is None` check and is rejected only because `""` is not in ALLOWED_PRIORITIES after `.strip().upper()`. This edge case (implied by the documented 'missing/null/unsupported -> 400' contract) is not asserted by any test.

```
service.py:46-53: `if priority is None: ...` then `normalised = priority.strip().upper()` / `if normalised not in ALLOWED_PRIORITIES: raise ValidationError("invalid_priority", ...)`; no test posts `"priority": ""` or `"priority": "   "`.
```

> **Recommendation:** Add `test_create_empty_priority_rejected` in sample-project/tests/test_tickets.py: `resp = client.post("/tickets", json={"title": "T", "priority": ""})` (and a variant with `"   "`); assert `resp.status_code == 400` and `resp.json()["error"] == "invalid_priority"`.

## Limitations and scope

- Analysis is static source review of the snapshot only; no code was executed.
- Non-string JSON values for priority (e.g. an integer) are handled by Pydantic model coercion/validation before reaching service.create_ticket; the resulting status (400 vs 422) depends on the installed Pydantic version, which was not in the allowed input set.
- Making priority required on POST /tickets is a deliberate, documented contract change (docs/api.md marks it required); clients that previously sent title-only bodies now receive 400 invalid_priority. Not reported as a defect because it matches the updated contract.
- All existing tests passed, but passing tests do not prove that changed behaviours are asserted. See behaviour-map.md for gaps.
- No coverage tool was run. Line coverage figures are not reported.
- Analysis is limited to files listed in context.json. Files outside the scope path were not examined.
- Only files under sample-project/ were analysed.

## Next steps

- **TEST-001:** Add `test_create_priority_is_trimmed` in sample-project/tests/test_tickets.py: `resp = client.post("/tickets", json={"title": "T", "priority": "  high  "})`; assert `resp.status_code == 201` and `resp.json()["priority"] == "HIGH"`.
- **TEST-002:** Add `test_get_returns_priority` in sample-project/tests/test_tickets.py: create with `client.post("/tickets", json={"title": "T", "priority": "low"})`, then `resp = client.get("/tickets/1")`; assert `resp.status_code == 200` and `resp.json()["priority"] == "LOW"`. Optionally assert the same field in `client.get("/tickets").json()[0]["priority"]`.

---

_This is a scoped pre-review check, not approval to merge._
