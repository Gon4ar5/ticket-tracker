from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Ticket
from app.schemas import TicketPriority, TicketStatus

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
DbSession = Annotated[Session, Depends(get_db)]
STATUSES = [item.value for item in TicketStatus]
PRIORITIES = [item.value for item in TicketPriority]


def get_ticket_or_404(db: Session, ticket_id: int) -> Ticket:
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


def normalized_title(value: str) -> str:
    title = value.strip()
    if not 3 <= len(title) <= 160:
        raise HTTPException(status_code=422, detail="Название должно содержать от 3 до 160 символов")
    return title


@router.get("/", response_class=HTMLResponse, name="board")
def board(request: Request, db: DbSession):
    tickets = db.scalars(select(Ticket).order_by(Ticket.updated_at.desc(), Ticket.id.desc())).all()
    grouped = {value: [] for value in STATUSES}
    for ticket in tickets:
        grouped.setdefault(ticket.status, []).append(ticket)
    return templates.TemplateResponse(
        request=request,
        name="board.html",
        context={"columns": STATUSES, "grouped": grouped, "ticket_count": len(tickets)},
    )


@router.get("/tickets/new", response_class=HTMLResponse, name="new_ticket")
def new_ticket(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="ticket_form.html",
        context={"ticket": None, "statuses": STATUSES, "priorities": PRIORITIES},
    )


@router.post("/tickets", name="create_ticket_form")
def create_ticket_form(
    db: DbSession,
    title: Annotated[str, Form(max_length=160)],
    description: Annotated[str, Form()] = "",
    acceptance_criteria: Annotated[str, Form()] = "",
    priority: Annotated[TicketPriority, Form()] = TicketPriority.MEDIUM,
    spec_ref: Annotated[str, Form(max_length=500)] = "",
    branch_name: Annotated[str, Form(max_length=200)] = "",
    pull_request_url: Annotated[str, Form(max_length=500)] = "",
):
    ticket = Ticket(
        title=normalized_title(title),
        description=description.strip(),
        acceptance_criteria=acceptance_criteria.strip(),
        priority=priority.value,
        spec_ref=spec_ref.strip(),
        branch_name=branch_name.strip(),
        pull_request_url=pull_request_url.strip(),
    )
    db.add(ticket)
    db.commit()
    return RedirectResponse(
        url=f"/tickets/{ticket.id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/tickets/{ticket_id}", response_class=HTMLResponse, name="ticket_detail")
def ticket_detail(request: Request, ticket_id: int, db: DbSession):
    ticket = get_ticket_or_404(db, ticket_id)
    return templates.TemplateResponse(
        request=request,
        name="ticket_detail.html",
        context={"ticket": ticket, "statuses": STATUSES},
    )


@router.get("/tickets/{ticket_id}/edit", response_class=HTMLResponse, name="edit_ticket")
def edit_ticket(request: Request, ticket_id: int, db: DbSession):
    ticket = get_ticket_or_404(db, ticket_id)
    return templates.TemplateResponse(
        request=request,
        name="ticket_form.html",
        context={"ticket": ticket, "statuses": STATUSES, "priorities": PRIORITIES},
    )


@router.post("/tickets/{ticket_id}/edit", name="update_ticket_form")
def update_ticket_form(
    ticket_id: int,
    db: DbSession,
    title: Annotated[str, Form(max_length=160)],
    description: Annotated[str, Form()] = "",
    acceptance_criteria: Annotated[str, Form()] = "",
    ticket_status: Annotated[TicketStatus, Form(alias="status")] = TicketStatus.BACKLOG,
    priority: Annotated[TicketPriority, Form()] = TicketPriority.MEDIUM,
    spec_ref: Annotated[str, Form(max_length=500)] = "",
    branch_name: Annotated[str, Form(max_length=200)] = "",
    pull_request_url: Annotated[str, Form(max_length=500)] = "",
):
    ticket = get_ticket_or_404(db, ticket_id)
    ticket.title = normalized_title(title)
    ticket.description = description.strip()
    ticket.acceptance_criteria = acceptance_criteria.strip()
    ticket.status = ticket_status.value
    ticket.priority = priority.value
    ticket.spec_ref = spec_ref.strip()
    ticket.branch_name = branch_name.strip()
    ticket.pull_request_url = pull_request_url.strip()
    db.commit()
    return RedirectResponse(
        url=f"/tickets/{ticket.id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/tickets/{ticket_id}/status", name="change_ticket_status")
def change_ticket_status(
    ticket_id: int,
    db: DbSession,
    ticket_status: Annotated[TicketStatus, Form(alias="status")],
):
    ticket = get_ticket_or_404(db, ticket_id)
    ticket.status = ticket_status.value
    db.commit()
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
