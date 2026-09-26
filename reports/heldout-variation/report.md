# ⚠️  ATTENTION REQUIRED

- **Blocking findings:** CODE-001, DOC-001, TEST-001, TEST-002

## Run identity

| Field | Value |
|---|---|
| Run ID | `run-20260926-071652-fff7077` |
| Base | `baseline-clean` (`76aedbfa2efa`) |
| Candidate | `heldout-variation` (`fff70778f609`) |
| Generated | 2026-09-26T07:19:35.000Z |
| Policy | v1.0 |

## Agents

| Agent | Status | Start | End | Duration |
|---|---|---|---|---|
| code-review | ✅ completed | 07:17:45Z | 07:18:21Z | 35s |
| testing | ✅ completed | 07:17:46Z | 07:19:15Z | 1m 29s |
| documentation | ✅ completed | 07:17:44Z | 07:18:22Z | 38s |

Wall clock: **1m 30s** (overlapping)  · Sum of agent time: 2m 43s

```
code-review     |================                        |
testing         |========================================|
documentation   |================                        |
```

## Test execution

**Command:** `python -m pytest -q --junitxml=../../logs/junit.xml`  
**Exit code:** `0`  
**Results:** collected 20 · passed 20 · failed 0 · skipped 0 · errors 0
**Log:** `runs/run-20260926-071652-fff7077/logs/pytest.log`

## Blocking findings

### code-review

**[HIGH] CODE-001 — Off-by-one: titles of exactly MAX_TITLE_LENGTH (100) characters are now rejected**  
`sample-project/app/service.py:38`  

The intent is a refactor of title length validation, which should not change behaviour, but the comparison was changed from `>` to `>=`. With MAX_TITLE_LENGTH = 100, the concrete input POST /tickets {"title": "A" * 100} (or any title whose trimmed length is exactly 100) previously returned 201 and now raises ValidationError("invalid_title") and returns HTTP 400. The effective maximum drops from 100 to 99 characters, breaking the previous 'at most 100 characters after trimming' contract. The error message was also reworded to 'less than 100', so it silently documents the new, narrower rule.

```
        if len(stripped) >= MAX_TITLE_LENGTH:
            raise ValidationError(
                "invalid_title",
                f"title must be less than {MAX_TITLE_LENGTH} characters after trimming",
            )
```

> **Recommendation:** Restore the original check `if len(stripped) > MAX_TITLE_LENGTH:` and the message `title must be at most {MAX_TITLE_LENGTH} characters after trimming`.

### documentation

**[MEDIUM] DOC-001 — Docs say title may be up to 100 chars, but code now rejects titles of exactly 100 chars**  
`sample-project/docs/api.md:40`  

The change replaced `len(stripped) > MAX_TITLE_LENGTH` with `len(stripped) >= MAX_TITLE_LENGTH` (MAX_TITLE_LENGTH = 100), so POST /tickets now returns 400 invalid_title for a trimmed title of exactly 100 characters. The API contract still documents an inclusive 1-100 character range in four places (data model line 15, request rules line 40, error table line 59, validation summary line 202). A caller sending a 100-character title per the docs would get an unexpected 400. The stated intent is only a 'refactor', so the code change itself may be an unintended off-by-one rather than a deliberate rule change.

```
Code: sample-project/app/service.py:38 `if len(stripped) >= MAX_TITLE_LENGTH:` (and :41 `f"title must be less than {MAX_TITLE_LENGTH} characters after trimming"`)  |  Doc: sample-project/docs/api.md:40 `| title | string | yes | 1–100 chars after trimming; blank/missing → 400 |`; also api.md:15 `1–100 characters after trimming whitespace`, api.md:59 `longer than 100 chars after trimming`, api.md:202 `must be at most 100 characters`
```

> **Recommendation:** Confirm the intended behaviour first; if the code is correct, update the docs accordingly (api.md lines 15, 40, 59 and 202 to state a maximum of 99 characters / 'at least 100 chars' as the rejection cause). If the intent was a pure refactor, restore `>` in service.py so the documented 1-100 range holds.

