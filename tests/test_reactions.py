"""Tests for the event-driven reaction engine."""

import asyncio
import pytest
import time

from sharanbot.agi.reactions import (
    ReactionEngine,
    ReactionConfig,
    ReactionAction,
    EventType,
    EventPriority,
    OrchestratorEvent,
    ReactionResult,
    DEFAULT_REACTIONS,
    infer_priority,
)


# ── Priority inference ──

def test_infer_priority_urgent():
    assert infer_priority(EventType.TASK_STUCK) == EventPriority.URGENT
    assert infer_priority(EventType.AGENT_ERROR) == EventPriority.URGENT
    assert infer_priority(EventType.TEAM_DEADLOCK) == EventPriority.URGENT

def test_infer_priority_warning():
    assert infer_priority(EventType.TASK_FAILED) == EventPriority.WARNING
    assert infer_priority(EventType.CI_FAILED) == EventPriority.WARNING

def test_infer_priority_info():
    assert infer_priority(EventType.TASK_COMPLETED) == EventPriority.INFO

def test_infer_priority_action():
    assert infer_priority(EventType.RATE_LIMITED) == EventPriority.ACTION
    assert infer_priority(EventType.REVIEW_REQUESTED) == EventPriority.ACTION


# ── ReactionConfig ──

def test_reaction_config_from_dict():
    cfg = ReactionConfig.from_dict({
        "action": "send-to-agent",
        "message": "Fix CI",
        "priority": "warning",
        "retries": 5,
        "escalate_after": 600,
        "auto": True,
    })
    assert cfg.action == ReactionAction.SEND_TO_AGENT
    assert cfg.message == "Fix CI"
    assert cfg.priority == EventPriority.WARNING
    assert cfg.retries == 5
    assert cfg.escalate_after == 600

def test_reaction_config_defaults():
    cfg = ReactionConfig.from_dict({})
    assert cfg.action == ReactionAction.NOTIFY
    assert cfg.retries == 3
    assert cfg.auto is True


# ── Default reactions ──

def test_default_reactions_exist():
    assert "task.failed" in DEFAULT_REACTIONS
    assert "task.stuck" in DEFAULT_REACTIONS
    assert "ci.failed" in DEFAULT_REACTIONS
    assert "rate.limited" in DEFAULT_REACTIONS
    assert "team.deadlock" in DEFAULT_REACTIONS
    assert "quality.gate_failed" in DEFAULT_REACTIONS


# ── ReactionEngine ──

@pytest.fixture
def engine():
    return ReactionEngine()


@pytest.mark.asyncio
async def test_engine_emit_unknown_event(engine):
    """Unknown event types return None (no reaction configured)."""
    # Create a custom event type not in defaults
    result = await engine.handle_event(OrchestratorEvent(
        id="test",
        type=EventType.REVIEW_REQUESTED,
        priority=EventPriority.INFO,
        message="test",
    ))
    # REVIEW_REQUESTED is not in DEFAULT_REACTIONS
    assert result is None


@pytest.mark.asyncio
async def test_engine_emit_task_stuck():
    """Task stuck event triggers send-to-agent reaction."""
    sent_messages = []

    async def mock_send(session_key, msg):
        sent_messages.append((session_key, msg))
        return True

    engine = ReactionEngine(on_send_to_agent=mock_send)
    result = await engine.emit(
        EventType.TASK_STUCK,
        "Agent stuck: repeated tool calls",
        session_key="cli:direct",
    )
    assert result is not None
    assert result.action == "send-to-agent"
    assert result.success is True
    assert len(sent_messages) == 1


@pytest.mark.asyncio
async def test_engine_retry_action():
    """Retry action returns retry result."""
    engine = ReactionEngine()
    result = await engine.emit(
        EventType.TASK_FAILED,
        "Task failed: timeout",
        session_key="cli:direct",
    )
    assert result is not None
    assert result.action == "retry"


