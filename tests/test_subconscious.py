"""Tests for the Subconscious module — persistent background memory layer."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from sharanbot.agent.subconscious import (
    MemoryBlock,
    Subconscious,
    SubconsciousMemory,
    _DEFAULT_BLOCKS,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def memory(workspace: Path) -> SubconsciousMemory:
    return SubconsciousMemory(workspace)


@pytest.fixture
def mock_provider() -> MagicMock:
    provider = MagicMock()
    provider.chat_with_retry = AsyncMock()
    return provider


@pytest.fixture
def subconscious(mock_provider: MagicMock, workspace: Path) -> Subconscious:
    return Subconscious(provider=mock_provider, model="test-model", workspace=workspace)


# ── MemoryBlock tests ──


class TestMemoryBlock:
    def test_to_dict_roundtrip(self):
        block = MemoryBlock(
            label="test",
            description="Test block",
            value="hello world",
            limit=1000,
            updated_at=12345.0,
        )
        data = block.to_dict()
        restored = MemoryBlock.from_dict(data)
        assert restored.label == "test"
        assert restored.description == "Test block"
        assert restored.value == "hello world"
        assert restored.limit == 1000
        assert restored.updated_at == 12345.0

    def test_from_dict_defaults(self):
        block = MemoryBlock.from_dict({"label": "x"})
        assert block.description == ""
        assert block.value == ""
        assert block.limit == 5000


# ── SubconsciousMemory tests ──


class TestSubconsciousMemory:
    def test_initializes_default_blocks(self, memory: SubconsciousMemory):
        blocks = memory.get_all_blocks()
        labels = {b.label for b in blocks}
        expected = {d["label"] for d in _DEFAULT_BLOCKS}
        assert labels == expected

    def test_persists_to_disk(self, workspace: Path):
        mem1 = SubconsciousMemory(workspace)
        mem1.update_block("guidance", "Test guidance content")

        # Load from same path
        mem2 = SubconsciousMemory(workspace)
        block = mem2.get_block("guidance")
        assert block is not None
        assert block.value == "Test guidance content"

    def test_update_block(self, memory: SubconsciousMemory):
        assert memory.update_block("guidance", "New guidance")
        block = memory.get_block("guidance")
        assert block.value == "New guidance"
        assert block.updated_at > 0

    def test_update_nonexistent_block(self, memory: SubconsciousMemory):
        assert not memory.update_block("nonexistent", "value")

    def test_update_respects_limit(self, memory: SubconsciousMemory):
        long_val = "x" * 10000
        memory.update_block("guidance", long_val)
        block = memory.get_block("guidance")
        assert len(block.value) == block.limit

    def test_get_block_values(self, memory: SubconsciousMemory):
        values = memory.get_block_values()
        assert isinstance(values, dict)
        assert "guidance" in values
        assert "core_directives" in values

    def test_create_block(self, memory: SubconsciousMemory):
        assert memory.create_block("custom_block", "Custom", "Custom value")
        block = memory.get_block("custom_block")
        assert block is not None
        assert block.value == "Custom value"

    def test_block_limit_12(self, memory: SubconsciousMemory):
        # 8 default blocks exist, can create 4 more
        for i in range(4):
            assert memory.create_block(f"extra_{i}", f"Extra {i}", f"val {i}")
        # 13th should fail
        assert not memory.create_block("extra_overflow", "Overflow", "val")

    def test_delete_custom_block(self, memory: SubconsciousMemory):
        memory.create_block("deletable", "Del", "val")
        assert memory.delete_block("deletable")
        assert memory.get_block("deletable") is None

    def test_cannot_delete_core_block(self, memory: SubconsciousMemory):
        assert not memory.delete_block("guidance")
        assert memory.get_block("guidance") is not None

    def test_file_on_disk(self, workspace: Path, memory: SubconsciousMemory):
        path = workspace / "subconscious_memory.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "blocks" in data
        assert len(data["blocks"]) == len(_DEFAULT_BLOCKS)


# ── Subconscious tests ──


class TestSubconscious:
    def test_init(self, subconscious: Subconscious):
        assert subconscious.mode == "whisper"
        assert subconscious._task_count == 0
        assert len(subconscious.memory.get_all_blocks()) == len(_DEFAULT_BLOCKS)

    def test_inject_guidance_off_mode(self, mock_provider, workspace):
        sub = Subconscious(provider=mock_provider, model="m", workspace=workspace, mode="off")
        assert sub.inject_guidance() is None

    def test_inject_guidance_whisper_no_active(self, subconscious: Subconscious):
        # Default blocks all start with "(No " so whisper returns None
        result = subconscious.inject_guidance()
        assert result is None

    def test_inject_guidance_whisper_with_content(self, subconscious: Subconscious):
        subconscious.memory.update_block("guidance", "Remember to use TypeScript for this project")
        result = subconscious.inject_guidance()
        assert result is not None
        assert "subconscious_guidance" in result
        assert "TypeScript" in result

    def test_inject_guidance_full_mode(self, mock_provider, workspace):
        sub = Subconscious(provider=mock_provider, model="m", workspace=workspace, mode="full")
        sub.memory.update_block("guidance", "Test guidance")
        sub.memory.update_block("project_context", "Python web app")
        result = sub.inject_guidance()
        assert result is not None
        assert "Test guidance" in result
        assert "Python web app" in result
        assert "Subconscious Layer" in result

    def test_inject_guidance_diff_no_changes(self, subconscious: Subconscious):
        # First call caches values
        subconscious.inject_guidance_diff()
        # Second call with no changes returns None
        result = subconscious.inject_guidance_diff()
        assert result is None

    def test_inject_guidance_diff_with_changes(self, subconscious: Subconscious):
        subconscious.inject_guidance_diff()  # cache initial
        subconscious.memory.update_block("guidance", "New important guidance")
        result = subconscious.inject_guidance_diff()
        assert result is not None
        assert "changed blocks" in result
        assert "New important guidance" in result

    @pytest.mark.asyncio
    async def test_process_transcript_short(self, subconscious: Subconscious):
        """Short transcripts (< 3 messages) are skipped."""
        await subconscious.process_transcript([{"role": "user", "content": "hi"}])
        assert subconscious._task_count == 0

    @pytest.mark.asyncio
    async def test_process_transcript_off_mode(self, mock_provider, workspace):
        sub = Subconscious(provider=mock_provider, model="m", workspace=workspace, mode="off")
        await sub.process_transcript([
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
            {"role": "user", "content": "msg3"},
        ])
        mock_provider.chat_with_retry.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_transcript_success(self, subconscious: Subconscious, mock_provider):
        mock_provider.chat_with_retry.return_value = MagicMock(
            content=json.dumps({
                "updates": {
                    "guidance": "User prefers Python over TypeScript",
                    "user_preferences": "- Prefers Python\n- Uses pytest",
                },
                "reasoning": "Observed Python usage pattern",
            })
        )

        messages = [
            {"role": "user", "content": "Write a Python test"},
            {"role": "assistant", "content": "Sure, using pytest..."},
            {"role": "user", "content": "Perfect, that's what I wanted"},
        ]
        await subconscious.process_transcript(messages)

        assert subconscious._task_count == 1
        assert subconscious.memory.get_block("guidance").value == "User prefers Python over TypeScript"
        assert "pytest" in subconscious.memory.get_block("user_preferences").value

    @pytest.mark.asyncio
    async def test_process_transcript_invalid_json(self, subconscious: Subconscious, mock_provider):
        mock_provider.chat_with_retry.return_value = MagicMock(content="not json at all")
        messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
            {"role": "user", "content": "msg3"},
        ]
        # Should not raise
        await subconscious.process_transcript(messages)
        assert subconscious._task_count == 1

    @pytest.mark.asyncio
    async def test_process_transcript_markdown_json(self, subconscious: Subconscious, mock_provider):
        """LLM wraps JSON in markdown code block."""
        mock_provider.chat_with_retry.return_value = MagicMock(
            content='```json\n{"updates": {"guidance": "test value"}, "reasoning": "ok"}\n```'
        )
        messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
            {"role": "user", "content": "msg3"},
        ]
        await subconscious.process_transcript(messages)
        assert subconscious.memory.get_block("guidance").value == "test value"

    def test_mid_task_sync_off(self, mock_provider, workspace):
        sub = Subconscious(provider=mock_provider, model="m", workspace=workspace, mode="off")
        result = sub.mid_task_sync()
        assert result is None

    def test_mid_task_sync_no_changes(self, subconscious: Subconscious):
        """Mid-task sync with no block changes returns None."""
        # diff cache is primed at init so no changes detected
        result = subconscious.mid_task_sync()
        assert result is None

    def test_mid_task_sync_with_changes(self, subconscious: Subconscious):
        """Mid-task sync returns diff when blocks changed (e.g. by background processing)."""
        # Simulate background process updating a block
        subconscious.memory.update_block("guidance", "New mid-task guidance")
        result = subconscious.mid_task_sync()
        assert result is not None
        assert "New mid-task guidance" in result
        # Second call with no changes returns None
        result2 = subconscious.mid_task_sync()
        assert result2 is None

    def test_get_stats(self, subconscious: Subconscious):
        stats = subconscious.get_stats()
        assert stats["mode"] == "whisper"
        assert stats["total_blocks"] == len(_DEFAULT_BLOCKS)
        assert stats["active_blocks"] == 2  # core_directives + self_improvement have real content
        assert stats["tasks_processed"] == 0

    def test_format_transcript_with_tool_calls(self, subconscious: Subconscious):
        messages = [
            {"role": "user", "content": "Do something"},
            {
                "role": "assistant",
                "content": "Let me run a command",
                "tool_calls": [
                    {"function": {"name": "exec", "arguments": '{"command": "ls -la"}'}},
                ],
            },
            {"role": "tool", "content": "file1.py\nfile2.py"},
        ]
        result = subconscious._format_transcript(messages)
        assert "[USER]: Do something" in result
        assert "[ASSISTANT]: Let me run a command" in result
        assert "[TOOL_CALL]: exec" in result
        assert "[TOOL]: file1.py" in result

    def test_format_transcript_multimodal(self, subconscious: Subconscious):
        """Content as list of blocks."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Look at this"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            },
        ]
        result = subconscious._format_transcript(messages)
        assert "Look at this" in result

    def test_format_transcript_truncation(self, subconscious: Subconscious):
        messages = [{"role": "user", "content": "x" * 2000}]
        result = subconscious._format_transcript(messages)
        assert "(truncated)" in result
        assert len(result) < 2000

    # ── Session lifecycle tests ──

    def test_on_session_start(self, subconscious: Subconscious):
        subconscious.on_session_start("session-123", channel="telegram")
        state = subconscious.memory.get_session_state()
        assert state["current_session"] == "session-123"
        assert state["channel"] == "telegram"
        assert state["last_processed_index"] == 0
        assert subconscious._session_message_count == 0

    def test_inject_guidance_auto_diffs(self, subconscious: Subconscious):
        """First call is full injection, subsequent calls use smart diff."""
        subconscious.memory.update_block("guidance", "Important guidance")

        # First call: full injection
        result1 = subconscious.inject_guidance()
        assert result1 is not None
        assert "Subconscious Layer" in result1
        assert "Important guidance" in result1

        # Second call with no changes: returns None (diff, nothing changed)
        result2 = subconscious.inject_guidance()
        assert result2 is None

        # Third call after a change: returns diff
        subconscious.memory.update_block("guidance", "Updated guidance")
        result3 = subconscious.inject_guidance()
        assert result3 is not None
        assert "changed blocks" in result3
        assert "Updated guidance" in result3

    @pytest.mark.asyncio
    async def test_process_transcript_incremental(self, subconscious: Subconscious, mock_provider):
        """Only processes new messages since last_processed_index."""
        mock_provider.chat_with_retry.return_value = MagicMock(
            content='{"updates": {}, "reasoning": "noted"}'
        )

        messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
            {"role": "user", "content": "msg3"},
            {"role": "assistant", "content": "msg4"},
        ]
        await subconscious.process_transcript(messages, session_key="s1")

        state = subconscious.memory.get_session_state()
        assert state["last_processed_index"] == 4

    @pytest.mark.asyncio
    async def test_process_transcript_retry(self, subconscious: Subconscious, mock_provider):
        """Retries once on failure."""
        # First call fails, second succeeds
        mock_provider.chat_with_retry.side_effect = [
            Exception("LLM error"),
            MagicMock(content='{"updates": {}, "reasoning": "ok"}'),
        ]
        messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
            {"role": "user", "content": "msg3"},
        ]
        await subconscious.process_transcript(messages)
        assert mock_provider.chat_with_retry.call_count == 2
        assert subconscious._task_count == 1


# ── SubconsciousMemory session state tests ──


class TestSubconsciousMemorySessionState:
    def test_session_state_roundtrip(self, workspace: Path):
        mem1 = SubconsciousMemory(workspace)
        mem1.update_session_state(current_session="s1", last_processed_index=5)

        mem2 = SubconsciousMemory(workspace)
        state = mem2.get_session_state()
        assert state["current_session"] == "s1"
        assert state["last_processed_index"] == 5

    def test_session_state_defaults_empty(self, memory: SubconsciousMemory):
        state = memory.get_session_state()
        assert isinstance(state, dict)
