"""Tests for sharanbot.agent.memory — MemoryStore and MemoryConsolidator."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from sharanbot.agent.memory import (
    MemoryConsolidator,
    MemoryStore,
    _ensure_text,
    _is_tool_choice_unsupported,
    _normalize_save_memory_args,
)


# ---------------------------------------------------------------------------
# MemoryStore
# ---------------------------------------------------------------------------


class TestMemoryStore:
    def test_read_long_term_empty(self, tmp_path):
        store = MemoryStore(tmp_path)
        assert store.read_long_term() == ""

    def test_write_and_read_long_term(self, tmp_path):
        store = MemoryStore(tmp_path)
        store.write_long_term("fact: sky is blue")
        assert store.read_long_term() == "fact: sky is blue"

    def test_append_history(self, tmp_path):
        store = MemoryStore(tmp_path)
        store.append_history("[2025-01-01] First entry")
        store.append_history("[2025-01-02] Second entry")
        text = store.history_file.read_text(encoding="utf-8")
        assert "First entry" in text
        assert "Second entry" in text

    def test_get_memory_context_empty(self, tmp_path):
        store = MemoryStore(tmp_path)
        assert store.get_memory_context() == ""

    def test_get_memory_context_populated(self, tmp_path):
        store = MemoryStore(tmp_path)
        store.write_long_term("key fact")
        ctx = store.get_memory_context()
        assert "Long-term Memory" in ctx
        assert "key fact" in ctx

    def test_format_messages(self, tmp_path):
        store = MemoryStore(tmp_path)
        msgs = [
            {"role": "user", "content": "hello", "timestamp": "2025-01-01 12:00:00"},
            {"role": "assistant", "content": "hi there"},
        ]
        text = store._format_messages(msgs)
        assert "USER" in text
        assert "ASSISTANT" in text

    def test_format_messages_with_tools(self, tmp_path):
        store = MemoryStore(tmp_path)
        msgs = [
            {"role": "assistant", "content": "done", "tools_used": ["read_file", "exec"]},
        ]
        text = store._format_messages(msgs)
        assert "read_file" in text
        assert "exec" in text

    @pytest.mark.asyncio
    async def test_consolidate_success(self, tmp_path):
        store = MemoryStore(tmp_path)
        provider = MagicMock()
        tc = SimpleNamespace(
            arguments={"history_entry": "[2025] Test", "memory_update": "new fact"},
        )
        resp = SimpleNamespace(
            content="",
            has_tool_calls=True,
            tool_calls=[tc],
            finish_reason="stop",
        )
        provider.chat_with_retry = AsyncMock(return_value=resp)

        msgs = [{"role": "user", "content": "test", "timestamp": "2025-01-01"}]
        result = await store.consolidate(msgs, provider, "model")
        assert result is True
        assert store.read_long_term() == "new fact"
        assert "Test" in store.history_file.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_consolidate_no_tool_call_fails(self, tmp_path):
        store = MemoryStore(tmp_path)
        provider = MagicMock()
        resp = SimpleNamespace(
            content="no tool call",
            has_tool_calls=False,
            tool_calls=[],
            finish_reason="stop",
        )
        provider.chat_with_retry = AsyncMock(return_value=resp)

        msgs = [{"role": "user", "content": "test"}]
        result = await store.consolidate(msgs, provider, "model")
        assert result is False  # First failure
        assert store._consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_consolidate_raw_archive_after_max_failures(self, tmp_path):
        store = MemoryStore(tmp_path)
        store._consecutive_failures = MemoryStore._MAX_FAILURES_BEFORE_RAW_ARCHIVE - 1

        provider = MagicMock()
        resp = SimpleNamespace(
            content="", has_tool_calls=False, tool_calls=[], finish_reason="stop",
        )
        provider.chat_with_retry = AsyncMock(return_value=resp)

        msgs = [{"role": "user", "content": "fallback test", "timestamp": "2025-01"}]
        result = await store.consolidate(msgs, provider, "model")
        # After max failures, raw-archive kicks in and returns True
        assert result is True
        assert store._consecutive_failures == 0
        assert store.history_file.exists()
        assert "[RAW]" in store.history_file.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_consolidate_tool_choice_unsupported_retries(self, tmp_path):
        store = MemoryStore(tmp_path)
        provider = MagicMock()

        error_resp = SimpleNamespace(
            content="tool_choice is not supported",
            has_tool_calls=False,
            tool_calls=[],
            finish_reason="error",
        )
        tc = SimpleNamespace(
            arguments={"history_entry": "[2025] Retry ok", "memory_update": "retry fact"},
        )
        ok_resp = SimpleNamespace(
            content="", has_tool_calls=True, tool_calls=[tc], finish_reason="stop",
        )
        provider.chat_with_retry = AsyncMock(side_effect=[error_resp, ok_resp])

        msgs = [{"role": "user", "content": "test"}]
        result = await store.consolidate(msgs, provider, "model")
        assert result is True
        assert provider.chat_with_retry.await_count == 2

    @pytest.mark.asyncio
    async def test_consolidate_exception_captured(self, tmp_path):
        store = MemoryStore(tmp_path)
        provider = MagicMock()
        provider.chat_with_retry = AsyncMock(side_effect=RuntimeError("boom"))

        msgs = [{"role": "user", "content": "test"}]
        result = await store.consolidate(msgs, provider, "model")
        assert result is False


# ---------------------------------------------------------------------------
# MemoryConsolidator
# ---------------------------------------------------------------------------


class TestMemoryConsolidator:
    def _make_consolidator(self, tmp_path, context_window=4096):
        provider = MagicMock()
        provider.chat_with_retry = AsyncMock()
        sessions = MagicMock()
        build = MagicMock(return_value=[{"role": "user", "content": "x"}])
        get_tools = MagicMock(return_value=[])
        return MemoryConsolidator(
            workspace=tmp_path,
            provider=provider,
            model="test",
            sessions=sessions,
            context_window_tokens=context_window,
            build_messages=build,
            get_tool_definitions=get_tools,
        )

    def test_get_lock_returns_same_lock(self, tmp_path):
        mc = self._make_consolidator(tmp_path)
        lock1 = mc.get_lock("session-a")
        lock2 = mc.get_lock("session-a")
        assert lock1 is lock2

    def test_get_lock_different_sessions(self, tmp_path):
        mc = self._make_consolidator(tmp_path)
        lock1 = mc.get_lock("session-a")
        lock2 = mc.get_lock("session-b")
        assert lock1 is not lock2

    def test_pick_consolidation_boundary_none_when_empty(self, tmp_path):
        mc = self._make_consolidator(tmp_path)
        session = MagicMock()
        session.last_consolidated = 0
        session.messages = []
        assert mc.pick_consolidation_boundary(session, 100) is None

    def test_pick_consolidation_boundary_no_tokens(self, tmp_path):
        mc = self._make_consolidator(tmp_path)
        session = MagicMock()
        session.last_consolidated = 0
        session.messages = [{"role": "user", "content": "hi"}]
        assert mc.pick_consolidation_boundary(session, 0) is None

    def test_pick_consolidation_finds_user_boundary(self, tmp_path):
        mc = self._make_consolidator(tmp_path)
        session = MagicMock()
        session.last_consolidated = 0
        session.messages = [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "second question"},
            {"role": "assistant", "content": "second answer"},
        ]
        result = mc.pick_consolidation_boundary(session, 1)
        assert result is not None
        idx, tokens = result
        assert idx == 2  # second user message is the boundary

    @pytest.mark.asyncio
    async def test_archive_messages_empty(self, tmp_path):
        mc = self._make_consolidator(tmp_path)
        assert await mc.archive_messages([]) is True


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


class TestUtilityFunctions:
    def test_ensure_text_str(self):
        assert _ensure_text("hello") == "hello"

    def test_ensure_text_dict(self):
        result = _ensure_text({"key": "val"})
        assert '"key"' in result

    def test_normalize_save_memory_args_dict(self):
        result = _normalize_save_memory_args({"a": 1})
        assert result == {"a": 1}

    def test_normalize_save_memory_args_json_str(self):
        result = _normalize_save_memory_args('{"a": 1}')
        assert result == {"a": 1}

    def test_normalize_save_memory_args_list(self):
        result = _normalize_save_memory_args([{"a": 1}])
        assert result == {"a": 1}

    def test_normalize_save_memory_args_none(self):
        assert _normalize_save_memory_args(42) is None

    def test_is_tool_choice_unsupported_true(self):
        assert _is_tool_choice_unsupported("tool_choice is not supported") is True
        assert _is_tool_choice_unsupported('should be ["none", "auto"]') is True

    def test_is_tool_choice_unsupported_false(self):
        assert _is_tool_choice_unsupported("all good") is False
        assert _is_tool_choice_unsupported(None) is False
