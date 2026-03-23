"""Worker health monitor — heartbeat-based crash detection.

Each worker sends periodic heartbeats. If a worker misses 3 consecutive
heartbeats (30 s each = 90 s), it's considered crashed and can be restarted
or replaced.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from loguru import logger


@dataclass
class WorkerStatus:
    worker_id: str
    worker_type: str = ""
    last_heartbeat: float = field(default_factory=time.time)
    missed_heartbeats: int = 0
    status: str = "running"  # running, degraded, crashed, stopped
    current_task: str | None = None
    started_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


class WorkerHealthMonitor:
    """Monitors worker health via heartbeats.

    Parameters
    ----------
    heartbeat_interval_s:
        Expected interval between worker heartbeats.
    max_missed:
        Number of missed heartbeats before a worker is considered crashed.
    on_worker_crash:
        Callback fired when a worker is detected as crashed.
    """

    def __init__(
        self,
        heartbeat_interval_s: float = 30.0,
        max_missed: int = 3,
        on_worker_crash: Callable[[str], Awaitable[None]] | None = None,
    ):
        self._interval = heartbeat_interval_s
        self._max_missed = max_missed
        self._on_crash = on_worker_crash
        self._workers: dict[str, WorkerStatus] = {}
        self._running = False
        self._monitor_task: asyncio.Task | None = None

    def register_worker(
        self, worker_id: str, worker_type: str = "", **metadata: Any,
    ) -> None:
        """Register a worker for health monitoring."""
        self._workers[worker_id] = WorkerStatus(
            worker_id=worker_id,
            worker_type=worker_type,
            metadata=metadata,
        )
        logger.debug("Registered worker for monitoring: {}", worker_id)

    def unregister_worker(self, worker_id: str) -> None:
        self._workers.pop(worker_id, None)

    def heartbeat(self, worker_id: str, task: str | None = None) -> None:
        """Record a heartbeat from a worker."""
        ws = self._workers.get(worker_id)
        if ws is None:
            self.register_worker(worker_id)
            ws = self._workers[worker_id]
        ws.last_heartbeat = time.time()
        ws.missed_heartbeats = 0
        ws.current_task = task
        if ws.status in ("degraded", "crashed"):
            ws.status = "running"
            logger.info("Worker {} recovered", worker_id)

    def mark_stopped(self, worker_id: str) -> None:
        ws = self._workers.get(worker_id)
        if ws:
            ws.status = "stopped"

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        self._monitor_task.add_done_callback(self._log_task_exception)
        logger.info("Worker health monitor started (interval={}s, max_missed={})",
                     self._interval, self._max_missed)

    @staticmethod
    def _log_task_exception(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("Worker health monitor task failed: {}", exc)

    async def stop(self) -> None:
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

    async def _monitor_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._interval)
                await self._check_workers()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Health monitor error: {}", exc)

    async def _check_workers(self) -> None:
        now = time.time()
        for wid, ws in list(self._workers.items()):
            if ws.status == "stopped":
                continue

            elapsed = now - ws.last_heartbeat
            missed = int(elapsed / self._interval)

            if missed > ws.missed_heartbeats:
                ws.missed_heartbeats = missed

            if ws.missed_heartbeats >= self._max_missed and ws.status != "crashed":
                ws.status = "crashed"
                logger.warning("Worker {} crashed (missed {} heartbeats)",
                               wid, ws.missed_heartbeats)
                if self._on_crash:
                    await self._on_crash(wid)
            elif ws.missed_heartbeats >= 1 and ws.status == "running":
                ws.status = "degraded"

    def get_status(self) -> dict[str, dict[str, Any]]:
        now = time.time()
        return {
            wid: {
                "status": ws.status,
                "type": ws.worker_type,
                "idle_s": round(now - ws.last_heartbeat, 1),
                "missed_heartbeats": ws.missed_heartbeats,
                "current_task": ws.current_task,
                "uptime_s": round(now - ws.started_at, 1),
            }
            for wid, ws in self._workers.items()
        }

    def get_healthy_workers(self) -> list[str]:
        return [wid for wid, ws in self._workers.items() if ws.status == "running"]

    def get_crashed_workers(self) -> list[str]:
        return [wid for wid, ws in self._workers.items() if ws.status == "crashed"]
