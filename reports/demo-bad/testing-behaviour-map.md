# Testing behaviour map — run-20260926-071648-db642d6

Intent: Add priority to tickets (LOW, MEDIUM, HIGH)

| Behaviour | Test(s) | Covered? |
|-----------|---------|----------|
| `CreateTicketRequest` accepts new optional `priority` field and `POST /tickets` passes it to `service.create_ticket` (main.py L30, models.py L13) | test_create_with_priority_is_normalised | yes |
| `create_ticket` upper-cases the supplied priority (`priority = priority.upper()`, service.py L45); lowercase `"high"` returned as `"HIGH"` | test_create_with_priority_is_normalised | yes |
| Created ticket stores and returns `priority` (service.py L53; `TicketResponse.priority: str`, models.py L25) | test_create_with_priority_is_normalised (HIGH only) | yes |
| Each allowed value (LOW, MEDIUM, HIGH) accepted and echoed; MEDIUM never sent, LOW sent by many tests but its value never asserted | none | no |
| Priority omitted from request body (`priority` defaults to `None`, then `None.upper()` at service.py L45) | none | no |
| Priority explicitly `null` in request body (same `None.upper()` path) | none | no |
| Unsupported priority value (e.g. `"URGENT"`); `ALLOWED_PRIORITIES` added at service.py L15 but no rejection path is exercised | none | no |
| Empty-string priority `""` (upper-cases to `""` and is stored) | none | no |
| `GET /tickets` and `GET /tickets/{id}` responses now include `priority` (`TicketResponse.priority: str`) | none (test_get_existing / test_list_returns_all only assert title) | no |
