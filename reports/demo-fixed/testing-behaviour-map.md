# Testing behaviour map — run-20260926-071649-85e2b7e

Intent: "Validate ticket priority; add tests and docs"

| Behaviour | Test(s) | Covered? |
|-----------|---------|----------|
| `POST /tickets` forwards `body.priority` to `service.create_ticket` (main.py:30) and a valid priority yields 201 | test_create_with_priority_is_normalised, test_create_lowercase_priority_is_normalised | yes |
| `CreateTicketRequest.priority` optional field; missing `priority` → 400 `invalid_priority` (service.py:46-47) | test_create_missing_priority_rejected | yes |
| `priority: null` → 400 `invalid_priority` (service.py:46-47) | test_create_null_priority_rejected | yes |
| Unsupported priority value (not LOW/MEDIUM/HIGH) → 400 `invalid_priority` (service.py:49-53) | test_create_unsupported_priority_rejected | yes |
| Priority is case-insensitive and stored upper-case (`.upper()`, service.py:48,61) | test_create_with_priority_is_normalised ("high" → "HIGH"), test_create_lowercase_priority_is_normalised ("medium" → "MEDIUM") | yes |
| Upper-case `LOW` accepted (201) | test_create_happy_path and other creation tests (status 201 asserted; `priority` value in body not asserted) | yes (acceptance only) |
| Priority is whitespace-trimmed before validation (`.strip()`, service.py:48), e.g. `" high "` → 201 with `"HIGH"` | none | no |
| Empty / whitespace-only priority (`""`, `"   "`) → 400 `invalid_priority` (edge case of service.py:48-53) | none | no |
| `TicketResponse.priority` (models.py:25) is returned by `GET /tickets/{id}` and `GET /tickets` (persisted value round-trips) | none (test_get_existing and test_list_returns_all only assert `title`) | no |
