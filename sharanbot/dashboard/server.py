"""Dashboard HTTP + WebSocket server.

Provides a real-time web dashboard for SharanBot showing:
- Running tasks, workers, subagents, teams
- Autonomous services and deployed apps
- Cron jobs and heartbeat status
- System health and recovery engine status
- Live log streaming via WebSocket
"""

from __future__ import annotations

import asyncio
import json
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
            jobs = self.agent.cron_service.list_jobs()
            return web.json_response({"jobs": [j.to_dict() if hasattr(j, "to_dict") else str(j) for j in jobs]})
        return web.json_response({"jobs": []})

    async def _handle_health(self, request: Any) -> Any:
        """Recovery engine health check."""
        if hasattr(self.agent, "recovery_engine"):
            health = self.agent.recovery_engine.health_check()
            return web.json_response(health)
        return web.json_response({"status": "no recovery engine"})

    async def _handle_vault_keys(self, request: Any) -> Any:
        """List vault credential keys (not values)."""
        if hasattr(self.agent, "vault") and self.agent.vault:
            return web.json_response({"keys": self.agent.vault.list_keys()})
        return web.json_response({"keys": []})

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

        # Health
        if hasattr(self.agent, "recovery_engine"):
            snapshot["health"] = self.agent.recovery_engine.health_check()

        # Learning
        if hasattr(self.agent, "learning_store"):
            snapshot["learning_summary"] = self.agent.learning_store.summary()

        return snapshot
