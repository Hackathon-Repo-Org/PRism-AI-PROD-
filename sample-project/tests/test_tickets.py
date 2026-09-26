"""Baseline tests for the Issue Tracker API (sample-project).

Covers every endpoint documented in docs/api.md:
  POST /tickets       - happy path, title validation (length + whitespace)
  GET  /tickets       - empty list, list all, ?status= filter, invalid ?status=
  GET  /tickets/{id}  - found, not found
  PATCH /tickets/{id}/status - all three valid values, not found, unknown status

Contract (from docs/api.md and app/service.py):
  - Status values: OPEN, IN_PROGRESS, CLOSED
  - Error body: {"error": "<snake_case_code>", "message": "..."}
  - Error codes: invalid_title, invalid_status, ticket_not_found
  - 400 for validation errors, 404 for missing tickets
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import store


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_store():
    """Reset in-memory store before every test."""
    store.reset()
    yield
    store.reset()


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# POST /tickets
# ---------------------------------------------------------------------------

class TestCreateTicket:
    def test_create_happy_path(self, client):
        resp = client.post("/tickets", json={"title": "Fix the bug"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["id"] == 1
        assert body["title"] == "Fix the bug"
        assert body["description"] is None
        assert body["status"] == "OPEN"
        assert "createdAt" in body

    def test_create_with_description(self, client):
        resp = client.post("/tickets", json={"title": "Bug", "description": "Details here"})
        assert resp.status_code == 201
        assert resp.json()["description"] == "Details here"

    def test_create_title_one_char(self, client):
        resp = client.post("/tickets", json={"title": "X"})
        assert resp.status_code == 201
        assert resp.json()["title"] == "X"

    def test_create_title_100_chars(self, client):
        title = "A" * 100
        resp = client.post("/tickets", json={"title": title})
        assert resp.status_code == 201
        assert len(resp.json()["title"]) == 100

    def test_create_title_101_chars_rejected(self, client):
        resp = client.post("/tickets", json={"title": "A" * 101})
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_title"

    def test_create_empty_title_rejected(self, client):
        resp = client.post("/tickets", json={"title": ""})
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_title"

    def test_create_whitespace_only_title_rejected(self, client):
        resp = client.post("/tickets", json={"title": "   "})
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_title"

    def test_create_missing_title_rejected(self, client):
        resp = client.post("/tickets", json={"description": "no title"})
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_title"

    def test_create_title_is_trimmed(self, client):
        resp = client.post("/tickets", json={"title": "  Hello  "})
        assert resp.status_code == 201
        assert resp.json()["title"] == "Hello"

    def test_create_ids_increment(self, client):
        r1 = client.post("/tickets", json={"title": "First"})
        r2 = client.post("/tickets", json={"title": "Second"})
        assert r1.json()["id"] == 1
        assert r2.json()["id"] == 2


# ---------------------------------------------------------------------------
# GET /tickets
# ---------------------------------------------------------------------------

class TestListTickets:
    def test_list_empty(self, client):
        resp = client.get("/tickets")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_returns_all(self, client):
        client.post("/tickets", json={"title": "Alpha"})
        client.post("/tickets", json={"title": "Beta"})
        resp = client.get("/tickets")
        assert resp.status_code == 200
        titles = [t["title"] for t in resp.json()]
        assert set(titles) == {"Alpha", "Beta"}

    def test_list_status_filter_open(self, client):
        client.post("/tickets", json={"title": "A"})
        client.post("/tickets", json={"title": "B"})
        client.patch("/tickets/1/status", json={"status": "CLOSED"})
        resp = client.get("/tickets?status=OPEN")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["title"] == "B"

    def test_list_invalid_status_rejected(self, client):
        resp = client.get("/tickets?status=BAD")
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_status"


# ---------------------------------------------------------------------------
# GET /tickets/{id}
# ---------------------------------------------------------------------------

class TestGetTicket:
    def test_get_existing(self, client):
        client.post("/tickets", json={"title": "Retrieve me"})
        resp = client.get("/tickets/1")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Retrieve me"

    def test_get_unknown_returns_404(self, client):
        resp = client.get("/tickets/999")
        assert resp.status_code == 404
        assert resp.json()["error"] == "ticket_not_found"


# ---------------------------------------------------------------------------
# PATCH /tickets/{id}/status
# ---------------------------------------------------------------------------

class TestUpdateStatus:
    def _create(self, client, title="Ticket"):
        return client.post("/tickets", json={"title": title}).json()["id"]

    def test_update_to_in_progress(self, client):
        tid = self._create(client)
        resp = client.patch(f"/tickets/{tid}/status", json={"status": "IN_PROGRESS"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "IN_PROGRESS"

    def test_update_to_closed(self, client):
        tid = self._create(client)
        resp = client.patch(f"/tickets/{tid}/status", json={"status": "CLOSED"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "CLOSED"

    def test_update_back_to_open(self, client):
        tid = self._create(client)
        client.patch(f"/tickets/{tid}/status", json={"status": "CLOSED"})
        resp = client.patch(f"/tickets/{tid}/status", json={"status": "OPEN"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "OPEN"

    def test_update_unknown_ticket_returns_404(self, client):
        resp = client.patch("/tickets/999/status", json={"status": "CLOSED"})
        assert resp.status_code == 404
        assert resp.json()["error"] == "ticket_not_found"

    def test_update_unknown_status_rejected(self, client):
        tid = self._create(client)
        resp = client.patch(f"/tickets/{tid}/status", json={"status": "RESOLVED"})
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_status"
