# Changes since previous run

- Previous run: `run-20260926-071648-db642d6` → **ATTENTION_REQUIRED**
- This run: `run-20260926-071649-85e2b7e` → **ATTENTION_REQUIRED**
- Resolved: **12** · Persistent: **3** · New: **3** · Unverified: **0**

## ✅ Resolved (12)

_The owning check ran on the new snapshot and no longer reports the issue._

| ID | Agent | Severity | Title | Location |
|---|---|---|---|---|
| CODE-001 | code-review | HIGH | priority.upper() called on None when priority is omitted, causing HTTP 500 | `sample-project/app/service.py:45` |
| CODE-002 | code-review | HIGH | priority is never checked against ALLOWED_PRIORITIES, so any string is accepted | `sample-project/app/service.py:45` |
| DOC-001 | documentation | MEDIUM | POST /tickets request table does not document the new `priority` field | `sample-project/docs/api.md:41` |
| DOC-002 | documentation | MEDIUM | Ticket data model does not document the new `priority` response field | `sample-project/docs/api.md:17` |
| DOC-003 | documentation | MEDIUM | Validation rules summary and error table have no priority rule | `sample-project/docs/api.md:203` |
| DOC-004 | documentation | MEDIUM | POST /tickets curl example omits `priority` and would fail against current code | `sample-project/docs/api.md:66` |
| TEST-001 | testing | MEDIUM | No test for creating a ticket without a priority | `sample-project/app/service.py:45` |
| TEST-002 | testing | MEDIUM | No test for creating a ticket with an explicit null priority | `sample-project/app/service.py:45` |
| TEST-003 | testing | MEDIUM | No test that an unsupported priority value is rejected | `sample-project/app/service.py:15` |
| DOC-006 | documentation | LOW | Ticket JSON response examples do not include the `priority` field | `sample-project/docs/api.md:50` |
| TEST-005 | testing | LOW | Allowed priority values LOW and MEDIUM are never asserted in responses | `sample-project/app/service.py:53` |
| TEST-006 | testing | LOW | GET endpoints never assert the new priority field | `sample-project/app/main.py:42` |

## 🔁 Persistent (3)

_The same issue is still reported._

| ID | Agent | Severity | Title | Location |
|---|---|---|---|---|
| DOC-001 (was DOC-005) | documentation | MEDIUM | README create-ticket curl example omits the now-required priority field | `sample-project/README.md:80` |
| DOC-002 (was DOC-007) | documentation | LOW | README ticket overview does not mention the new priority field | `sample-project/README.md:9` |
| TEST-003 (was TEST-004) | testing | LOW | No test for empty or whitespace-only priority | `sample-project/app/service.py:49` |

## 🆕 New (3)

_Reported for the first time in this run._

| ID | Agent | Severity | Title | Location |
|---|---|---|---|---|
| TEST-001 | testing | MEDIUM | No test asserts that surrounding whitespace in priority is trimmed | `sample-project/app/service.py:48` |
| TEST-002 | testing | MEDIUM | No test asserts priority is returned when a ticket is retrieved or listed | `sample-project/app/models.py:25` |
| DOC-003 | documentation | LOW | Validation rules summary table has no priority rules | `sample-project/docs/api.md:210` |

## ❔ Unverified (0)

_The owning check did not complete, so these cannot be called resolved._

None.

_Resolution is evidence from the scoped checks, not proof that no defects remain. Human review is still required._
