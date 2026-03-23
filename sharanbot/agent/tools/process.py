"""Background process management tools.

Provides tools for starting, monitoring, and stopping long-running processes
like dev servers (npm run dev, python manage.py runserver, flask run, etc.).

This solves the critical gap where exec() blocks until completion — dev servers
never return, so exec() times out and kills the server. These tools let the
agent start a process in the background, check if it's ready, read its output,
and shut it down when done.
"""

from __future__ import annotations

import asyncio
import os
import re
import socket
import time
from typing import Any

from loguru import logger

from sharanbot.agent.tools.base import Tool


class _ProcessEntry:
    """Internal record for a managed background process."""

    __slots__ = (
        "pid", "proc", "command", "working_dir", "started_at",
        "output_lines", "max_lines",
    )

    def __init__(
        self,
        pid: str,
        proc: asyncio.subprocess.Process,
        command: str,
        working_dir: str,
    ):
        self.pid = pid
        self.proc = proc
        self.command = command
        self.working_dir = working_dir
        self.started_at = time.time()
        self.output_lines: list[str] = []
        self.max_lines = 500

    @property
    def alive(self) -> bool:
        return self.proc.returncode is None

    @property
    def uptime(self) -> float:
        return time.time() - self.started_at

    def append_output(self, line: str) -> None:
        self.output_lines.append(line)
        if len(self.output_lines) > self.max_lines:
            self.output_lines = self.output_lines[-self.max_lines:]


# Global process registry — shared across tool instances
_PROCESS_REGISTRY: dict[str, _ProcessEntry] = {}
_COUNTER = 0


def _next_id() -> str:
    global _COUNTER
    _COUNTER += 1
    return f"bg_{_COUNTER}"


async def _read_stream(entry: _ProcessEntry, stream: asyncio.StreamReader | None) -> None:
    """Background task: read stream line by line and buffer output."""
    if stream is None:
        return
    try:
        while True:
            line_bytes = await stream.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace").rstrip("\n\r")
            entry.append_output(line)
    except Exception:
        pass


