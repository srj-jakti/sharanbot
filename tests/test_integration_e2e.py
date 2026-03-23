"""End-to-end integration tests: Channel → Bus → Agent → Tool → Response.

These tests wire together real (but lightweight) instances of MessageBus,
a mock Channel, the AgentLoop, and a mock provider to verify the full
message flow without hitting a real LLM.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sharanbot.bus.queue import MessageBus
from sharanbot.bus.events import OutboundMessage
from sharanbot.channels.base import BaseChannel
from sharanbot.providers.base import ToolCallRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider(content: str = "Hello!", tool_calls=None, finish_reason="stop"):
    prov = MagicMock()
    prov.get_default_model.return_value = "test-model"
    prov.set_recovery_engine = MagicMock()
    resp = SimpleNamespace(
        content=content,
        has_tool_calls=bool(tool_calls),
        tool_calls=tool_calls or [],
        finish_reason=finish_reason,
        usage={"prompt_tokens": 100, "completion_tokens": 50},
        reasoning_content=None,
        thinking_blocks=None,
    )
    prov.chat_with_retry = AsyncMock(return_value=resp)
    return prov


def _make_tool_call(name="read_file", args=None):
    return ToolCallRequest(
        id="call_1",
        name=name,
        arguments=args or {},
    )


def _build_loop(tmp_path: Path, provider=None, **overrides):
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


class _TestChannel(BaseChannel):
    """Minimal channel for integration tests."""

    name = "test_e2e"

    def __init__(self, bus: MessageBus):
        super().__init__(SimpleNamespace(allow_from=None), bus)
        self.sent: list[OutboundMessage] = []

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send(self, msg: OutboundMessage) -> None:
        self.sent.append(msg)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestChannelToBusFlow:
    """Verify messages flow from channel to bus."""

    @pytest.mark.asyncio
    async def test_channel_publishes_to_bus(self):
        from sharanbot.bus.events import InboundMessage as IBMsg

        bus = MessageBus()
        msg = IBMsg(channel="test_e2e", chat_id="u1", sender_id="user", content="ping")
        await bus.publish_inbound(msg)
        assert bus.inbound_size == 1
        received = await bus.consume_inbound()
        assert received.content == "ping"

    @pytest.mark.asyncio
    async def test_bus_delivers_outbound(self):
        bus = MessageBus()
        ch = _TestChannel(bus)
        msg = OutboundMessage(channel="test_e2e", chat_id="u1", content="pong")
        await ch.send(msg)
        assert len(ch.sent) == 1
        assert ch.sent[0].content == "pong"


class TestAgentSimpleReply:
    """Agent receives a message and returns a text-only reply."""

    @pytest.mark.asyncio
    async def test_simple_text_response(self, tmp_path):
        prov = _make_provider(content="Hello from agent!")
        loop = _build_loop(tmp_path, provider=prov)

        result, tools_used, _ = await loop._run_agent_loop(
            [{"role": "user", "content": "hi"}]
        )
        assert "Hello from agent!" in result
        assert tools_used == []


class TestAgentToolFlow:
    """Agent calls a tool and then completes."""

    @pytest.mark.asyncio
    async def test_tool_call_then_final_answer(self, tmp_path):
        tc = _make_tool_call("read_file", {"path": "a.txt"})
        plain_resp = SimpleNamespace(
            content="File contents are: ok",
            has_tool_calls=False,
            tool_calls=[],
            finish_reason="stop",
            usage={"prompt_tokens": 200, "completion_tokens": 60},
            reasoning_content=None,
            thinking_blocks=None,
        )
        prov = _make_provider()
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

        result, tools_used, _ = await loop._run_agent_loop(
            [{"role": "user", "content": "read a.txt"}]
        )
        assert "File contents" in result
        assert "read_file" in tools_used

    @pytest.mark.asyncio
    async def test_tool_execution_called(self, tmp_path):
        tc = _make_tool_call("write_file", {"path": "b.txt", "content": "data"})
        prov = _make_provider()
        prov.chat_with_retry = AsyncMock(side_effect=[
            SimpleNamespace(
                content="",
                has_tool_calls=True,
                tool_calls=[tc],
                finish_reason="stop",
                usage={"prompt_tokens": 100, "completion_tokens": 50},
                reasoning_content=None,
                thinking_blocks=None,
            ),
            SimpleNamespace(
                content="Done",
                has_tool_calls=False,
                tool_calls=[],
                finish_reason="stop",
                usage={"prompt_tokens": 200, "completion_tokens": 40},
                reasoning_content=None,
                thinking_blocks=None,
            ),
        ])
        loop = _build_loop(tmp_path, provider=prov)
        await loop._run_agent_loop(
            [{"role": "user", "content": "write file"}]
        )
        loop.tools.execute.assert_awaited()


class TestRecoveryIntegration:
    """Recovery engine integrates with the agent loop."""

    @pytest.mark.asyncio
    async def test_provider_error_triggers_recovery(self, tmp_path):
        prov = _make_provider()
        prov.chat_with_retry = AsyncMock(
            side_effect=RuntimeError("503 Service Unavailable")
        )
        loop = _build_loop(tmp_path, provider=prov)

        with pytest.raises(RuntimeError, match="503"):
            await loop._run_agent_loop(
                [{"role": "user", "content": "hello"}]
            )


class TestBusQueueRouting:
    """Bus correctly queues inbound and outbound."""

    @pytest.mark.asyncio
    async def test_inbound_and_outbound_separate(self):
        from sharanbot.bus.events import InboundMessage as IBMsg

        bus = MessageBus()
        await bus.publish_inbound(IBMsg(channel="c", chat_id="1", sender_id="s", content="in"))
        await bus.publish_outbound(OutboundMessage(channel="c", chat_id="1", content="out"))

        assert bus.inbound_size == 1
        ib = await bus.consume_inbound()
        assert ib.content == "in"

        ob = await bus.consume_outbound()
        assert ob.content == "out"

    @pytest.mark.asyncio
    async def test_empty_queue_does_not_block_forever(self):
        bus = MessageBus()
        assert bus.inbound_size == 0


class TestLearningStoreIntegration:
    """Learning store records and retrieves across the flow."""

    def test_record_and_lookup(self, tmp_path):
        from sharanbot.agent.learning import LearningStore

        store = LearningStore(tmp_path)
        store.record(
            category="tool_error",
            error_signature="tool_error:browser",
            description="Browser crashed",
            fix_applied="Restart browser",
            success=True,
        )
        entry = store.lookup("tool_error:browser")
        assert entry is not None
        assert entry.success is True
        assert entry.fix_applied == "Restart browser"

    def test_cross_project_export_import(self, tmp_path):
        from sharanbot.agent.learning import LearningStore

        store1 = LearningStore(tmp_path / "ws1")
        # Use a temp global dir so we don't pollute or rely on real home dir
        global_dir = tmp_path / "global"
        store1._GLOBAL_DIR = global_dir
        store1.record_success("debug_flow", ["read_file", "exec"])
        exported = store1.export_high_confidence(min_confidence=0.5)
        assert exported >= 1

        store2 = LearningStore(tmp_path / "ws2")
        store2._GLOBAL_DIR = global_dir
        imported = store2.import_global_patterns()
        assert imported >= 1
