from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
import os
import re
import queue
import socket
import subprocess
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

DEFAULT_CONFIG = Path.home() / ".config/tiny-ticket-tracker/worker.toml"
DEFAULT_WORKTREE_ROOT = Path.home() / ".local/share/tiny-ticket-tracker/worktrees"
TERMINAL_EVENTS = {"turn.completed", "turn.failed", "error"}


class TrackerClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def request(self, path: str, body: dict | None = None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = response.read()
            return json.loads(payload) if payload else None


def load_config(path: Path) -> dict:
    with path.open("rb") as handle:
        config = tomllib.load(handle)
    projects = config.get("projects", {})
    for project_id, project in projects.items():
        root = Path(project.get("path", "")).expanduser().resolve()
        if not root.is_dir() or not (root / ".git").exists():
            print(f"Skipping {project_id}: not a Git checkout: {root}", file=sys.stderr)
            project["_valid"] = False
            continue
        project["_path"] = root
        project["_valid"] = True
    return config


def projects_for_registration(config: dict) -> list[dict]:
    return [
        {"id": project_id, "name": project.get("name", project_id),
         "base_branch": project.get("base_branch", "main")}
        for project_id, project in config.get("projects", {}).items()
        if project.get("_valid")
    ]


def git(*args: str, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], text=True, capture_output=True, check=check)


def codex_child_environment(environment: Mapping[str, str] | None = None) -> dict[str, str]:
    child_environment = dict(os.environ if environment is None else environment)
    child_environment.pop("TICKET_AGENT_TOKEN", None)
    return child_environment


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.lower()).strip("-.")
    return slug[:48] or "ticket"


def read_project_instructions(root: Path) -> str:
    instructions = []
    for path in [root / "AGENTS.md"]:
        if path.is_file():
            instructions.append(f"--- {path.name} ---\n{path.read_text(errors='replace')[:24000]}")
    return "\n\n".join(instructions) or "No root AGENTS.md was found; follow any applicable project instructions you discover."


def read_spec(root: Path, spec_ref: str) -> str:
    if not spec_ref:
        return "(no specification reference)"
    candidate = Path(spec_ref).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return f"Specification reference: {spec_ref}\n(The referenced file is not available inside this project's checkout.)"
    if not resolved.is_file():
        return f"Specification reference: {spec_ref}\n(The reference is not a file.)"
    return f"--- Specification: {spec_ref} ---\n{resolved.read_text(errors='replace')[:30000]}"


def build_prompt(ticket: dict, instructions: str, spec_text: str) -> str:
    return f"""Implement the ticket below in this isolated Git worktree.

Hard limits:
- Work only inside the current worktree.
- Do not merge, push, create a pull request, deploy, or change the user's original working copy.
- Do not access other repositories or arbitrary paths.
- Follow the project instructions and approved specification.
- Add or update tests where appropriate and run project checks; report checks and limitations.
- If a required product decision is missing, do not guess: explain what is blocked.

Ticket {ticket['key']}: {ticket['title']}

Description:
{ticket['description']}

Acceptance criteria:
{ticket['acceptance_criteria']}

Specification reference: {ticket['spec_ref']}

Project instructions:
{instructions}

Specification content:
{spec_text}
"""


def send_event(client: TrackerClient, run_id: int, kind: str, message: str, thread_id=""):
    try:
        client.request(f"/api/agent/runs/{run_id}/events", {
            "event_type": kind[:80], "message": message[:12000], "thread_id": thread_id,
        })
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        print(f"Could not send run event: {error}", file=sys.stderr)


