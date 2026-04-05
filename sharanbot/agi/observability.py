"""Observability — structured metrics, traces, and health monitoring.

Provides real-time insight into SharanBot's operations:

1. **Metrics** — counters for operations (tasks spawned, tools called, errors)
2. **Traces** — timestamped records of significant operations
3. **Health** — surface-level health status (ok/warn/error)
4. **Session tracking** — per-session operation history

Inspired by: agent-orchestrator/packages/core/src/observability.ts
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from loguru import logger


class HealthStatus(str, Enum):
    OK = "ok"
    WARN = "warn"
    ERROR = "error"


class MetricName(str, Enum):
    TASK_SPAWNED = "task_spawned"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TOOL_CALL = "tool_call"
    TOOL_ERROR = "tool_error"
    LLM_CALL = "llm_call"
    LLM_ERROR = "llm_error"
    TEAM_CREATED = "team_created"
    ESCALATION = "escalation"
    REACTION_TRIGGERED = "reaction_triggered"
    STUCK_DETECTED = "stuck_detected"
    RECOVERY_ATTEMPT = "recovery_attempt"


@dataclass
class MetricCounter:
    total: int = 0
    success: int = 0
    failure: int = 0
    last_at: float = 0
    last_failure_reason: str = ""

    def record(self, success: bool = True, reason: str = "") -> None:
        self.total += 1
        self.last_at = time.time()
        if success:
            self.success += 1
        else:
            self.failure += 1
            self.last_failure_reason = reason

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "success": self.success,
            "failure": self.failure,
            "success_rate": f"{self.success / self.total:.0%}" if self.total > 0 else "N/A",
            "last_at": self.last_at,
        }


@dataclass
class TraceRecord:
    timestamp: float
    component: str
    operation: str
    outcome: str  # "success" or "failure"
    session_key: str = ""
    reason: str = ""
    duration_ms: float = 0
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class HealthSurface:
    surface: str
    status: HealthStatus
    updated_at: float = field(default_factory=time.time)
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)


class Observer:
    """Central observability hub for SharanBot.

    Records metrics, traces, and health status. Persists snapshots
    to disk for dashboard consumption and crash recovery analysis.
    """

    _TRACE_LIMIT = 200
    _PERSIST_INTERVAL = 60  # seconds between disk writes

    def __init__(self, workspace: Path | None = None):
        self._workspace = workspace
        self._metrics: dict[str, MetricCounter] = defaultdict(MetricCounter)
        self._traces: list[TraceRecord] = []
        self._health: dict[str, HealthSurface] = {}
        self._session_ops: dict[str, list[str]] = defaultdict(list)  # session -> recent ops
        self._start_time = time.time()
        self._last_persist = 0

    def record(
        self,
        metric: str | MetricName,
        *,
        success: bool = True,
        component: str = "agent",
        operation: str = "",
        session_key: str = "",
        reason: str = "",
        duration_ms: float = 0,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Record a metric and optionally a trace."""
        metric_key = metric.value if isinstance(metric, MetricName) else metric
        self._metrics[metric_key].record(success, reason)

        trace = TraceRecord(
            timestamp=time.time(),
            component=component,
            operation=operation or metric_key,
            outcome="success" if success else "failure",
            session_key=session_key,
            reason=reason,
            duration_ms=duration_ms,
            data=data or {},
        )
        self._traces.append(trace)
        if len(self._traces) > self._TRACE_LIMIT:
            self._traces = self._traces[-self._TRACE_LIMIT:]

        if session_key:
            ops = self._session_ops[session_key]
            ops.append(f"{metric_key}:{'ok' if success else 'fail'}")
            if len(ops) > 50:
                self._session_ops[session_key] = ops[-50:]

        # Auto-persist periodically
        if self._workspace and time.time() - self._last_persist > self._PERSIST_INTERVAL:
            self._persist()

    def set_health(
        self,
        surface: str,
        status: HealthStatus,
        reason: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        """Set health status for a surface (e.g., 'llm_provider', 'mcp', 'browser')."""
        self._health[surface] = HealthSurface(
            surface=surface,
            status=status,
            reason=reason,
            details=details or {},
        )

    def get_summary(self) -> dict[str, Any]:
        """Get a complete observability summary."""
        uptime = time.time() - self._start_time
        overall = HealthStatus.OK
        for h in self._health.values():
            if h.status == HealthStatus.ERROR:
                overall = HealthStatus.ERROR
                break
            if h.status == HealthStatus.WARN and overall != HealthStatus.ERROR:
                overall = HealthStatus.WARN

        return {
            "uptime_s": round(uptime),
            "overall_health": overall.value,
            "metrics": {k: v.to_dict() for k, v in self._metrics.items()},
            "health": {
                k: {"status": v.status.value, "reason": v.reason}
                for k, v in self._health.items()
            },
            "recent_traces": [
                {
                    "time": t.timestamp,
                    "component": t.component,
                    "op": t.operation,
                    "outcome": t.outcome,
                    "session": t.session_key,
                }
                for t in self._traces[-20:]
            ],
            "active_sessions": len(self._session_ops),
        }

    def get_session_history(self, session_key: str) -> list[str]:
        """Get recent operations for a session."""
        return list(self._session_ops.get(session_key, []))

    def format_status(self) -> str:
        """Format a human-readable status string."""
        summary = self.get_summary()
        lines = [
            f"📊 Observability | Health: {summary['overall_health']} | Uptime: {summary['uptime_s']}s",
        ]
        for name, m in summary["metrics"].items():
            lines.append(f"  {name}: {m['total']} (✓{m['success']} ✗{m['failure']})")
        for name, h in summary["health"].items():
            icon = {"ok": "🟢", "warn": "🟡", "error": "🔴"}.get(h["status"], "⚪")
            lines.append(f"  {icon} {name}: {h['status']}" + (f" — {h['reason']}" if h["reason"] else ""))
        return "\n".join(lines)

    def _persist(self) -> None:
        """Persist snapshot to disk."""
        if not self._workspace:
            return
        try:
            obs_dir = self._workspace / ".observability"
            obs_dir.mkdir(parents=True, exist_ok=True)
            snapshot = {
                "timestamp": time.time(),
                "metrics": {k: v.to_dict() for k, v in self._metrics.items()},
                "health": {
                    k: {"status": v.status.value, "reason": v.reason}
                    for k, v in self._health.items()
                },
            }
            path = obs_dir / "snapshot.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
            tmp.replace(path)
            self._last_persist = time.time()
        except Exception as e:
            logger.debug("Observability persist failed: {}", e)
