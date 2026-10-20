import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app.agent_api import FinishInput, finish_run
from app.database import Base
from app.models import AgentProject, AgentRun, AgentRunEvent, Notification, Ticket
from app.routers.web import start_agent_run


class AgentRunLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.ticket = Ticket(
            title="Implement workflow", description="Details", acceptance_criteria="Criteria",
            spec_ref="docs/spec.md", status="In Progress", priority="Medium",
        )
        self.db.add(self.ticket)
        self.db.commit()
        self.run = AgentRun(ticket_id=self.ticket.id, project_id="demo", status="Running", active_key="active")
        self.db.add(self.run)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_success_moves_ticket_to_review_and_persists_report_and_notification(self):
        response = finish_run(self.run.id, FinishInput(
            status="Succeeded", final_report="Implementation complete", branch_name="agent/tt-1-1-demo",
            worktree_path="/tmp/worktree", thread_id="thread-123",
        ), self.db, None)
        self.db.refresh(self.ticket)
        self.db.refresh(self.run)
        self.assertEqual(response["status"], "Succeeded")
        self.assertEqual(self.ticket.status, "Review")
        self.assertEqual(self.ticket.branch_name, "agent/tt-1-1-demo")
        self.assertEqual(self.run.final_report, "Implementation complete")
        self.assertEqual(self.run.codex_thread_id, "thread-123")
        self.assertIsNone(self.run.active_key)
        self.assertEqual(len(self.db.scalars(select(Notification)).all()), 1)
        self.assertEqual(len(self.db.scalars(select(AgentRunEvent)).all()), 1)

    def test_failure_keeps_ticket_in_progress_and_records_notification(self):
        finish_run(self.run.id, FinishInput(status="Failed", error="Codex unavailable"), self.db, None)
        self.db.refresh(self.ticket)
        self.db.refresh(self.run)
        notification = self.db.scalar(select(Notification))
        self.assertEqual(self.ticket.status, "In Progress")
        self.assertEqual(self.run.error, "Codex unavailable")
        self.assertEqual(self.run.status, "Failed")
        self.assertIn("Codex unavailable", notification.message)

    def test_waiting_for_user_persists_as_finished_attention_state(self):
        finish_run(self.run.id, FinishInput(status="Waiting for user", error="Approval needed"), self.db, None)
        self.db.refresh(self.ticket)
        self.db.refresh(self.run)
        self.assertEqual(self.ticket.status, "In Progress")
        self.assertEqual(self.run.status, "Waiting for user")
        self.assertIsNone(self.run.active_key)

    def test_waiting_run_stays_in_history_and_allows_new_attempt(self):
        finish_run(self.run.id, FinishInput(status="Waiting for user", error="Approval needed"), self.db, None)
        project = AgentProject(
            id="demo", name="Demo", base_branch="main", worker_id="worker",
            last_seen_at=datetime.now(timezone.utc),
        )
        self.db.add(project)
        self.db.commit()
        request = Request({
            "type": "http", "http_version": "1.1", "method": "POST", "scheme": "http",
            "path": f"/tickets/{self.ticket.id}/agent-runs", "raw_path": b"/",
            "query_string": b"", "headers": [(b"origin", b"http://localhost:8000")],
            "server": ("localhost", 8000), "client": ("127.0.0.1", 1234),
        })

        response = start_agent_run(request, self.ticket.id, self.db, "demo")
        self.assertEqual(response.status_code, 303)
        runs = self.db.scalars(select(AgentRun).where(AgentRun.ticket_id == self.ticket.id).order_by(AgentRun.id)).all()
        self.assertEqual([run.status for run in runs], ["Waiting for user", "Queued"])
        self.assertEqual(len(self.db.scalars(select(Notification).where(Notification.run_id == self.run.id)).all()), 1)

    def test_launch_creates_one_queue_entry_and_marks_ticket_in_progress(self):
        project = AgentProject(
            id="demo", name="Demo", base_branch="main", worker_id="worker",
            last_seen_at=datetime.now(timezone.utc),
        )
        self.db.add(project)
        self.db.delete(self.run)
        self.db.commit()
        request = Request({
            "type": "http", "http_version": "1.1", "method": "POST", "scheme": "http",
            "path": f"/tickets/{self.ticket.id}/agent-runs", "raw_path": b"/",
            "query_string": b"", "headers": [(b"origin", b"http://localhost:8000")],
            "server": ("localhost", 8000), "client": ("127.0.0.1", 1234),
        })
        response = start_agent_run(request, self.ticket.id, self.db, "demo")
        self.assertEqual(response.status_code, 303)
        self.db.refresh(self.ticket)
        runs = self.db.scalars(select(AgentRun).where(AgentRun.ticket_id == self.ticket.id)).all()
        self.assertEqual(self.ticket.status, "In Progress")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].status, "Queued")
        second = start_agent_run(request, self.ticket.id, self.db, "demo")
        self.assertEqual(second.status_code, 303)
        self.assertEqual(len(self.db.scalars(select(AgentRun).where(AgentRun.ticket_id == self.ticket.id)).all()), 1)

    def test_cross_origin_launch_is_rejected(self):
        project = AgentProject(
            id="demo", name="Demo", base_branch="main", worker_id="worker",
            last_seen_at=datetime.now(timezone.utc),
        )
        self.db.add(project)
        self.db.delete(self.run)
        self.db.commit()
        request = Request({
            "type": "http", "http_version": "1.1", "method": "POST", "scheme": "http",
            "path": f"/tickets/{self.ticket.id}/agent-runs", "raw_path": b"/",
            "query_string": b"", "headers": [(b"origin", b"http://attacker.invalid")],
            "server": ("localhost", 8000), "client": ("127.0.0.1", 1234),
        })
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as raised:
            start_agent_run(request, self.ticket.id, self.db, "demo")
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(len(self.db.scalars(select(AgentRun).where(AgentRun.ticket_id == self.ticket.id)).all()), 0)


if __name__ == "__main__":
    unittest.main()
