"""In-memory ticket store with reset support for tests."""

from __future__ import annotations

from typing import Dict, List, Optional


class TicketStore:
    def __init__(self) -> None:
        self._tickets: Dict[int, dict] = {}
        self._next_id: int = 1

    def add(self, ticket: dict) -> dict:
        ticket = dict(ticket, id=self._next_id)
        self._tickets[self._next_id] = ticket
        self._next_id += 1
        return ticket

    def get(self, ticket_id: int) -> Optional[dict]:
        return self._tickets.get(ticket_id)

    def list_all(self, status: Optional[str] = None) -> List[dict]:
        tickets = list(self._tickets.values())
        if status is not None:
            tickets = [t for t in tickets if t["status"] == status]
        return tickets

    def update_status(self, ticket_id: int, status: str) -> Optional[dict]:
        ticket = self._tickets.get(ticket_id)
        if ticket is None:
            return None
        ticket["status"] = status
        return ticket

    def reset(self) -> None:
        """Clear all tickets and reset the id counter. Used in tests only."""
        self._tickets.clear()
        self._next_id = 1


# Module-level singleton used by the FastAPI app.
store = TicketStore()
