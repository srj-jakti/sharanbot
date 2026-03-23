"""Tests for the self-evolution meta-tool (evolve.py)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from sharanbot.agent.tools.evolve import EvolveTool


def _make_loop(tmp_path: Path) -> MagicMock:
    loop = MagicMock()
    loop.workspace = tmp_path
    loop.worker_manager = None  # default: no worker
    loop.handle_reload = AsyncMock(return_value="Reloaded.")
    return loop


class TestEvolveToolProperties:
    def test_name(self, tmp_path):
        tool = EvolveTool(_make_loop(tmp_path))
        assert tool.name == "evolve"

    def test_parameters_required(self, tmp_path):
        tool = EvolveTool(_make_loop(tmp_path))
        params = tool.parameters
        assert "capability_type" in params["properties"]
        assert "capability_name" in params["properties"]
        assert "specification" in params["properties"]
        assert set(params["required"]) == {"capability_type", "capability_name", "specification"}


class TestEvolveExecuteNoWorker:
    @pytest.mark.asyncio
    async def test_fallback_message(self, tmp_path):
        tool = EvolveTool(_make_loop(tmp_path))
        result = await tool.execute(
            capability_type="tool",
            capability_name="my_tool",
            specification="does something",
        )
        assert "No worker available" in result
        assert "my_tool" in result

    @pytest.mark.asyncio
    async def test_unknown_type(self, tmp_path):
        tool = EvolveTool(_make_loop(tmp_path))
        result = await tool.execute(
            capability_type="invalid",
            capability_name="x",
            specification="y",
        )
        assert "unknown capability type" in result.lower()


class TestEvolveExecuteWithWorker:
    @pytest.mark.asyncio
    async def test_success_path(self, tmp_path):
        loop = _make_loop(tmp_path)
        loop.worker_manager = MagicMock()
        loop.worker_manager.spawn = AsyncMock(return_value="worker-id-1")

        tool = EvolveTool(loop)
        dest = tmp_path / "plugins" / "tools" / "hello.py"
        # Simulate the worker writing a valid tool file
        dest.parent.mkdir(parents=True, exist_ok=True)

        async def _fake_spawn(**kwargs):
            dest.write_text(
                "from sharanbot.agent.tools.base import Tool\n"
                "class Hello(Tool):\n"
                "    name = 'hello'\n"
                "    description = 'say hello'\n"
                "    parameters = {}\n"
                "    async def execute(self, **kw): return 'hi'\n",
                encoding="utf-8",
            )
            return "worker-ok"

        loop.worker_manager.spawn = AsyncMock(side_effect=_fake_spawn)

        result = await tool.execute(
            capability_type="tool",
            capability_name="hello",
            specification="greet the user",
        )
        assert "Evolution started" in result
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_validation_rejects_syntax_error(self, tmp_path):
        loop = _make_loop(tmp_path)
        loop.worker_manager = MagicMock()
        dest = tmp_path / "plugins" / "tools" / "bad_tool.py"

        async def _write_bad(**kw):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("def broken(:\n  pass\n", encoding="utf-8")
            return "worker-ok"

        loop.worker_manager.spawn = AsyncMock(side_effect=_write_bad)

        tool = EvolveTool(loop)
        result = await tool.execute(
            capability_type="tool",
            capability_name="bad_tool",
            specification="won't work",
        )
        assert "FAILED" in result
        assert "Syntax error" in result
        # File should be cleaned up (no backup existed)
        assert not dest.exists()

    @pytest.mark.asyncio
    async def test_rollback_on_validation_failure(self, tmp_path):
        loop = _make_loop(tmp_path)
        loop.worker_manager = MagicMock()
        dest = tmp_path / "plugins" / "tools" / "existing.py"
        dest.parent.mkdir(parents=True, exist_ok=True)
        original = (
            "from sharanbot.agent.tools.base import Tool\n"
            "class Existing(Tool):\n"
            "    name = 'existing'\n"
            "    description = 'original'\n"
            "    parameters = {}\n"
            "    async def execute(self, **kw): return 'v1'\n"
        )
        dest.write_text(original, encoding="utf-8")

        async def _overwrite_bad(**kw):
            dest.write_text("not valid python {{{{", encoding="utf-8")
            return "worker-ok"

        loop.worker_manager.spawn = AsyncMock(side_effect=_overwrite_bad)

        tool = EvolveTool(loop)
        result = await tool.execute(
            capability_type="tool",
            capability_name="existing",
            specification="try to break it",
        )
        assert "FAILED" in result
        # Original content should be restored
        assert dest.read_text(encoding="utf-8") == original

    @pytest.mark.asyncio
    async def test_validation_rejects_no_tool_class(self, tmp_path):
        loop = _make_loop(tmp_path)
        loop.worker_manager = MagicMock()
        dest = tmp_path / "plugins" / "tools" / "no_class.py"

        async def _write_no_class(**kw):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("x = 42\n", encoding="utf-8")
            return "worker-ok"

        loop.worker_manager.spawn = AsyncMock(side_effect=_write_no_class)

        tool = EvolveTool(loop)
        result = await tool.execute(
            capability_type="tool",
            capability_name="no_class",
            specification="should fail",
        )
        assert "FAILED" in result
        assert "No class inheriting from Tool" in result


class TestValidatePythonTool:
    def test_valid_tool(self, tmp_path):
        f = tmp_path / "good.py"
        f.write_text(
            "from sharanbot.agent.tools.base import Tool\n"
            "class Good(Tool):\n"
            "    async def execute(self): pass\n",
            encoding="utf-8",
        )
        assert EvolveTool._validate_python_tool(f, "good") is None

    def test_syntax_error(self, tmp_path):
        f = tmp_path / "bad.py"
        f.write_text("def broken(:\n", encoding="utf-8")
        err = EvolveTool._validate_python_tool(f, "bad")
        assert err is not None
        assert "Syntax error" in err

    def test_no_tool_subclass(self, tmp_path):
        f = tmp_path / "nope.py"
        f.write_text("class Foo:\n  pass\n", encoding="utf-8")
        err = EvolveTool._validate_python_tool(f, "nope")
        assert err is not None
        assert "No class inheriting from Tool" in err

    def test_unreadable_file(self, tmp_path):
        f = tmp_path / "missing.py"
        err = EvolveTool._validate_python_tool(f, "missing")
        assert err is not None
        assert "Cannot read" in err
