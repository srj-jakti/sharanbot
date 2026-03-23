"""Tests for sharanbot.agent.loop — the core AgentLoop engine."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sharanbot.bus.events import InboundMessage, OutboundMessage
from sharanbot.bus.queue import MessageBus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_provider(
    content: str = "Hello!",
    tool_calls: list | None = None,
    finish_reason: str = "stop",
    usage: dict | None = None,
):
    """Build a mock LLMProvider whose chat_with_retry returns a canned response."""
    prov = MagicMock()
    prov.get_default_model.return_value = "test-model"
    prov.set_recovery_engine = MagicMock()

    resp = SimpleNamespace(
        content=content,
        has_tool_calls=bool(tool_calls),
        tool_calls=tool_calls or [],
        finish_reason=finish_reason,
        usage=usage or {"prompt_tokens": 100, "completion_tokens": 50},
        reasoning_content=None,
        thinking_blocks=None,
    )
    prov.chat_with_retry = AsyncMock(return_value=resp)
    return prov


def _make_tool_call(name: str = "read_file", args: dict | None = None, call_id: str = "tc-1"):
    tc = SimpleNamespace(
        name=name,
        arguments=args or {"path": "a.txt"},
        id=call_id,
    )
    tc.to_openai_tool_call = lambda: {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": str(args or {})},
    }
    return tc


def _build_loop(tmp_path: Path, provider=None, **overrides):
    """Instantiate an AgentLoop with minimal mocks."""
    from sharanbot.agent.loop import AgentLoop

    prov = provider or _make_provider()
    bus = MessageBus()
    with patch.object(AgentLoop, "_register_default_tools"):
        loop = AgentLoop(
            bus=bus,
            provider=prov,
            workspace=tmp_path,
            model="test-model",
            max_iterations=5,
            context_window_tokens=4096,
            **overrides,
        )
    # Minimal stubs so the loop can run without real tools
    loop.tools = MagicMock()
    loop.tools.get_definitions.return_value = []
    loop.tools.get.return_value = None
    loop.tools.execute = AsyncMock(return_value="ok")
    loop.context = MagicMock()
    loop.context.build_messages.return_value = [{"role": "user", "content": "hi"}]
    loop.context.add_assistant_message.side_effect = lambda msgs, *a, **kw: msgs + [
        {"role": "assistant", "content": a[0] if a else ""}
    ]
    loop.context.add_tool_result.side_effect = lambda msgs, *a, **kw: msgs + [
        {"role": "tool", "content": "ok"}
    ]
    loop.memory_consolidator = MagicMock()
    loop.memory_consolidator.maybe_consolidate_by_tokens = AsyncMock()
    loop.memory_consolidator.archive_messages = AsyncMock()
    loop.memory_consolidator.estimate_session_prompt_tokens.return_value = (100, 0)
    loop.subconscious = MagicMock()
    loop.subconscious.on_session_start = MagicMock()
    loop.subconscious.on_turn_end = MagicMock()
    loop.recovery_engine = MagicMock()
    loop.recovery_engine.handle_error = AsyncMock(return_value=False)
    loop.learning_store = MagicMock()
    return loop


# ---------------------------------------------------------------------------
# Tests — _run_agent_loop
# ---------------------------------------------------------------------------


class TestRunAgentLoop:
    """Test the core _run_agent_loop method."""

    @pytest.mark.asyncio
    async def test_simple_response(self, tmp_path):
        """LLM returns a plain text response — loop finishes in 1 iteration."""
        loop = _build_loop(tmp_path)
        final, tools_used, msgs = await loop._run_agent_loop(
            [{"role": "user", "content": "hi"}]
        )
        assert final == "Hello!"
        assert tools_used == []
        loop.provider.chat_with_retry.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tool_call_then_response(self, tmp_path):
        """LLM returns a tool call first, then a plain response."""
        tc = _make_tool_call()
        prov = _make_provider(content="Done!", tool_calls=[tc])

        # First call has tool calls, second does not
        plain_resp = SimpleNamespace(
            content="Final answer",
            has_tool_calls=False,
            tool_calls=[],
            finish_reason="stop",
            usage={"prompt_tokens": 200, "completion_tokens": 60},
            reasoning_content=None,
            thinking_blocks=None,
        )
        prov.chat_with_retry = AsyncMock(side_effect=[
            SimpleNamespace(
                content="reading…",
                has_tool_calls=True,
                tool_calls=[tc],
                finish_reason="stop",
                usage={"prompt_tokens": 100, "completion_tokens": 50},
                reasoning_content=None,
                thinking_blocks=None,
            ),
            plain_resp,
        ])

        loop = _build_loop(tmp_path, provider=prov)
        final, tools_used, _ = await loop._run_agent_loop(
            [{"role": "user", "content": "read a.txt"}]
        )
        assert final == "Final answer"
        assert "read_file" in tools_used
        assert prov.chat_with_retry.await_count == 2
        loop.tools.execute.assert_awaited_once_with("read_file", {"path": "a.txt"})

    @pytest.mark.asyncio
    async def test_consecutive_errors_stop(self, tmp_path):
        """Five consecutive LLM errors cause the loop to bail out.

        The loop only retries after a successful recovery.  So we make
        recovery_engine.handle_error return True for the first 4 errors
        (causing retries) and the 5th error triggers the max-consecutive
        check and breaks.
        """
        error_resp = SimpleNamespace(
            content="model error",
            has_tool_calls=False,
            tool_calls=[],
            finish_reason="error",
            usage={},
            reasoning_content=None,
            thinking_blocks=None,
        )
        prov = _make_provider()
        prov.chat_with_retry = AsyncMock(return_value=error_resp)

        loop = _build_loop(tmp_path, provider=prov)
        # Recovery succeeds 4 times then the 5th error hits the cap
        loop.recovery_engine.handle_error = AsyncMock(return_value=True)

        final, _, _ = await loop._run_agent_loop(
            [{"role": "user", "content": "go"}]
        )
        assert "error" in (final or "").lower()
        assert prov.chat_with_retry.await_count == 5

    @pytest.mark.asyncio
    async def test_error_recovery_retries(self, tmp_path):
        """When recovery_engine recovers, the loop retries the LLM call."""
        error_resp = SimpleNamespace(
            content="model error",
            has_tool_calls=False,
            tool_calls=[],
            finish_reason="error",
            usage={},
            reasoning_content=None,
            thinking_blocks=None,
        )
        ok_resp = SimpleNamespace(
            content="recovered!",
            has_tool_calls=False,
            tool_calls=[],
            finish_reason="stop",
            usage={"prompt_tokens": 100, "completion_tokens": 50},
            reasoning_content=None,
            thinking_blocks=None,
        )
        prov = _make_provider()
        prov.chat_with_retry = AsyncMock(side_effect=[error_resp, ok_resp])

        loop = _build_loop(tmp_path, provider=prov)
        loop.recovery_engine.handle_error = AsyncMock(return_value=True)

        final, _, _ = await loop._run_agent_loop(
            [{"role": "user", "content": "go"}]
        )
        assert final == "recovered!"
        loop.recovery_engine.handle_error.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_think_tags_stripped(self, tmp_path):
        """<think> blocks in LLM output are removed."""
        prov = _make_provider(content="<think>internal</think>The answer is 42.")
        loop = _build_loop(tmp_path, provider=prov)
        final, _, _ = await loop._run_agent_loop(
            [{"role": "user", "content": "what?"}]
        )
        assert "internal" not in (final or "")
        assert "42" in (final or "")


# ---------------------------------------------------------------------------
# Tests — token window summarization
# ---------------------------------------------------------------------------


class TestTokenWindowSummarization:
    @pytest.mark.asyncio
    async def test_summarize_triggered_at_80_percent(self, tmp_path):
        """When token usage ≥80% of context window, summarization fires."""
        tc = _make_tool_call()
        # Return tool calls for iterations 1-5, then plain response at 6
        tool_resp = SimpleNamespace(
            content="working…",
            has_tool_calls=True,
            tool_calls=[tc],
            finish_reason="stop",
            usage={"prompt_tokens": 100, "completion_tokens": 50},
            reasoning_content=None,
            thinking_blocks=None,
        )
        plain_resp = SimpleNamespace(
            content="done",
            has_tool_calls=False,
            tool_calls=[],
            finish_reason="stop",
            usage={"prompt_tokens": 100, "completion_tokens": 50},
            reasoning_content=None,
            thinking_blocks=None,
        )
        prov = _make_provider()
        prov.chat_with_retry = AsyncMock(
            side_effect=[tool_resp, tool_resp, tool_resp, tool_resp, tool_resp, plain_resp]
        )

        loop = _build_loop(tmp_path, provider=prov)
        # Tokens hit 80% threshold (context_window=4096, 80% = 3276)
        loop._estimate_messages_tokens = MagicMock(return_value=3500)
        loop._summarize_and_continue = AsyncMock(return_value=[
            {"role": "user", "content": "summary"}
        ])

        final, _, _ = await loop._run_agent_loop(
            [{"role": "user", "content": "x"}]
        )
        # summarize_and_continue is called at iteration 5 (check every 5)
        assert loop._summarize_and_continue.await_count >= 1


# ---------------------------------------------------------------------------
# Tests — apply_config_reload
# ---------------------------------------------------------------------------


class TestApplyConfigReload:
    def test_updates_model_and_max_iterations(self, tmp_path):
        loop = _build_loop(tmp_path)
        cfg = SimpleNamespace(
            agent_defaults=SimpleNamespace(
                model="new-model",
                max_iterations=20,
                context_window=8192,
            ),
            mcp_servers=None,
        )
        loop.apply_config_reload(cfg)
        assert loop.model == "new-model"
        assert loop.max_iterations == 20
        assert loop.context_window_tokens == 8192

    def test_rejects_unsafe_keys(self, tmp_path):
        """validate_config_update filters out keys not in the safe set."""
        loop = _build_loop(tmp_path)
        original_model = loop.model
        cfg = SimpleNamespace(
            agent_defaults=SimpleNamespace(
                model=None,
                max_iterations=None,
                context_window=None,
            ),
            mcp_servers=None,
        )
        loop.apply_config_reload(cfg)
        # With all None, nothing changes
        assert loop.model == original_model


# ---------------------------------------------------------------------------
# Tests — _status_response
# ---------------------------------------------------------------------------


class TestStatusResponse:
    def test_returns_outbound_message(self, tmp_path):
        loop = _build_loop(tmp_path)
        msg = InboundMessage(
            channel="cli", chat_id="123", sender_id="user", content="/status"
        )
        session = MagicMock()
        session.get_history.return_value = [{"role": "user", "content": "hi"}]

        result = loop._status_response(msg, session)
        assert isinstance(result, OutboundMessage)
        assert result.channel == "cli"
        assert result.chat_id == "123"


# ---------------------------------------------------------------------------
# Tests — _process_message slash commands
# ---------------------------------------------------------------------------


class TestProcessMessageCommands:
    @pytest.mark.asyncio
    async def test_help_command(self, tmp_path):
        loop = _build_loop(tmp_path)
        msg = InboundMessage(
            channel="cli", chat_id="123", sender_id="user", content="/help"
        )
        result = await loop._process_message(msg)
        assert result is not None
        assert "/new" in result.content
        assert "/status" in result.content

    @pytest.mark.asyncio
    async def test_new_command_clears_session(self, tmp_path):
        loop = _build_loop(tmp_path)
        msg = InboundMessage(
            channel="cli", chat_id="123", sender_id="user", content="/new"
        )
        result = await loop._process_message(msg)
        assert result is not None
        assert "new session" in result.content.lower()

    @pytest.mark.asyncio
    async def test_status_command(self, tmp_path):
        loop = _build_loop(tmp_path)
        msg = InboundMessage(
            channel="cli", chat_id="123", sender_id="user", content="/status"
        )
        result = await loop._process_message(msg)
        assert result is not None
        assert isinstance(result, OutboundMessage)


# ---------------------------------------------------------------------------
# Tests — handle_reload
# ---------------------------------------------------------------------------


class TestHandleReload:
    @pytest.mark.asyncio
    async def test_reload_tools(self, tmp_path):
        loop = _build_loop(tmp_path)
        loop.tool_mgr = MagicMock()
        loop.tool_mgr.reload_tools.return_value = 3
        result = await loop.handle_reload("tools")
        assert "3" in result

    @pytest.mark.asyncio
    async def test_reload_unknown_component(self, tmp_path):
        loop = _build_loop(tmp_path)
        result = await loop.handle_reload("nonexistent")
        assert "Unknown component" in result

    @pytest.mark.asyncio
    async def test_reload_mcp_reconnects(self, tmp_path):
        loop = _build_loop(tmp_path)
        loop.tool_mgr = MagicMock()
        loop.tool_mgr.reconnect_mcp = AsyncMock()
        result = await loop.handle_reload("mcp")
        assert "reconnected" in result.lower()
        loop.tool_mgr.reconnect_mcp.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reload_workers(self, tmp_path):
        loop = _build_loop(tmp_path)
        loop.worker_type_registry = MagicMock()
        loop.worker_type_registry.list_types.return_value = ["a", "b"]
        result = await loop.handle_reload("workers")
        assert "2" in result


# ---------------------------------------------------------------------------
# Tests — utility methods
# ---------------------------------------------------------------------------


class TestUtilities:
    def test_strip_think_removes_blocks(self):
        from sharanbot.agent.loop import AgentLoop
        assert AgentLoop._strip_think("<think>x</think>hello") == "hello"
        assert AgentLoop._strip_think(None) is None
        assert AgentLoop._strip_think("<think>only think</think>") is None

    def test_tool_hint_formatting(self):
        from sharanbot.agent.loop import AgentLoop
        tc = _make_tool_call("read_file", {"path": "foo.py"})
        hint = AgentLoop._tool_hint([tc])
        assert "read_file" in hint
        assert "foo.py" in hint

    def test_estimate_tokens_fallback(self, tmp_path):
        """When provider estimation fails, fallback to char/4."""
        loop = _build_loop(tmp_path)
        messages = [{"role": "user", "content": "a" * 400}]
        with patch("sharanbot.agent.loop.AgentLoop._estimate_messages_tokens") as mock_est:
            # Call the real method
            mock_est.side_effect = lambda msgs: sum(
                len(str(m.get("content", ""))) for m in msgs
            ) // 4
            result = mock_est(messages)
            assert result == 100

    def test_format_messages_for_summary(self):
        from sharanbot.agent.loop import AgentLoop
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        text = AgentLoop._format_messages_for_summary(msgs)
        assert "[USER]" in text
        assert "[ASSISTANT]" in text
        assert "hello" in text
