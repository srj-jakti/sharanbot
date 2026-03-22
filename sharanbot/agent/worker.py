"""Worker manager for GSD-based worker processes.

Manages GSD (Get Shit Done) worker processes that operate via JSON-RPC over
stdin/stdout.  Each worker runs inside a GSD Node.js process spawned with
``--mode rpc``, and SharanBot communicates with it using JSON-line messages.

Workers share SharanBot's LLM API key, MCP servers, and skills.
"""

import asyncio
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from sharanbot.agent.worker_registry import WorkerTypeRegistry


# ---------------------------------------------------------------------------
# Worker state
# ---------------------------------------------------------------------------

class WorkerState:
    """Tracks a single GSD worker process."""

    __slots__ = (
        "id", "label", "project_dir", "process", "reader_task",
        "status", "messages", "origin",
    )

    def __init__(
        self,
        worker_id: str,
        label: str,
        project_dir: Path,
        process: asyncio.subprocess.Process,
        origin: dict[str, str],
    ):
        self.id = worker_id
        self.label = label
        self.project_dir = project_dir
        self.process = process
        self.status: str = "running"
        self.messages: list[dict[str, Any]] = []
        self.origin = origin
        self.reader_task: asyncio.Task | None = None


# ---------------------------------------------------------------------------
# WorkerManager
# ---------------------------------------------------------------------------

