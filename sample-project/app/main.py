"""FastAPI application — routes and error handling."""

from __future__ import annotations

from typing import List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .models import CreateTicketRequest, TicketResponse, UpdateStatusRequest
from .service import ValidationError, service

app = FastAPI(title="Issue Tracker API", version="1.0")


@app.exception_handler(ValidationError)
async def validation_error_handler(request: Request, exc: ValidationError):
    status_code = 404 if exc.code == "ticket_not_found" else 400
    return JSONResponse(
        status_code=status_code,
        content={"error": exc.code, "message": exc.message},
    )


@app.post("/tickets", response_model=TicketResponse, status_code=201)
async def create_ticket(body: CreateTicketRequest):
    ticket = service.create_ticket(
        title=body.title,
        description=body.description,
    )
    return ticket


@app.get("/tickets", response_model=List[TicketResponse])
async def list_tickets(status: Optional[str] = None):
    return service.list_tickets(status)


@app.get("/tickets/{ticket_id}", response_model=TicketResponse)
async def get_ticket(ticket_id: int):
    return service.get_ticket(ticket_id)


@app.patch("/tickets/{ticket_id}/status", response_model=TicketResponse)
async def update_status(ticket_id: int, body: UpdateStatusRequest):
    return service.update_status(ticket_id, body.status)
