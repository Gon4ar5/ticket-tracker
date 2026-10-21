import os
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from datetime import datetime, timezone

from app.models import AgentProject, AgentRun, AgentRunEvent, Notification, Ticket
from app.schemas import TicketPriority, TicketStatus

router = APIRouter()


def cancel_active_runs(db: Session, ticket: Ticket, reason: str) -> None:
    """Release active runs when a user changes the task they were given."""
    runs = db.scalars(select(AgentRun).where(
        AgentRun.ticket_id == ticket.id,
        AgentRun.status.in_(["Queued", "Running"]),
    )).all()
    now = datetime.now(timezone.utc)
    for run in runs:
        run.status = "Canceled"
        run.active_key = None
        run.finished_at = now
        run.error = reason
        db.add(AgentRunEvent(run_id=run.id, event_type="status", message=reason))
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


@router.get("/notifications", response_class=HTMLResponse, name="notifications_page")
def notifications_page(request: Request, db: DbSession):
    notifications = db.scalars(select(Notification).order_by(Notification.id.desc())).all()
    return templates.TemplateResponse(
        request=request,
        name="notifications.html",
        context={"notifications": notifications},
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
def ticket_detail(request: Request, ticket_id: int, db: DbSession, error: str = ""):
    ticket = get_ticket_or_404(db, ticket_id)
    runs = db.scalars(select(AgentRun).where(AgentRun.ticket_id == ticket_id).order_by(AgentRun.id.desc())).all()
    notifications = db.scalars(select(Notification).where(Notification.ticket_id == ticket_id).order_by(Notification.id.desc())).all()
    projects = db.scalars(select(AgentProject).order_by(AgentProject.name)).all()
    now = datetime.now(timezone.utc)
    available_projects = [
        project for project in projects
        if (now - (project.last_seen_at.replace(tzinfo=timezone.utc) if project.last_seen_at.tzinfo is None else project.last_seen_at)).total_seconds() < 90
    ]
    run_events = {}
    for run in runs:
        run_events[run.id] = db.scalars(
            select(AgentRunEvent).where(AgentRunEvent.run_id == run.id).order_by(AgentRunEvent.id.desc()).limit(30)
        ).all()
    return templates.TemplateResponse(
        request=request,
        name="ticket_detail.html",
        context={"ticket": ticket, "statuses": STATUSES, "runs": runs, "run_events": run_events,
                 "notifications": notifications, "projects": available_projects, "error": error},
    )


@router.post("/tickets/{ticket_id}/agent-runs", name="start_agent_run")
def start_agent_run(
    request: Request,
    ticket_id: int,
    db: DbSession,
    project_id: Annotated[str, Form(max_length=80)],
):
    from fastapi.responses import RedirectResponse
    from sqlalchemy import select
    from app.models import AgentRun

    origin = request.headers.get("origin")
    if origin:
        allowed_origins = {
            item.strip().rstrip("/")
            for item in os.getenv(
                "TICKET_ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"
            ).split(",")
        }
        if origin.rstrip("/") not in allowed_origins:
            raise HTTPException(status_code=403, detail="Cross-origin agent launch is not allowed")

    ticket = get_ticket_or_404(db, ticket_id)
    problems = []
    if not ticket.description.strip():
        problems.append("добавь описание")
    if not ticket.acceptance_criteria.strip():
        problems.append("добавь критерии приёмки")
    if not ticket.spec_ref.strip():
        problems.append("укажи утверждённую спецификацию")
    project = db.get(AgentProject, project_id)
    if project is None:
        problems.append("выбери проект из списка worker")
    else:
        now = datetime.now(timezone.utc)
        seen = project.last_seen_at.replace(tzinfo=timezone.utc) if project.last_seen_at.tzinfo is None else project.last_seen_at
        if (now - seen).total_seconds() >= 90:
            problems.append("worker не отвечает; запусти worker и обнови страницу")
    if problems:
        return ticket_detail(request, ticket_id, db, "Перед запуском: " + "; ".join(problems) + ".")

    active_statuses = ["Queued", "Running"]
    existing = db.scalar(select(AgentRun).where(AgentRun.ticket_id == ticket_id, AgentRun.status.in_(active_statuses)))
    if existing:
        return RedirectResponse(f"/tickets/{ticket_id}#agent-runs", status_code=303)
    run = AgentRun(ticket_id=ticket.id, project_id=project_id, status="Queued", active_key="active")
    db.add(run)
    ticket.status = "In Progress"
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(AgentRun).where(
            AgentRun.ticket_id == ticket_id,
            AgentRun.status.in_(active_statuses),
        ))
        if existing:
            return RedirectResponse(f"/tickets/{ticket_id}#run-{existing.id}", status_code=303)
        raise
    db.refresh(run)
    db.add(AgentRunEvent(run_id=run.id, event_type="status", message="Запуск поставлен в очередь"))
    db.commit()
    return RedirectResponse(f"/tickets/{ticket_id}#run-{run.id}", status_code=303)


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
    next_title = normalized_title(title)
    next_description = description.strip()
    next_criteria = acceptance_criteria.strip()
    next_spec_ref = spec_ref.strip()
    requirements_changed = (ticket.title, ticket.description, ticket.acceptance_criteria, ticket.spec_ref) != (
        next_title, next_description, next_criteria, next_spec_ref
    )
    if requirements_changed:
        cancel_active_runs(db, ticket, "Запуск отменён: требования тикета изменились. Запусти агента повторно.")
    ticket.title = next_title
    ticket.description = next_description
    ticket.acceptance_criteria = next_criteria
    ticket.status = ticket_status.value
    ticket.priority = priority.value
    ticket.spec_ref = next_spec_ref
    ticket.branch_name = "" if requirements_changed else branch_name.strip()
    ticket.pull_request_url = "" if requirements_changed else pull_request_url.strip()
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
    if ticket_status.value == "Backlog" and ticket.status != "Backlog":
        cancel_active_runs(db, ticket, "Запуск отменён: тикет возвращён в бэклог.")
    ticket.status = ticket_status.value
    db.commit()
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
