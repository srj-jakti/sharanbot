"""Graceful shutdown handler — clean SIGTERM/SIGINT handling.

Registers signal handlers that:
1. Stop accepting new work
2. Drain in-flight tasks (with a deadline)
3. Flush logs and state
4. Exit cleanly
"""

from __future__ import annotations

import asyncio
import signal
import time
from typing import Any, Awaitable, Callable

from loguru import logger


class GracefulShutdownHandler:
    """Manages orderly shutdown of async components.

    Usage:
        handler = GracefulShutdownHandler()
        handler.register_cleanup(my_server.stop)
        handler.register_cleanup(my_db.close)
        handler.install_signal_handlers(loop)
    """

    def __init__(self, drain_timeout_s: float = 30.0):
        self._drain_timeout = drain_timeout_s
        self._cleanups: list[Callable[[], Awaitable[None] | None]] = []
        self._shutting_down = False
        self._shutdown_event = asyncio.Event()

    @property
    def is_shutting_down(self) -> bool:
        return self._shutting_down

    def register_cleanup(self, fn: Callable[[], Awaitable[None] | None]) -> None:
        """Register a cleanup function to call during shutdown."""
        self._cleanups.append(fn)

    def install_signal_handlers(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Install SIGTERM/SIGINT handlers on the event loop."""
        loop = loop or asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(self._handle_signal(s)))
            except NotImplementedError:
                # Windows doesn't support add_signal_handler for all signals
                signal.signal(sig, lambda s, f: asyncio.create_task(self._handle_signal(s)))

    async def _handle_signal(self, sig: int | signal.Signals) -> None:
        sig_name = signal.Signals(sig).name if isinstance(sig, int) else sig.name
        if self._shutting_down:
            logger.warning("Received {} again during shutdown — forcing exit", sig_name)
            raise SystemExit(1)

        logger.info("Received {} — initiating graceful shutdown", sig_name)
        await self.shutdown()

    async def shutdown(self) -> None:
        """Execute all registered cleanups with a timeout."""
        if self._shutting_down:
            return
        self._shutting_down = True

        logger.info("Graceful shutdown started (timeout={}s, {} cleanups)",
                     self._drain_timeout, len(self._cleanups))
        start = time.time()

        for cleanup in reversed(self._cleanups):
            try:
                result = cleanup()
                if asyncio.iscoroutine(result):
                    remaining = self._drain_timeout - (time.time() - start)
                    if remaining > 0:
                        await asyncio.wait_for(result, timeout=remaining)
                    else:
                        logger.warning("Shutdown timeout reached, skipping remaining cleanups")
                        break
            except asyncio.TimeoutError:
                logger.warning("Cleanup {} timed out", cleanup)
            except Exception as exc:
                logger.error("Cleanup {} failed: {}", cleanup, exc)

        elapsed = time.time() - start
        logger.info("Graceful shutdown completed in {:.1f}s", elapsed)
        self._shutdown_event.set()

    async def wait_for_shutdown(self) -> None:
        """Block until shutdown is complete."""
        await self._shutdown_event.wait()
