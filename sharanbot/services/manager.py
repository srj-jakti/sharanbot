"""Autonomous service manager for SharanBot.

Manages long-running services, deployed apps, and autonomous agents.
Tracks lifecycle (start → running → stopped), health, and logs.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sharanbot.utils.helpers import atomic_write

from loguru import logger


@dataclass
class ServiceEntry:
    """Runtime state for a managed service."""

    id: str
    name: str
    kind: str  # "app" | "agent" | "monitor" | "cron" | "custom"
    status: str = "stopped"  # stopped | starting | running | error | completed
    pid: int | None = None
    started_at: float | None = None
    stopped_at: float | None = None
    error: str | None = None
    restart_count: int = 0
    max_restarts: int = 3
    auto_restart: bool = True
    command: str = ""
    working_dir: str = ""
    env: dict[str, str] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "status": self.status,
            "pid": self.pid,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "error": self.error,
            "restart_count": self.restart_count,
            "auto_restart": self.auto_restart,
            "command": self.command,
            "working_dir": self.working_dir,
            "uptime_s": (time.time() - self.started_at) if self.started_at and self.status == "running" else 0,
            "log_lines": len(self.logs),
            "metadata": self.metadata,
        }


class ServiceManager:
    """Manages autonomous services, deployed apps, and monitoring agents."""

    def __init__(self, workspace: Path):
        self._workspace = workspace
        self._services: dict[str, ServiceEntry] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._readers: dict[str, asyncio.Task] = {}
        self._state_path = workspace / "services.json"
        self._load_state()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def register(
        self,
        name: str,
        command: str,
        kind: str = "app",
        working_dir: str | None = None,
        env: dict[str, str] | None = None,
        auto_restart: bool = True,
        max_restarts: int = 3,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Register and start a new service. Returns service_id."""
        sid = str(uuid.uuid4())[:8]
        entry = ServiceEntry(
            id=sid,
            name=name,
            kind=kind,
            command=command,
            working_dir=working_dir or str(self._workspace),
            env=env or {},
            auto_restart=auto_restart,
            max_restarts=max_restarts,
            metadata=metadata or {},
        )
        self._services[sid] = entry
        await self._start_service(entry)
        self._save_state()
        logger.info("Service '{}' registered with id {}", name, sid)
        return sid

    async def stop(self, service_id: str) -> str:
        """Stop a running service."""
        entry = self._services.get(service_id)
        if not entry:
            return f"Service {service_id} not found."
        await self._stop_service(entry)
        return f"Service '{entry.name}' stopped."

    async def restart(self, service_id: str) -> str:
        """Restart a service."""
        entry = self._services.get(service_id)
        if not entry:
            return f"Service {service_id} not found."
        await self._stop_service(entry)
        await self._start_service(entry)
        return f"Service '{entry.name}' restarted."

    def remove(self, service_id: str) -> str:
        """Remove a service from tracking."""
        entry = self._services.pop(service_id, None)
        if not entry:
            return f"Service {service_id} not found."
        self._save_state()
        return f"Service '{entry.name}' removed."

    def get_status(self, service_id: str | None = None) -> dict[str, Any] | list[dict[str, Any]]:
        """Get status of one or all services."""
        if service_id:
            entry = self._services.get(service_id)
            return entry.to_dict() if entry else {"error": "not found"}
        return [e.to_dict() for e in self._services.values()]

    def get_logs(self, service_id: str, tail: int = 50) -> list[str]:
        """Get recent log lines from a service."""
        entry = self._services.get(service_id)
        if not entry:
            return [f"Service {service_id} not found."]
        return entry.logs[-tail:]

    def get_dashboard_snapshot(self) -> dict[str, Any]:
        """Full dashboard snapshot for the UI."""
        running = [e for e in self._services.values() if e.status == "running"]
        stopped = [e for e in self._services.values() if e.status == "stopped"]
        errored = [e for e in self._services.values() if e.status == "error"]
        return {
            "total": len(self._services),
            "running": len(running),
            "stopped": len(stopped),
            "errored": len(errored),
            "services": [e.to_dict() for e in self._services.values()],
        }

    async def health_check(self) -> dict[str, str]:
        """Check all services and auto-restart dead ones."""
        results: dict[str, str] = {}
        for sid, entry in list(self._services.items()):
            proc = self._processes.get(sid)
            if entry.status == "running" and (proc is None or proc.returncode is not None):
                entry.status = "error"
                entry.error = "Process exited unexpectedly"
                entry.stopped_at = time.time()
                logger.warning("Service '{}' died unexpectedly", entry.name)

                if entry.auto_restart and entry.restart_count < entry.max_restarts:
                    entry.restart_count += 1
                    await self._start_service(entry)
                    results[sid] = "auto-restarted"
                else:
                    results[sid] = "dead"
            else:
                results[sid] = entry.status
        self._save_state()
        return results

    async def stop_all(self) -> None:
        """Stop all services (called on gateway shutdown)."""
        for entry in list(self._services.values()):
            await self._stop_service(entry)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _start_service(self, entry: ServiceEntry) -> None:
        """Start the service process."""
        entry.status = "starting"
        try:
            import shlex
            import sys

            env = {**dict(__import__("os").environ), **entry.env}

            if sys.platform == "win32":
                cmd_parts = entry.command
                proc = await asyncio.create_subprocess_shell(
                    cmd_parts,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=entry.working_dir,
                    env=env,
                )
            else:
                cmd_parts = shlex.split(entry.command)
                proc = await asyncio.create_subprocess_exec(
                    *cmd_parts,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=entry.working_dir,
                    env=env,
                )

            self._processes[entry.id] = proc
            entry.pid = proc.pid
            entry.status = "running"
            entry.started_at = time.time()
            entry.error = None

            # Start log reader
            self._readers[entry.id] = asyncio.create_task(
                self._read_logs(entry, proc)
            )

            logger.info("Service '{}' started (pid={})", entry.name, proc.pid)
            self._save_state()

        except Exception as e:
            entry.status = "error"
            entry.error = str(e)
            logger.error("Failed to start service '{}': {}", entry.name, e)

    async def _stop_service(self, entry: ServiceEntry) -> None:
        """Stop a service process."""
        reader = self._readers.pop(entry.id, None)
        if reader and not reader.done():
            reader.cancel()

        proc = self._processes.pop(entry.id, None)
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            except ProcessLookupError:
                pass

        entry.status = "stopped"
        entry.stopped_at = time.time()
        entry.pid = None
        self._save_state()

    async def _read_logs(self, entry: ServiceEntry, proc: asyncio.subprocess.Process) -> None:
        """Read stdout from the service and buffer log lines."""
        max_log_lines = 500
        try:
            while proc.stdout and not proc.stdout.at_eof():
                raw = await proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    entry.logs.append(line)
                    if len(entry.logs) > max_log_lines:
                        entry.logs = entry.logs[-max_log_lines:]
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("Service '{}' log reader error: {}", entry.name, e)
        finally:
            if proc.returncode is not None and entry.status == "running":
                entry.status = "completed" if proc.returncode == 0 else "error"
                entry.stopped_at = time.time()
                if proc.returncode != 0:
                    entry.error = f"Exit code {proc.returncode}"

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_state(self) -> None:
        """Save service registry to disk (excludes runtime-only fields)."""
        data = []
        for e in self._services.values():
            data.append({
                "id": e.id,
                "name": e.name,
                "kind": e.kind,
                "command": e.command,
                "working_dir": e.working_dir,
                "env": e.env,
                "auto_restart": e.auto_restart,
                "max_restarts": e.max_restarts,
                "metadata": e.metadata,
                "status": e.status,
            })
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self._state_path, json.dumps(data, indent=2))

    def _load_state(self) -> None:
        """Load service registry from disk."""
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            for item in data:
                entry = ServiceEntry(
                    id=item["id"],
                    name=item["name"],
                    kind=item.get("kind", "app"),
                    command=item.get("command", ""),
                    working_dir=item.get("working_dir", str(self._workspace)),
                    env=item.get("env", {}),
                    auto_restart=item.get("auto_restart", True),
                    max_restarts=item.get("max_restarts", 3),
                    metadata=item.get("metadata", {}),
                    status="stopped",  # Always start as stopped
                )
                self._services[entry.id] = entry
        except Exception:
            logger.warning("Failed to load services state")
