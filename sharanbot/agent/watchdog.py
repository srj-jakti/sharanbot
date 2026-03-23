"""Auto-recovery watchdog — detects stuck states and self-heals.

Monitors the agent loop and active tasks for:
- Stuck states (no progress for configurable timeout)
- Repeated errors (same error > N times)
- Process crashes

Recovery strategy (progressive):
1. Retry the current operation (attempts 1-2)
2. Restart the component (attempts 3-4)
3. Full restart + notify user (attempt 5)
4. Mark as failed and notify (attempt 6+)
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger


@dataclass
class WatchdogState:
    """Tracks health state of a monitored component."""

    component_id: str
    last_activity: float = field(default_factory=time.time)
    consecutive_errors: int = 0
    last_error: str = ""
    last_error_time: float = 0
    stuck_count: int = 0
    recovery_attempts: int = 0
    status: str = "healthy"  # healthy, idle, degraded, recovering, failed


@dataclass
class RecoveryCheckpoint:
    """State snapshot for resume-after-restart."""

    session_key: str
    last_message: str = ""
    task_in_progress: str | None = None
    task_progress: float = 0.0
    context_summary: str = ""
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


class AutoRecoveryWatchdog:
    """Monitors agent health and triggers auto-recovery.

    Features:
    - Heartbeat monitoring: tracks last activity per component
    - Stuck detection: triggers after STUCK_TIMEOUT_S with no progress
    - Error tracking: counts consecutive errors, triggers recovery
    - Checkpoint system: saves state for resume after crashes
    - Progressive recovery: retry → skip → restart → notify user
    """

    STUCK_TIMEOUT_S: float = 600.0  # 10 minutes
    ERROR_THRESHOLD: int = 5
    MAX_RECOVERY_ATTEMPTS: int = 5
    CHECKPOINT_INTERVAL_S: float = 30.0
    WATCHDOG_INTERVAL_S: float = 30.0

    def __init__(
        self,
        workspace: Path,
        on_recovery: Callable[[str, str], Awaitable[None]] | None = None,
        on_restart_needed: Callable[[], Awaitable[None]] | None = None,
        on_notify_user: Callable[[str], Awaitable[None]] | None = None,
    ):
        self._workspace = workspace
        self._checkpoint_dir = workspace / "Sharan_workplace" / "memory" / "checkpoints"
        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self._states: dict[str, WatchdogState] = {}
        self._on_recovery = on_recovery
        self._on_restart_needed = on_restart_needed
        self._on_notify_user = on_notify_user
        self._running = False
        self._watchdog_task: asyncio.Task | None = None
        self._error_history: list[dict[str, Any]] = []
        self._recovery_log: list[dict[str, Any]] = []

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._watchdog_task = asyncio.create_task(self._monitor_loop())
        logger.info("Auto-recovery watchdog started (stuck_timeout={}s)", self.STUCK_TIMEOUT_S)

    async def stop(self) -> None:
        self._running = False
        if self._watchdog_task:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass

    def heartbeat(self, component_id: str) -> None:
        """Record activity from a component."""
        state = self._states.get(component_id)
        if state is None:
            state = WatchdogState(component_id=component_id)
            self._states[component_id] = state
        state.last_activity = time.time()
        state.status = "healthy"
        state.consecutive_errors = 0

    def mark_idle(self, component_id: str) -> None:
        """Mark a component as idle (exempt from stuck detection)."""
        state = self._states.get(component_id)
        if state is not None:
            state.status = "idle"
            state.last_activity = time.time()

    def report_error(self, component_id: str, error: str) -> None:
        """Report an error from a component."""
        state = self._states.get(component_id)
        if state is None:
            state = WatchdogState(component_id=component_id)
            self._states[component_id] = state

        state.consecutive_errors += 1
        state.last_error = error[:500]
        state.last_error_time = time.time()

        self._error_history.append({
            "component": component_id,
            "error": error[:500],
            "time": time.time(),
        })
        if len(self._error_history) > 100:
            self._error_history = self._error_history[-100:]

        if state.consecutive_errors >= self.ERROR_THRESHOLD:
            state.status = "degraded"
            logger.warning(
                "Component {} has {} consecutive errors, entering degraded state",
                component_id, state.consecutive_errors,
            )

    def report_progress(self, component_id: str, progress: float = 0) -> None:
        self.heartbeat(component_id)

    def save_checkpoint(self, checkpoint: RecoveryCheckpoint) -> None:
        """Save a recovery checkpoint to disk."""
        safe_key = checkpoint.session_key.replace(":", "_")
        path = self._checkpoint_dir / f"{safe_key}.json"
        data = {
            "session_key": checkpoint.session_key,
            "last_message": checkpoint.last_message,
            "task_in_progress": checkpoint.task_in_progress,
            "task_progress": checkpoint.task_progress,
            "context_summary": checkpoint.context_summary,
            "timestamp": checkpoint.timestamp,
            "metadata": checkpoint.metadata,
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load_checkpoint(self, session_key: str) -> RecoveryCheckpoint | None:
        safe_key = session_key.replace(":", "_")
        path = self._checkpoint_dir / f"{safe_key}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return RecoveryCheckpoint(**data)
        except Exception as exc:
            logger.debug("Failed to load checkpoint for {}: {}", session_key, exc)
            return None

    def clear_checkpoint(self, session_key: str) -> None:
        safe_key = session_key.replace(":", "_")
        path = self._checkpoint_dir / f"{safe_key}.json"
        if path.exists():
            path.unlink()

    def get_health_report(self) -> dict[str, Any]:
        now = time.time()
        components = {}
        for cid, state in self._states.items():
            idle_time = now - state.last_activity
            components[cid] = {
                "status": state.status,
                "idle_seconds": round(idle_time, 1),
                "consecutive_errors": state.consecutive_errors,
                "last_error": state.last_error or None,
                "recovery_attempts": state.recovery_attempts,
                "stuck_count": state.stuck_count,
            }
        return {
            "overall": (
                "healthy"
                if all(s.status in ("healthy", "idle") for s in self._states.values())
                else "degraded"
            ),
            "components": components,
            "total_errors": len(self._error_history),
            "total_recoveries": len(self._recovery_log),
        }

    async def _monitor_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.WATCHDOG_INTERVAL_S)
                await self._check_health()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Watchdog error: {}", exc)

    async def _check_health(self) -> None:
        now = time.time()
        for cid, state in list(self._states.items()):
            idle_time = now - state.last_activity

            if state.status == "idle":
                continue

            if state.status != "failed" and idle_time > self.STUCK_TIMEOUT_S:
                state.stuck_count += 1
                logger.warning(
                    "Component {} appears stuck (idle {:.0f}s, stuck_count={})",
                    cid, idle_time, state.stuck_count,
                )
                await self._attempt_recovery(cid, state, "stuck")

            elif (
                state.status == "degraded"
                and state.consecutive_errors >= self.ERROR_THRESHOLD
            ):
                await self._attempt_recovery(cid, state, "errors")

    async def _attempt_recovery(
        self, component_id: str, state: WatchdogState, reason: str,
    ) -> None:
        state.recovery_attempts += 1
        state.status = "recovering"

        self._recovery_log.append({
            "component": component_id,
            "reason": reason,
            "attempt": state.recovery_attempts,
            "time": time.time(),
        })

        if state.recovery_attempts > self.MAX_RECOVERY_ATTEMPTS:
            state.status = "failed"
            logger.error(
                "Component {} exceeded max recovery attempts, marking as failed",
                component_id,
            )
            if self._on_notify_user:
                await self._on_notify_user(
                    f"⚠️ Component `{component_id}` has failed after "
                    f"{self.MAX_RECOVERY_ATTEMPTS} recovery attempts. "
                    f"Last error: {state.last_error or 'stuck/unresponsive'}"
                )
            return

        logger.info(
            "Recovery attempt {} for {} (reason: {})",
            state.recovery_attempts, component_id, reason,
        )

        # Strategy 1: Retry (attempt 1-2)
        if state.recovery_attempts <= 2:
            if self._on_recovery:
                await self._on_recovery(component_id, "retry")
            state.last_activity = time.time()
            state.status = "healthy"

        # Strategy 2: Restart component (attempt 3-4)
        elif state.recovery_attempts <= 4:
            if self._on_recovery:
                await self._on_recovery(component_id, "restart")
            state.last_activity = time.time()
            state.consecutive_errors = 0
            state.status = "healthy"

        # Strategy 3: Full restart (attempt 5)
        else:
            if self._on_restart_needed:
                await self._on_restart_needed()
            if self._on_notify_user:
                await self._on_notify_user(
                    f"🔄 Auto-restarting due to persistent issues with `{component_id}`"
                )
