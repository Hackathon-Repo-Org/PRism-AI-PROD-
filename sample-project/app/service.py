"""Business rules and validation for the Issue Tracker.

All validation is manual here so that defects introduced later in the
fixture branch sit in a realistic, inspectable location.
"""

from __future__ import annotations

import datetime
from typing import Optional

from .store import store

ALLOWED_STATUSES = {"OPEN", "IN_PROGRESS", "CLOSED"}
MAX_TITLE_LENGTH = 100


class ValidationError(Exception):
    """Raised when a request fails business-rule validation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class TicketService:
    def create_ticket(
        self,
        title: Optional[str],
        description: Optional[str],
    ) -> dict:
        if title is None:
            raise ValidationError("invalid_title", "title is required")
        stripped = title.strip()
        if not stripped:
            raise ValidationError("invalid_title", "title must not be blank")
        if len(stripped) > MAX_TITLE_LENGTH:
            raise ValidationError(
                "invalid_title",
                f"title must be at most {MAX_TITLE_LENGTH} characters after trimming",
            )
        now = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )
        ticket = {
            "title": stripped,
            "description": description,
            "status": "OPEN",
            "createdAt": now,
        }
        return store.add(ticket)

    def list_tickets(self, status: Optional[str]) -> list:
        if status is not None and status not in ALLOWED_STATUSES:
            raise ValidationError(
                "invalid_status",
                f"status must be one of {', '.join(sorted(ALLOWED_STATUSES))}",
            )
        return store.list_all(status)

    def get_ticket(self, ticket_id: int) -> dict:
        ticket = store.get(ticket_id)
        if ticket is None:
            raise ValidationError("ticket_not_found", f"ticket {ticket_id} not found")
        return ticket

    def update_status(self, ticket_id: int, status: Optional[str]) -> dict:
        if status is None or status not in ALLOWED_STATUSES:
            raise ValidationError(
                "invalid_status",
                f"status must be one of {', '.join(sorted(ALLOWED_STATUSES))}",
            )
        ticket = store.update_status(ticket_id, status)
        if ticket is None:
            raise ValidationError("ticket_not_found", f"ticket {ticket_id} not found")
        return ticket


service = TicketService()
