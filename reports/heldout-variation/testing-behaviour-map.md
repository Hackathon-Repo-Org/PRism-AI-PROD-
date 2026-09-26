# Testing behaviour map — run-20260926-071652-fff7077

Intent: "Refactor title length validation". Changed function: `TicketService.create_ticket`
(`sample-project/app/service.py`, reached via `POST /tickets`). The diff also deletes
`test_create_title_100_chars` from `sample-project/tests/test_tickets.py`.

| Behaviour | Test(s) | Covered? |
|-----------|---------|----------|
| Upper-bound comparison changed from `len(stripped) > MAX_TITLE_LENGTH` to `>=` (service.py:38): outcome for a title of exactly 100 characters after trimming | none (`test_create_title_100_chars`, which asserted 201 for a 100-char title, was deleted by this diff) | no |
| Title of 101 characters is rejected with 400 / `invalid_title` | `TestCreateTicket.test_create_title_101_chars_rejected` | yes |
| Error message for over-length title changed to "title must be less than 100 characters after trimming" (service.py:41) | none (`test_create_title_101_chars_rejected` asserts only status code and `error` code, not `message`) | no |
| Boundary just below the limit: title of 99 characters is accepted (201) | none | no |
| Length check applies after trimming: a padded title whose trimmed length is exactly at the limit (raw length > 100) | none (`test_create_title_is_trimmed` uses a short title only) | no |
| Minimum length: 1-character title accepted | `TestCreateTicket.test_create_title_one_char` | yes |
| Blank / whitespace-only title rejected with 400 / `invalid_title` (unchanged path) | `test_create_empty_title_rejected`, `test_create_whitespace_only_title_rejected` | yes |
| Missing title rejected with 400 / `invalid_title` (unchanged path) | `test_create_missing_title_rejected` | yes |
| Title is trimmed in the stored/returned ticket (unchanged path) | `test_create_title_is_trimmed` | yes |
