"""Import agent profiles from external sources (e.g. agency-agents repo)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from sharanbot.agent.tools.base import Tool


class ImportAgentTool(Tool):
    """Import and convert agent profiles from external markdown sources."""

    def __init__(self, plugins_dir: Path, agent_loop=None):
        self._plugins_dir = plugins_dir
        self._agent_loop = agent_loop

    @property
    def name(self) -> str:
        return "import_agent"

    @property
    def description(self) -> str:
        return (
            "Import an agent profile from a markdown definition or external source. "
            "Converts structured agent descriptions into SharanBot YAML profiles. "
            "After import, the profile is immediately available for spawning."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Short name for the agent (e.g. 'frontend_developer', 'api_tester')",
                },
                "display_name": {
                    "type": "string",
                    "description": "Human-readable display name (e.g. 'Frontend Developer')",
                },
                "description": {
                    "type": "string",
                    "description": "Brief description of the agent's specialty",
                },
                "system_prompt": {
                    "type": "string",
                    "description": "The full system prompt / personality for the agent",
                },
                "allowed_tools": {
                    "type": "string",
                    "description": "Comma-separated list of allowed tools, or '*' for all (default: '*')",
                },
                "denied_tools": {
                    "type": "string",
                    "description": "Comma-separated list of denied tools (default: none)",
                },
                "max_iterations": {
                    "type": "integer",
                    "description": "Maximum iterations for the agent (default: 15)",
                },
            },
            "required": ["name", "description", "system_prompt"],
        }

    async def execute(
        self,
        name: str,
        description: str,
        system_prompt: str,
        display_name: str | None = None,
        allowed_tools: str | None = None,
        denied_tools: str | None = None,
        max_iterations: int = 15,
        **kwargs: Any,
    ) -> str:
        """Create a YAML profile and save it to the plugins directory."""
        # Sanitize name
        safe_name = re.sub(r"[^a-z0-9_]", "_", name.lower().strip())
        if not safe_name:
            return "Invalid agent name."

        # Build profile data
        profile_data = {
            "name": safe_name,
            "display_name": display_name or name.replace("_", " ").title(),
            "description": description,
            "system_prompt": system_prompt,
            "allowed_tools": (
                [t.strip() for t in allowed_tools.split(",")]
                if allowed_tools and allowed_tools != "*"
                else ["*"]
            ),
            "denied_tools": (
                [t.strip() for t in denied_tools.split(",")]
                if denied_tools
                else []
            ),
            "max_iterations": max_iterations,
            "temperature": 0.1,
        }

        # Ensure plugins dir exists
        self._plugins_dir.mkdir(parents=True, exist_ok=True)

        # Write YAML profile
        profile_path = self._plugins_dir / f"{safe_name}.yaml"
        if profile_path.exists():
            return f"Profile '{safe_name}' already exists at {profile_path}. Delete it first or use a different name."

        profile_path.write_text(
            yaml.dump(profile_data, default_flow_style=False, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

        # Hot-reload agent profiles
        if self._agent_loop and hasattr(self._agent_loop, "agent_profile_registry"):
            self._agent_loop.agent_profile_registry.scan_plugins()

        logger.info("Imported agent profile: {} → {}", safe_name, profile_path)
        return (
            f"Agent profile '{safe_name}' imported successfully.\n"
            f"Saved to: {profile_path}\n"
            f"You can now use it with: spawn(task=..., profile='{safe_name}')"
        )
