"""Parallel task engine — priority-based concurrent scheduler with chat fast-lane.

Ensures user chat responses are never starved by background work by reserving
dedicated slots for interactive messages. Supports checkpoint/resume and
task lifecycle management.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Awaitable, Callable

from loguru import logger


class TaskPriority(IntEnum):
    CRITICAL = 0
    CHAT = 1
    HIGH = 2
    NORMAL = 3
    LOW = 4


class TaskState:
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskEntry:
    id: str
    priority: TaskPriority
    coro_factory: Callable[[], Awaitable[Any]]
    session_id: str = ""
    state: str = TaskState.PENDING
    progress: float = 0.0
    checkpoint: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    _task: asyncio.Task | None = field(default=None, repr=False)


class ParallelTaskEngine:
    """Priority-based concurrent task scheduler.

    Reserves ``chat_reserved`` slots for CHAT priority tasks to ensure
    interactive responses are never blocked by background work.
    """

    def __init__(self, max_workers: int = 8, chat_reserved: int = 2):
        self._max_workers = max(max_workers, chat_reserved + 1)
        self._chat_reserved = chat_reserved
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._tasks: dict[str, TaskEntry] = {}
        self._running_count = 0
        self._running_chat_count = 0
        self._scheduler_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        logger.info("ParallelTaskEngine started (workers={}, chat_reserved={})",
                     self._max_workers, self._chat_reserved)

    async def stop(self) -> None:
        self._running = False
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
        # Cancel all running tasks
        for entry in self._tasks.values():
            if entry._task and not entry._task.done():
                entry._task.cancel()

    def submit(self, coro_factory: Callable[[], Awaitable[Any]],
               priority: TaskPriority = TaskPriority.NORMAL,
               session_id: str = "", task_id: str = "") -> str:
        """Submit a task for execution. Returns task ID."""
        tid = task_id or f"task-{uuid.uuid4().hex[:12]}"
        entry = TaskEntry(id=tid, priority=priority, coro_factory=coro_factory, session_id=session_id)
        self._tasks[tid] = entry
        self._queue.put_nowait((priority, entry.created_at, tid))
        return tid

    def submit_chat(self, coro_factory: Callable[[], Awaitable[Any]],
                    session_id: str = "") -> str:
        """Submit a chat-priority task (guaranteed fast-lane)."""
        return self.submit(coro_factory, TaskPriority.CHAT, session_id)

    def cancel_task(self, task_id: str) -> bool:
        entry = self._tasks.get(task_id)
        if not entry:
            return False
        if entry._task and not entry._task.done():
            entry._task.cancel()
        entry.state = TaskState.CANCELLED
        return True

    def cancel_session(self, session_id: str) -> int:
        """Cancel all tasks for a session."""
        count = 0
        for entry in self._tasks.values():
            if entry.session_id == session_id and entry.state in (TaskState.PENDING, TaskState.RUNNING):
                self.cancel_task(entry.id)
                count += 1
        return count

    def update_progress(self, task_id: str, progress: float) -> None:
        entry = self._tasks.get(task_id)
        if entry:
            entry.progress = min(1.0, max(0.0, progress))

    def save_checkpoint(self, task_id: str, data: dict) -> None:
        entry = self._tasks.get(task_id)
        if entry:
            entry.checkpoint = data

    def get_status(self, task_id: str) -> dict[str, Any] | None:
        entry = self._tasks.get(task_id)
        if not entry:
            return None
        return {
            "id": entry.id, "state": entry.state, "priority": entry.priority.name,
            "progress": entry.progress, "error": entry.error,
        }

    async def _scheduler_loop(self) -> None:
        """Pull from priority queue and run tasks, respecting slot limits."""
        while self._running:
            try:
                # Clean up old completed tasks (older than 1 hour)
                self._cleanup_old_tasks()

                # Check if we can run more tasks
                available = self._max_workers - self._running_count
                if available <= 0:
                    await asyncio.sleep(0.1)
                    continue

                try:
                    priority, _, tid = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                entry = self._tasks.get(tid)
                if not entry or entry.state == TaskState.CANCELLED:
                    continue

                # Enforce chat reservation: non-chat tasks can't use reserved slots
                is_chat = entry.priority <= TaskPriority.CHAT
                non_chat_slots = self._max_workers - self._chat_reserved
                non_chat_running = self._running_count - self._running_chat_count

                if not is_chat and non_chat_running >= non_chat_slots:
                    # Re-queue — no slots available for non-chat tasks
                    self._queue.put_nowait((priority, entry.created_at, tid))
                    await asyncio.sleep(0.1)
                    continue

                # Launch the task
                entry.state = TaskState.RUNNING
                entry.started_at = time.time()
                self._running_count += 1
                if is_chat:
                    self._running_chat_count += 1
                entry._task = asyncio.create_task(self._run_task(entry, is_chat))

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Scheduler error: {}", e)
                await asyncio.sleep(1)

    async def _run_task(self, entry: TaskEntry, is_chat: bool) -> None:
        """Execute a task with error handling."""
        try:
            entry.result = await entry.coro_factory()
            entry.state = TaskState.COMPLETED
            entry.progress = 1.0
        except asyncio.CancelledError:
            entry.state = TaskState.CANCELLED
        except Exception as e:
            entry.state = TaskState.FAILED
            entry.error = str(e)[:500]
            logger.warning("Task {} failed: {}", entry.id, e)
        finally:
            entry.completed_at = time.time()
            self._running_count -= 1
            if is_chat:
                self._running_chat_count -= 1

    def _cleanup_old_tasks(self) -> None:
        cutoff = time.time() - 3600
        to_remove = [
            tid for tid, entry in self._tasks.items()
            if entry.state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED)
            and entry.completed_at and entry.completed_at < cutoff
        ]
        for tid in to_remove:
            del self._tasks[tid]
