# Testing behaviour map — run-20260926-071651-a49b71b

Intent: Add title search filter (?q=) to GET /tickets with tests and docs

| Behaviour | Test(s) | Covered? |
|-----------|---------|----------|
| GET /tickets accepts new optional `q` query parameter and passes it to the service (main.py `list_tickets(status, q)`) | test_list_q_filter_match | yes |
| `q` filters tickets by substring match against `title` (store.py `q_lower in t["title"].lower()`) | test_list_q_filter_match | yes |
| `q` match is case-insensitive | test_list_q_filter_case_insensitive | yes |
| `q` with no matching title returns 200 and `[]` | test_list_q_filter_no_match | yes |
| Whitespace-only `q` is treated as no filter (strip -> "" -> None) | test_list_q_filter_blank_ignored | yes |
| `q` combined with `status` applies both filters | test_list_q_and_status_combined | yes |
| `q` absent (None) returns all tickets (unchanged behaviour on new path) | test_list_returns_all, test_list_empty | yes |
| Invalid `status` still rejected with 400 `invalid_status` when list signature changed | test_list_invalid_status_rejected | yes |
| Empty-string `q` (`?q=`) is treated as no filter (documented: "empty ... string is ignored") | none (only whitespace-only `q=   ` is asserted) | no |
| Non-blank `q` with surrounding whitespace is trimmed before matching (`q.strip()`) | none | no |