class ProcessStartTool(Tool):
    """Start a command as a background process."""

    def __init__(self, working_dir: str | None = None, path_append: str = ""):
        self._working_dir = working_dir
        self._path_append = path_append

    @property
    def name(self) -> str:
        return "process_start"

    @property
    def description(self) -> str:
        return (
            "Start a long-running command in the background (e.g. dev server, watcher). "
            "Returns a process ID to monitor or stop it later. "
            "Use this instead of exec for commands that run indefinitely like "
            "'npm run dev', 'python manage.py runserver', 'flask run', etc."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The command to run in the background",
                },
                "working_dir": {
                    "type": "string",
                    "description": "Working directory for the command",
                },
                "wait_for": {
                    "type": "string",
                    "description": (
                        "Optional text pattern to wait for in output before returning "
                        "(e.g. 'Listening on port', 'ready', 'compiled successfully'). "
                        "Waits up to wait_timeout seconds."
                    ),
                },
                "wait_timeout": {
                    "type": "integer",
                    "description": "Max seconds to wait for wait_for pattern (default 30)",
                    "minimum": 1,
                    "maximum": 120,
                },
            },
            "required": ["command"],
        }

    async def execute(
        self,
        command: str,
        working_dir: str | None = None,
        wait_for: str | None = None,
        wait_timeout: int = 30,
        **kwargs: Any,
    ) -> str:
        cwd = working_dir or self._working_dir or os.getcwd()

        if not os.path.isdir(cwd):
            return f"Error: Working directory does not exist: {cwd}"

        # Safety: block dangerous commands
        lower = command.lower().strip()
        dangerous = [
            r"\brm\s+-[rf]{1,2}\b", r"\bdel\s+/[fq]\b", r"\brmdir\s+/s\b",
            r"\b(shutdown|reboot|poweroff)\b", r":\(\)\s*\{.*\};\s*:",
        ]
        for pat in dangerous:
            if re.search(pat, lower):
                return "Error: Command blocked by safety guard"

        env = os.environ.copy()
        if self._path_append:
            env["PATH"] = env.get("PATH", "") + os.pathsep + self._path_append

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
                env=env,
            )
        except Exception as e:
            return f"Error starting process: {e}"

        pid = _next_id()
        entry = _ProcessEntry(pid=pid, proc=proc, command=command, working_dir=cwd)
        _PROCESS_REGISTRY[pid] = entry

        # Start background reader
        asyncio.create_task(_read_stream(entry, proc.stdout))

        logger.info("Background process started: {} (pid={}, cmd={})", pid, proc.pid, command[:80])

        # Optionally wait for readiness pattern
        if wait_for:
            pattern = wait_for.lower()
            deadline = time.time() + min(wait_timeout, 120)
            seen_lines = 0
            while time.time() < deadline:
                await asyncio.sleep(0.3)
                # Check if process died
                if not entry.alive:
                    output = "\n".join(entry.output_lines[-20:])
                    return (
                        f"Error: Process exited immediately (code {proc.returncode})\n"
                        f"Process ID: {pid}\n"
                        f"Output:\n{output}"
                    )
                # Check new output lines for pattern
                while seen_lines < len(entry.output_lines):
                    if pattern in entry.output_lines[seen_lines].lower():
                        output = "\n".join(entry.output_lines[-10:])
                        return (
                            f"Process started and ready.\n"
                            f"Process ID: {pid}\n"
                            f"Matched: '{wait_for}'\n"
                            f"Recent output:\n{output}"
                        )
                    seen_lines += 1

            # Timeout waiting for pattern
            output = "\n".join(entry.output_lines[-15:])
            alive_status = "running" if entry.alive else f"exited (code {proc.returncode})"
            return (
                f"Process started but '{wait_for}' not detected within {wait_timeout}s.\n"
                f"Process ID: {pid}\n"
                f"Status: {alive_status}\n"
                f"Recent output:\n{output}"
            )

        # No wait_for: give process a moment to fail fast
        await asyncio.sleep(0.5)
        if not entry.alive:
            output = "\n".join(entry.output_lines[-20:])
            return (
                f"Error: Process exited immediately (code {proc.returncode})\n"
                f"Process ID: {pid}\n"
                f"Output:\n{output}"
            )

        return f"Process started in background.\nProcess ID: {pid}\nCommand: {command}"


class ProcessStatusTool(Tool):
    """Check the status of a background process."""

    @property
    def name(self) -> str:
        return "process_status"

    @property
    def description(self) -> str:
        return (
            "Check if a background process is still running, view its recent output, "
            "or list all managed background processes."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "process_id": {
                    "type": "string",
                    "description": (
                        "The process ID returned by process_start. "
                        "Omit to list all managed processes."
                    ),
                },
                "tail": {
                    "type": "integer",
                    "description": "Number of recent output lines to show (default 20)",
                    "minimum": 1,
                    "maximum": 100,
                },
            },
            "required": [],
        }

    async def execute(
        self,
        process_id: str | None = None,
        tail: int = 20,
        **kwargs: Any,
    ) -> str:
        if process_id is None:
            # List all processes
            if not _PROCESS_REGISTRY:
                return "No managed background processes."
            lines = ["Managed background processes:\n"]
            for pid, entry in _PROCESS_REGISTRY.items():
                status = "running" if entry.alive else f"exited (code {entry.proc.returncode})"
                uptime = f"{entry.uptime:.0f}s"
                lines.append(f"  {pid}: {status} | uptime={uptime} | {entry.command[:60]}")
            return "\n".join(lines)

        entry = _PROCESS_REGISTRY.get(process_id)
        if not entry:
            return f"Error: Process '{process_id}' not found. Use process_status without args to list all."

        status = "running" if entry.alive else f"exited (code {entry.proc.returncode})"
        output = "\n".join(entry.output_lines[-tail:]) if entry.output_lines else "(no output)"

        return (
            f"Process: {process_id}\n"
            f"Status: {status}\n"
            f"Command: {entry.command}\n"
            f"Working dir: {entry.working_dir}\n"
            f"Uptime: {entry.uptime:.0f}s\n"
            f"Output lines buffered: {len(entry.output_lines)}\n\n"
            f"Recent output ({min(tail, len(entry.output_lines))} lines):\n{output}"
        )


