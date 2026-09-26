| Behaviour | Test(s) | Covered? |
|-----------|---------|----------|
| `create_ticket` rejects null `priority` field | none | no |
| `create_ticket` accepts and returns integer `priority` value | none | no |
| `create_ticket` returns 422 with `{"error": "Invalid priority"}` for out-of-range value | none | no |
| `create_ticket` happy path (title, description, status=open) | `test_create_happy_path` | yes |
| `create_ticket` empty title rejected | `test_create_empty_title_rejected` | yes |
| `GET /tickets/{id}` returns 404 for unknown id | `test_get_unknown_returns_404` | yes |
