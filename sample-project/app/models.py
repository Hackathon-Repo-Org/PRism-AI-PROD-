"""Pydantic models for request and response bodies."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class CreateTicketRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None


class UpdateStatusRequest(BaseModel):
    status: Optional[str] = None


class TicketResponse(BaseModel):
    id: int
    title: str
    description: Optional[str]
    status: str
    createdAt: str
