"""Sandbox tool — isolated Docker container for safe code execution."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from sharanbot.agent.tools.base import Tool

# Default image with common dev tools
DEFAULT_IMAGE = "python:3.11-slim"
MAX_OUTPUT_LEN = 30000
CONTAINER_TIMEOUT = 120  # seconds


class SandboxTool(Tool):
    """Run code safely in an isolated Docker container.

    Provides a sandboxed environment where SharanBot can:
    - Execute arbitrary code without risking the host system
    - Install packages, start servers, run tests
    - Build and test applications in isolation

    Requires Docker to be installed and accessible.
    """

    def __init__(self, workspace: Path | None = None):
        self._workspace = workspace or Path.cwd()
        self._containers: dict[str, str] = {}  # sandbox_id → container_id

    @property
    def name(self) -> str:
        return "sandbox"

    @property
    def description(self) -> str:
        return (
            "Run commands in an isolated Docker sandbox. "
            "Actions: create (start new sandbox), exec (run command in sandbox), "
            "copy_in (copy file into sandbox), copy_out (copy file from sandbox), "
            "stop (stop sandbox), list (list active sandboxes). "
            "Use for: running untrusted code, testing builds, installing packages, "
            "starting servers, and any operation that should not affect the host system."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Sandbox action: create, exec, copy_in, copy_out, stop, list",
                    "enum": ["create", "exec", "copy_in", "copy_out", "stop", "list"],
                },
                "sandbox_id": {
                    "type": "string",
                    "description": "ID of the sandbox to operate on (returned by create)",
                },
                "command": {
                    "type": "string",
                    "description": "Shell command to execute in the sandbox (for 'exec' action)",
                },
                "image": {
                    "type": "string",
                    "description": f"Docker image to use (default: {DEFAULT_IMAGE})",
                },
                "src_path": {
                    "type": "string",
                    "description": "Source file path for copy operations",
                },
                "dest_path": {
                    "type": "string",
                    "description": "Destination path for copy operations",
                },
                "timeout": {
                    "type": "integer",
                    "description": f"Command timeout in seconds (default: {CONTAINER_TIMEOUT})",
                },
            },
            "required": ["action"],
        }

    async def _docker_available(self) -> bool:
        """Check if Docker is available."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "version", "--format", "{{.Server.Version}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            return proc.returncode == 0
        except FileNotFoundError:
            return False

    async def execute(self, action: str, **kwargs: Any) -> str:
        """Execute a sandbox action."""
        action = action.lower().strip()

        if action == "list":
            return self._list_sandboxes()

        if action == "create":
            return await self._create(**kwargs)

        if action not in ("exec", "copy_in", "copy_out", "stop"):
            return f"Unknown action '{action}'. Use: create, exec, copy_in, copy_out, stop, list"

        sandbox_id = kwargs.pop("sandbox_id", "")
        if not sandbox_id or sandbox_id not in self._containers:
            available = ", ".join(self._containers.keys()) if self._containers else "none"
            return f"Invalid sandbox_id. Active sandboxes: {available}"

        dispatch = {
            "exec": self._exec,
            "copy_in": self._copy_in,
            "copy_out": self._copy_out,
            "stop": self._stop,
        }
        try:
            return await dispatch[action](sandbox_id=sandbox_id, **kwargs)
        except Exception as e:
            return f"Sandbox error ({action}): {e}"

    async def _create(self, image: str = DEFAULT_IMAGE, **_: Any) -> str:
        if not await self._docker_available():
            return (
                "Docker is not available. Install Docker Desktop to use sandboxes.\n"
                "Download: https://docs.docker.com/get-docker/"
            )

        sandbox_id = f"sb-{uuid.uuid4().hex[:8]}"
        container_name = f"sharanbot-sandbox-{sandbox_id}"

        # Start a persistent container with limited resources
        cmd = [
            "docker", "run", "-d",
            "--name", container_name,
            "--memory", "512m",
            "--cpus", "1.0",
            "--network", "bridge",
            "--security-opt", "no-new-privileges",
            "--cap-drop", "ALL",
            "--read-only",
            "--tmpfs", "/tmp:rw,noexec,size=256m",
            "--pids-limit", "256",
            image,
            "sleep", "3600",  # Keep alive for 1 hour
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error = stderr.decode().strip()
            return f"Failed to create sandbox: {error}"

        container_id = stdout.decode().strip()[:12]
        self._containers[sandbox_id] = container_name
        logger.info("Created sandbox {} (container: {})", sandbox_id, container_name)

        return (
            f"Sandbox created: {sandbox_id}\n"
            f"Image: {image}\n"
            f"Container: {container_name}\n"
            f"Memory: 512MB, CPU: 1 core\n"
            f"Use sandbox_id='{sandbox_id}' for subsequent operations."
        )

    async def _exec(
        self, sandbox_id: str, command: str = "", timeout: int = CONTAINER_TIMEOUT, **_: Any
    ) -> str:
        if not command:
            return "Error: 'command' is required for exec action."

        container = self._containers[sandbox_id]
        cmd = ["docker", "exec", container, "sh", "-c", command]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            return f"Command timed out after {timeout}s. Use a higher timeout or break up the command."

        output = stdout.decode(errors="replace")
        err_output = stderr.decode(errors="replace")
        combined = output + ("\n--- STDERR ---\n" + err_output if err_output else "")

        if len(combined) > MAX_OUTPUT_LEN:
            separator = f"\n\n... ({len(combined) - MAX_OUTPUT_LEN:,} chars truncated) ...\n\n"
            half = (MAX_OUTPUT_LEN - len(separator)) // 2
            combined = combined[:half] + separator + combined[-half:]

        exit_code = proc.returncode
        return f"Exit code: {exit_code}\n\n{combined}"

    async def _copy_in(
        self, sandbox_id: str, src_path: str = "", dest_path: str = "/workspace/", **_: Any
    ) -> str:
        if not src_path:
            return "Error: 'src_path' is required for copy_in action."
        src = Path(src_path)
        if not src.exists():
            return f"Source file not found: {src_path}"

        container = self._containers[sandbox_id]
        cmd = ["docker", "cp", str(src), f"{container}:{dest_path}"]

        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            return f"Copy failed: {stderr.decode().strip()}"

        return f"Copied {src_path} → {container}:{dest_path}"

    async def _copy_out(
        self, sandbox_id: str, src_path: str = "", dest_path: str = "", **_: Any
    ) -> str:
        if not src_path:
            return "Error: 'src_path' (path inside container) is required for copy_out."
        if not dest_path:
            dest_path = str(self._workspace / Path(src_path).name)

        container = self._containers[sandbox_id]
        cmd = ["docker", "cp", f"{container}:{src_path}", dest_path]

        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            return f"Copy failed: {stderr.decode().strip()}"

        return f"Copied {container}:{src_path} → {dest_path}"

    async def _stop(self, sandbox_id: str, **_: Any) -> str:
        container = self._containers.pop(sandbox_id, None)
        if not container:
            return f"Sandbox {sandbox_id} not found."

        cmd = ["docker", "rm", "-f", container]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        logger.info("Stopped sandbox {} (container: {})", sandbox_id, container)
        return f"Sandbox {sandbox_id} stopped and removed."

    def _list_sandboxes(self) -> str:
        if not self._containers:
            return "No active sandboxes."
        lines = ["Active sandboxes:"]
        for sid, cname in self._containers.items():
            lines.append(f"  - {sid} (container: {cname})")
        return "\n".join(lines)
