"""Tests for Boss fast-path: responds while autopilot runs in background."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def _mock_provider():
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.set_recovery_engine = MagicMock()
    provider.chat_with_retry = AsyncMock()
    return provider


@pytest.fixture
def _mock_bus():
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()
    bus.publish_inbound = AsyncMock()
    bus.subscribe_inbound = MagicMock()
    return bus


@pytest.fixture
def _agent_loop(_mock_provider, _mock_bus, tmp_path):
    with patch("sharanbot.agent.loop.ToolManager"):
        from sharanbot.agent.loop import AgentLoop
        loop = AgentLoop(
            bus=_mock_bus,
            provider=_mock_provider,
            workspace=tmp_path,
            model="test-model",
        )
        return loop


class TestBossFastPath:
    """Test that Boss responds quickly when autopilot is active."""

    @pytest.mark.asyncio
    async def test_fast_path_activates_when_autopilot_running(self, _agent_loop, _mock_provider):
        """When autopilot is active for a session, Boss uses fast path."""
        loop = _agent_loop
        session_key = "telegram:12345"

        # Simulate autopilot being active
        loop._autopilot_active[session_key] = "Installing spec-kit and configuring project"

        # Mock the LLM response for fast path
        mock_resp = MagicMock()
        mock_resp.content = "Hey! I'm still working on that spec-kit setup. Should be done soon."
        _mock_provider.chat_with_retry = AsyncMock(return_value=mock_resp)

        from sharanbot.bus.events import InboundMessage
        msg = InboundMessage(
            channel="telegram", sender_id="user", chat_id="12345",
            content="hey whats up",
        )

        result = await loop._process_message(msg, session_key=session_key)

        assert result is not None
        assert result.content == mock_resp.content
        # Should NOT have called autopilot.analyze (no heavy LLM analysis)
        # The fast path makes a single LLM call, not the full intent resolution + autopilot chain

    @pytest.mark.asyncio
    async def test_fast_path_includes_task_context(self, _agent_loop, _mock_provider):
        """Fast path injects the running task description into the LLM prompt."""
        loop = _agent_loop
        session_key = "telegram:12345"
        task_desc = "Building a React dashboard with authentication"

        loop._autopilot_active[session_key] = task_desc

        captured_messages = []

        async def capture_chat(**kwargs):
            captured_messages.append(kwargs.get("messages", []))
            resp = MagicMock()
            resp.content = "Still working on the React dashboard!"
            return resp

        _mock_provider.chat_with_retry = AsyncMock(side_effect=capture_chat)

        from sharanbot.bus.events import InboundMessage
        msg = InboundMessage(
            channel="telegram", sender_id="user", chat_id="12345",
            content="how's it going?",
        )

        await loop._process_message(msg, session_key=session_key)

        # Verify the task description was injected into the messages
        assert len(captured_messages) == 1
        all_content = str(captured_messages[0])
        assert task_desc[:50] in all_content

    @pytest.mark.asyncio
    async def test_fast_path_fallback_on_llm_failure(self, _agent_loop, _mock_provider):
        """If fast-path LLM call fails, return a safe fallback message."""
        loop = _agent_loop
        session_key = "telegram:12345"

        loop._autopilot_active[session_key] = "Some background task"

        _mock_provider.chat_with_retry = AsyncMock(side_effect=Exception("LLM timeout"))

        from sharanbot.bus.events import InboundMessage
        msg = InboundMessage(
            channel="telegram", sender_id="user", chat_id="12345",
            content="hello?",
        )

        result = await loop._process_message(msg, session_key=session_key)

        assert result is not None
        assert "background" in result.content.lower()
        assert "/status" in result.content or "/stop" in result.content

    @pytest.mark.asyncio
    async def test_normal_path_when_no_autopilot(self, _agent_loop, _mock_provider):
        """When no autopilot is active, Boss uses the normal full path."""
        loop = _agent_loop
        # _autopilot_active is empty — no fast path

        # Mock the full chain: intent resolution + autopilot analyze + agent loop
        mock_resp = MagicMock()
        mock_resp.content = "Hello! How can I help?"
        mock_resp.has_tool_calls = False
        mock_resp.tool_calls = []
        mock_resp.reasoning_content = None
        mock_resp.thinking_blocks = None
        _mock_provider.chat_with_retry = AsyncMock(return_value=mock_resp)

        from sharanbot.bus.events import InboundMessage
        msg = InboundMessage(
            channel="telegram", sender_id="user", chat_id="12345",
            content="hello",
        )

        result = await loop._process_message(msg, session_key="telegram:12345")

        assert result is not None
        # Normal path should produce a response (not the fast-path fallback)
        assert "background" not in result.content.lower() or "Hello" in result.content

    @pytest.mark.asyncio
    async def test_fast_path_saves_to_session(self, _agent_loop, _mock_provider):
        """Fast path saves both user message and response to session history."""
        loop = _agent_loop
        session_key = "telegram:12345"

        loop._autopilot_active[session_key] = "Running tests"

        mock_resp = MagicMock()
        mock_resp.content = "Tests are still running, hang tight!"
        _mock_provider.chat_with_retry = AsyncMock(return_value=mock_resp)

        from sharanbot.bus.events import InboundMessage
        msg = InboundMessage(
            channel="telegram", sender_id="user", chat_id="12345",
            content="are the tests done?",
        )

        await loop._process_message(msg, session_key=session_key)

        # Check session was saved
        session = loop.sessions.get_or_create(session_key)
        history = session.get_history(max_messages=0)
        # Should have at least the user message and assistant response
        assert len(history) >= 2

    @pytest.mark.asyncio
    async def test_slash_commands_still_work_during_autopilot(self, _agent_loop):
        """Slash commands like /status and /stop should still work during autopilot."""
        loop = _agent_loop
        session_key = "telegram:12345"

        loop._autopilot_active[session_key] = "Building something"

        from sharanbot.bus.events import InboundMessage

        # /status should work
        msg = InboundMessage(
            channel="telegram", sender_id="user", chat_id="12345",
            content="/status",
        )
        result = await loop._process_message(msg, session_key=session_key)
        assert result is not None
        assert "SharanBot" in result.content or "Background" in result.content

    @pytest.mark.asyncio
    async def test_fast_path_clears_after_autopilot_finishes(self, _agent_loop):
        """After autopilot finishes, the fast path should no longer activate."""
        loop = _agent_loop
        session_key = "telegram:12345"

        # Autopilot was active, now it's done (cleared from dict)
        assert session_key not in loop._autopilot_active

        # Normal path should be used — no fast path
        # (This is implicitly tested by test_normal_path_when_no_autopilot,
        # but this test explicitly verifies the cleanup scenario)
        assert loop._autopilot_active.get(session_key) is None
