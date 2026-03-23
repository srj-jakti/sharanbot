"""Persistent JSONL-backed priority task queue with retry and subtask tracking."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import IntEnum
from pathlib import Path
from typing import Any

from loguru import logger


class TaskPriority(IntEnum):
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    BACKGROUND = 4


class TaskStatus:
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


@dataclass
class TaskEntry:
    task_id: str
    description: str
    priority: int = TaskPriority.NORMAL
    status: str = TaskStatus.PENDING
    project_id: str = ""
    parent_id: str = ""          # For subtask tracking
    tags: list[str] = field(default_factory=list)
    retries: int = 0
    max_retries: int = 3
    created_at: float = 0.0
    started_at: float = 0.0
    completed_at: float = 0.0
    result: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TaskEntry:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class PersistentTaskQueue:
    """JSONL-backed priority queue with persistence and project grouping."""

    MAX_TASKS = 10_000

    def __init__(self, path: Path | None = None):
        self._path = path or Path("Sharan_workplace/tasks/queue.jsonl")
        self._tasks: dict[str, TaskEntry] = {}
        self._load()

    # ── CRUD ──────────────────────────────────────────────────────────────

    def add(self, description: str, priority: int = TaskPriority.NORMAL,
            project_id: str = "", parent_id: str = "",
            tags: list[str] | None = None,
            metadata: dict[str, Any] | None = None) -> str:
        """Add task to queue. Returns task ID."""
        if len(self._tasks) >= self.MAX_TASKS:
            self._evict_completed()

        task_id = f"t-{uuid.uuid4().hex[:10]}"
        entry = TaskEntry(
            task_id=task_id,
            description=description,
            priority=priority,
            project_id=project_id,
            parent_id=parent_id,
            tags=tags or [],
            metadata=metadata or {},
        )
        self._tasks[task_id] = entry
        self._save()
        logger.debug("Task added: {} ({})", task_id, description[:60])
        return task_id

    def add_subtask(self, parent_id: str, description: str,
                    priority: int | None = None) -> str:
        """Add a subtask linked to a parent task."""
        parent = self._tasks.get(parent_id)
        if not parent:
            raise KeyError(f"Parent task {parent_id} not found")
        return self.add(
            description=description,
            priority=priority if priority is not None else parent.priority,
            project_id=parent.project_id,
            parent_id=parent_id,
            tags=parent.tags[:],
        )

    def pop(self, project_id: str = "") -> TaskEntry | None:
        """Pop highest-priority pending task (optionally filtered by project)."""
        candidates = [
            t for t in self._tasks.values()
            if t.status == TaskStatus.PENDING
            and (not project_id or t.project_id == project_id)
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda t: (t.priority, t.created_at))
        task = candidates[0]
        task.status = TaskStatus.IN_PROGRESS
        task.started_at = time.time()
        self._save()
        return task

    def complete(self, task_id: str, result: str = "") -> bool:
        """Mark task as done."""
        task = self._tasks.get(task_id)
        if not task:
            return False
        task.status = TaskStatus.DONE
        task.completed_at = time.time()
        task.result = result
        self._save()
        return True

    def fail(self, task_id: str, error: str = "") -> bool:
        """Mark task as failed. Auto-retries if under max_retries."""
        task = self._tasks.get(task_id)
        if not task:
            return False
        task.retries += 1
        task.error = error
        if task.retries < task.max_retries:
            task.status = TaskStatus.PENDING
            logger.info("Task {} retry {}/{}", task_id, task.retries, task.max_retries)
        else:
            task.status = TaskStatus.FAILED
            logger.warning("Task {} exhausted retries", task_id)
        self._save()
        return True

    def cancel(self, task_id: str) -> bool:
        """Cancel a task."""
        task = self._tasks.get(task_id)
        if not task or task.status in (TaskStatus.DONE, TaskStatus.CANCELLED):
            return False
        task.status = TaskStatus.CANCELLED
        self._save()
        return True

    def block(self, task_id: str, reason: str = "") -> bool:
        """Mark a task as blocked."""
        task = self._tasks.get(task_id)
        if not task:
            return False
        task.status = TaskStatus.BLOCKED
        task.error = reason
        self._save()
        return True

    def unblock(self, task_id: str) -> bool:
        """Unblock a task, returning it to pending."""
        task = self._tasks.get(task_id)
        if not task or task.status != TaskStatus.BLOCKED:
            return False
        task.status = TaskStatus.PENDING
        task.error = ""
        self._save()
        return True

    def get(self, task_id: str) -> TaskEntry | None:
        return self._tasks.get(task_id)

    # ── Queries ───────────────────────────────────────────────────────────

    def list_tasks(self, status: str = "", project_id: str = "",
                   limit: int = 50) -> list[TaskEntry]:
        """List tasks with optional filters."""
        results = list(self._tasks.values())
        if status:
            results = [t for t in results if t.status == status]
        if project_id:
            results = [t for t in results if t.project_id == project_id]
        results.sort(key=lambda t: (t.priority, t.created_at))
        return results[:limit]

    def get_subtasks(self, parent_id: str) -> list[TaskEntry]:
        """Get all subtasks of a parent task."""
        return [t for t in self._tasks.values() if t.parent_id == parent_id]

    def pending_count(self, project_id: str = "") -> int:
        return len([
            t for t in self._tasks.values()
            if t.status == TaskStatus.PENDING
            and (not project_id or t.project_id == project_id)
        ])

    def get_stats(self) -> dict[str, int]:
        """Get task count by status."""
        counts: dict[str, int] = {}
        for t in self._tasks.values():
            counts[t.status] = counts.get(t.status, 0) + 1
        return counts

    def get_project_summary(self, project_id: str) -> str:
        """Human-readable project task summary."""
        tasks = [t for t in self._tasks.values() if t.project_id == project_id]
        if not tasks:
            return f"No tasks for project {project_id}"
        stats = {}
        for t in tasks:
            stats[t.status] = stats.get(t.status, 0) + 1
        lines = [f"Project {project_id}: {len(tasks)} tasks"]
        for status, count in sorted(stats.items()):
            lines.append(f"  {status}: {count}")
        return "\n".join(lines)

    # ── Persistence ───────────────────────────────────────────────────────

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            for task in self._tasks.values():
                f.write(json.dumps(task.to_dict()) + "\n")

    def _load(self):
        if not self._path.exists():
            return
        self._tasks.clear()
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                entry = TaskEntry.from_dict(d)
                self._tasks[entry.task_id] = entry
            except (json.JSONDecodeError, TypeError):
                continue
        logger.debug("Loaded {} tasks from {}", len(self._tasks), self._path)

    def _evict_completed(self):
        """Remove oldest completed/cancelled tasks when at capacity."""
        removable = [
            t for t in self._tasks.values()
            if t.status in (TaskStatus.DONE, TaskStatus.CANCELLED)
        ]
        removable.sort(key=lambda t: t.completed_at or t.created_at)
        to_remove = removable[: max(len(removable) // 2, 100)]
        for t in to_remove:
            del self._tasks[t.task_id]
        if to_remove:
            self._save()
            logger.info("Evicted {} completed tasks", len(to_remove))
