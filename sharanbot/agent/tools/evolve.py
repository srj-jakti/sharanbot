"""Self-evolution meta-tool — lets SharanBot extend itself at runtime.

The ``evolve`` tool orchestrates:
1. Spawning a GSD worker to *build* a new component (tool, worker, agent profile, skill)
2. Writing it to the correct ``plugins/`` directory
3. Triggering a hot-reload so the new component is immediately usable

This creates the ∞-capability loop: SharanBot → builds → loads → uses → builds more.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sharanbot.agent.tools.base import Tool

if TYPE_CHECKING:
    from sharanbot.agent.loop import AgentLoop


_SPEC_TEMPLATES: dict[str, str] = {
    "tool": (
        "Create a Python file that defines a SharanBot tool.\n"
        "The file MUST:\n"
        "- Import: from sharanbot.agent.tools.base import Tool\n"
        "- Define one class that inherits from Tool\n"
        "- Implement properties: name (str), description (str), parameters (dict)\n"
        "- Implement: async def execute(self, **kwargs) -> Any\n"
        "- Be self-contained (import only stdlib + pip packages)\n\n"
        "Write the file to: {dest}\n\n"
        "Tool specification:\n{spec}"
    ),
    "worker": (
        "Create a JSON manifest file for a new SharanBot worker type.\n"
        "The file MUST contain: name, display_name, loader_path, mode_args, "
        "protocol, runtime, model_config, description.\n\n"
        "Write the file to: {dest}\n\n"
        "Worker specification:\n{spec}"
    ),
    "agent": (
        "Create a YAML file defining a specialised SharanBot agent profile.\n"
        "The file MUST contain: name, display_name, system_prompt, "
        "allowed_tools (list or ['*']), denied_tools, max_iterations, "
        "temperature, description.\n\n"
        "Write the file to: {dest}\n\n"
        "Agent specification:\n{spec}"
    ),
    "skill": (
        "Create a skill directory with a SKILL.md file.\n"
        "The SKILL.md MUST have YAML frontmatter with name and description, "
        "followed by Markdown instructions.\n\n"
        "Write the directory to: {dest}\n\n"
        "Skill specification:\n{spec}"
    ),
}


class EvolveTool(Tool):
    """Meta-tool that lets SharanBot evolve itself by building new capabilities."""

    def __init__(self, agent_loop: "AgentLoop"):
        self._loop = agent_loop

    @property
    def name(self) -> str:
        return "evolve"

    @property
    def description(self) -> str:
        return (
            "Create a NEW SharanBot capability and hot-load it immediately. "
            "Types: tool (Python Tool class), worker (worker engine manifest), "
            "agent (specialised subagent profile), skill (SKILL.md package). "
            "Uses the GSD worker to build the component, writes it to the "
            "plugins directory, then triggers a hot-reload. The new capability "
            "becomes available instantly without restart."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "capability_type": {
                    "type": "string",
                    "enum": ["tool", "worker", "agent", "skill"],
                    "description": "Type of capability to create",
                },
                "capability_name": {
                    "type": "string",
                    "description": "Name for the new capability (lowercase, underscores ok)",
                },
                "specification": {
                    "type": "string",
                    "description": "Detailed description of what the capability should do",
                },
            },
            "required": ["capability_type", "capability_name", "specification"],
        }

    async def execute(
        self,
        capability_type: str,
        capability_name: str,
        specification: str,
        **kwargs: Any,
    ) -> str:
        workspace = self._loop.workspace
        plugins = workspace / "plugins"

        # Determine destination path
        dest_map = {
            "tool": plugins / "tools" / f"{capability_name}.py",
            "worker": plugins / "workers" / f"{capability_name}.json",
            "agent": plugins / "agents" / f"{capability_name}.yaml",
            "skill": workspace / "skills" / capability_name,
        }
        dest = dest_map.get(capability_type)
        if dest is None:
            return f"Error: unknown capability type '{capability_type}'"

        # Ensure parent directory exists
        dest.parent.mkdir(parents=True, exist_ok=True)

        # Build the task for the worker
        template = _SPEC_TEMPLATES.get(capability_type, "{spec}")
        task = template.format(dest=dest, spec=specification)

        # Try to use the GSD worker if available
        if self._loop.worker_manager:
            result = await self._loop.worker_manager.spawn(
                task=task,
                label=f"evolve:{capability_name}",
                project_dir=str(workspace),
            )
            # After the worker starts, trigger reload once it's done
            # For now return info — the worker runs async
            reload_result = await self._loop.handle_reload(
                "tools" if capability_type == "tool" else
                "workers" if capability_type == "worker" else
                "agents" if capability_type == "agent" else
                "skills",
            )
            return (
                f"Evolution started for {capability_type} '{capability_name}'.\n"
                f"Worker: {result}\n"
                f"Reload: {reload_result}\n"
                f"Destination: {dest}"
            )

        # Fallback: no worker available — ask the agent to create it directly
        return (
            f"No worker available for auto-build. Please create the {capability_type} manually:\n"
            f"1. Write the file to: {dest}\n"
            f"2. Then call: reload(component=\"{'tools' if capability_type == 'tool' else capability_type + 's'}\")\n\n"
            f"Specification: {specification}"
        )
