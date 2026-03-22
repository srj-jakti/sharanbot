"""Task DAG — directed acyclic graph for task decomposition and execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class TaskNode:
    """A single task in the DAG."""

    id: str
    description: str
    agent_profile: str  # which profile should handle this
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"  # pending, running, completed, failed, retrying, skipped
    result: str | None = None
    error: str | None = None
    retry_count: int = 0
    max_retries: int = 3
    agent_id: str | None = None  # assigned subagent/worker id
    started_at: datetime | None = None
    completed_at: datetime | None = None
    quality_gate: bool = False  # requires QA before proceeding
    on_fail_goto: str | None = None  # task to retry on QA failure
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float | None:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None


class TaskDAG:
    """Directed acyclic graph of tasks with dependency tracking."""

    def __init__(self) -> None:
        self.tasks: dict[str, TaskNode] = {}
        self._execution_order: list[str] | None = None

    def add_task(self, task: TaskNode) -> None:
        """Add a task node to the DAG."""
        self.tasks[task.id] = task
        self._execution_order = None  # invalidate cache

    def remove_task(self, task_id: str) -> None:
        self.tasks.pop(task_id, None)
        # Remove from other tasks' dependencies
        for t in self.tasks.values():
            if task_id in t.depends_on:
                t.depends_on.remove(task_id)
        self._execution_order = None

    def get_ready_tasks(self) -> list[TaskNode]:
        """Return tasks whose dependencies are all completed and that are pending."""
        ready = []
        for task in self.tasks.values():
            if task.status != "pending":
                continue
            deps_met = all(
                self.tasks[dep].status == "completed"
                for dep in task.depends_on
                if dep in self.tasks
            )
            if deps_met:
                ready.append(task)
        return ready

    def mark_running(self, task_id: str, agent_id: str | None = None) -> None:
        if task_id in self.tasks:
            self.tasks[task_id].status = "running"
            self.tasks[task_id].started_at = datetime.now()
            if agent_id:
                self.tasks[task_id].agent_id = agent_id

    def mark_completed(self, task_id: str, result: str) -> None:
        if task_id in self.tasks:
            self.tasks[task_id].status = "completed"
            self.tasks[task_id].result = result
            self.tasks[task_id].completed_at = datetime.now()

    def mark_failed(self, task_id: str, error: str) -> None:
        if task_id in self.tasks:
            t = self.tasks[task_id]
            t.retry_count += 1
            if t.retry_count >= t.max_retries:
                t.status = "failed"
                t.error = error
                t.completed_at = datetime.now()
            else:
                t.status = "retrying"
                t.error = error

    def reset_for_retry(self, task_id: str) -> None:
        """Reset a retrying task back to pending so it can be re-processed."""
        if task_id in self.tasks and self.tasks[task_id].status == "retrying":
            self.tasks[task_id].status = "pending"
            self.tasks[task_id].agent_id = None

    def is_complete(self) -> bool:
        """True if all tasks are completed or failed (no pending/running)."""
        return all(t.status in ("completed", "failed", "skipped") for t in self.tasks.values())

    def has_failures(self) -> bool:
        return any(t.status == "failed" for t in self.tasks.values())

    def get_progress(self) -> dict[str, int]:
        counts = {"total": 0, "pending": 0, "running": 0, "completed": 0,
                  "failed": 0, "retrying": 0, "skipped": 0}
        for t in self.tasks.values():
            counts["total"] += 1
            counts[t.status] = counts.get(t.status, 0) + 1
        return counts

    def get_task(self, task_id: str) -> TaskNode | None:
        return self.tasks.get(task_id)

    def topological_order(self) -> list[str]:
        """Return task IDs in topological order."""
        if self._execution_order is not None:
            return self._execution_order

        visited: set[str] = set()
        order: list[str] = []

        def visit(tid: str) -> None:
            if tid in visited:
                return
            visited.add(tid)
            task = self.tasks.get(tid)
            if task:
                for dep in task.depends_on:
                    if dep in self.tasks:
                        visit(dep)
                order.append(tid)

        for tid in self.tasks:
            visit(tid)

        self._execution_order = order
        return order

    def to_summary(self) -> str:
        """Human-readable DAG summary."""
        progress = self.get_progress()
        lines = [
            f"Tasks: {progress['total']} total | "
            f"{progress['completed']} done | {progress['running']} running | "
            f"{progress['pending']} pending | {progress['failed']} failed",
            "",
        ]
        for tid in self.topological_order():
            t = self.tasks[tid]
            status_icon = {
                "pending": "⏳", "running": "🔄", "completed": "✅",
                "failed": "❌", "retrying": "🔁", "skipped": "⏭️",
            }.get(t.status, "❓")
            deps = f" (after: {', '.join(t.depends_on)})" if t.depends_on else ""
            lines.append(f"  {status_icon} [{t.id}] {t.description} [{t.agent_profile}]{deps}")
            if t.result and t.status == "completed":
                lines.append(f"      Result: {t.result[:120]}...")
            if t.error:
                lines.append(f"      Error: {t.error[:120]}...")
        return "\n".join(lines)

    @classmethod
    def from_plan(cls, plan: list[dict]) -> "TaskDAG":
        """Create a DAG from a list of task dicts (from LLM planner)."""
        dag = cls()
        for item in plan:
            dag.add_task(TaskNode(
                id=item["id"],
                description=item.get("description", ""),
                agent_profile=item.get("profile", "general"),
                depends_on=item.get("depends_on", []),
                quality_gate=item.get("quality_gate", False),
                max_retries=item.get("max_retries", 3),
                on_fail_goto=item.get("on_fail", None),
            ))
        return dag
