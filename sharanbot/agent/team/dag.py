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
        """Add a task node to the DAG.

        Raises ValueError if adding this task would create a cycle.
        """
        self.tasks[task.id] = task
        self._execution_order = None  # invalidate cache
        try:
            self._check_cycles()
        except ValueError:
            # Roll back on cycle
            self.tasks.pop(task.id, None)
            raise

    def remove_task(self, task_id: str) -> None:
        self.tasks.pop(task_id, None)
        # Remove from other tasks' dependencies
        for t in self.tasks.values():
            if task_id in t.depends_on:
                t.depends_on.remove(task_id)
        self._execution_order = None

    def get_ready_tasks(self) -> list[TaskNode]:
        """Return tasks whose dependencies are all resolved and that are pending.

        A dependency is resolved if it's completed, failed, or skipped.
        Failed/skipped dependencies still allow downstream tasks to proceed
        so the DAG doesn't deadlock — the downstream agent can adapt.
        """
        ready = []
        for task in self.tasks.values():
            if task.status != "pending":
                continue
            deps_met = all(
                self.tasks[dep].status in ("completed", "failed", "skipped")
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
        """Return task IDs in topological order (Kahn's algorithm).

        Raises ValueError if a cycle is detected.
        """
        if self._execution_order is not None:
            return self._execution_order

        # Build in-degree map (only count edges within known tasks)
        in_degree: dict[str, int] = {tid: 0 for tid in self.tasks}
        for task in self.tasks.values():
            for dep in task.depends_on:
                if dep in self.tasks:
                    in_degree[task.id] = in_degree.get(task.id, 0) + 1

        # Start with zero-in-degree nodes
        queue = [tid for tid, deg in in_degree.items() if deg == 0]
        order: list[str] = []

        while queue:
            tid = queue.pop(0)
            order.append(tid)
            # Reduce in-degree for tasks that depend on this one
            for task in self.tasks.values():
                if tid in task.depends_on and task.id in in_degree:
                    in_degree[task.id] -= 1
                    if in_degree[task.id] == 0:
                        queue.append(task.id)

        if len(order) != len(self.tasks):
            missing = set(self.tasks) - set(order)
            raise ValueError(
                f"Circular dependency detected involving tasks: {missing}"
            )

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
        """Create a DAG from a list of task dicts (from LLM planner).

        Raises ValueError if the plan contains invalid dependency references
        or circular dependencies.
        """
        dag = cls()
        task_ids = {item["id"] for item in plan}
        for item in plan:
            deps = item.get("depends_on", [])
            # Strip invalid dependency references
            valid_deps = [d for d in deps if d in task_ids]
            stripped = set(deps) - set(valid_deps)
            if stripped:
                import logging
                logging.getLogger(__name__).warning(
                    "Task '%s': stripped invalid dependency refs: %s",
                    item["id"], stripped,
                )
            dag.add_task(TaskNode(
                id=item["id"],
                description=item.get("description", ""),
                agent_profile=item.get("profile", "general"),
                depends_on=valid_deps,
                quality_gate=item.get("quality_gate", False),
                max_retries=item.get("max_retries", 3),
                on_fail_goto=item.get("on_fail", None),
            ))
        # Validate no circular dependencies
        dag._check_cycles()
        return dag

    def _check_cycles(self) -> None:
        """Raise ValueError if the DAG contains a cycle."""
        visited: set[str] = set()
        in_stack: set[str] = set()

        def dfs(tid: str) -> None:
            if tid in in_stack:
                raise ValueError(f"Circular dependency detected involving task '{tid}'")
            if tid in visited:
                return
            in_stack.add(tid)
            task = self.tasks.get(tid)
            if task:
                for dep in task.depends_on:
                    if dep in self.tasks:
                        dfs(dep)
            in_stack.discard(tid)
            visited.add(tid)

        for tid in self.tasks:
            dfs(tid)