### testing

**[MEDIUM] TEST-001 — No test asserts the outcome for a title of exactly MAX_TITLE_LENGTH (100) characters; the existing boundary test was deleted**  
`sample-project/app/service.py:38`  

The diff changes the upper-bound check from `>` to `>=`, which changes how a title of exactly 100 characters (after trimming) is handled. In the same diff, `test_create_title_100_chars` (which asserted a 201 for a 100-character title) was removed from tests/test_tickets.py. No remaining test exercises the exact boundary, so the behaviour at the limit is now unasserted. All 20 remaining tests pass, which is why the change is not detected by the suite.

```
service.py diff hunk @@ -35,10 +35,10 @@:
-        if len(stripped) > MAX_TITLE_LENGTH:
+        if len(stripped) >= MAX_TITLE_LENGTH:
test_tickets.py diff hunk @@ -62,12 +62,6 @@:
-    def test_create_title_100_chars(self, client):
-        title = "A" * 100
-        resp = client.post("/tickets", json={"title": title})
-        assert resp.status_code == 201
-        assert len(resp.json()["title"]) == 100
MAX_TITLE_LENGTH = 100 (service.py:15). pytest.log: "20 passed, 1 warning in 3.40s".
```

> **Recommendation:** Restore a boundary test in tests/test_tickets.py, e.g. `test_create_title_100_chars`: `resp = client.post("/tickets", json={"title": "A" * 100})`, then assert the documented contract for the maximum length - `assert resp.status_code == 201` and `assert len(resp.json()["title"]) == 100` if 100 characters is meant to be allowed (as the pre-change test and the previous "at most 100" message state), or `assert resp.status_code == 400` and `assert resp.json()["error"] == "invalid_title"` if the limit was intentionally tightened. Either way the boundary must be pinned by an assertion.

**[MEDIUM] TEST-002 — Changed over-length error message is not asserted by any test**  
`sample-project/app/service.py:41`  

The diff changes the error message returned for an over-length title. The only over-length test, `test_create_title_101_chars_rejected`, asserts the status code and the `error` code but not the `message` field, so the changed message text is unasserted.

```
service.py diff:
-                f"title must be at most {MAX_TITLE_LENGTH} characters after trimming",
+                f"title must be less than {MAX_TITLE_LENGTH} characters after trimming",
test_tickets.py:65-68:
    def test_create_title_101_chars_rejected(self, client):
        resp = client.post("/tickets", json={"title": "A" * 101})
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_title"
```

> **Recommendation:** Extend `test_create_title_101_chars_rejected` (or add `test_create_title_too_long_message`): `resp = client.post("/tickets", json={"title": "A" * 101})`; assert `resp.status_code == 400`, `resp.json()["error"] == "invalid_title"`, and assert `resp.json()["message"]` equals the documented message for the maximum title length.

## Non-blocking findings

### code-review

**[MEDIUM] CODE-002 — Boundary test for 100-character titles deleted, hiding the off-by-one behaviour change**  
`sample-project/tests/test_tickets.py`  

The diff removes test_create_title_100_chars, which asserted that POST /tickets with {"title": "A" * 100} returns 201. That test would fail against the new `>=` check in create_ticket, so deleting it masks the regression in CODE-001 instead of catching it. The remaining tests only cover 1 character and 101 characters, leaving the upper boundary (100 accepted) untested.

```
    def test_create_title_one_char(self, client):
        resp = client.post("/tickets", json={"title": "X"})
        assert resp.status_code == 201
        assert resp.json()["title"] == "X"

    def test_create_title_101_chars_rejected(self, client):
```

> **Recommendation:** Restore test_create_title_100_chars (100-character title returns 201 with a 100-character title) so the maximum-length boundary stays covered.

### testing

**[LOW] TEST-003 — No test asserts that a title one character below the limit (99) is accepted**  
`sample-project/app/service.py:38`  

