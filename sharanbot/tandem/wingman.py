"""Tandem Wingman worker — bridges tandem-browser's AI agent as a SharanBot worker.

This module lets SharanBot assign tasks to tandem-browser's Wingman using the
task management API (POST /tasks).  Tasks are executed inside the Electron browser
with human-in-the-loop approval gates for risky actions.

Unlike GSD workers (subprocess + JSON-RPC), the Wingman worker communicates over
HTTP to the running tandem-browser instance.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import requests
from loguru import logger

from sharanbot.tandem.manager import TANDEM_API_BASE


class WingmanWorker:
    """Manages a single task delegated to tandem-browser's Wingman agent."""

    def __init__(self, task_id: str, description: str):
        self.task_id = task_id
        self.description = description
        self.status = "pending"
        self.messages: list[dict[str, Any]] = []
        self.created_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.task_id,
            "description": self.description,
            "status": self.status,
            "messages": self.messages[-10:],
            "created_at": self.created_at,
        }


class WingmanManager:
    """Manages Wingman tasks via tandem-browser's /tasks API.

    Integrates with SharanBot's worker system — the agent can create tasks,
    check status, steer with chat messages, and approve/reject steps.
    """

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "SharanBot-Wingman/1.0",
        })
        self._tasks: dict[str, WingmanWorker] = {}
        self._load_auth()

    def _load_auth(self) -> None:
        """Load tandem API auth token."""
        import os
        from pathlib import Path
        token = None
        token_path = Path.home() / ".tandem" / "api-token"
        try:
            if token_path.exists():
                t = token_path.read_text().strip()
                if len(t) >= 16:
                    token = t
        except Exception:
            pass
        if not token:
            token = os.environ.get("TANDEM_API_TOKEN")
        if token:
            self._session.headers["Authorization"] = f"Bearer {token}"

    def _api(self, method: str, endpoint: str, json: dict | None = None,
             params: dict | None = None, timeout: int = 30) -> Any:
        url = f"{TANDEM_API_BASE}{endpoint}"
        try:
            resp = self._session.request(method, url, json=json, params=params, timeout=timeout)
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "")
            if "application/json" in ct:
                return resp.json()
            return resp.text
        except requests.exceptions.ConnectionError:
            raise RuntimeError(
                f"Cannot connect to Tandem Browser at {TANDEM_API_BASE}. "
                "Make sure tandem-browser is running."
            )
        except requests.exceptions.HTTPError as e:
            raise RuntimeError(f"Tandem API error: {e.response.status_code}")

    @property
    def is_available(self) -> bool:
        try:
            r = requests.get(f"{TANDEM_API_BASE}/status", timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Task Management
    # ------------------------------------------------------------------

    async def create_task(
        self,
        description: str,
        steps: list[dict[str, Any]] | None = None,
    ) -> str:
        """Create a new task for Wingman to execute.

        Args:
            description: What to do (natural language)
            steps: Optional list of explicit steps with actions

        Returns:
            Status message with task ID
        """
        if not self.is_available:
            return "Error: Tandem Browser is not running."

        payload: dict[str, Any] = {"description": description}
        if steps:
            payload["steps"] = steps

        result = self._api("POST", "/tasks", json=payload)
        task_id = result.get("id", str(uuid.uuid4())[:8])

        worker = WingmanWorker(task_id=task_id, description=description)
        worker.status = "running"
        self._tasks[task_id] = worker

        logger.info("Wingman task created: {} — {}", task_id, description)
        return (
            f"Wingman task **{description[:50]}** started (id: `{task_id}`).\n"
            f"Use `wingman_status` to check progress or `wingman_chat` to communicate."
        )

    async def get_task_status(self, task_id: str | None = None) -> str:
        """Get status of one or all Wingman tasks."""
        if not self.is_available:
            return "Tandem Browser is not running."

        if task_id:
            try:
                result = self._api("GET", f"/tasks/{task_id}")
                return self._format_task(result)
            except Exception as e:
                return f"Error getting task {task_id}: {e}"

        # List all tasks
        try:
            tasks = self._api("GET", "/tasks")
            if not tasks:
                return "No active Wingman tasks."
            lines = ["**Wingman Tasks:**", ""]
            for t in (tasks if isinstance(tasks, list) else [tasks]):
                status = t.get("status", "unknown")
                desc = t.get("description", "")[:60]
                tid = t.get("id", "?")
                step = t.get("currentStep", 0)
                total = len(t.get("steps", []))
                lines.append(f"- `{tid}` [{status}] {desc} (step {step}/{total})")
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing tasks: {e}"

    async def approve_step(self, task_id: str) -> str:
        """Approve the current pending step for a task."""
        try:
            self._api("POST", f"/tasks/{task_id}/approve")
            return f"Step approved for task `{task_id}`."
        except Exception as e:
            return f"Error approving step: {e}"

    async def reject_step(self, task_id: str, reason: str = "") -> str:
        """Reject the current pending step for a task."""
        try:
            payload = {"reason": reason} if reason else {}
            self._api("POST", f"/tasks/{task_id}/reject", json=payload)
            return f"Step rejected for task `{task_id}`."
        except Exception as e:
            return f"Error rejecting step: {e}"

    async def emergency_stop(self) -> str:
        """Emergency stop all running Wingman tasks."""
        try:
            self._api("POST", "/emergency-stop")
            return "All Wingman tasks stopped."
        except Exception as e:
            return f"Error stopping tasks: {e}"

    # ------------------------------------------------------------------
    # Chat / Communication
    # ------------------------------------------------------------------

    async def send_chat(self, message: str) -> str:
        """Send a message to Wingman chat."""
        try:
            self._api("POST", "/chat", json={"text": message})
            return f"Message sent to Wingman: {message[:80]}"
        except Exception as e:
            return f"Error sending chat: {e}"

    async def get_chat_history(self, limit: int = 20) -> str:
        """Get recent Wingman chat messages."""
        try:
            messages = self._api("GET", "/chat", params={"limit": limit})
            if not messages:
                return "No chat messages."
            lines = ["**Wingman Chat:**", ""]
            msg_list = messages if isinstance(messages, list) else messages.get("messages", [])
            for m in msg_list[-limit:]:
                sender = m.get("sender", m.get("role", "?"))
                text = m.get("text", m.get("content", ""))[:200]
                lines.append(f"[{sender}] {text}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error getting chat: {e}"

    # ------------------------------------------------------------------
    # Autonomy Settings
    # ------------------------------------------------------------------

    async def get_autonomy(self) -> str:
        """Get current Wingman autonomy settings."""
        try:
            settings = self._api("GET", "/autonomy")
            lines = ["**Wingman Autonomy Settings:**", ""]
            for key, val in settings.items():
                lines.append(f"- {key}: {val}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"

    async def set_autonomy(self, **settings: Any) -> str:
        """Update Wingman autonomy settings."""
        try:
            self._api("PATCH", "/autonomy", json=settings)
            return f"Autonomy updated: {settings}"
        except Exception as e:
            return f"Error: {e}"

    # ------------------------------------------------------------------
    # Activity Log
    # ------------------------------------------------------------------

    async def get_activity_log(self) -> str:
        """Get Wingman agent activity log."""
        try:
            log_data = self._api("GET", "/activity-log/agent")
            if not log_data:
                return "No activity logged."
            entries = log_data if isinstance(log_data, list) else log_data.get("entries", [])
            lines = ["**Wingman Activity Log:**", ""]
            for entry in entries[-20:]:
                action = entry.get("action", "?")
                target = entry.get("target", "")
                risk = entry.get("riskLevel", "")
                lines.append(f"- [{risk}] {action}: {target[:80]}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"

    def _format_task(self, task: dict) -> str:
        lines = [
            f"**Task: {task.get('description', '?')}** (`{task.get('id', '?')}`)",
            f"Status: {task.get('status', '?')}",
            f"Step: {task.get('currentStep', 0)}/{len(task.get('steps', []))}",
            "",
        ]
        for i, step in enumerate(task.get("steps", [])):
            marker = "→" if step.get("status") == "running" else "✓" if step.get("status") == "done" else "○"
            lines.append(f"  {marker} {i+1}. [{step.get('riskLevel', '?')}] {step.get('description', '?')}")
        return "\n".join(lines)
