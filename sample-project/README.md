# Issue Tracker API

A small FastAPI-based issue tracker used as the sample application for the PRism-AI demo.

---

## What it is

A REST API for managing support tickets. Tickets have a title, an optional description, a status
(`OPEN` → `IN_PROGRESS` → `CLOSED`), and a creation timestamp. All data is stored in memory;
it resets when the process restarts.

Full endpoint and field documentation: [`docs/api.md`](docs/api.md).

---

## Prerequisites

- Python 3.11+

---

## Install dependencies

```bash
cd sample-project
pip install -r requirements.txt
```

---

## Run the server

```bash
cd sample-project
uvicorn app.main:app --reload
```

The API is then available at `http://localhost:8000`.  
Interactive docs (Swagger UI): `http://localhost:8000/docs`.

---

## Run the tests

```bash
cd sample-project
python -m pytest -q
```

All tests should pass on `main`. Tests live in `sample-project/tests/` (owned by Member 3).

---

## Project layout

```
sample-project/
├── app/
│   ├── __init__.py
│   ├── main.py        # FastAPI app, routes, error handler
│   ├── models.py      # Pydantic request/response models
│   ├── service.py     # TicketService — all business rules and validation
│   └── store.py       # In-memory store with reset() for tests
├── docs/
│   └── api.md         # API contract (source of truth for intended behaviour)
├── tests/             # pytest suite (owned by Member 3)
├── README.md          # this file
└── requirements.txt   # fastapi, uvicorn, httpx, pytest
```

---

## Quick smoke test with curl

```bash
# Create a ticket
curl -s -X POST http://localhost:8000/tickets \
  -H "Content-Type: application/json" \
  -d '{"title": "Fix login bug"}' | python -m json.tool

# List tickets
curl -s http://localhost:8000/tickets | python -m json.tool

# Get ticket 1
curl -s http://localhost:8000/tickets/1 | python -m json.tool

# Update status
curl -s -X PATCH http://localhost:8000/tickets/1/status \
  -H "Content-Type: application/json" \
  -d '{"status": "IN_PROGRESS"}' | python -m json.tool
```
