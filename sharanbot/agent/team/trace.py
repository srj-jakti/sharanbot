"""Execution tracing and metrics for team orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class TraceEvent:
    """A single trace event in team execution."""

    timestamp: datetime
    event_type: str  # task_start, task_complete, task_fail, handoff, quality_gate, retry
    agent_id: str
    task_id: str
    detail: str
    duration_ms: float | None = None


@dataclass
class TeamMetrics:
    """Aggregated metrics for a team execution."""

    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    total_retries: int = 0
    avg_task_duration_s: float = 0.0
    quality_gate_pass_rate: float = 0.0
    agents_used: list[str] = field(default_factory=list)
    total_duration_s: float = 0.0


class TeamTracer:
    """Zero-code execution tracing for team orchestration."""

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []
        self._start_time: datetime | None = None

    def start(self) -> None:
        self._start_time = datetime.now()

    def trace_start(self, agent_id: str, task_id: str, description: str) -> None:
        self.events.append(TraceEvent(
            timestamp=datetime.now(),
            event_type="task_start",
            agent_id=agent_id,
            task_id=task_id,
            detail=description,
        ))

    def trace_complete(self, agent_id: str, task_id: str, result_summary: str) -> None:
        # Calculate duration from start event
        start_evt = next(
            (e for e in reversed(self.events)
             if e.task_id == task_id and e.event_type == "task_start"),
            None,
        )
        duration_ms = None
        if start_evt:
            duration_ms = (datetime.now() - start_evt.timestamp).total_seconds() * 1000

        self.events.append(TraceEvent(
            timestamp=datetime.now(),
            event_type="task_complete",
            agent_id=agent_id,
            task_id=task_id,
            detail=result_summary[:200],
            duration_ms=duration_ms,
        ))

    def trace_fail(self, agent_id: str, task_id: str, error: str) -> None:
        self.events.append(TraceEvent(
            timestamp=datetime.now(),
            event_type="task_fail",
            agent_id=agent_id,
            task_id=task_id,
            detail=error[:200],
        ))

    def trace_handoff(self, from_agent: str, to_agent: str, task_id: str, context: str) -> None:
        self.events.append(TraceEvent(
            timestamp=datetime.now(),
            event_type="handoff",
            agent_id=from_agent,
            task_id=task_id,
            detail=f"→ {to_agent}: {context[:150]}",
        ))

    def trace_quality_gate(self, task_id: str, passed: bool, feedback: str) -> None:
        self.events.append(TraceEvent(
            timestamp=datetime.now(),
            event_type="quality_gate",
            agent_id="qa",
            task_id=task_id,
            detail=f"{'PASS' if passed else 'FAIL'}: {feedback[:150]}",
        ))

    def trace_retry(self, agent_id: str, task_id: str, attempt: int) -> None:
        self.events.append(TraceEvent(
            timestamp=datetime.now(),
            event_type="retry",
            agent_id=agent_id,
            task_id=task_id,
            detail=f"Retry attempt {attempt}",
        ))

    def compute_metrics(self) -> TeamMetrics:
        """Compute aggregated metrics from trace events."""
        total_duration = 0.0
        if self._start_time:
            total_duration = (datetime.now() - self._start_time).total_seconds()

        completed = [e for e in self.events if e.event_type == "task_complete"]
        failed = [e for e in self.events if e.event_type == "task_fail"]
        retries = [e for e in self.events if e.event_type == "retry"]
        qa_events = [e for e in self.events if e.event_type == "quality_gate"]

        durations = [e.duration_ms / 1000 for e in completed if e.duration_ms]
        avg_dur = sum(durations) / len(durations) if durations else 0.0

        qa_passed = sum(1 for e in qa_events if "PASS" in e.detail)
        qa_rate = qa_passed / len(qa_events) if qa_events else 1.0

        agents = list({e.agent_id for e in self.events if e.agent_id != "qa"})

        return TeamMetrics(
            total_tasks=len(completed) + len(failed),
            completed_tasks=len(completed),
            failed_tasks=len(failed),
            total_retries=len(retries),
            avg_task_duration_s=avg_dur,
            quality_gate_pass_rate=qa_rate,
            agents_used=agents,
            total_duration_s=total_duration,
        )

    def get_summary(self) -> str:
        """Human-readable execution summary."""
        m = self.compute_metrics()
        lines = [
            f"Team Execution Summary",
            f"  Tasks: {m.completed_tasks}/{m.total_tasks} completed",
            f"  Failed: {m.failed_tasks}",
            f"  Retries: {m.total_retries}",
            f"  Avg task time: {m.avg_task_duration_s:.1f}s",
            f"  Quality pass rate: {m.quality_gate_pass_rate:.0%}",
            f"  Total time: {m.total_duration_s:.1f}s",
            f"  Agents used: {', '.join(m.agents_used)}",
        ]
        return "\n".join(lines)

    def get_timeline(self) -> list[dict[str, Any]]:
        """Chronological event timeline as dicts."""
        return [
            {
                "time": e.timestamp.isoformat(),
                "type": e.event_type,
                "agent": e.agent_id,
                "task": e.task_id,
                "detail": e.detail,
                "duration_ms": e.duration_ms,
            }
            for e in self.events
        ]