class ProcessStopTool(Tool):
    """Stop a background process."""

    @property
    def name(self) -> str:
        return "process_stop"

    @property
    def description(self) -> str:
        return "Stop a running background process by its process ID."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "process_id": {
                    "type": "string",
                    "description": "The process ID to stop",
                },
            },
            "required": ["process_id"],
        }

    async def execute(self, process_id: str, **kwargs: Any) -> str:
        entry = _PROCESS_REGISTRY.get(process_id)
        if not entry:
            return f"Error: Process '{process_id}' not found."

        if not entry.alive:
            tail = "\n".join(entry.output_lines[-10:])
            _PROCESS_REGISTRY.pop(process_id, None)
            return (
                f"Process {process_id} already exited (code {entry.proc.returncode}).\n"
                f"Last output:\n{tail}"
            )

        try:
            entry.proc.terminate()
            try:
                await asyncio.wait_for(entry.proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                entry.proc.kill()
                await asyncio.wait_for(entry.proc.wait(), timeout=3)
        except Exception as e:
            return f"Error stopping process: {e}"

        tail = "\n".join(entry.output_lines[-10:])
        _PROCESS_REGISTRY.pop(process_id, None)
        logger.info("Background process stopped: {}", process_id)

        return (
            f"Process {process_id} stopped (code {entry.proc.returncode}).\n"
            f"Last output:\n{tail}"
        )


class PortCheckTool(Tool):
    """Check if a network port is available or in use."""

    @property
    def name(self) -> str:
        return "port_check"

    @property
    def description(self) -> str:
        return (
            "Check if a TCP port is available or in use. "
            "Useful before starting a dev server to avoid port conflicts, "
            "or to verify a server is listening after startup."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "port": {
                    "type": "integer",
                    "description": "The port number to check",
                    "minimum": 1,
                    "maximum": 65535,
                },
                "host": {
                    "type": "string",
                    "description": "Host to check (default: 127.0.0.1)",
                },
            },
            "required": ["port"],
        }

    async def execute(self, port: int, host: str = "127.0.0.1", **kwargs: Any) -> str:
        # Validate host to prevent SSRF
        allowed_hosts = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}
        if host not in allowed_hosts:
            return "Error: Only localhost checks are allowed (127.0.0.1, localhost, 0.0.0.0, ::1)"

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((host, port))
            sock.close()

            if result == 0:
                return f"Port {port} on {host}: IN USE (something is listening)"
            else:
                return f"Port {port} on {host}: AVAILABLE (nothing listening)"
        except Exception as e:
            return f"Error checking port {port}: {e}"


class WaitForPortTool(Tool):
    """Wait for a port to become available (something starts listening)."""

    @property
    def name(self) -> str:
        return "wait_for_port"

    @property
    def description(self) -> str:
        return (
            "Wait until a TCP port has something listening on it. "
            "Use after process_start to confirm a dev server is ready to accept connections."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "port": {
                    "type": "integer",
                    "description": "The port to wait for",
                    "minimum": 1,
                    "maximum": 65535,
                },
                "timeout": {
                    "type": "integer",
                    "description": "Max seconds to wait (default 30)",
                    "minimum": 1,
                    "maximum": 120,
                },
                "host": {
                    "type": "string",
                    "description": "Host to check (default: 127.0.0.1)",
                },
            },
            "required": ["port"],
        }

    async def execute(
        self, port: int, timeout: int = 30, host: str = "127.0.0.1", **kwargs: Any,
    ) -> str:
        allowed_hosts = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}
        if host not in allowed_hosts:
            return "Error: Only localhost checks are allowed"

        deadline = time.time() + min(timeout, 120)
        while time.time() < deadline:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex((host, port))
                sock.close()
                if result == 0:
                    return f"Port {port} is ready (listening on {host})"
            except Exception:
                pass
            await asyncio.sleep(0.5)

        return f"Timeout: Port {port} on {host} not ready after {timeout}s"
