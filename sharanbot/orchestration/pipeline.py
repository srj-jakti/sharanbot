"""Pipeline state machine — 7-phase software development lifecycle.

Phases: Discovery → Architecture & Design → Sprint Planning →
Development → QA & Security → Deployment → Post-Launch.

Supports strict phase ordering, skip/retry, serialization for
crash recovery, and pipeline re-entry for iterative development.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Phase(str, Enum):
    DISCOVERY = "discovery"
    ARCHITECTURE = "architecture"
    SPRINT_PLANNING = "sprint_planning"
    DEVELOPMENT = "development"
    QA_SECURITY = "qa_security"
    DEPLOYMENT = "deployment"
    POST_LAUNCH = "post_launch"


class PhaseStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


# Ordered list of phases
PHASE_ORDER = list(Phase)

MAX_RETRIES = 2


@dataclass
class PhaseState:
    phase: Phase
    status: PhaseStatus = PhaseStatus.PENDING
    retries: int = 0
    failure_reason: str = ""
    started_at: float | None = None
    completed_at: float | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)


class PipelineStateMachine:
    """State machine managing a 7-phase development lifecycle."""

    def __init__(self, skip_phases: list[Phase] | None = None):
        self._phases: dict[Phase, PhaseState] = {}
        skip = set(skip_phases or [])

        for phase in PHASE_ORDER:
            status = PhaseStatus.SKIPPED if phase in skip else PhaseStatus.PENDING
            self._phases[phase] = PhaseState(phase=phase, status=status)

        # Start first non-skipped phase
        for phase in PHASE_ORDER:
            if self._phases[phase].status == PhaseStatus.PENDING:
                self._phases[phase].status = PhaseStatus.IN_PROGRESS
                self._phases[phase].started_at = time.time()
                break

    @property
    def current_phase(self) -> Phase | None:
        for phase in PHASE_ORDER:
            if self._phases[phase].status == PhaseStatus.IN_PROGRESS:
                return phase
        return None

    @property
    def is_complete(self) -> bool:
        return all(
            s.status in (PhaseStatus.COMPLETED, PhaseStatus.SKIPPED)
            for s in self._phases.values()
        )

    def advance(self) -> Phase | None:
        """Move to the next non-skipped phase. Returns new phase or None if done."""
        current = self.current_phase
        if not current:
            return None

        state = self._phases[current]
        if state.status != PhaseStatus.COMPLETED:
            raise ValueError(f"Cannot advance: {current} is {state.status}, not completed")

        # Find next non-skipped phase
        idx = PHASE_ORDER.index(current)
        for phase in PHASE_ORDER[idx + 1:]:
            ps = self._phases[phase]
            if ps.status != PhaseStatus.SKIPPED:
                ps.status = PhaseStatus.IN_PROGRESS
                ps.started_at = time.time()
                return phase
        return None

    def complete_current(self, artifacts: dict[str, Any] | None = None) -> None:
        current = self.current_phase
        if not current:
            return
        state = self._phases[current]
        state.status = PhaseStatus.COMPLETED
        state.completed_at = time.time()
        if artifacts:
            state.artifacts.update(artifacts)

    def fail_current(self, reason: str) -> bool:
        """Mark current phase as failed. Returns True if can retry."""
        current = self.current_phase
        if not current:
            return False
        state = self._phases[current]
        state.failure_reason = reason
        if state.retries < MAX_RETRIES:
            state.retries += 1
            return True
        state.status = PhaseStatus.FAILED
        return False

    def retry_current(self) -> None:
        current = self.current_phase
        if current:
            state = self._phases[current]
            state.failure_reason = ""
            state.started_at = time.time()

    def reset_from_phase(self, phase: Phase) -> None:
        """Re-entry: reset all phases from a given point onward."""
        idx = PHASE_ORDER.index(phase)
        for p in PHASE_ORDER[idx:]:
            state = self._phases[p]
            if state.status != PhaseStatus.SKIPPED:
                state.status = PhaseStatus.PENDING
                state.retries = 0
                state.failure_reason = ""
                state.started_at = None
                state.completed_at = None
                state.artifacts = {}

        # Start the reset phase
        self._phases[phase].status = PhaseStatus.IN_PROGRESS
        self._phases[phase].started_at = time.time()

    def get_progress(self) -> dict[str, Any]:
        total = len([p for p in self._phases.values() if p.status != PhaseStatus.SKIPPED])
        done = len([p for p in self._phases.values() if p.status == PhaseStatus.COMPLETED])
        return {
            "pct_complete": (done / total * 100) if total > 0 else 0,
            "current": self.current_phase,
            "phases": {
                p.value: {
                    "status": self._phases[p].status.value,
                    "retries": self._phases[p].retries,
                }
                for p in PHASE_ORDER
            },
        }

    def serialize(self) -> dict[str, Any]:
        return {
            p.value: {
                "status": s.status.value,
                "retries": s.retries,
                "failure_reason": s.failure_reason,
                "started_at": s.started_at,
                "completed_at": s.completed_at,
                "artifacts": s.artifacts,
            }
            for p, s in self._phases.items()
        }

    @classmethod
    def deserialize(cls, data: dict[str, Any]) -> PipelineStateMachine:
        instance = cls.__new__(cls)
        instance._phases = {}
        for phase in PHASE_ORDER:
            pdata = data.get(phase.value, {})
            instance._phases[phase] = PhaseState(
                phase=phase,
                status=PhaseStatus(pdata.get("status", "pending")),
                retries=pdata.get("retries", 0),
                failure_reason=pdata.get("failure_reason", ""),
                started_at=pdata.get("started_at"),
                completed_at=pdata.get("completed_at"),
                artifacts=pdata.get("artifacts", {}),
            )
        return instance
