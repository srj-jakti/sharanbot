"""Event-Driven Reaction System — inspired by agent-orchestrator's lifecycle manager.

When events happen (task failed, stuck, CI failed, review requested, etc.),
the reaction engine automatically triggers configurable responses:

1. **send-to-agent** — Send a message/instruction to the working agent
2. **notify** — Notify the user via their active channel
3. **escalate** — Escalate to human after N retries or timeout
4. **retry** — Retry the failed operation
5. **pause** — Pause all new task spawns (rate limit cooldown)

Each reaction has:
- max retries before escalation
- escalation timeout
- priority level (urgent/action/warning/info)
- configurable message templates

Inspired by: agent-orchestrator/packages/core/src/lifecycle-manager.ts
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine

from loguru import logger


class EventType(str, Enum):
    """Events that can trigger reactions."""
    TASK_FAILED = "task.failed"
    TASK_STUCK = "task.stuck"
    TASK_COMPLETED = "task.completed"
    CI_FAILED = "ci.failed"
    REVIEW_REQUESTED = "review.requested"
    REVIEW_CHANGES = "review.changes_requested"
    AGENT_IDLE = "agent.idle"
    AGENT_ERROR = "agent.error"
    AGENT_KILLED = "agent.killed"
    RATE_LIMITED = "rate.limited"
    ESCALATION_TIMEOUT = "escalation.timeout"
    TEAM_DEADLOCK = "team.deadlock"
    QUALITY_GATE_FAILED = "quality.gate_failed"


class EventPriority(str, Enum):
    """Priority levels for notification routing."""
    URGENT = "urgent"      # Needs immediate attention
    ACTION = "action"      # Requires user action soon
    WARNING = "warning"    # Something went wrong but auto-handling
    INFO = "info"          # FYI — no action needed


class ReactionAction(str, Enum):
    """What to do when an event fires."""
    SEND_TO_AGENT = "send-to-agent"
    NOTIFY = "notify"
    RETRY = "retry"
    PAUSE = "pause"
    ESCALATE = "escalate"



@dataclass
class ReactionConfig:
    """Configuration for a single reaction."""
    action: ReactionAction = ReactionAction.NOTIFY
    message: str = ""
    priority: EventPriority = EventPriority.INFO
    retries: int = 3
    escalate_after: int | float = 300  # seconds or retry count
    auto: bool = True  # False = skip automated action, only notify

    @classmethod
    def from_dict(cls, data: dict) -> ReactionConfig:
        return cls(
            action=ReactionAction(data.get("action", "notify")),
            message=data.get("message", ""),
            priority=EventPriority(data.get("priority", "info")),
            retries=data.get("retries", 3),
            escalate_after=data.get("escalate_after", 300),
            auto=data.get("auto", True),
        )


@dataclass
class OrchestratorEvent:
    """An event emitted by the system."""
    id: str
    type: EventType
    priority: EventPriority
    message: str
    timestamp: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)
    session_key: str = ""
    task_id: str = ""


@dataclass
class ReactionResult:
    """Result of executing a reaction."""
    reaction_type: str
    success: bool
    action: str
    message: str = ""
    escalated: bool = False


@dataclass
class ReactionTracker:
    """Track retry attempts for a specific reaction."""
    attempts: int = 0
    first_triggered: float = field(default_factory=time.time)
    last_triggered: float = field(default_factory=time.time)


# Default reaction configurations
DEFAULT_REACTIONS: dict[str, ReactionConfig] = {
    "task.failed": ReactionConfig(
        action=ReactionAction.RETRY,
        message="Task failed. Retrying with a different approach.",
        priority=EventPriority.WARNING,
        retries=2,
        escalate_after=3,
    ),
    "task.stuck": ReactionConfig(
        action=ReactionAction.SEND_TO_AGENT,
        message="You appear stuck. Try a fundamentally different approach.",
        priority=EventPriority.WARNING,
        retries=2,
        escalate_after=2,
    ),
    "ci.failed": ReactionConfig(
        action=ReactionAction.SEND_TO_AGENT,
        message="CI checks failed. Fix the failures and push again.",
        priority=EventPriority.ACTION,
        retries=3,
        escalate_after=300,
    ),
    "agent.error": ReactionConfig(
        action=ReactionAction.RETRY,
        message="Agent encountered an error.",
        priority=EventPriority.WARNING,
        retries=3,
        escalate_after=5,
    ),
    "rate.limited": ReactionConfig(
        action=ReactionAction.PAUSE,
        message="Rate limit detected. Pausing new task spawns.",
        priority=EventPriority.URGENT,
        retries=0,
        escalate_after=600,
    ),
    "team.deadlock": ReactionConfig(
        action=ReactionAction.NOTIFY,
        message="Team DAG deadlock detected.",
        priority=EventPriority.URGENT,
        retries=0,
    ),
    "quality.gate_failed": ReactionConfig(
        action=ReactionAction.NOTIFY,
        message="Quality gate failed — the team orchestrator will handle retries internally.",
        priority=EventPriority.WARNING,
        retries=0,
        escalate_after=3,
    ),
}


def infer_priority(event_type: EventType) -> EventPriority:
    """Infer a reasonable priority from event type."""
    t = event_type.value
    if "stuck" in t or "error" in t or "killed" in t or "deadlock" in t:
        return EventPriority.URGENT
    if "failed" in t or "changes" in t:
        return EventPriority.WARNING
    if "completed" in t:
        return EventPriority.INFO
    if "review" in t or "rate" in t:
        return EventPriority.ACTION
    return EventPriority.INFO


class ReactionEngine:
    """Event-driven reaction engine.

    Monitors events and triggers configurable reactions with retry logic
    and automatic escalation to human when auto-handling fails.

    Usage:
        engine = ReactionEngine(on_notify=my_notify_fn)
        engine.configure("ci.failed", ReactionConfig(action="send-to-agent", retries=3))
        result = await engine.handle_event(event)
    """

    def __init__(
        self,
        on_notify: Callable[[OrchestratorEvent], Coroutine[Any, Any, None]] | None = None,
        on_send_to_agent: Callable[[str, str], Coroutine[Any, Any, bool]] | None = None,
        reactions: dict[str, ReactionConfig] | None = None,
        is_autopilot_active: Callable[[], bool] | None = None,
        on_evolve: Callable[[str], Coroutine[Any, Any, Any]] | None = None,
    ):
        self._on_notify = on_notify
        self._on_send_to_agent = on_send_to_agent
        self._is_autopilot_active = is_autopilot_active
        self._on_evolve = on_evolve  # Callback to trigger self-evolution
        self._reactions = dict(DEFAULT_REACTIONS)
        if reactions:
            for key, cfg in reactions.items():
                self._reactions[key] = cfg
        self._trackers: dict[str, ReactionTracker] = {}
        self._event_log: list[OrchestratorEvent] = []
        self._paused = False
        self._pause_until: float = 0
        self._stats = {
            "total_events": 0,
            "reactions_triggered": 0,
            "escalations": 0,
            "retries": 0,
            "suppressed": 0,
            "by_type": {},
        }

    @property
    def is_paused(self) -> bool:
        """Check if new task spawns are paused (rate limit cooldown)."""
        if self._paused and time.time() < self._pause_until:
            return True
        if self._paused and time.time() >= self._pause_until:
            self._paused = False
            logger.info("Global pause lifted (cooldown expired)")
        return False

    def configure(self, event_type: str, config: ReactionConfig) -> None:
        """Configure or override a reaction for an event type."""
        self._reactions[event_type] = config

    def configure_from_dict(self, reactions: dict[str, dict]) -> None:
        """Load reaction configs from a dictionary (e.g., from config file)."""
        for key, data in reactions.items():
            self._reactions[key] = ReactionConfig.from_dict(data)

    async def emit(
        self,
        event_type: EventType,
        message: str,
        *,
        session_key: str = "",
        task_id: str = "",
        data: dict[str, Any] | None = None,
    ) -> ReactionResult | None:
        """Emit an event and trigger the configured reaction."""
        event = OrchestratorEvent(
            id=f"evt_{int(time.time())}_{self._stats['total_events']}",
            type=event_type,
            priority=infer_priority(event_type),
            message=message,
            session_key=session_key,
            task_id=task_id,
            data=data or {},
        )
        return await self.handle_event(event)

    async def handle_event(self, event: OrchestratorEvent) -> ReactionResult | None:
        """Process an event and execute the configured reaction."""
        self._stats["total_events"] += 1
        type_key = event.type.value
        self._stats["by_type"][type_key] = self._stats["by_type"].get(type_key, 0) + 1
        self._event_log.append(event)
        if len(self._event_log) > 500:
            self._event_log = self._event_log[-500:]

        logger.info("Reaction engine: event {} (priority={})", type_key, event.priority.value)

        # Suppress quality gate and non-critical events during active autopilot
        # The autopilot handles its own quality gates within the team orchestrator.
        _suppress_during_autopilot = {
            EventType.QUALITY_GATE_FAILED.value,
            EventType.TASK_COMPLETED.value,
        }
        if type_key in _suppress_during_autopilot and self._is_autopilot_active and self._is_autopilot_active():
            logger.info("Reaction engine: suppressing {} during active autopilot", type_key)
            self._stats["suppressed"] = self._stats.get("suppressed", 0) + 1
            return ReactionResult(
                reaction_type=type_key,
                success=True,
                action="suppressed",
                message="Suppressed during active autopilot",
            )

        config = self._reactions.get(type_key)
        if not config:
            logger.debug("No reaction configured for {}", type_key)
            return None

        # Override event priority with config priority
        event.priority = config.priority

        return await self._execute_reaction(event, config)

    async def _execute_reaction(
        self, event: OrchestratorEvent, config: ReactionConfig,
    ) -> ReactionResult:
        """Execute a reaction with retry tracking and escalation logic."""
        tracker_key = f"{event.session_key}:{event.type.value}"
        tracker = self._trackers.get(tracker_key)
        if not tracker:
            tracker = ReactionTracker()
            self._trackers[tracker_key] = tracker

        tracker.attempts += 1
        tracker.last_triggered = time.time()
        self._stats["reactions_triggered"] += 1

        # Check if we should escalate
        should_escalate = False
        if isinstance(config.escalate_after, (int, float)):
            if config.escalate_after <= 10:
                # Treat as retry count
                if tracker.attempts > config.escalate_after:
                    should_escalate = True
            else:
                # Treat as seconds
                elapsed = time.time() - tracker.first_triggered
                if elapsed > config.escalate_after:
                    should_escalate = True

        if tracker.attempts > config.retries and config.retries > 0:
            should_escalate = True

        if should_escalate:
            self._stats["escalations"] += 1
            logger.warning(
                "Reaction '{}' escalated after {} attempts for {}",
                event.type.value, tracker.attempts, event.session_key,
            )

            # Trigger self-evolution on escalation — the system couldn't fix it automatically
            if self._on_evolve:
                try:
                    asyncio.create_task(self._on_evolve(
                        f"Repeated failure escalated: {event.type.value} — {event.message[:300]}. "
                        f"Auto-handling failed after {tracker.attempts} attempts. "
                        f"Diagnose root cause and propose a code fix."
                    ))
                    logger.info("Evolution triggered from escalation: {}", event.type.value)
                except Exception as e:
                    logger.debug("Evolution trigger failed: {}", e)

            if self._on_notify:
                escalation_event = OrchestratorEvent(
                    id=f"esc_{event.id}",
                    type=event.type,
                    priority=EventPriority.URGENT,
                    message=f"⚠️ Auto-handling failed after {tracker.attempts} attempts: {event.message}",
                    session_key=event.session_key,
                    task_id=event.task_id,
                    data=event.data,
                )
                try:
                    await self._on_notify(escalation_event)
                except Exception as e:
                    logger.error("Escalation notification failed: {}", e)
            self._clear_tracker(tracker_key)
            return ReactionResult(
                reaction_type=event.type.value,
                success=True,
                action="escalated",
                escalated=True,
                message=f"Escalated after {tracker.attempts} attempts",
            )

        # Execute the reaction action
        if not config.auto:
            # auto=False: only notify, skip automated action
            if self._on_notify:
                try:
                    await self._on_notify(event)
                except Exception as e:
                    logger.error("Notification failed: {}", e)
            return ReactionResult(
                reaction_type=event.type.value,
                success=True,
                action="notify",
                message="Notification sent (auto=False)",
            )

        action = config.action

        if action == ReactionAction.SEND_TO_AGENT:
            msg = config.message or event.message
            if self._on_send_to_agent:
                try:
                    success = await self._on_send_to_agent(event.session_key, msg)
                    return ReactionResult(
                        reaction_type=event.type.value,
                        success=success,
                        action="send-to-agent",
                        message=msg,
                    )
                except Exception as e:
                    logger.error("Send-to-agent failed: {}", e)
                    return ReactionResult(
                        reaction_type=event.type.value,
                        success=False,
                        action="send-to-agent",
                        message=str(e),
                    )
            # No callback — fall through to notify
            action = ReactionAction.NOTIFY

        if action == ReactionAction.RETRY:
            self._stats["retries"] += 1
            return ReactionResult(
                reaction_type=event.type.value,
                success=True,
                action="retry",
                message=f"Retry #{tracker.attempts}",
            )

        if action == ReactionAction.PAUSE:
            cooldown = config.escalate_after if isinstance(config.escalate_after, (int, float)) and config.escalate_after > 10 else 300
            self._paused = True
            self._pause_until = time.time() + cooldown
            logger.warning("Global pause activated for {}s", cooldown)
            if self._on_notify:
                try:
                    await self._on_notify(event)
                except Exception:
                    pass
            return ReactionResult(
                reaction_type=event.type.value,
                success=True,
                action="pause",
                message=f"Paused for {cooldown}s",
            )

        # Default: notify
        if self._on_notify:
            try:
                await self._on_notify(event)
            except Exception as e:
                logger.error("Notification failed: {}", e)
        return ReactionResult(
            reaction_type=event.type.value,
            success=True,
            action="notify",
            message=config.message or event.message,
        )

    def clear_tracker(self, session_key: str, event_type: str) -> None:
        """Clear retry tracker when state changes (e.g., task succeeded after retries)."""
        self._clear_tracker(f"{session_key}:{event_type}")

    def _clear_tracker(self, key: str) -> None:
        self._trackers.pop(key, None)

    def clear_all_trackers(self, session_key: str) -> None:
        """Clear all trackers for a session (e.g., on task completion)."""
        to_remove = [k for k in self._trackers if k.startswith(f"{session_key}:")]
        for k in to_remove:
            del self._trackers[k]

    def get_recent_events(self, n: int = 20) -> list[OrchestratorEvent]:
        return self._event_log[-n:]

    def get_stats(self) -> dict[str, Any]:
        return {
            **self._stats,
            "active_trackers": len(self._trackers),
            "paused": self.is_paused,
        }