def execute_run(client: TrackerClient, run: dict, config: dict, timeout_seconds: int) -> None:
    run_id = run["id"]
    project = config.get("projects", {}).get(run["project"]["id"])
    if not project or not project.get("_valid"):
        client.request(f"/api/agent/runs/{run_id}/finish", {
            "status": "Failed", "error": "Проект отсутствует в локальном allowlist worker или его Git-каталог недоступен."
        })
        return
    root: Path = project["_path"]
    branch = f"agent/{run['ticket']['key'].lower()}-{run_id}-{safe_slug(run['ticket']['title'])}"
    worktree_root = Path(config.get("worker", {}).get("worktree_root", str(DEFAULT_WORKTREE_ROOT))).expanduser()
    worktree_root.mkdir(parents=True, exist_ok=True)
    worktree = (worktree_root / f"{run['ticket']['key'].lower()}-{run_id}").resolve()
    if worktree.exists():
        raise RuntimeError(f"Worktree path already exists; refusing to overwrite: {worktree}")
    output_path = worktree_root / f"run-{run_id}-final-message.txt"
    thread_id = ""
    final_report = ""
    waiting_for_user = False
    try:
        base_ref = project.get("base_branch", "main")
        base_commit = git("-C", str(root), "rev-parse", "--verify", f"{base_ref}^{{commit}}").stdout.strip()
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", base_commit):
            raise RuntimeError(f"Configured base ref did not resolve to a commit: {base_ref}")
        if git("-C", str(root), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False).returncode == 0:
            raise RuntimeError(f"Branch already exists; refusing to reuse it: {branch}")
        for line in git("-C", str(root), "worktree", "list", "--porcelain").stdout.splitlines():
            if line.startswith("worktree ") and Path(line[9:]).resolve() == worktree:
                raise RuntimeError(f"Path is already registered as a Git worktree: {worktree}")
        result = git("-C", str(root), "worktree", "add", "-b", branch, str(worktree), base_commit)
        send_event(client, run_id, "worktree", f"Создана ветка {branch} из {base_ref} ({base_commit[:12]}) и отдельный worktree {worktree}")
        instructions = read_project_instructions(worktree)
        spec_text = read_spec(worktree, run["ticket"].get("spec_ref", ""))
        prompt = build_prompt(run["ticket"], instructions, spec_text)
        command = ["codex", "exec", "--json", "--sandbox", "workspace-write", "-C", str(worktree), "-o", str(output_path), prompt]
        send_event(client, run_id, "codex", "Запускаю codex exec --json (sandbox: workspace-write)")
        process = subprocess.Popen(
            command,
            cwd=worktree,
            env=codex_child_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        lines: queue.Queue[str | None] = queue.Queue()

        def read_output():
            try:
                for output_line in process.stdout or []:
                    lines.put(output_line)
            finally:
                lines.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        start = time.monotonic()
        failure = ""
        output_closed = False
        while not output_closed or process.poll() is None:
            try:
                line = lines.get(timeout=1)
            except queue.Empty:
                line = ""
            if line is None:
                output_closed = True
            elif line:
                pass
            if time.monotonic() - start > timeout_seconds:
                process.kill()
                failure = f"Codex превысил лимит времени ({timeout_seconds} секунд)."
                break
            if not line or line is None:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                send_event(client, run_id, "output", line.strip()[:2000])
                continue
            kind = str(payload.get("type", "event"))
            if kind == "thread.started":
                thread_id = str(payload.get("thread_id", ""))
            item = payload.get("item", {}) if isinstance(payload.get("item"), dict) else {}
            text = item.get("text") or payload.get("message") or payload.get("error") or ""
            if kind == "item.completed" and text:
                send_event(client, run_id, str(item.get("type", kind)), str(text), thread_id)
            elif kind in {"approval.requested", "input_required", "user_input.required", "turn.awaiting_approval"}:
                waiting_for_user = True
                failure = str(text or "Codex ожидает решения пользователя; продолжение будет сделано после настройки интерактивного approval flow.")
                send_event(client, run_id, "waiting_user", failure, thread_id)
                process.terminate()
                break
            elif kind in TERMINAL_EVENTS:
                send_event(client, run_id, kind, str(text or kind), thread_id)
                if kind in {"turn.failed", "error"}:
                    failure = str(text or "Codex сообщила об ошибке")
        try:
            code = process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            code = process.wait(timeout=10)
        if output_path.is_file():
            final_report = output_path.read_text(errors="replace")[:30000]
        if code != 0 or failure:
            if waiting_for_user:
                status = "Waiting for user"
                error = failure
                final_report = ""
            else:
                raise RuntimeError(failure or f"Codex завершился с кодом {code}; смотри события запуска.")
        else:
            status = "Succeeded"
            error = ""
        if status == "Succeeded" and not final_report:
            final_report = "Codex завершил запуск без итогового сообщения. Подробности смотри в событиях."
    except Exception as exc:
        status = "Failed"
        error = str(exc)[:12000]
        send_event(client, run_id, "error", error, thread_id)
    try:
        client.request(f"/api/agent/runs/{run_id}/finish", {
            "status": status, "final_report": final_report, "error": error,
            "branch_name": branch if worktree.exists() else "",
            "worktree_path": str(worktree) if worktree.exists() else "", "thread_id": thread_id,
        })
    except Exception as exc:
        print(f"Unable to save run completion; run {run_id} may need recovery: {exc}", file=sys.stderr)


def run_worker(config_path: Path, once=False) -> None:
    config = load_config(config_path)
    tracker = config.get("worker", {}).get("tracker_url", "http://127.0.0.1:8000")
    token = os.getenv("TICKET_AGENT_TOKEN") or config.get("worker", {}).get("token", "local-dev-only-change-me")
    worker_id = config.get("worker", {}).get("worker_id", socket.gethostname())
    client = TrackerClient(tracker, token)
    projects = projects_for_registration(config)
    if not projects:
        print("No valid Git projects in worker allowlist. Check worker.toml.", file=sys.stderr)
    while True:
        try:
            client.request("/api/agent/workers/register", {"worker_id": worker_id, "projects": projects})
            run = client.request("/api/agent/runs/claim", {})
            if run:
                print(f"Claimed run {run['id']} ({run['ticket']['key']})", flush=True)
                execute_run(client, run, config, int(config.get("worker", {}).get("timeout_seconds", 21600)))
            elif once:
                return
        except urllib.error.URLError as exc:
            print(f"Tracker unavailable: {exc}", file=sys.stderr)
            if once:
                raise
        except Exception as exc:
            print(f"Worker error: {exc}", file=sys.stderr)
            if once:
                raise
        if once:
            return
        time.sleep(float(config.get("worker", {}).get("poll_seconds", 3)))


def main():
    parser = argparse.ArgumentParser(description="Host-side Codex worker for Tiny Ticket Tracker")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--once", action="store_true", help="claim at most one run, then exit")
    args = parser.parse_args()
    if not args.config.is_file():
        parser.error(f"Worker config not found: {args.config}. Copy worker/worker.example.toml and edit it.")
    run_worker(args.config, args.once)


if __name__ == "__main__":
    main()
