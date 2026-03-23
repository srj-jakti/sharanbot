"""Offline manager — degraded mode when the LLM provider is unreachable.

When the LLM goes down the agent drops into degraded mode:
- Only a curated set of local-only tools are available
- A reconnect loop tries to ping the provider every 60 s
- On reconnect the agent silently returns to full mode
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from loguru import logger

# Tools safe to execute without an LLM
OFFLINE_TOOLS: frozenset[str] = frozenset({
    "read_file",
    "write_file",
    "edit_file",
    "list_dir",
    "exec",
    "search_files",
})


class OfflineManager:
    """Switches the agent between full and degraded mode.

    Parameters
    ----------
    provider_health_check:
        An async callable that returns True if the LLM provider is reachable.
    on_mode_change:
        Optional callback ``(is_online: bool) -> Awaitable`` fired on transitions.
    reconnect_interval_s:
        How often (in seconds) to retry the provider when offline.
    """

    def __init__(
        self,
        provider_health_check: Callable[[], Awaitable[bool]],
        on_mode_change: Callable[[bool], Awaitable[None]] | None = None,
        reconnect_interval_s: float = 60.0,
    ):
        self._health_check = provider_health_check
        self._on_mode_change = on_mode_change
        self._reconnect_interval = reconnect_interval_s
        self._is_online = True
        self._reconnect_task: asyncio.Task | None = None

    @property
    def is_online(self) -> bool:
        return self._is_online

    @property
    def available_tools(self) -> frozenset[str] | None:
        """Return the set of offline-safe tools, or None when online (all tools)."""
        return None if self._is_online else OFFLINE_TOOLS

    async def check_provider(self) -> bool:
        """Ping the LLM provider to verify availability."""
        try:
            ok = await self._health_check()
        except Exception:
            ok = False

        was_online = self._is_online
        self._is_online = ok

        if was_online and not ok:
            await self.enter_offline()
        elif not was_online and ok:
            await self._enter_online()

        return ok

    async def enter_offline(self) -> None:
        """Transition to degraded mode."""
        if not self._is_online and self._reconnect_task is not None:
            return  # already offline
        self._is_online = False
        logger.warning("LLM provider unreachable — entering offline/degraded mode")
        if self._on_mode_change:
            await self._on_mode_change(False)
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _enter_online(self) -> None:
        """Transition back to full mode."""
        self._is_online = True
        logger.info("LLM provider reconnected — returning to full mode")
        if self._on_mode_change:
            await self._on_mode_change(True)
        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None

    async def _reconnect_loop(self) -> None:
        """Periodically try to reconnect to the provider."""
        while not self._is_online:
            try:
                await asyncio.sleep(self._reconnect_interval)
                ok = await self._health_check()
                if ok:
                    await self._enter_online()
                    return
                logger.debug("Provider still unreachable, retrying in {}s", self._reconnect_interval)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Reconnect attempt failed: {}", exc)

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Whether the given tool may run in the current mode."""
        if self._is_online:
            return True
        return tool_name in OFFLINE_TOOLS

    async def handle_offline_message(self, message: str) -> str:
        """Process a user message while offline, returning a canned response."""
        return (
            "⚠️ I'm currently in offline / degraded mode because the LLM provider "
            "is unreachable. I can still run local file operations (read, write, edit, "
            "list, exec). I'll automatically reconnect when the provider is back."
        )

    async def stop(self) -> None:
        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None
