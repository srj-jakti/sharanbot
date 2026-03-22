"""Reload meta-tool — lets the agent hot-reload SharanBot components at runtime.

Components that can be reloaded:
  - **tools**   — re-scan ``workspace/plugins/tools/`` for Python Tool subclasses
  - **mcp**     — disconnect and reconnect all MCP servers
  - **config**  — re-read ``config.json`` and apply live-updatable settings
  - **workers** — re-scan ``workspace/plugins/workers/`` for worker manifests
  - **agents**  — re-scan ``workspace/plugins/agents/`` for agent profile YAML files
  - **skills**  — (already scanned per-message; this forces a summary refresh)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sharanbot.agent.tools.base import Tool

if TYPE_CHECKING:
    from sharanbot.agent.loop import AgentLoop


class ReloadTool(Tool):
    """Hot-reload SharanBot components without restarting the gateway."""

    def __init__(self, agent_loop: "AgentLoop"):
        self._loop = agent_loop

    @property
    def name(self) -> str:
        return "reload"

    @property
    def description(self) -> str:
        return (
            "Hot-reload a SharanBot component without restarting the gateway. "
            "Use after creating new tools, worker types, agent profiles, "
            "or after editing config.json or MCP server settings.\n"
            "Components: tools, mcp, config, workers, agents, skills"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "component": {
                    "type": "string",
                    "enum": ["tools", "mcp", "config", "workers", "agents", "skills"],
                    "description": "Which component to reload",
                },
                "target": {
                    "type": "string",
                    "description": "Specific item name (optional — omit to reload all of that component)",
                },
            },
            "required": ["component"],
        }

    async def execute(self, component: str, target: str | None = None, **kwargs: Any) -> str:
        return await self._loop.handle_reload(component, target)
