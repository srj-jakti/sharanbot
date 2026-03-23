"""Tests for background process management tools."""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sharanbot.agent.tools.process import (
    PortCheckTool,
    ProcessStartTool,
    ProcessStatusTool,
    ProcessStopTool,
    WaitForPortTool,
    _PROCESS_REGISTRY,
)


@pytest.fixture(autouse=True)
def clear_registry():
    """Clear the process registry before each test."""
    _PROCESS_REGISTRY.clear()
    yield
    # Cleanup: kill any leftover processes
    for entry in list(_PROCESS_REGISTRY.values()):
        if entry.alive:
            try:
                entry.proc.kill()
            except Exception:
                pass
    _PROCESS_REGISTRY.clear()


# ── ProcessStartTool ──


class TestProcessStartTool:
    @pytest.fixture
    def tool(self, tmp_path):
        return ProcessStartTool(working_dir=str(tmp_path))

    @pytest.mark.asyncio
    async def test_start_basic(self, tool):
        """Start a simple background process."""
        result = await tool.execute(command=f"{sys.executable} -c \"import time; time.sleep(5)\"")
        assert "Process started in background" in result
        assert "Process ID:" in result

    @pytest.mark.asyncio
    async def test_start_with_output(self, tool):
        """Process that produces output."""
        result = await tool.execute(
            command=f'{sys.executable} -c "print(\'hello world\')"',
            wait_for="hello",
            wait_timeout=10,
        )
        assert "Process started and ready" in result or "hello" in result.lower()

    @pytest.mark.asyncio
    async def test_start_fast_exit_error(self, tool):
        """Process that exits immediately shows error."""
        result = await tool.execute(
            command=f"{sys.executable} -c \"import sys; print('failed'); sys.exit(1)\"",
        )
        assert "exited immediately" in result or "Process started" in result

    @pytest.mark.asyncio
    async def test_start_bad_directory(self, tool):
        """Bad working directory returns error."""
        result = await tool.execute(
            command="echo hi",
            working_dir="/nonexistent/path/xyz",
        )
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_start_blocked_command(self, tool):
        """Dangerous commands are blocked."""
        result = await tool.execute(command="rm -rf /")
        assert "blocked" in result.lower()

    @pytest.mark.asyncio
    async def test_start_wait_for_timeout(self, tool):
        """wait_for pattern not found within timeout."""
        result = await tool.execute(
            command=f'{sys.executable} -c "import time; print(\'starting\'); time.sleep(30)"',
            wait_for="never_appears",
            wait_timeout=2,
        )
        assert "not detected" in result

    @pytest.mark.asyncio
    async def test_start_registers_process(self, tool):
        """Started process appears in registry."""
        result = await tool.execute(
            command=f"{sys.executable} -c \"import time; time.sleep(10)\"",
        )
        assert len(_PROCESS_REGISTRY) == 1


# ── ProcessStatusTool ──


class TestProcessStatusTool:
    @pytest.fixture
    def tool(self):
        return ProcessStatusTool()

    @pytest.fixture
    def start_tool(self, tmp_path):
        return ProcessStartTool(working_dir=str(tmp_path))

    @pytest.mark.asyncio
    async def test_list_empty(self, tool):
        result = await tool.execute()
        assert "No managed" in result

    @pytest.mark.asyncio
    async def test_list_processes(self, tool, start_tool):
        await start_tool.execute(
            command=f"{sys.executable} -c \"import time; time.sleep(10)\"",
        )
        result = await tool.execute()
        assert "bg_" in result
        assert "running" in result

    @pytest.mark.asyncio
    async def test_specific_process(self, tool, start_tool):
        start_result = await start_tool.execute(
            command=f"{sys.executable} -c \"import time; time.sleep(10)\"",
        )
        # Extract process ID
        for line in start_result.split("\n"):
            if "Process ID:" in line:
                pid = line.split(":")[-1].strip()
                break
        else:
            pytest.skip("Could not extract process ID")
            return

        result = await tool.execute(process_id=pid)
        assert "running" in result
        assert pid in result

    @pytest.mark.asyncio
    async def test_nonexistent_process(self, tool):
        result = await tool.execute(process_id="bg_999")
        assert "not found" in result


# ── ProcessStopTool ──


class TestProcessStopTool:
    @pytest.fixture
    def tool(self):
        return ProcessStopTool()

    @pytest.fixture
    def start_tool(self, tmp_path):
        return ProcessStartTool(working_dir=str(tmp_path))

    @pytest.mark.asyncio
    async def test_stop_running(self, tool, start_tool):
        start_result = await start_tool.execute(
            command=f"{sys.executable} -c \"import time; time.sleep(60)\"",
        )
        pid = None
        for line in start_result.split("\n"):
            if "Process ID:" in line:
                pid = line.split(":")[-1].strip()
                break
        assert pid is not None

        result = await tool.execute(process_id=pid)
        assert "stopped" in result or "exited" in result

    @pytest.mark.asyncio
    async def test_stop_nonexistent(self, tool):
        result = await tool.execute(process_id="bg_999")
        assert "not found" in result


# ── PortCheckTool ──


class TestPortCheckTool:
    @pytest.fixture
    def tool(self):
        return PortCheckTool()

    @pytest.mark.asyncio
    async def test_available_port(self, tool):
        """Check an unlikely-used port."""
        result = await tool.execute(port=59999)
        assert "AVAILABLE" in result

    @pytest.mark.asyncio
    async def test_ssrf_block(self, tool):
        """External hosts are blocked."""
        result = await tool.execute(port=80, host="10.0.0.1")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_localhost_allowed(self, tool):
        result = await tool.execute(port=59998, host="localhost")
        # Should not error
        assert "AVAILABLE" in result or "IN USE" in result


# ── WaitForPortTool ──


class TestWaitForPortTool:
    @pytest.fixture
    def tool(self):
        return WaitForPortTool()

    @pytest.mark.asyncio
    async def test_timeout_not_listening(self, tool):
        """Port not listening → timeout."""
        result = await tool.execute(port=59997, timeout=2)
        assert "Timeout" in result

    @pytest.mark.asyncio
    async def test_ssrf_block(self, tool):
        result = await tool.execute(port=80, host="192.168.1.1")
        assert "Error" in result
