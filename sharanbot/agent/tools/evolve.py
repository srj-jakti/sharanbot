"""Self-evolution meta-tool — lets SharanBot extend itself at runtime.

The ``evolve`` tool orchestrates:
1. Spawning a GSD worker to *build* a new component (tool, worker, agent profile, skill)
2. Writing it to the correct ``plugins/`` directory
3. Triggering a hot-reload so the new component is immediately usable

This creates the ∞-capability loop: SharanBot → builds → loads → uses → builds more.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

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

        # Backup existing file for rollback
        backup = None
        if dest.is_file():
            backup = dest.with_suffix(dest.suffix + ".bak")
            shutil.copy2(dest, backup)
            logger.debug("Backed up {} → {}", dest.name, backup.name)

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

            # Validate the generated file before reloading
            if capability_type == "tool" and dest.is_file():
                error = self._validate_python_tool(dest, capability_name)
                if error:
                    logger.warning("Generated tool failed validation: {}", error)
                    if backup:
                        shutil.copy2(backup, dest)
                        backup.unlink()
                        logger.info("Rolled back {} from backup", dest.name)
                    else:
                        dest.unlink()
                    return (
                        f"Evolution FAILED for tool '{capability_name}': {error}\n"
                        f"The file has been rolled back. Please fix the specification and retry."
                    )

            # Clean up backup on success
            if backup and backup.exists():
                backup.unlink()

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
        if backup and backup.exists():
            backup.unlink()
        return (
            f"No worker available for auto-build. Please create the {capability_type} manually:\n"
            f"1. Write the file to: {dest}\n"
            f"2. Then call: reload(component=\"{'tools' if capability_type == 'tool' else capability_type + 's'}\")\n\n"
            f"Specification: {specification}"
        )

    @staticmethod
    def _validate_python_tool(path: Path, expected_name: str) -> str | None:
        """Validate a generated Python tool file. Returns error string or None."""
        try:
            source = path.read_text(encoding="utf-8")
        except Exception as exc:
            return f"Cannot read file: {exc}"

        # 1. AST parse — catch syntax errors
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            return f"Syntax error at line {exc.lineno}: {exc.msg}"

        # 2. Check for at least one class definition inheriting from Tool
        has_tool_class = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    base_name = None
                    if isinstance(base, ast.Name):
                        base_name = base.id
                    elif isinstance(base, ast.Attribute):
                        base_name = base.attr
                    if base_name == "Tool":
                        has_tool_class = True
                        break
            if has_tool_class:
                break

        if not has_tool_class:
            return "No class inheriting from Tool found in the generated file"

        return None