Boundary testing around the changed comparison should pin both sides of the limit. The suite only tests 1 character (accepted) and 101 characters (rejected); nothing asserts the value just below MAX_TITLE_LENGTH.

```
service.py:38 `if len(stripped) >= MAX_TITLE_LENGTH:`; test_tickets.py contains only `test_create_title_one_char` ("X") and `test_create_title_101_chars_rejected` ("A" * 101) as length tests.
```

> **Recommendation:** Add `test_create_title_99_chars`: `resp = client.post("/tickets", json={"title": "A" * 99})`; assert `resp.status_code == 201` and `len(resp.json()["title"]) == 99`.

**[LOW] TEST-004 — No test asserts that the length limit is applied after trimming at the boundary**  
`sample-project/app/service.py:38`  

The length check operates on the trimmed title (`stripped`), and the error message states the limit applies "after trimming". No test sends a title whose raw length exceeds the limit but whose trimmed length is at or below it, so the trim-then-measure ordering is unasserted.

```
service.py:35 `stripped = title.strip()`; service.py:38 `if len(stripped) >= MAX_TITLE_LENGTH:`; test_tickets.py:85-88 `test_create_title_is_trimmed` uses only "  Hello  ".
```

> **Recommendation:** Add `test_create_padded_title_at_limit`: `resp = client.post("/tickets", json={"title": "  " + "A" * 99 + "  "})`; assert `resp.status_code == 201` and `resp.json()["title"] == "A" * 99`, demonstrating that surrounding whitespace does not count toward the limit.

## Limitations and scope

- sample-project/app/models.py (CreateTicketRequest) is imported by main.py but was not in changedFiles or relevantSource, so request-model-level validation was not inspected.
- Documented API contract (sample-project/docs/api.md) was not in the permitted read list; the previous 100-character limit was inferred from the removed code, error message, and deleted test in diff.patch.
- All existing tests passed, but passing tests do not prove that changed behaviours are asserted. See behaviour-map.md for gaps.
- No coverage tool was run. Line coverage figures are not reported.
- Analysis is limited to files listed in context.json. Files outside the scope path were not examined.
- sample-project/app/models.py is imported by app/main.py but is not listed in context.json relevantSource, so request-model validation (e.g. explicit null titles) was not examined.
- Only files under sample-project/ were analysed; api-surface.md (OpenAPI) does not reflect manual validation, so title length rules were checked against sample-project/app/service.py directly.
- Only files under sample-project/ were analysed.

## Next steps

- **CODE-001:** Restore the original check `if len(stripped) > MAX_TITLE_LENGTH:` and the message `title must be at most {MAX_TITLE_LENGTH} characters after trimming`.
- **DOC-001:** Confirm the intended behaviour first; if the code is correct, update the docs accordingly (api.md lines 15, 40, 59 and 202 to state a maximum of 99 characters / 'at least 100 chars' as the rejection cause). If the intent was a pure refactor, restore `>` in service.py so the documented 1-100 range holds.
- **TEST-001:** Restore a boundary test in tests/test_tickets.py, e.g. `test_create_title_100_chars`: `resp = client.post("/tickets", json={"title": "A" * 100})`, then assert the documented contract for the maximum length - `assert resp.status_code == 201` and `assert len(resp.json()["title"]) == 100` if 100 characters is meant to be allowed (as the pre-change test and the previous "at most 100" message state), or `assert resp.status_code == 400` and `assert resp.json()["error"] == "invalid_title"` if the limit was intentionally tightened. Either way the boundary must be pinned by an assertion.
- **TEST-002:** Extend `test_create_title_101_chars_rejected` (or add `test_create_title_too_long_message`): `resp = client.post("/tickets", json={"title": "A" * 101})`; assert `resp.status_code == 400`, `resp.json()["error"] == "invalid_title"`, and assert `resp.json()["message"]` equals the documented message for the maximum title length.

---

_This is a scoped pre-review check, not approval to merge._