class WorkerManager:
    """Manages GSD worker processes for SharanBot."""

    def __init__(
        self,
        workspace: Path,
        api_key: str,
        api_base: str | None = None,
        model: str | None = None,
        extra_headers: dict[str, str] | None = None,
        mcp_servers: dict | None = None,
        worker_type_registry: WorkerTypeRegistry | None = None,
    ):
        self.workspace = workspace
        self.api_key = api_key
        self.api_base = api_base or ""
        self.model = model or ""
        self.extra_headers = extra_headers or {}
        self.mcp_servers = mcp_servers or {}
        self._workers: dict[str, WorkerState] = {}
        self.type_registry = worker_type_registry or WorkerTypeRegistry(
            plugins_dir=workspace / "plugins" / "workers"
        )
        self._recovery_engine = None  # Set via set_recovery_engine()

    def set_recovery_engine(self, engine) -> None:
        """Attach a RecoveryEngine for auto-recovery of stuck workers."""
        self._recovery_engine = engine

    async def health_check(self) -> dict[str, str]:
        """Check all workers and auto-restart stuck/dead ones.

        Returns a mapping of worker_id to status.
        """
        results: dict[str, str] = {}
        for wid, worker in list(self._workers.items()):
            if worker.process.returncode is not None and worker.status == "running":
                # Process exited but status not updated
                worker.status = "stopped"
                logger.warning("Worker [{}] died unexpectedly (rc={})", wid, worker.process.returncode)
                if self._recovery_engine:
                    await self._recovery_engine.handle_error(
                        RuntimeError(f"Worker {wid} exited with code {worker.process.returncode}"),
                        component=f"worker_{wid}",
                        context={"worker_id": wid, "label": worker.label},
                    )
                results[wid] = "dead"
            else:
                results[wid] = worker.status
        return results

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        project_dir: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        worker_type: str = "gsd",
    ) -> str:
        """Spawn a worker process of the given type.

        Returns a human-readable status string.
        """
        # Look up worker type from registry
        wtype = self.type_registry.get(worker_type)
        if not wtype:
            available = ", ".join(t.name for t in self.type_registry.list_types())
            return f"Error: Unknown worker type '{worker_type}'. Available: {available}"

        worker_id = str(uuid.uuid4())[:8]
        display_label = label or task[:40] + ("…" if len(task) > 40 else "")
        proj = Path(project_dir) if project_dir else self.workspace
        origin = {"channel": origin_channel, "chat_id": origin_chat_id}

        # Prepare home directory for this worker
        worker_home = self.workspace / ".sharanbot_workers" / worker_id
        worker_home.mkdir(parents=True, exist_ok=True)

        # Write models.json so worker can use SharanBot's LLM
        self._write_models_json(worker_home)

        # Write .mcp.json for MCP server sharing
        self._write_mcp_json(worker_home)

        # Build environment
        env = self._build_env(worker_home)

        # Determine runtime executable
        loader_path = Path(wtype.loader_path)
        if not loader_path.exists():
            return f"Error: Worker loader not found at {loader_path}"

        runtime = wtype.runtime or "node"
        runtime_exe = shutil.which(runtime)
        if not runtime_exe:
            return f"Error: {runtime} not found. Cannot start {wtype.display_name} worker."

        cmd = [runtime_exe, str(loader_path)] + list(wtype.mode_args)
        if self.model:
            cmd.extend(["--model", f"sharanbot-llm/{self.model}"])

        logger.info("Spawning {} worker [{}]: {} in {}", wtype.display_name, worker_id, display_label, proj)

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(proj),
            env=env,
        )

        worker = WorkerState(
            worker_id=worker_id,
            label=display_label,
            project_dir=proj,
            process=process,
            origin=origin,
        )
        self._workers[worker_id] = worker

        # Start background reader for stdout
        worker.reader_task = asyncio.create_task(
            self._read_output(worker),
        )

        # Send the initial prompt
        await self._send_rpc(worker, {"type": "prompt", "message": task})

        return (
            f"Worker **{display_label}** started (id: `{worker_id}`).\n"
            f"Project: `{proj}`\n"
            f"Use `worker_status` to check progress or `worker_steer` to give guidance."
        )

    async def stop(self, worker_id: str) -> str:
        """Stop a running worker."""
        worker = self._workers.get(worker_id)
        if not worker:
            return f"Worker `{worker_id}` not found."
        await self._kill_worker(worker)
        return f"Worker `{worker.label}` stopped."

    async def steer(self, worker_id: str, message: str) -> str:
        """Send a follow-up message to a running worker."""
        worker = self._workers.get(worker_id)
        if not worker:
            return f"Worker `{worker_id}` not found."
        if worker.status != "running":
            return f"Worker `{worker.label}` is not running (status: {worker.status})."
        await self._send_rpc(worker, {"type": "prompt", "message": message})
        return f"Message sent to worker `{worker.label}`."

    def status(self, worker_id: str | None = None) -> str:
        """Get worker status.  If worker_id is None, list all workers."""
        if worker_id:
            worker = self._workers.get(worker_id)
            if not worker:
                return f"Worker `{worker_id}` not found."
            recent = worker.messages[-5:] if worker.messages else []
            lines = [
                f"**Worker: {worker.label}** (`{worker.id}`)",
                f"Status: {worker.status}",
                f"Project: `{worker.project_dir}`",
                "",
                "Recent output:",
            ]
            for msg in recent:
                content = msg.get("content", msg.get("text", str(msg)))
                if isinstance(content, str):
                    lines.append(f"  {content[:200]}")
            return "\n".join(lines)

        if not self._workers:
            return "No active workers."

        lines = ["**Active Workers:**", ""]
        for w in self._workers.values():
            lines.append(f"- `{w.id}` — {w.label} [{w.status}] in `{w.project_dir}`")
        return "\n".join(lines)

    async def stop_all(self) -> None:
        """Stop all workers.  Called on gateway shutdown."""
        for worker in list(self._workers.values()):
            await self._kill_worker(worker)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_models_json(self, gsd_home: Path) -> None:
        """Write a models.json so GSD can use SharanBot's LLM provider."""
        agent_dir = gsd_home / "agent"
        agent_dir.mkdir(parents=True, exist_ok=True)

        models_config: dict[str, Any] = {
            "providers": {
                "sharanbot-llm": {
                    "baseUrl": self.api_base,
                    "apiKey": "SHARANBOT_WORKER_API_KEY",
                    "api": "openai-completions",
                    "models": [
                        {
                            "id": self.model or "default",
                            "name": f"SharanBot Worker ({self.model or 'default'})",
                            "contextWindow": 200000,
                            "maxTokens": 16384,
                        }
                    ],
                }
            }
        }

        if self.extra_headers:
            models_config["providers"]["sharanbot-llm"]["headers"] = self.extra_headers

        (agent_dir / "models.json").write_text(
            json.dumps(models_config, indent=2), encoding="utf-8"
        )

    def _write_mcp_json(self, gsd_home: Path) -> None:
        """Write .mcp.json for MCP server sharing."""
        if not self.mcp_servers:
            return
        mcp_config: dict[str, Any] = {"mcpServers": {}}
        for name, server_cfg in self.mcp_servers.items():
            entry: dict[str, Any] = {}
            if hasattr(server_cfg, "command") and server_cfg.command:
                entry["command"] = server_cfg.command
                if hasattr(server_cfg, "args"):
                    entry["args"] = list(server_cfg.args)
                if hasattr(server_cfg, "env") and server_cfg.env:
                    entry["env"] = dict(server_cfg.env)
            elif hasattr(server_cfg, "url") and server_cfg.url:
                entry["url"] = server_cfg.url
            if entry:
                mcp_config["mcpServers"][name] = entry

        if mcp_config["mcpServers"]:
            (gsd_home / ".mcp.json").write_text(
                json.dumps(mcp_config, indent=2), encoding="utf-8"
            )

    def _build_env(self, gsd_home: Path) -> dict[str, str]:
        """Build the environment for the GSD process."""
        env = dict(os.environ)
        env["GSD_HOME"] = str(gsd_home)
        env["SHARANBOT_WORKER_API_KEY"] = self.api_key
        # Also set CUSTOM_OPENAI_API_KEY as fallback
        env["CUSTOM_OPENAI_API_KEY"] = self.api_key
        # Disable interactive prompts
        env["GSD_NON_INTERACTIVE"] = "1"
        env["CI"] = "1"
        return env

    async def _send_rpc(self, worker: WorkerState, msg: dict[str, Any]) -> None:
        """Send a JSON-RPC message to the worker's stdin."""
        if worker.process.stdin is None:
            return
        line = json.dumps(msg, ensure_ascii=False) + "\n"
        worker.process.stdin.write(line.encode("utf-8"))
        await worker.process.stdin.drain()

    async def _read_output(self, worker: WorkerState) -> None:
        """Read JSON lines from the worker's stdout."""
        try:
            while worker.process.stdout and not worker.process.stdout.at_eof():
                raw = await worker.process.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    worker.messages.append(msg)
                    # Log significant events
                    msg_type = msg.get("type", "")
                    if msg_type in ("result", "error", "tool_use", "content"):
                        logger.debug("Worker [{}] {}: {}", worker.id, msg_type,
                                     str(msg.get("content", msg.get("text", "")))[:100])
                except json.JSONDecodeError:
                    worker.messages.append({"type": "raw", "content": line})
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Worker [{}] reader error: {}", worker.id, e)
        finally:
            worker.status = "stopped"
            logger.info("Worker [{}] output stream ended", worker.id)

    async def _kill_worker(self, worker: WorkerState) -> None:
        """Terminate a worker process and clean up."""
        worker.status = "stopping"
        if worker.reader_task and not worker.reader_task.done():
            worker.reader_task.cancel()
            try:
                await worker.reader_task
            except asyncio.CancelledError:
                pass

        if worker.process.returncode is None:
            try:
                # Try graceful shutdown first
                if worker.process.stdin:
                    await self._send_rpc(worker, {"type": "abort"})
                    try:
                        await asyncio.wait_for(worker.process.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        worker.process.kill()
                        await worker.process.wait()
                else:
                    worker.process.kill()
                    await worker.process.wait()
            except ProcessLookupError:
                pass

        worker.status = "stopped"
        logger.info("Worker [{}] terminated", worker.id)
