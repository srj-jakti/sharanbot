"""Task Ledger — proof-of-work and lifecycle tracking for every task.

Every task the user requests gets a ledger entry that tracks:
- What was requested
- When it started / completed / failed
- What strategy was used (direct, single_agent, team)
- What was actually done (artifacts, commands, files created)
- Outcome and proof (test results, URLs, screenshots)
- Duration and cost (tokens used)

This gives the user a complete audit trail and lets SharanBot
resume, retry, or reference past work accurately.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from loguru import logger

from sharanbot.utils.helpers import atomic_write


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


class TaskType(str, Enum):
    BUILD = "build"           # Building software/apps
    SERVICE = "service"       # Running a bot/service 24/7
    RESEARCH = "research"     # Research/analysis tasks
    AUTOMATION = "automation" # Automated workflows
    CHAT = "chat"             # Simple conversation
    MAINTENANCE = "maintenance"  # Fixes, updates, deployments
    OTHER = "other"


@dataclass
class TaskArtifact:
    """A concrete output from a task."""
    artifact_type: str   # "file", "url", "process", "screenshot", "test_result", "deployment"
    path_or_value: str   # file path, URL, process ID, etc.
    description: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.artifact_type,
            "value": self.path_or_value,
            "description": self.description,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskArtifact:
        return cls(
            artifact_type=data.get("type", "other"),
            path_or_value=data.get("value", ""),
            description=data.get("description", ""),
            created_at=data.get("created_at", 0),
        )


@dataclass
class TaskEntry:
    """A single task in the ledger."""

    id: str
    title: str                          # Short description
    description: str = ""               # Full task description from user
    task_type: str = TaskType.OTHER
    status: str = TaskStatus.PENDING
    strategy: str = ""                  # direct, single_agent, team
    session_key: str = ""               # Which chat session
    team_id: str = ""                   # If team was used

    # Lifecycle timestamps
    created_at: float = field(default_factory=time.time)
    started_at: float = 0
    completed_at: float = 0

    # Proof of work
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    commands_run: list[str] = field(default_factory=list)
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)

    # Outcome
    outcome: str = ""                   # Human-readable outcome summary
    error: str = ""                     # Error message if failed
    tokens_used: int = 0                # Total tokens consumed
    llm_calls: int = 0                  # Number of LLM calls

    # Metadata
    tags: list[str] = field(default_factory=list)
    project_id: str = ""                # Linked project
    parent_task_id: str = ""            # If this is a subtask
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        if self.started_at and self.completed_at:
            return self.completed_at - self.started_at
        if self.started_at:
            return time.time() - self.started_at
        return 0

    @property
    def is_active(self) -> bool:
        return self.status in (TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.PAUSED)

    @property
    def is_terminal(self) -> bool:
        return self.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description[:500],
            "task_type": self.task_type,
            "status": self.status,
            "strategy": self.strategy,
            "session_key": self.session_key,
            "team_id": self.team_id,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "artifacts": self.artifacts,
            "commands_run": self.commands_run[-20:],
            "files_created": self.files_created[-50:],
            "files_modified": self.files_modified[-50:],
            "tools_used": self.tools_used[-30:],
            "outcome": self.outcome[:1000],
            "error": self.error[:500],
            "tokens_used": self.tokens_used,
            "llm_calls": self.llm_calls,
            "tags": self.tags,
            "project_id": self.project_id,
            "parent_task_id": self.parent_task_id,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskEntry:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ServiceEntry:
    """A tracked long-running service/bot."""

    id: str
    name: str                           # Human-readable name
    command: str                        # The command that runs it
    working_dir: str = ""
    status: str = "stopped"             # running, stopped, stopped_permanently, crashed, paused
    process_id: str = ""                # Background process ID (bg_1, etc.)

    # Lifecycle
    created_at: float = field(default_factory=time.time)
    last_started: float = 0
    last_stopped: float = 0
    total_uptime_s: float = 0
    restart_count: int = 0
    auto_restart: bool = False

    # Configuration
    description: str = ""
    tags: list[str] = field(default_factory=list)
    log_file: str = ""                  # Path to log file
    health_check: str = ""              # URL or command to check health
    linked_task_id: str = ""            # Task that created this service

    # User decisions
    stop_reason: str = ""               # Why it was stopped
    stopped_by: str = ""                # "user", "system", "watchdog", "error"

    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_running(self) -> bool:
        return self.status == "running"

    @property
    def is_permanently_stopped(self) -> bool:
        return self.status == "stopped_permanently"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "command": self.command,
            "working_dir": self.working_dir,
            "status": self.status,
            "process_id": self.process_id,
            "created_at": self.created_at,
            "last_started": self.last_started,
            "last_stopped": self.last_stopped,
            "total_uptime_s": self.total_uptime_s,
            "restart_count": self.restart_count,
            "auto_restart": self.auto_restart,
            "description": self.description,
            "tags": self.tags,
            "log_file": self.log_file,
            "health_check": self.health_check,
            "linked_task_id": self.linked_task_id,
            "stop_reason": self.stop_reason,
            "stopped_by": self.stopped_by,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ServiceEntry:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class TaskLedger:
    """Persistent ledger of all tasks and services.

    Provides:
    - Task lifecycle tracking (create → start → complete/fail)
    - Proof-of-work recording (artifacts, files, commands)
    - Service registry (long-running bots/processes)
    - Query by status, type, date range
    - Survives restarts — everything is persisted to JSON
    """

    _TASKS_FILE = "task_ledger.json"
    _SERVICES_FILE = "service_registry.json"
    _MAX_TASKS = 1000
    _MAX_COMPLETED_KEEP = 500

    def __init__(self, workspace: Path):
        self._workspace = workspace
        self._tasks_path = workspace / self._TASKS_FILE
        self._services_path = workspace / self._SERVICES_FILE
        self._tasks: dict[str, TaskEntry] = {}
        self._services: dict[str, ServiceEntry] = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if self._tasks_path.exists():
            try:
                data = json.loads(self._tasks_path.read_text(encoding="utf-8"))
                for tdata in data.get("tasks", []):
                    t = TaskEntry.from_dict(tdata)
                    self._tasks[t.id] = t
            except Exception as e:
                logger.warning("Failed to load task ledger: {}", e)

        if self._services_path.exists():
            try:
                data = json.loads(self._services_path.read_text(encoding="utf-8"))
                for sdata in data.get("services", []):
                    s = ServiceEntry.from_dict(sdata)
                    self._services[s.id] = s
            except Exception as e:
                logger.warning("Failed to load service registry: {}", e)

    def _save_tasks(self) -> None:
        try:
            self._tasks_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "tasks": [t.to_dict() for t in self._tasks.values()],
                "saved_at": time.time(),
            }
            atomic_write(self._tasks_path, json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.warning("Failed to save task ledger: {}", e)

    def _save_services(self) -> None:
        try:
            self._services_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "services": [s.to_dict() for s in self._services.values()],
                "saved_at": time.time(),
            }
            atomic_write(self._services_path, json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.warning("Failed to save service registry: {}", e)

    def _prune_tasks(self) -> None:
        """Keep ledger from growing unbounded."""
        if len(self._tasks) <= self._MAX_TASKS:
            return
        completed = sorted(
            [t for t in self._tasks.values() if t.is_terminal],
            key=lambda t: t.completed_at or t.created_at,
        )
        to_remove = len(self._tasks) - self._MAX_COMPLETED_KEEP
        for t in completed[:to_remove]:
            del self._tasks[t.id]

    # ------------------------------------------------------------------
    # Task CRUD
    # ------------------------------------------------------------------

    def create_task(
        self,
        title: str,
        description: str = "",
        task_type: str = TaskType.OTHER,
        session_key: str = "",
        strategy: str = "",
        project_id: str = "",
        tags: list[str] | None = None,
    ) -> TaskEntry:
        """Create a new task entry in the ledger."""
        task = TaskEntry(
            id=f"task_{uuid.uuid4().hex[:8]}",
            title=title[:200],
            description=description[:2000],
            task_type=task_type,
            session_key=session_key,
            strategy=strategy,
            project_id=project_id,
            tags=tags or [],
        )
        self._tasks[task.id] = task
        self._prune_tasks()
        self._save_tasks()
        logger.info("Task created: {} — {}", task.id, title[:80])
        return task

    def start_task(self, task_id: str) -> bool:
        """Mark a task as started."""
        task = self._tasks.get(task_id)
        if not task:
            return False
        task.status = TaskStatus.RUNNING
        task.started_at = time.time()
        self._save_tasks()
        return True

    def complete_task(
        self,
        task_id: str,
        outcome: str = "",
        artifacts: list[TaskArtifact] | None = None,
    ) -> bool:
        """Mark a task as completed with proof of work."""
        task = self._tasks.get(task_id)
        if not task:
            return False
        task.status = TaskStatus.COMPLETED
        task.completed_at = time.time()
        task.outcome = outcome[:2000]
        if artifacts:
            task.artifacts.extend(a.to_dict() for a in artifacts)
        self._save_tasks()
        logger.info("Task completed: {} ({:.0f}s)", task_id, task.duration_s)
        return True

    def fail_task(self, task_id: str, error: str = "") -> bool:
        """Mark a task as failed."""
        task = self._tasks.get(task_id)
        if not task:
            return False
        task.status = TaskStatus.FAILED
        task.completed_at = time.time()
        task.error = error[:1000]
        self._save_tasks()
        logger.info("Task failed: {} — {}", task_id, error[:100])
        return True

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a task."""
        task = self._tasks.get(task_id)
        if not task:
            return False
        task.status = TaskStatus.CANCELLED
        task.completed_at = time.time()
        self._save_tasks()
        return True

    def add_artifact(self, task_id: str, artifact: TaskArtifact) -> bool:
        """Add a proof-of-work artifact to a task."""
        task = self._tasks.get(task_id)
        if not task:
            return False
        task.artifacts.append(artifact.to_dict())
        self._save_tasks()
        return True

    def record_tool_use(self, task_id: str, tool_name: str) -> None:
        """Record that a tool was used during a task."""
        task = self._tasks.get(task_id)
        if task and tool_name not in task.tools_used:
            task.tools_used.append(tool_name)
            # Don't save on every tool call — too expensive. Batch save on completion.

    def record_file_created(self, task_id: str, path: str) -> None:
        """Record a file created during a task."""
        task = self._tasks.get(task_id)
        if task and path not in task.files_created:
            task.files_created.append(path)

    def record_file_modified(self, task_id: str, path: str) -> None:
        """Record a file modified during a task."""
        task = self._tasks.get(task_id)
        if task and path not in task.files_modified:
            task.files_modified.append(path)

    def record_command(self, task_id: str, command: str) -> None:
        """Record a command executed during a task."""
        task = self._tasks.get(task_id)
        if task:
            task.commands_run.append(command[:200])

    def update_tokens(self, task_id: str, tokens: int, llm_calls: int = 1) -> None:
        """Update token usage for a task."""
        task = self._tasks.get(task_id)
        if task:
            task.tokens_used += tokens
            task.llm_calls += llm_calls

    # ------------------------------------------------------------------
    # Task queries
    # ------------------------------------------------------------------

    def get_task(self, task_id: str) -> TaskEntry | None:
        return self._tasks.get(task_id)

    def get_active_tasks(self) -> list[TaskEntry]:
        """Get all currently active (pending/running/paused) tasks."""
        return [t for t in self._tasks.values() if t.is_active]

    def get_recent_tasks(self, n: int = 20) -> list[TaskEntry]:
        """Get the N most recent tasks."""
        return sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)[:n]

    def get_tasks_by_status(self, status: str) -> list[TaskEntry]:
        return [t for t in self._tasks.values() if t.status == status]

    def get_tasks_by_type(self, task_type: str) -> list[TaskEntry]:
        return [t for t in self._tasks.values() if t.task_type == task_type]

    def get_tasks_by_session(self, session_key: str) -> list[TaskEntry]:
        return [t for t in self._tasks.values() if t.session_key == session_key]

    def search_tasks(self, query: str) -> list[TaskEntry]:
        """Search tasks by title/description."""
        q = query.lower()
        return [
            t for t in self._tasks.values()
            if q in t.title.lower() or q in t.description.lower()
        ]

    # ------------------------------------------------------------------
    # Service Registry
    # ------------------------------------------------------------------

    def register_service(
        self,
        name: str,
        command: str,
        working_dir: str = "",
        description: str = "",
        auto_restart: bool = False,
        log_file: str = "",
        health_check: str = "",
        linked_task_id: str = "",
        tags: list[str] | None = None,
    ) -> ServiceEntry:
        """Register a new long-running service."""
        # Check if service with same name exists
        for s in self._services.values():
            if s.name.lower() == name.lower():
                # Update existing
                s.command = command
                s.working_dir = working_dir
                s.description = description or s.description
                s.auto_restart = auto_restart
                s.log_file = log_file or s.log_file
                s.health_check = health_check or s.health_check
                if tags:
                    s.tags = tags
                self._save_services()
                return s

        service = ServiceEntry(
            id=f"svc_{uuid.uuid4().hex[:8]}",
            name=name,
            command=command,
            working_dir=working_dir,
            description=description,
            auto_restart=auto_restart,
            log_file=log_file,
            health_check=health_check,
            linked_task_id=linked_task_id,
            tags=tags or [],
        )
        self._services[service.id] = service
        self._save_services()
        logger.info("Service registered: {} — {}", service.id, name)
        return service

    def start_service(self, service_id: str, process_id: str = "") -> bool:
        """Mark a service as started."""
        svc = self._find_service(service_id)
        if not svc:
            return False
        if svc.is_permanently_stopped:
            logger.warning("Cannot start permanently stopped service: {}", svc.name)
            return False
        svc.status = "running"
        svc.process_id = process_id
        svc.last_started = time.time()
        svc.restart_count += 1
        self._save_services()
        return True

    def stop_service(
        self,
        service_id: str,
        permanent: bool = False,
        reason: str = "",
        stopped_by: str = "user",
    ) -> bool:
        """Stop a service."""
        svc = self._find_service(service_id)
        if not svc:
            return False
        # Calculate uptime
        if svc.last_started and svc.is_running:
            svc.total_uptime_s += time.time() - svc.last_started
        svc.status = "stopped_permanently" if permanent else "stopped"
        svc.last_stopped = time.time()
        svc.stop_reason = reason
        svc.stopped_by = stopped_by
        svc.process_id = ""
        self._save_services()
        logger.info("Service stopped: {} (permanent={})", svc.name, permanent)
        return True

    def get_service(self, service_id: str) -> ServiceEntry | None:
        return self._find_service(service_id)

    def get_all_services(self) -> list[ServiceEntry]:
        return list(self._services.values())

    def get_running_services(self) -> list[ServiceEntry]:
        return [s for s in self._services.values() if s.is_running]

    def get_services_to_restart(self) -> list[ServiceEntry]:
        """Get services that should be auto-restarted after gateway restart."""
        return [
            s for s in self._services.values()
            if s.auto_restart and s.status == "stopped" and not s.is_permanently_stopped
        ]

    def _find_service(self, identifier: str) -> ServiceEntry | None:
        """Find service by ID or name (fuzzy)."""
        if identifier in self._services:
            return self._services[identifier]
        identifier_lower = identifier.lower()
        for s in self._services.values():
            if s.name.lower() == identifier_lower:
                return s
            if identifier_lower in s.name.lower():
                return s
        return None

    # ------------------------------------------------------------------
    # Status & Formatting
    # ------------------------------------------------------------------

    def format_task_status(self, n: int = 10) -> str:
        """Format a human-readable task status."""
        active = self.get_active_tasks()
        recent = self.get_recent_tasks(n)

        lines = ["📋 Task Ledger:"]

        if active:
            lines.append(f"\n🔄 Active ({len(active)}):")
            for t in active:
                dur = f" ({t.duration_s:.0f}s)" if t.started_at else ""
                lines.append(f"  [{t.status}] {t.title}{dur}")
                if t.tools_used:
                    lines.append(f"    Tools: {', '.join(t.tools_used[-5:])}")

        completed = [t for t in recent if t.is_terminal]
        if completed:
            lines.append(f"\n✅ Recent ({len(completed)}):")
            for t in completed[:5]:
                icon = {"completed": "✅", "failed": "❌", "cancelled": "⏹️"}.get(t.status, "⚪")
                dur = f" ({t.duration_s:.0f}s)" if t.duration_s else ""
                lines.append(f"  {icon} {t.title}{dur}")
                if t.outcome:
                    lines.append(f"    → {t.outcome[:100]}")
                if t.artifacts:
                    lines.append(f"    📎 {len(t.artifacts)} artifact(s)")

        return "\n".join(lines)

    def format_service_status(self) -> str:
        """Format a human-readable service status."""
        services = self.get_all_services()
        if not services:
            return "No services registered."

        lines = ["🔧 Service Registry:"]
        for s in services:
            icon = {
                "running": "🟢",
                "stopped": "🔴",
                "stopped_permanently": "🛑",
                "crashed": "💥",
                "paused": "⏸️",
            }.get(s.status, "⚪")
            uptime = f" | uptime: {s.total_uptime_s:.0f}s" if s.total_uptime_s else ""
            restarts = f" | restarts: {s.restart_count}" if s.restart_count else ""
            lines.append(f"  {icon} {s.name} [{s.status}]{uptime}{restarts}")
            if s.stop_reason:
                lines.append(f"    Reason: {s.stop_reason}")
            if s.command:
                lines.append(f"    Cmd: {s.command[:80]}")
        return "\n".join(lines)

    def get_stats(self) -> dict[str, Any]:
        """Get ledger statistics."""
        all_tasks = list(self._tasks.values())
        return {
            "total_tasks": len(all_tasks),
            "active": sum(1 for t in all_tasks if t.is_active),
            "completed": sum(1 for t in all_tasks if t.status == TaskStatus.COMPLETED),
            "failed": sum(1 for t in all_tasks if t.status == TaskStatus.FAILED),
            "total_services": len(self._services),
            "running_services": sum(1 for s in self._services.values() if s.is_running),
            "total_tokens": sum(t.tokens_used for t in all_tasks),
            "total_artifacts": sum(len(t.artifacts) for t in all_tasks),
        }
