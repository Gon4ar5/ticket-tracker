from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Ticket
from app.schemas import TicketCreate, TicketRead, TicketStatus, TicketUpdate

router = APIRouter(prefix="/api/tickets", tags=["tickets"])
DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=list[TicketRead])
def list_tickets(
    db: DbSession,
    ticket_status: Annotated[TicketStatus | None, Query(alias="status")] = None,
):
    statement = select(Ticket).order_by(Ticket.updated_at.desc(), Ticket.id.desc())
    if ticket_status:
        statement = statement.where(Ticket.status == ticket_status.value)
    return db.scalars(statement).all()


@router.post("", response_model=TicketRead, status_code=status.HTTP_201_CREATED)
def create_ticket(payload: TicketCreate, db: DbSession):
    ticket = Ticket(**payload.model_dump())
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


@router.get("/{ticket_id}", response_model=TicketRead)
def get_ticket(ticket_id: int, db: DbSession):
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


@router.patch("/{ticket_id}", response_model=TicketRead)
def update_ticket(ticket_id: int, payload: TicketUpdate, db: DbSession):
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        if isinstance(value, (TicketStatus,)):
            value = value.value
        elif hasattr(value, "value"):
            value = value.value
        setattr(ticket, field, value)

    db.commit()
    db.refresh(ticket)
    return ticket
