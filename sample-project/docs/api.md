# Issue Tracker API — Contract

Version: 1.0  
Base URL: `http://localhost:8000`

---

## Data model

### Ticket

| Field | Type | Description |
|---|---|---|
| `id` | integer | Auto-incremented, assigned by the server |
| `title` | string | 1–100 characters after trimming whitespace |
| `description` | string or null | Optional free text |
| `status` | string | One of `OPEN`, `IN_PROGRESS`, `CLOSED` |
| `createdAt` | string (ISO-8601 UTC) | Set by the server at creation time |

### Error response

All `400` and `404` responses use:

```json
{ "error": "<snake_case_code>", "message": "<human-readable text>" }
```

---

## Endpoints

### POST /tickets

Create a new ticket.

**Request body (JSON)**

| Field | Type | Required | Rules |
|---|---|---|---|
| `title` | string | yes | 1–100 chars after trimming; blank/missing → `400` |
| `description` | string | no | Any string; omit or `null` for none |

**Response `201 Created`**

```json
{
  "id": 1,
  "title": "Fix login bug",
  "description": "Users cannot log in after password reset",
  "status": "OPEN",
  "createdAt": "2026-09-26T10:15:00.000Z"
}
```

**Error codes**

| HTTP | `error` | Cause |
|---|---|---|
| 400 | `invalid_title` | `title` is missing, null, blank, or longer than 100 chars after trimming |

**Example**

```bash
curl -s -X POST http://localhost:8000/tickets \
  -H "Content-Type: application/json" \
  -d '{"title": "Fix login bug", "description": "Users cannot log in after password reset"}'
```

---

### GET /tickets

List all tickets, with an optional status filter.

**Query parameters**

| Parameter | Type | Required | Rules |
|---|---|---|---|
| `status` | string | no | Must be `OPEN`, `IN_PROGRESS`, or `CLOSED` if provided; other values → `400` |

**Response `200 OK`**

Array of ticket objects (same fields as POST response). Empty array when no tickets match.

```json
[
  {
    "id": 1,
    "title": "Fix login bug",
    "description": "Users cannot log in after password reset",
    "status": "OPEN",
    "createdAt": "2026-09-26T10:15:00.000Z"
  }
]
```

**Error codes**

| HTTP | `error` | Cause |
|---|---|---|
| 400 | `invalid_status` | `status` query parameter value is not one of the allowed values |

**Example**

```bash
curl -s "http://localhost:8000/tickets?status=OPEN"
```

---

### GET /tickets/{id}

Retrieve a single ticket by its id.

**Path parameters**

| Parameter | Type | Required |
|---|---|---|
| `id` | integer | yes |

**Response `200 OK`**

Single ticket object.

```json
{
  "id": 1,
  "title": "Fix login bug",
  "description": "Users cannot log in after password reset",
  "status": "OPEN",
  "createdAt": "2026-09-26T10:15:00.000Z"
}
```

**Error codes**

| HTTP | `error` | Cause |
|---|---|---|
| 404 | `ticket_not_found` | No ticket with the given id exists |

**Example**

```bash
curl -s http://localhost:8000/tickets/1
```

---

### PATCH /tickets/{id}/status

Update the status of an existing ticket.

**Path parameters**

| Parameter | Type | Required |
|---|---|---|
| `id` | integer | yes |

**Request body (JSON)**

| Field | Type | Required | Rules |
|---|---|---|---|
| `status` | string | yes | Must be `OPEN`, `IN_PROGRESS`, or `CLOSED`; other values → `400` |

**Response `200 OK`**

Updated ticket object.

```json
{
  "id": 1,
  "title": "Fix login bug",
  "description": "Users cannot log in after password reset",
  "status": "IN_PROGRESS",
  "createdAt": "2026-09-26T10:15:00.000Z"
}
```

**Error codes**

| HTTP | `error` | Cause |
|---|---|---|
| 400 | `invalid_status` | `status` value is not one of `OPEN`, `IN_PROGRESS`, `CLOSED` |
| 404 | `ticket_not_found` | No ticket with the given id exists |

**Example**

```bash
curl -s -X PATCH http://localhost:8000/tickets/1/status \
  -H "Content-Type: application/json" \
  -d '{"status": "IN_PROGRESS"}'
```

---

## Validation rules summary

| Rule | Detail |
|---|---|
| Title required | `title` must be present and non-null |
| Title non-blank | `title` after `.strip()` must have at least 1 character |
| Title max length | `title` after `.strip()` must be at most 100 characters |
| Status allowed values | `OPEN`, `IN_PROGRESS`, `CLOSED` — exact case, no other values accepted |

---

## Running the server

```bash
cd sample-project
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Running the tests

```bash
cd sample-project
python -m pytest -q
```
