"""WaitTool — async sleep for a specified duration."""

from __future__ import annotations

import asyncio
from typing import Any

from sharanbot.agent.tools.base import Tool


class WaitTool(Tool):
    """Wait/sleep for a specified duration in seconds. Max 120s."""

    @property
    def name(self) -> str:
        return "wait"

    @property
    def description(self) -> str:
        return "Wait/sleep for a specified duration in seconds. Max 120s."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "number",
                    "description": "Duration to wait in seconds (max 120)",
                },
            },
            "required": ["seconds"],
        }

    async def execute(self, seconds: float = 10, **kwargs: Any) -> str:
        """Sleep for the given duration, clamped to [0, 120].

        Args:
            seconds: Requested sleep duration in seconds.

        Returns:
            Confirmation message with actual wait duration.
        """
        duration = min(max(0, seconds), 120)
        await asyncio.sleep(duration)
        return f"Waited {duration:.1f} seconds."
