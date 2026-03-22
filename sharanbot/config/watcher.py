"""Configuration file watcher for hot-reload support.

Polls ``config.json`` for changes and triggers reload events
on the MessageBus so live components can update without restart.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger

from sharanbot.config.loader import get_config_path, load_config


class ConfigWatcher:
    """Watches config.json for changes and invokes a callback on modification."""

    def __init__(
        self,
        on_change,
        config_path: Path | None = None,
        poll_interval: float = 5.0,
    ):
        self._on_change = on_change
        self._path = config_path or get_config_path()
        self._interval = poll_interval
        self._last_mtime: float = 0.0
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the polling loop."""
        if self._path.exists():
            self._last_mtime = self._path.stat().st_mtime
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("ConfigWatcher started ({})", self._path)

    def stop(self) -> None:
        """Cancel the polling task."""
        if self._task and not self._task.done():
            self._task.cancel()
            logger.debug("ConfigWatcher stopped")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._interval)
                if not self._path.exists():
                    continue
                mtime = self._path.stat().st_mtime
                if mtime > self._last_mtime:
                    self._last_mtime = mtime
                    logger.info("Config file changed — triggering reload")
                    try:
                        new_config = load_config(self._path)
                        await self._on_change(new_config)
                    except Exception as exc:
                        logger.error("Config reload failed: {}", exc)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("ConfigWatcher poll error: {}", exc)
