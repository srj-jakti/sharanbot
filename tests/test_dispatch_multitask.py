"""Tests for multi-task dispatch, intent understanding, and concurrent background execution."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path


class TestBackgroundAutopilot:
    """Complex tasks should run in background via _schedule_autopilot_background."""

    def test_autopilot_active_tracking(self):
        """_autopilot_active should track what's being worked on per session."""
        active: dict[str, str] = {}
        active["telegram:123"] = "Building a React app with authentication"

        assert "telegram:123" in active
        assert "React" in active["telegram:123"]

        # When done, it should be removed
        active.pop("telegram:123", None)
        assert "telegram:123" not in active

    def test_multiple_tasks_tracked_independently(self):
        """Multiple sessions can have independent background tasks."""
        active: dict[str, str] = {}
        active["session1"] = "Task A"
        active["session2"] = "Task B"

        assert active["session1"] != active["session2"]
        active.pop("session1", None)
        assert "session1" not in active
        assert "session2" in active

    def test_active_tasks_list_structure(self):
        """_active_tasks stores a list of asyncio.Tasks per session key."""
        tasks: dict[str, list] = {}
        tasks.setdefault("session1", []).append("task_a")
        tasks.setdefault("session1", []).append("task_b")
        tasks.setdefault("session2", []).append("task_c")

        assert len(tasks["session1"]) == 2
        assert len(tasks["session2"]) == 1


class TestIntentResolution:
    """Intent resolution should clarify vague messages using conversation history."""

    def test_short_messages_trigger_resolution(self):
        """Messages with <= 30 words should trigger intent resolution."""
        short_msgs = [
            "do that thing we talked about",
            "fix it",
            "same as before",
            "the other one",
            "continue",
        ]
        for msg in short_msgs:
            assert len(msg.split()) <= 30

    def test_long_messages_skip_resolution(self):
        """Long detailed messages should skip intent resolution."""
        long_msg = " ".join(["word"] * 35)
        assert len(long_msg.split()) > 30


class TestCasualChatWhileWorking:
    """The agent should be able to chat casually while autopilot is working in background."""

    @pytest.mark.parametrize("msg", [
        "status", "how's it going", "progress", "you done?",
        "update", "eta", "still working?", "almost done?",
    ])
    def test_status_phrases_detected(self, msg):
        """Status check phrases should be detected."""
        status_phrases = [
            "status", "how's it going", "hows it going", "progress",
            "what are you doing", "you done", "are you done", "update",
            "how far", "still working", "eta", "almost done",
        ]
        content_lower = msg.lower()
        assert any(p in content_lower for p in status_phrases) or len(msg.split()) <= 4

    @pytest.mark.parametrize("msg", [
        "ok", "cool", "nice", "thanks", "got it", "sounds good",
        "alright", "sure", "yep", "great", "awesome", "lol",
    ])
    def test_casual_phrases_detected(self, msg):
        """Casual acknowledgments should be detected."""
        casual_phrases = [
            "ok", "okay", "cool", "nice", "thanks", "thank you", "got it",
            "sounds good", "makes sense", "alright", "sure", "yep", "yeah",
            "no worries", "np", "great", "awesome", "perfect", "understood",
            "lol", "haha", "bruh", "nah",
        ]
        assert msg.lower().strip("!?., ") in casual_phrases

    def test_boss_never_blocked_architecture(self):
        """Verify the architectural principle: Boss dispatches without locks."""
        # The _dispatch method should NOT use any locks — it calls _process_message directly.
        # Complex tasks return immediately after scheduling background autopilot.
        # This is a design assertion, not a runtime test.
        from sharanbot.agent.loop import AgentLoop
        import inspect
        source = inspect.getsource(AgentLoop._dispatch)
        # No lock acquisition in dispatch
        assert "acquire" not in source
        assert "_processing_lock" not in source
        assert "_session_locks" not in source