@pytest.mark.asyncio
async def test_engine_escalation_after_retries():
    """After max retries, reaction escalates to human."""
    notifications = []

    async def mock_notify(event):
        notifications.append(event)

    engine = ReactionEngine(on_notify=mock_notify)
    # Configure with 2 retries, escalate after 3
    engine.configure("task.failed", ReactionConfig(
        action=ReactionAction.RETRY,
        retries=2,
        escalate_after=3,
    ))

    # First 2 attempts: retry
    r1 = await engine.emit(EventType.TASK_FAILED, "fail 1", session_key="s1")
    assert r1.action == "retry"
    r2 = await engine.emit(EventType.TASK_FAILED, "fail 2", session_key="s1")
    assert r2.action == "retry"
    # 3rd attempt: should escalate
    r3 = await engine.emit(EventType.TASK_FAILED, "fail 3", session_key="s1")
    assert r3.escalated is True
    assert len(notifications) == 1  # escalation notification sent


@pytest.mark.asyncio
async def test_engine_pause_on_rate_limit():
    """Rate limit event triggers global pause."""
    engine = ReactionEngine()
    result = await engine.emit(
        EventType.RATE_LIMITED,
        "Rate limit hit",
    )
    assert result is not None
    assert result.action == "pause"
    assert engine.is_paused is True


@pytest.mark.asyncio
async def test_engine_pause_expires():
    """Global pause expires after cooldown."""
    engine = ReactionEngine()
    # Use retries=0 so it doesn't escalate, and escalate_after > 10 for time-based
    engine.configure("rate.limited", ReactionConfig(
        action=ReactionAction.PAUSE,
        retries=0,
        escalate_after=300,
    ))
    await engine.emit(EventType.RATE_LIMITED, "Rate limit")
    assert engine.is_paused is True
    # Force expire the pause
    engine._pause_until = time.time() - 1
    assert engine.is_paused is False


@pytest.mark.asyncio
async def test_engine_clear_trackers():
    """Clearing trackers resets retry counts."""
    engine = ReactionEngine()
    await engine.emit(EventType.TASK_FAILED, "fail", session_key="s1")
    await engine.emit(EventType.TASK_FAILED, "fail", session_key="s1")
    engine.clear_all_trackers("s1")
    # After clearing, next attempt should be attempt 1 again
    stats = engine.get_stats()
    assert stats["active_trackers"] == 0


@pytest.mark.asyncio
async def test_engine_auto_false_only_notifies():
    """auto=False skips automated action, only notifies."""
    notifications = []

    async def mock_notify(event):
        notifications.append(event)

    engine = ReactionEngine(on_notify=mock_notify)
    engine.configure("task.stuck", ReactionConfig(
        action=ReactionAction.SEND_TO_AGENT,
        auto=False,
    ))
    result = await engine.emit(EventType.TASK_STUCK, "stuck", session_key="s1")
    assert result.action == "notify"
    assert len(notifications) == 1


@pytest.mark.asyncio
async def test_engine_stats():
    """Stats track events correctly."""
    engine = ReactionEngine()
    await engine.emit(EventType.TASK_FAILED, "fail 1", session_key="s1")
    await engine.emit(EventType.TASK_STUCK, "stuck", session_key="s2")
    stats = engine.get_stats()
    assert stats["total_events"] == 2
    assert stats["reactions_triggered"] == 2
    assert stats["by_type"]["task.failed"] == 1
    assert stats["by_type"]["task.stuck"] == 1


@pytest.mark.asyncio
async def test_engine_configure_from_dict():
    """Configure reactions from a dictionary."""
    engine = ReactionEngine()
    engine.configure_from_dict({
        "task.failed": {
            "action": "notify",
            "priority": "urgent",
            "retries": 0,
        },
    })
    result = await engine.emit(EventType.TASK_FAILED, "fail", session_key="s1")
    assert result.action == "notify"


@pytest.mark.asyncio
async def test_engine_recent_events():
    """Recent events are tracked."""
    engine = ReactionEngine()
    await engine.emit(EventType.TASK_FAILED, "fail 1")
    await engine.emit(EventType.TASK_STUCK, "stuck 1")
    events = engine.get_recent_events(10)
    assert len(events) == 2
    assert events[0].type == EventType.TASK_FAILED
    assert events[1].type == EventType.TASK_STUCK
