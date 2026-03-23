"""Tool manager — owns tool registration, MCP lifecycle, and hot-reload.

Extracted from ``loop.py`` to keep the core agent loop focused on
message processing.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

from loguru import logger

from sharanbot.agent.tools.registry import ToolRegistry
from sharanbot.agent.tools.dynamic import DynamicToolLoader

if TYPE_CHECKING:
    from pathlib import Path

    from sharanbot.agent.learning import LearningStore
    from sharanbot.agent.recovery import RecoveryEngine


class ToolManager:
    """Manages tool registration, MCP server connections, and hot-reload.

    The *registry* is the public ``ToolRegistry`` used by the agent loop.
    MCP state is encapsulated here so the loop only calls
    :meth:`connect_mcp` / :meth:`reconnect_mcp` without knowing
    transport details.
    """

    # Seconds between automatic MCP health probes.
    _HEALTH_INTERVAL = 120

    def __init__(
        self,
        registry: ToolRegistry,
        workspace: "Path",
        mcp_servers: dict | None = None,
    ):
        self.registry = registry
        self.workspace = workspace
        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connecting = False
        self._mcp_last_health: float = 0.0
        self._mcp_sessions: dict[str, Any] = {}  # server_name → ClientSession
        self._dynamic_loader = DynamicToolLoader(
            tools_dir=workspace / "plugins" / "tools",
            registry=registry,
        )
        self._dynamic_loader.scan_and_load()

    # Expose frequently used registry methods for convenience ----------

    def get_definitions(self) -> list[dict[str, Any]]:
        return self.registry.get_definitions()

    async def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        return await self.registry.execute(name, arguments)

    def get(self, name: str):
        return self.registry.get(name)

    # MCP lifecycle ----------------------------------------------------

    async def connect_mcp(
        self,
        recovery_engine: "RecoveryEngine | None" = None,
        learning_store: "LearningStore | None" = None,
    ) -> None:
        """Connect to configured MCP servers (one-time, lazy) with auto-recovery."""
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        from sharanbot.agent.tools.mcp import connect_mcp_servers

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                self._mcp_stack = AsyncExitStack()
                await self._mcp_stack.__aenter__()
                await connect_mcp_servers(
                    self._mcp_servers, self.registry, self._mcp_stack,
                )
                self._mcp_connected = True
                if attempt > 1 and recovery_engine:
                    recovery_engine.reset_circuit("mcp")
                if attempt > 1 and learning_store:
                    learning_store.record(
                        category="mcp_failure",
                        error_signature="mcp_failure:connect",
                        description="MCP connection recovered",
                        fix_applied=f"Retry attempt {attempt} succeeded",
                        success=True,
                    )
                break
            except BaseException as e:
                logger.error(
                    "Failed to connect MCP servers (attempt {}/{}): {}",
                    attempt, max_attempts, e,
                )
                if self._mcp_stack:
                    try:
                        await self._mcp_stack.aclose()
                    except Exception as exc:
                        logger.warning("Error closing MCP stack during retry: {}", exc)
                    self._mcp_stack = None
                if attempt < max_attempts:
                    wait = 2 ** attempt
                    logger.info("Retrying MCP connection in {}s...", wait)
                    if learning_store:
                        learning_store.record(
                            category="mcp_failure",
                            error_signature="mcp_failure:connect",
                            description=str(e)[:200],
                            fix_applied=f"Retry with {wait}s backoff",
                            success=False,
                            cooldown_seconds=wait,
                        )
                    await asyncio.sleep(wait)
                else:
                    logger.error(
                        "MCP connection failed after {} attempts, will retry next message",
                        max_attempts,
                    )
        self._mcp_connecting = False

    async def reconnect_mcp(self) -> None:
        """Tear down and reconnect all MCP servers."""
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass
            except Exception as exc:
                logger.warning("Error closing MCP stack during reconnect: {}", exc)
            self._mcp_stack = None
        # Unregister MCP tools
        for name in list(self.registry._tools.keys()):
            if name.startswith("mcp_"):
                self.registry.unregister(name)
        self._mcp_connected = False
        self._mcp_connecting = False
        self._mcp_sessions.clear()
        self._mcp_last_health = 0.0
        await self.connect_mcp()

    async def health_check_mcp(self) -> dict[str, bool]:
        """Probe each MCP session; reconnect any that are unhealthy.

        Returns a dict mapping server name → healthy (bool).
        Skipped if no sessions exist or the last check was recent.
        """
        if not self._mcp_sessions:
            return {}
        now = time.time()
        if now - self._mcp_last_health < self._HEALTH_INTERVAL:
            return {name: True for name in self._mcp_sessions}

        self._mcp_last_health = now
        results: dict[str, bool] = {}
        unhealthy: list[str] = []
        for name, session in list(self._mcp_sessions.items()):
            try:
                await asyncio.wait_for(session.list_tools(), timeout=10)
                results[name] = True
            except Exception as exc:
                logger.warning("MCP health check failed for '{}': {}", name, exc)
                results[name] = False
                unhealthy.append(name)

        if unhealthy:
            logger.info(
                "Unhealthy MCP servers: {} — triggering reconnect",
                ", ".join(unhealthy),
            )
            await self.reconnect_mcp()

        return results

    # Reload helpers ---------------------------------------------------

    def reload_tools(self) -> int:
        """Hot-reload dynamic tools from plugins directory."""
        loaded = self._dynamic_loader.reload_all()
        return len(loaded) if isinstance(loaded, list) else loaded

    def update_mcp_servers(self, mcp_servers: dict) -> None:
        """Update the MCP server list for the next reconnect."""
        self._mcp_servers = mcp_servers
