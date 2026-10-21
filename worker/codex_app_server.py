"""Small JSON-RPC client for the local Codex app-server protocol."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable


class CodexAppServerError(RuntimeError):
    pass


class CodexAppServer:
    """Own a Codex app-server process and exchange JSON-RPC messages over stdio."""

    def __init__(self, cwd: Path, on_notification: Callable[[dict], None] | None = None):
        environment = dict(os.environ)
        # The tracker credential is only for worker-to-tracker requests and must not
        # become visible to a coding agent or one of its shell commands.
        environment.pop("TICKET_AGENT_TOKEN", None)
        self.process = subprocess.Popen(
            ["codex", "app-server", "--listen", "stdio://"],
            cwd=cwd,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self.cwd = cwd
        self.on_notification = on_notification or (lambda _message: None)
        self._messages: queue.Queue[dict | None] = queue.Queue()
        self._counter = 0
        self._write_lock = threading.Lock()
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        try:
            self.request("initialize", {"clientInfo": {
                "name": "tiny_ticket_tracker",
                "title": "Tiny Ticket Tracker",
                "version": "0.1.0",
            }, "capabilities": {}})
            self.notify("initialized", {})
        except Exception:
            self.close()
            raise

    def _read_stdout(self) -> None:
        try:
            for line in self.process.stdout or []:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(message, dict):
                    self._messages.put(message)
        finally:
            self._messages.put(None)

    def _write(self, message: dict) -> None:
        if self.process.poll() is not None:
            raise CodexAppServerError("Codex app-server завершился до ответа")
        with self._write_lock:
            if not self.process.stdin:
                raise CodexAppServerError("У Codex app-server закрыт stdin")
            self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.process.stdin.flush()

    def notify(self, method: str, params: dict) -> None:
        self._write({"method": method, "params": params})

    def request(self, method: str, params: dict, timeout: float = 60) -> dict:
        self._counter += 1
        request_id = self._counter
        self._write({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        deferred = []
        try:
            while time.monotonic() < deadline:
                try:
                    message = self._messages.get(timeout=min(1, max(.01, deadline - time.monotonic())))
                except queue.Empty:
                    continue
                if message is None:
                    raise CodexAppServerError("Codex app-server закрыл соединение")
                if message.get("id") == request_id:
                    if "error" in message:
                        raise CodexAppServerError(str(message["error"].get("message", message["error"])))
                    return message.get("result", {})
                if "method" in message and "id" not in message:
                    self.on_notification(message)
                else:
                    deferred.append(message)
            raise TimeoutError(f"Codex app-server не ответил на {method}")
        finally:
            for message in deferred:
                self._messages.put(message)

    def start_thread(self, developer_instructions: str = "") -> str:
        result = self.request("thread/start", {
            "cwd": str(self.cwd),
            "sandbox": "workspace-write",
            "approvalPolicy": "never",
            "developerInstructions": developer_instructions or None,
        })
        thread = result.get("thread") or {}
        thread_id = str(thread.get("id", ""))
        if not thread_id:
            raise CodexAppServerError("Codex app-server не вернул ID нового чата")
        return thread_id

    def start_turn(self, thread_id: str, prompt: str) -> str:
        started = self.request("turn/start", {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt}],
        })
        turn = started.get("turn") or {}
        turn_id = str(turn.get("id", ""))
        if not turn_id:
            raise CodexAppServerError("Codex app-server не вернул ID запуска")
        return turn_id

    def thread_snapshot(self, thread_id: str) -> dict:
        result = self.request("thread/read", {"threadId": thread_id, "includeTurns": True})
        return result.get("thread") or {}

    def wait_for_turn(self, thread_id: str, turn_id: str, timeout: int,
                      poll_seconds: float = 3, canceled: Callable[[], bool] | None = None) -> dict:
        """Wait for an app-server turn to finish, polling durable thread history."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if canceled and canceled():
                try:
                    self.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})
                except Exception:
                    # Some app-server versions may not support interrupt; the caller
                    # still stops before review, push, or PR creation.
                    pass
                return {"status": "canceled", "text": ""}
            thread = self.thread_snapshot(thread_id)
            turns = thread.get("turns") or []
            target = next((turn for turn in turns if turn.get("id") == turn_id), None)
            if target and target.get("status") in {"completed", "failed", "interrupted"}:
                messages = [item.get("text", "") for item in target.get("items", [])
                            if item.get("type") == "agentMessage"]
                return {"status": target["status"], "text": "\n\n".join(messages), "turn": target}
            time.sleep(poll_seconds)
        raise TimeoutError(f"Codex turn {turn_id or '(unknown)'} превысил лимит ожидания")

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

    def __enter__(self) -> "CodexAppServer":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

