import os
import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AgentProject, AgentRun, AgentRunEvent, Notification, Ticket

router = APIRouter(prefix="/api/agent", tags=["agent runner"])
Db = Annotated[Session, Depends(get_db)]
ACTIVE = ("Queued", "Running")


def require_worker_token(authorization: Annotated[str | None, Header()] = None) -> None:
    expected = os.getenv("TICKET_AGENT_TOKEN", "local-dev-only-change-me")
    if not authorization or not secrets.compare_digest(authorization, f"Bearer {expected}"):
        raise HTTPException(status_code=401, detail="Invalid worker token")


WorkerAuth = Annotated[None, Depends(require_worker_token)]


class ProjectRegistration(BaseModel):
    worker_id: str = Field(min_length=1, max_length=100)
    projects: list[dict]


class EventInput(BaseModel):
    event_type: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=12000)
    thread_id: str = Field(default="", max_length=200)


class FinishInput(BaseModel):
    status: str
    final_report: str = ""
    error: str = ""
    branch_name: str = ""
    worktree_path: str = ""
    thread_id: str = ""
    pull_request_url: str = Field(default="", max_length=500)


class NotificationInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=12000)


def event(db: Session, run_id: int, kind: str, message: str) -> None:
    db.add(AgentRunEvent(run_id=run_id, event_type=kind, message=message[:12000]))


@router.get("/projects")
def projects(db: Db):
    now = datetime.now(timezone.utc)
    all_projects = db.scalars(select(AgentProject).order_by(AgentProject.name)).all()
    return [
        {"id": p.id, "name": p.name, "base_branch": p.base_branch,
         "available": (now - p.last_seen_at.replace(tzinfo=timezone.utc) if p.last_seen_at.tzinfo is None else now - p.last_seen_at).total_seconds() < 90}
        for p in all_projects
    ]


@router.post("/workers/register")
def register_worker(payload: ProjectRegistration, db: Db, _: WorkerAuth):
    now = datetime.now(timezone.utc)
    for item in payload.projects:
        project_id = str(item.get("id", ""))
        name = str(item.get("name", ""))
        branch = str(item.get("base_branch", "main"))
        if not project_id or not name or len(project_id) > 80 or len(name) > 160:
            continue
        project = db.get(AgentProject, project_id)
        if project is None:
            project = AgentProject(id=project_id, name=name, base_branch=branch, worker_id=payload.worker_id, last_seen_at=now)
            db.add(project)
        else:
            project.name, project.base_branch, project.worker_id, project.last_seen_at = name, branch, payload.worker_id, now
    db.commit()
    return {"registered": True}


@router.post("/runs/claim")
def claim_run(db: Db, _: WorkerAuth):
    run = db.scalar(select(AgentRun).where(AgentRun.status == "Queued").order_by(AgentRun.created_at).limit(1))
    if run is None:
        return None
    now = datetime.now(timezone.utc)
    result = db.execute(update(AgentRun).where(AgentRun.id == run.id, AgentRun.status == "Queued").values(status="Running", started_at=now))
    if result.rowcount != 1:
        db.rollback()
        return None
    run.status = "Running"
    run.started_at = now
    ticket = db.get(Ticket, run.ticket_id)
    project = db.get(AgentProject, run.project_id)
    event(db, run.id, "status", "Worker начал запуск Codex")
    db.commit()
    return {
        "id": run.id,
        "project": {"id": project.id, "name": project.name, "base_branch": project.base_branch},
        "ticket": {"id": ticket.id, "key": ticket.key, "title": ticket.title,
                   "description": ticket.description, "acceptance_criteria": ticket.acceptance_criteria,
                   "spec_ref": ticket.spec_ref},
    }


@router.post("/runs/{run_id}/events")
def add_event(run_id: int, payload: EventInput, db: Db, _: WorkerAuth):
    run = db.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    if run.status not in ACTIVE:
        raise HTTPException(409, "Run is already finished")
    if payload.thread_id:
        run.codex_thread_id = payload.thread_id
    run.progress = payload.message[:2000]
    event(db, run_id, payload.event_type, payload.message)
    db.commit()
    return {"saved": True}


@router.post("/runs/{run_id}/notifications")
def add_run_notification(run_id: int, payload: NotificationInput, db: Db, _: WorkerAuth):
    run = db.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    if run.status not in ACTIVE:
        raise HTTPException(409, "Run is already finished")
    ticket = db.get(Ticket, run.ticket_id)
    notification = Notification(
        ticket_id=ticket.id, run_id=run.id,
        title=payload.title, message=payload.message,
    )
    db.add(notification)
    db.commit()
    return {"created": True, "id": notification.id}


