"""Agent profile registry — specialised subagent configurations.

Profiles let you define subagent "personalities" with custom system
prompts, tool restrictions, iteration limits and optional model overrides.
Profiles are loaded from YAML files in ``workspace/plugins/agents/``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml  # PyYAML — already a sharanbot dependency
from loguru import logger


@dataclass
class AgentProfile:
    """A named subagent profile."""

    name: str
    display_name: str = ""
    system_prompt: str = ""
    allowed_tools: list[str] = field(default_factory=lambda: ["*"])
    denied_tools: list[str] = field(default_factory=list)
    model: str | None = None
    max_iterations: int = 15
    temperature: float = 0.1
    description: str = ""

    @property
    def label(self) -> str:
        return self.display_name or self.name


class AgentProfileRegistry:
    """Registry for agent profiles."""

    def __init__(self, plugins_dir: Path | None = None):
        self.profiles: dict[str, AgentProfile] = {}
        self._plugins_dir = plugins_dir
        self._register_builtins()

    # ------------------------------------------------------------------
    # Builtins
    # ------------------------------------------------------------------

    def _register_builtins(self) -> None:
        self.register(AgentProfile(
            name="general",
            display_name="General",
            description="Default subagent with all tools.",
        ))
        self.register(AgentProfile(
            name="coder",
            display_name="Coder",
            system_prompt=(
                "You are a focused coding assistant. Write clean, correct code. "
                "Test your changes when possible."
            ),
            max_iterations=25,
            description="Coding-focused subagent with extended iterations.",
        ))
        self.register(AgentProfile(
            name="researcher",
            display_name="Researcher",
            system_prompt=(
                "You are a research assistant. Search the web, read documents, "
                "and synthesise findings. Do not modify project files."
            ),
            allowed_tools=["read_file", "list_dir", "web_search", "web_fetch"],
            denied_tools=["write_file", "edit_file", "exec"],
            max_iterations=20,
            description="Read-only research agent with web access.",
        ))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(self, profile: AgentProfile) -> None:
        self.profiles[profile.name] = profile
        logger.debug("AgentProfile registered: {}", profile.name)

    def unregister(self, name: str) -> None:
        if name not in ("general", "coder", "researcher"):
            self.profiles.pop(name, None)

    def get(self, name: str) -> AgentProfile | None:
        return self.profiles.get(name)

    def list_profiles(self) -> list[AgentProfile]:
        return list(self.profiles.values())

    # ------------------------------------------------------------------
    # Plugin scanning
    # ------------------------------------------------------------------

    def scan_plugins(self) -> list[str]:
        """Scan ``plugins/agents/`` for YAML profile files."""
        if not self._plugins_dir or not self._plugins_dir.is_dir():
            return []

        registered: list[str] = []
        for yf in sorted(self._plugins_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(yf.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                name = data.get("name")
                if not name:
                    continue
                p = AgentProfile(
                    name=name,
                    display_name=data.get("display_name", name),
                    system_prompt=data.get("system_prompt", ""),
                    allowed_tools=data.get("allowed_tools", ["*"]),
                    denied_tools=data.get("denied_tools", []),
                    model=data.get("model"),
                    max_iterations=int(data.get("max_iterations", 15)),
                    temperature=float(data.get("temperature", 0.1)),
                    description=data.get("description", ""),
                )
                self.register(p)
                registered.append(name)
            except Exception as exc:
                logger.warning("Bad agent profile {}: {}", yf.name, exc)

        if registered:
            logger.info("Agent profiles loaded: {}", ", ".join(registered))
        return registered

    def reload_plugins(self) -> list[str]:
        """Remove non-builtin profiles and re-scan."""
        for name in list(self.profiles):
            if name not in ("general", "coder", "researcher"):
                self.unregister(name)
        return self.scan_plugins()
