"""Tests for Spec Kit integration tool."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sharanbot.agent.tools.specify_init import SpecifyInitTool, _find_specify, auto_init_speckit


class TestSpecifyInitTool:
    """Tests for the SpecifyInitTool."""

    def test_tool_metadata(self):
        tool = SpecifyInitTool()
        assert tool.name == "specify_init"
        assert "Spec Kit" in tool.description
        assert "project_path" in tool.parameters["properties"]

    @pytest.mark.asyncio
    async def test_execute_no_cli_available(self, tmp_path):
        """When neither specify nor uvx is available, return install instructions."""
        tool = SpecifyInitTool()
        with patch("sharanbot.agent.tools.specify_init.shutil.which", return_value=None):
            result = await tool.execute(project_path=str(tmp_path / "myproject"))
        assert "not found" in result.lower() or "Install" in result

    @pytest.mark.asyncio
    async def test_execute_success(self, tmp_path):
        """When CLI succeeds, return success message."""
        tool = SpecifyInitTool()
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"Initialized successfully", None))

        with patch("sharanbot.agent.tools.specify_init._find_specify", return_value="specify"), \
             patch("asyncio.create_subprocess_shell", return_value=mock_proc):
            result = await tool.execute(project_path=str(project_dir))

        assert "✅" in result
        assert "Spec Kit" in result

    @pytest.mark.asyncio
    async def test_execute_failure(self, tmp_path):
        """When CLI fails, return error message."""
        tool = SpecifyInitTool()
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"Error: something went wrong", None))

        with patch("sharanbot.agent.tools.specify_init._find_specify", return_value="specify"), \
             patch("asyncio.create_subprocess_shell", return_value=mock_proc):
            result = await tool.execute(project_path=str(project_dir))

        assert "failed" in result.lower()

    @pytest.mark.asyncio
    async def test_execute_timeout(self, tmp_path):
        """When CLI times out, return timeout message."""
        tool = SpecifyInitTool()
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()

        with patch("sharanbot.agent.tools.specify_init._find_specify", return_value="specify"), \
             patch("asyncio.create_subprocess_shell", side_effect=asyncio.TimeoutError()):
            result = await tool.execute(project_path=str(project_dir))

        assert "timed out" in result.lower() or "error" in result.lower()

    @pytest.mark.asyncio
    async def test_auto_init_speckit_no_cli(self, tmp_path):
        """auto_init_speckit returns None when CLI not available."""
        with patch("sharanbot.agent.tools.specify_init._find_specify", return_value=None):
            result = await auto_init_speckit(tmp_path)
        assert result is None

    @pytest.mark.asyncio
    async def test_auto_init_speckit_success(self, tmp_path):
        """auto_init_speckit returns result when CLI available."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"Done", None))

        with patch("sharanbot.agent.tools.specify_init._find_specify", return_value="specify"), \
             patch("asyncio.create_subprocess_shell", return_value=mock_proc):
            result = await auto_init_speckit(tmp_path)

        assert result is not None
        assert "✅" in result


class TestFindSpecify:
    """Tests for _find_specify helper."""

    def test_finds_specify_in_path(self):
        with patch("sharanbot.agent.tools.specify_init.shutil.which") as mock_which:
            mock_which.side_effect = lambda x: "/usr/bin/specify" if x == "specify" else None
            assert _find_specify() == "specify"

    def test_falls_back_to_uvx(self):
        with patch("sharanbot.agent.tools.specify_init.shutil.which") as mock_which:
            mock_which.side_effect = lambda x: "/usr/bin/uvx" if x == "uvx" else None
            result = _find_specify()
            assert result is not None
            assert "uvx" in result

    def test_returns_none_when_nothing_available(self):
        with patch("sharanbot.agent.tools.specify_init.shutil.which", return_value=None):
            assert _find_specify() is None
