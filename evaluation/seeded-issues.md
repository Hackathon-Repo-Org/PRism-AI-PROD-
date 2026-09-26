# Seeded Issues — Ground Truth

**Branch:** `fixture/ticket-priority`  
**Tags:** `demo-bad` (planted), `demo-fixed` (resolved)  
**Do not read at runtime.** This file is for human review and Member 3's scorer only.

---

## SEED-01 — Null-deref on priority before validation (Code Review)

**File:** `sample-project/app/service.py`  
**Symbol:** `create_ticket`  
**Accepted categories:** `NULL_HANDLING`, `ERROR_HANDLING`

**What was planted:**  
`priority = priority.upper()` is called on line ~47 before any null/missing check.
When a caller omits `priority` (or sends `null`), `priority` is `None` and `.upper()` raises
`AttributeError`, which FastAPI catches as an unhandled exception and returns HTTP 500.

**Reproduction:**
```bash
curl -s -X POST http://localhost:8000/tickets \
  -H "Content-Type: application/json" \
  -d '{"title": "Test ticket"}'
# Expected: 400 { "error": "invalid_priority", "message": "priority is required" }
# Actual:   500 Internal Server Error
```

**What a correct finding must say:**  
Identifies `priority.upper()` being called before a null check; notes the concrete input
(missing/null priority) and the wrong result (AttributeError → HTTP 500 instead of 400).

---

## SEED-02 — No allowed-value check on priority (Code Review)

**File:** `sample-project/app/service.py`  
**Symbol:** `create_ticket`  
**Accepted categories:** `INPUT_VALIDATION`, `LOGIC_ERROR`

**What was planted:**  
After calling `.upper()`, there is no check that the result is one of `{"LOW", "MEDIUM", "HIGH"}`.
Any string is accepted and stored verbatim (uppercased).

**Reproduction:**
```bash
curl -s -X POST http://localhost:8000/tickets \
  -H "Content-Type: application/json" \
  -d '{"title": "Test", "priority": "URGENT"}'
# Expected: 400 { "error": "invalid_priority", "message": "..." }
# Actual:   201 { "priority": "URGENT", ... }
```

**What a correct finding must say:**  
Identifies the absence of an allowed-value check after normalisation; notes the concrete input
(`"URGENT"` or any non-`LOW/MEDIUM/HIGH` string) and the wrong result (201 accepted).

---

## SEED-03 — No test for missing/null priority (Testing)

**File:** `sample-project/app/service.py` (source file under test)  
**Symbol:** `create_ticket`  
**Accepted categories:** `MISSING_TEST`  
**Subject hints:** `null`, `missing`, `none`

**What was planted:**  
No test in `tests/test_tickets.py` asserts that omitting `priority` (or sending `null`)
produces `400 invalid_priority`. The only priority test is the happy-path case.

**What a correct finding must say:**  
Notes that no test covers the missing/null-priority path; states the expected behaviour
(400, `invalid_priority`); references `create_ticket` as the function under test.

---

## SEED-04 — No test for unsupported priority (Testing)

**File:** `sample-project/app/service.py` (source file under test)  
**Symbol:** `create_ticket`  
**Accepted categories:** `MISSING_TEST`  
**Subject hints:** `unsupported`, `invalid`, `unknown`

**What was planted:**  
No test asserts that an unsupported priority value (e.g. `"URGENT"`) produces
`400 invalid_priority`. This path is silently accepted.

**What a correct finding must say:**  
Notes that no test covers the unsupported-priority path; states the expected behaviour
(400, `invalid_priority`); references `create_ticket` as the function under test.

---

## SEED-05 — docs/api.md not updated with priority field (Documentation)

**File:** `sample-project/docs/api.md`  
**Accepted categories:** `DOC_MISSING`, `DOC_MISMATCH`, `DOC_EXAMPLE_INVALID`  
**Subject hints:** `priority`

**What was planted:**  
`docs/api.md` was not updated when the `priority` field was added.
The document does not mention:
- `priority` as a request field on `POST /tickets` (required, allowed values, case rules)
- `priority` in the response schema
- the `invalid_priority` error code

**Reproduction:**  
```bash
grep -i priority sample-project/docs/api.md
# (no output)
```

**What a correct finding must say:**  
Identifies that `priority` is present in the code and response but absent from `docs/api.md`;
cites the missing section (POST /tickets request fields and/or error codes table); recommends
adding the field description with allowed values, case-insensitivity note, and `invalid_priority`
error code.

---

## SEED-06 — Off-by-one on title length limit (heldout-variation)

**File:** `sample-project/app/service.py`  
**Symbol:** `create_ticket`  
**Accepted categories:** `LOGIC_ERROR`, `INPUT_VALIDATION`  
**Subject hints:** `title`, `max_title_length`

**What was planted:**  
The title length guard was changed from `> MAX_TITLE_LENGTH` to `>= MAX_TITLE_LENGTH`.
The API contract (`docs/api.md`) says titles of up to **100 characters** are valid (`1–100 chars
after trimming`), but the code now rejects titles of exactly 100 characters with `400 invalid_title`.

The error message was also changed to say "must be less than 100 characters" instead of
"must be at most 100 characters", further contradicting the documented limit.

**Reproduction:**
```bash
python -c "print('a'*100)" | xargs -I{} curl -s -X POST http://localhost:8000/tickets \
  -H "Content-Type: application/json" \
  -d '{"title": "{}"}'
# Expected: 201 Created
# Actual:   400 { "error": "invalid_title", "message": "must be less than 100 characters..." }
```

**What a correct finding must say:**  
Identifies the `>=` operator on line ~38 of `service.py`; notes that the documented maximum
is 100 characters (inclusive) and a 100-character title should be accepted; recommends
changing back to `>` and restoring the error message to "at most 100 characters".
