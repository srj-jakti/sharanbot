"""Dashboard HTTP + WebSocket server.

Provides a real-time web dashboard for SharanBot showing:
- Running tasks, workers, subagents, teams
- Autonomous services and deployed apps
- Cron jobs and heartbeat status
- System health and recovery engine status
- Workspace projects with one-click run
- MCP servers, tools, and skills status
- Live log streaming via WebSocket
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from loguru import logger

try:
    from aiohttp import web

    _HAS_AIOHTTP = True
except ImportError:
    _HAS_AIOHTTP = False


class DashboardServer:
    """Lightweight HTTP + WebSocket server for the SharanBot dashboard."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 18791,
        agent_loop: Any = None,
    ):
        self.host = host
        self.port = port
        self.agent = agent_loop
        self._app: Any = None
        self._runner: Any = None
        self._ws_clients: list[Any] = []
        self._broadcast_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the dashboard server."""
        if not _HAS_AIOHTTP:
            logger.warning("Dashboard requires aiohttp — install with: pip install aiohttp")
            return

        self._app = web.Application()
        self._app.router.add_get("/", self._handle_index)
        self._app.router.add_get("/api/status", self._handle_status)
        self._app.router.add_get("/api/services", self._handle_services)
        self._app.router.add_get("/api/teams", self._handle_teams)
        self._app.router.add_get("/api/workers", self._handle_workers)
        self._app.router.add_get("/api/cron", self._handle_cron)
        self._app.router.add_get("/api/health", self._handle_health)
        self._app.router.add_get("/api/vault/keys", self._handle_vault_keys)
        self._app.router.add_get("/api/projects", self._handle_projects)
        self._app.router.add_get("/api/projects/{name}/files", self._handle_project_files)
        self._app.router.add_get("/api/projects/{name}/file", self._handle_project_file_content)
        self._app.router.add_post("/api/projects/{name}/run", self._handle_project_run)
        self._app.router.add_get("/api/integrations", self._handle_integrations)
        self._app.router.add_get("/ws", self._handle_websocket)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()

        # Start periodic broadcast to WebSocket clients
        self._broadcast_task = asyncio.create_task(self._periodic_broadcast())

        logger.info("Dashboard started at http://{}:{}", self.host, self.port)

    async def stop(self) -> None:
        """Stop the dashboard server."""
        if self._broadcast_task:
            self._broadcast_task.cancel()
        for ws in self._ws_clients:
            await ws.close()
        if self._runner:
            await self._runner.cleanup()

    # ------------------------------------------------------------------
    # API endpoints
    # ------------------------------------------------------------------

    async def _handle_index(self, request: Any) -> Any:
        """Serve the dashboard HTML."""
        html_path = Path(__file__).parent / "index.html"
        if html_path.exists():
            return web.FileResponse(html_path)
        return web.Response(text="Dashboard HTML not found", status=404)

    async def _handle_status(self, request: Any) -> Any:
        """Full system status snapshot."""
        data = self._build_snapshot()
        return web.json_response(data)

    async def _handle_services(self, request: Any) -> Any:
        """Service manager status."""
        if hasattr(self.agent, "service_manager"):
            return web.json_response(self.agent.service_manager.get_dashboard_snapshot())
        return web.json_response({"services": [], "total": 0})

    async def _handle_teams(self, request: Any) -> Any:
        """Team orchestrator status."""
        if hasattr(self.agent, "team_orchestrator"):
            teams = self.agent.team_orchestrator.list_teams()
            return web.json_response({"teams": teams})
        return web.json_response({"teams": []})

    async def _handle_workers(self, request: Any) -> Any:
        """Worker manager status."""
        if hasattr(self.agent, "worker_manager") and self.agent.worker_manager:
            status_text = self.agent.worker_manager.status()
            return web.json_response({"workers": status_text})
        return web.json_response({"workers": "No workers active"})

    async def _handle_cron(self, request: Any) -> Any:
        """Cron jobs list."""
        if hasattr(self.agent, "cron_service") and self.agent.cron_service:
            from dataclasses import asdict
            jobs = self.agent.cron_service.list_jobs()
            return web.json_response({"jobs": [asdict(j) for j in jobs]})
        return web.json_response({"jobs": []})

    async def _handle_health(self, request: Any) -> Any:
        """Recovery engine health check."""
        if hasattr(self.agent, "recovery_engine"):
            health = await self.agent.recovery_engine.health_check()
            return web.json_response(health)
        return web.json_response({"status": "no recovery engine"})

    async def _handle_vault_keys(self, request: Any) -> Any:
        """List vault credential keys (not values)."""
        if hasattr(self.agent, "vault") and self.agent.vault:
            return web.json_response({"keys": self.agent.vault.list_keys()})
        return web.json_response({"keys": []})

    async def _handle_projects(self, request: Any) -> Any:
        """List workspace projects (directories with package.json, requirements.txt, etc.)."""
        projects = self._scan_projects()
        return web.json_response({"projects": projects})

    async def _handle_project_files(self, request: Any) -> Any:
        """List files in a project directory (non-recursive, top-level)."""
        name = request.match_info["name"]
        project_dir = self._resolve_project(name)
        if not project_dir:
            return web.json_response({"error": "Project not found"}, status=404)

        files = []
        try:
            for entry in sorted(project_dir.iterdir()):
                if entry.name.startswith("."):
                    continue
                if entry.name == "node_modules":
                    continue
                files.append({
                    "name": entry.name,
                    "type": "dir" if entry.is_dir() else "file",
                    "size": entry.stat().st_size if entry.is_file() else 0,
                })
        except OSError:
            pass
        return web.json_response({"project": name, "files": files})

    async def _handle_project_file_content(self, request: Any) -> Any:
        """Read a specific file's content from a project."""
        name = request.match_info["name"]
        file_path = request.query.get("path", "")
        project_dir = self._resolve_project(name)
        if not project_dir:
            return web.json_response({"error": "Project not found"}, status=404)

        target = (project_dir / file_path).resolve()
        # Path traversal protection
        if not str(target).startswith(str(project_dir.resolve())):
            return web.json_response({"error": "Access denied"}, status=403)
        if not target.is_file():
            return web.json_response({"error": "File not found"}, status=404)

        try:
            content = target.read_text(encoding="utf-8", errors="replace")
            # Truncate very large files
            if len(content) > 50_000:
                content = content[:50_000] + "\n\n... (truncated)"
            return web.json_response({"path": file_path, "content": content})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_project_run(self, request: Any) -> Any:
        """Start a project (npm start, python main.py, etc.)."""
        name = request.match_info["name"]
        project_dir = self._resolve_project(name)
        if not project_dir:
            return web.json_response({"error": "Project not found"}, status=404)

        ptype = self._detect_project_type(project_dir)
        run_cmd = self._get_run_command(project_dir, ptype)
        if not run_cmd:
            return web.json_response({"error": "Cannot determine run command"}, status=400)

        # Register with service manager if available
        if hasattr(self.agent, "service_manager"):
            svc_name = f"project-{name}"
            try:
                self.agent.service_manager.register(
                    name=svc_name,
                    command=run_cmd,
                    working_dir=str(project_dir),
                    kind="app",
                )
                await self.agent.service_manager.start(svc_name)
                return web.json_response({
                    "status": "started",
                    "service": svc_name,
                    "command": run_cmd,
                    "type": ptype,
                })
            except Exception as e:
                return web.json_response({"error": str(e)}, status=500)

        return web.json_response({"error": "Service manager not available"}, status=500)

    async def _handle_integrations(self, request: Any) -> Any:
        """Return connected MCP servers, registered tools, and loaded skills."""
        data: dict[str, Any] = {
            "mcp_servers": [],
            "mcp_connected": False,
            "tools": [],
            "skills": [],
            "plugins": [],
            "pipelines": [],
            "profiles": [],
        }
        if not self.agent:
            return web.json_response(data)

        # MCP servers
        if hasattr(self.agent, "_mcp_servers"):
            for name in self.agent._mcp_servers:
                data["mcp_servers"].append({"name": name, "connected": self.agent._mcp_connected})
            data["mcp_connected"] = self.agent._mcp_connected

        # Tools (group by type)
        if hasattr(self.agent, "tools"):
            for tname in self.agent.tools.tool_names:
                category = "mcp" if tname.startswith("mcp_") else "builtin"
                data["tools"].append({"name": tname, "category": category})

        # Skills
        if hasattr(self.agent, "context") and hasattr(self.agent.context, "skills"):
            try:
                skills = self.agent.context.skills.list_skills(filter_unavailable=False)
                data["skills"] = [{"name": s["name"], "source": s.get("source", "builtin")} for s in skills]
            except Exception:
                pass

        # Agent profiles (plugins/agents/*.yaml)
        if hasattr(self.agent, "profile_registry"):
            try:
                for p in self.agent.profile_registry.list_profiles():
                    data["profiles"].append({"name": p.name, "display": p.display_name})
            except Exception:
                pass

        # Pipelines
        if hasattr(self.agent, "team_orchestrator") and hasattr(self.agent.team_orchestrator, "pipelines"):
            try:
                data["pipelines"] = self.agent.team_orchestrator.pipelines.list_templates()
            except Exception:
                pass

        # Plugins (scan plugins dir)
        plugins_dir = getattr(self.agent, "workspace", None)
        if plugins_dir:
            pdir = plugins_dir / "plugins"
            if pdir.is_dir():
                try:
                    for entry in sorted(pdir.iterdir()):
                        if entry.is_dir() and not entry.name.startswith("."):
                            plugin_files = [f.name for f in entry.iterdir() if f.is_file()]
                            data["plugins"].append({"name": entry.name, "files": plugin_files})
                except OSError:
                    pass

        return web.json_response(data)

    # ------------------------------------------------------------------
    # Project helpers
    # ------------------------------------------------------------------

    def _scan_projects(self) -> list[dict[str, Any]]:
        """Scan workspace for project directories."""
        projects = []
        if not self.agent or not hasattr(self.agent, "workspace"):
            return projects

        workspace = self.agent.workspace
        if not workspace.is_dir():
            return projects

        try:
            entries = sorted(workspace.iterdir())
        except OSError:
            return projects

        for entry in entries:
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            if entry.name in ("memory", "plugins", "__pycache__", "node_modules"):
                continue

            ptype = self._detect_project_type(entry)

            # Fast file count — top-level only to avoid slow rglob on large dirs
            try:
                file_count = sum(
                    1 for f in entry.iterdir()
                    if f.is_file() and not f.name.startswith(".")
                )
            except OSError:
                file_count = 0

            projects.append({
                "name": entry.name,
                "type": ptype,
                "path": str(entry),
                "files": file_count,
            })

        return projects

    def _resolve_project(self, name: str) -> Path | None:
        """Resolve a project name to its directory path, with path traversal protection."""
        if not self.agent or not hasattr(self.agent, "workspace"):
            return None
        # Block path traversal
        if ".." in name or "/" in name or "\\" in name:
            return None
        project_dir = self.agent.workspace / name
        if project_dir.is_dir() and str(project_dir.resolve()).startswith(str(self.agent.workspace.resolve())):
            return project_dir
        return None

    @staticmethod
    def _detect_project_type(path: Path) -> str:
        """Detect the type of project from its files."""
        if (path / "package.json").exists():
            pkg = {}
            try:
                pkg = json.loads((path / "package.json").read_text(encoding="utf-8"))
            except Exception:
                pass
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            if "expo" in deps:
                return "expo"
            if "next" in deps:
                return "next"
            if "vite" in deps:
                return "vite"
            if "react-scripts" in deps:
                return "cra"
            return "node"
        if (path / "requirements.txt").exists() or (path / "pyproject.toml").exists():
            if (path / "manage.py").exists():
                return "django"
            if any((path / f).exists() for f in ("main.py", "app.py", "server.py")):
                return "python"
            return "python"
        if (path / "Cargo.toml").exists():
            return "rust"
        if (path / "go.mod").exists():
            return "go"
        return "unknown"

    @staticmethod
    def _get_run_command(path: Path, ptype: str) -> str | None:
        """Get the appropriate run command for a project type."""
        commands = {
            "expo": "npx expo start --web --port 19006",
            "next": "npx next dev --port 3000",
            "vite": "npx vite --port 5173",
            "cra": "npx react-scripts start",
            "node": "npm start",
            "django": "python manage.py runserver 0.0.0.0:8000",
            "python": None,  # determined below
            "rust": "cargo run",
            "go": "go run .",
        }
        cmd = commands.get(ptype)
        if ptype == "python":
            for entry in ("main.py", "app.py", "server.py"):
                if (path / entry).exists():
                    return f"python {entry}"
        return cmd

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------

    async def _handle_websocket(self, request: Any) -> Any:
        """WebSocket endpoint for real-time dashboard updates."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws_clients.append(ws)
        logger.debug("Dashboard WebSocket client connected")

        try:
            # Send initial snapshot
            await ws.send_json({"type": "snapshot", "data": self._build_snapshot()})

            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        cmd = json.loads(msg.data)
                        await self._handle_ws_command(ws, cmd)
                    except json.JSONDecodeError:
                        pass
                elif msg.type in (web.WSMsgType.ERROR, web.WSMsgType.CLOSE):
                    break
        finally:
            self._ws_clients.remove(ws)
            logger.debug("Dashboard WebSocket client disconnected")

        return ws

    async def _handle_ws_command(self, ws: Any, cmd: dict) -> None:
        """Handle incoming WebSocket commands."""
        action = cmd.get("action")
        if action == "refresh":
            await ws.send_json({"type": "snapshot", "data": self._build_snapshot()})
        elif action == "service_logs":
            sid = cmd.get("service_id")
            if hasattr(self.agent, "service_manager"):
                logs = self.agent.service_manager.get_logs(sid)
                await ws.send_json({"type": "logs", "service_id": sid, "data": logs})

    async def _periodic_broadcast(self) -> None:
        """Broadcast status updates to all WebSocket clients every 3 seconds."""
        while True:
            try:
                await asyncio.sleep(3)
                if not self._ws_clients:
                    continue
                snapshot = self._build_snapshot()
                msg = json.dumps({"type": "update", "data": snapshot})
                dead: list[Any] = []
                for ws in self._ws_clients:
                    try:
                        await ws.send_str(msg)
                    except Exception:
                        dead.append(ws)
                for ws in dead:
                    self._ws_clients.remove(ws)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("Dashboard broadcast error: {}", e)

    # ------------------------------------------------------------------
    # Snapshot builder
    # ------------------------------------------------------------------

    def _build_snapshot(self) -> dict[str, Any]:
        """Build the full dashboard snapshot."""
        snapshot: dict[str, Any] = {
            "timestamp": time.time(),
            "uptime_s": 0,
            "services": {"total": 0, "running": 0, "services": []},
            "teams": [],
            "workers": "No workers",
            "cron_jobs": 0,
            "health": {},
            "learning_summary": {},
        }

        if not self.agent:
            return snapshot

        # Services
        if hasattr(self.agent, "service_manager"):
            snapshot["services"] = self.agent.service_manager.get_dashboard_snapshot()

        # Teams
        if hasattr(self.agent, "team_orchestrator"):
            snapshot["teams"] = self.agent.team_orchestrator.list_teams()

        # Workers
        if hasattr(self.agent, "worker_manager") and self.agent.worker_manager:
            snapshot["workers"] = self.agent.worker_manager.status()

        # Cron
        if hasattr(self.agent, "cron_service") and self.agent.cron_service:
            jobs = self.agent.cron_service.list_jobs()
            snapshot["cron_jobs"] = len(jobs)

        # Health (extract circuit states directly, avoiding async health_check)
        if hasattr(self.agent, "recovery_engine"):
            re = self.agent.recovery_engine
            circuits = {}
            for name, circuit in re._circuits.items():
                circuits[name] = {
                    "state": "open" if circuit.is_open else "closed",
                    "failures": circuit.failures,
                    "cooldown": circuit.cooldown,
                }
            snapshot["health"] = {
                "status": "degraded" if any(c.is_open for c in re._circuits.values()) else "healthy",
                "circuits": circuits,
            }

        # Learning
        if hasattr(self.agent, "learning_store"):
            snapshot["learning_summary"] = self.agent.learning_store.summary()

        # Projects
        snapshot["projects"] = self._scan_projects()

        # Integrations
        integrations: dict[str, Any] = {"mcp_servers": [], "mcp_connected": False, "tools": 0, "skills": 0}
        if hasattr(self.agent, "_mcp_servers"):
            integrations["mcp_servers"] = list(self.agent._mcp_servers.keys())
            integrations["mcp_connected"] = self.agent._mcp_connected
        if hasattr(self.agent, "tools"):
            integrations["tools"] = len(self.agent.tools.tool_names)
        if hasattr(self.agent, "context") and hasattr(self.agent.context, "skills"):
            try:
                integrations["skills"] = len(self.agent.context.skills.list_skills(filter_unavailable=False))
            except Exception:
                pass
        snapshot["integrations"] = integrations

        return snapshot