@router.post("/runs/{run_id}/finish")
def finish_run(run_id: int, payload: FinishInput, db: Db, _: WorkerAuth):
    run = db.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    if run.status == "Canceled" and payload.status == "Canceled":
        return {"status": run.status}
    if run.status not in ACTIVE:
        raise HTTPException(409, "Run is already finished")
    if payload.status not in ("Succeeded", "Failed", "Waiting for user", "Canceled"):
        raise HTTPException(422, "Unsupported final status")
    ticket = db.get(Ticket, run.ticket_id)
    run.status = payload.status
    run.final_report = payload.final_report[:30000]
    run.error = payload.error[:12000]
    run.branch_name = payload.branch_name[:200]
    run.worktree_path = payload.worktree_path[:1000]
    run.codex_thread_id = payload.thread_id[:200] or run.codex_thread_id
    run.pull_request_url = payload.pull_request_url[:500]
    run.finished_at = datetime.now(timezone.utc)
    run.active_key = None
    if run.branch_name:
        ticket.branch_name = run.branch_name
    if run.pull_request_url:
        ticket.pull_request_url = run.pull_request_url
    if payload.status == "Succeeded":
        ticket.status = "Review"
        title, message = f"Codex завершил {ticket.key}", run.final_report or "Запуск завершён успешно; ветка готова к ревью."
    elif payload.status == "Waiting for user":
        title, message = f"Требуется решение по {ticket.key}", run.error or run.progress or "Запуск ожидает решения пользователя."
    elif payload.status == "Canceled":
        title, message = f"Запуск отменён: {ticket.key}", run.error or "Тикет изменён; запусти агента повторно."
    else:
        title = f"Ошибка запуска {ticket.key}"
        message = run.error or "Codex завершился с ошибкой."
        if run.pull_request_url:
            message += f"\nDraft PR: {run.pull_request_url}"
    event(db, run_id, "status", f"Запуск завершён: {payload.status}")
    db.add(Notification(ticket_id=ticket.id, run_id=run.id, title=title, message=message[:12000]))
    db.commit()
    return {"status": run.status}


@router.get("/runs/{run_id}")
def run_detail(run_id: int, db: Db):
    run = db.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    events = db.scalars(select(AgentRunEvent).where(AgentRunEvent.run_id == run_id).order_by(AgentRunEvent.id)).all()
    return {"id": run.id, "status": run.status, "progress": run.progress,
            "final_report": run.final_report, "error": run.error,
            "branch_name": run.branch_name, "worktree_path": run.worktree_path,
            "thread_id": run.codex_thread_id,
            "thread_url": f"codex://threads/{run.codex_thread_id}" if run.codex_thread_id else "",
            "pull_request_url": run.pull_request_url, "project_id": run.project_id,
            "created_at": run.created_at.isoformat(),
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "events": [{"type": e.event_type, "message": e.message, "created_at": e.created_at.isoformat()} for e in events]}


@router.get("/tickets/{ticket_id}/runs")
def ticket_runs(ticket_id: int, db: Db):
    if db.get(Ticket, ticket_id) is None:
        raise HTTPException(404, "Ticket not found")
    runs = db.scalars(select(AgentRun).where(AgentRun.ticket_id == ticket_id).order_by(AgentRun.id.desc())).all()
    return [{"id": r.id, "status": r.status, "project_id": r.project_id,
             "branch_name": r.branch_name, "created_at": r.created_at.isoformat(),
             "finished_at": r.finished_at.isoformat() if r.finished_at else None,
             "progress": r.progress, "final_report": r.final_report, "error": r.error,
             "thread_id": r.codex_thread_id,
             "thread_url": f"codex://threads/{r.codex_thread_id}" if r.codex_thread_id else "",
             "pull_request_url": r.pull_request_url}
            for r in runs]


@router.get("/notifications")
def notifications(db: Db):
    items = db.scalars(select(Notification).where(Notification.read_at.is_(None)).order_by(Notification.id.desc())).all()
    return [{"id": n.id, "ticket_id": n.ticket_id, "run_id": n.run_id,
             "title": n.title, "message": n.message, "created_at": n.created_at.isoformat()} for n in items]


@router.post("/notifications/{notification_id}/read")
def read_notification(notification_id: int, db: Db):
    notification = db.get(Notification, notification_id)
    if notification is None:
        raise HTTPException(404, "Notification not found")
    notification.read_at = datetime.now(timezone.utc)
    db.commit()
    return {"read": True}
