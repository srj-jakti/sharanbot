"""CreateToolTool — create and hot‑register new tools at runtime.

Generates a tool Python file from a name, description, and parameter spec,
then dynamically imports it and registers it in the live ToolRegistry
so it's immediately available without restart.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from sharanbot.agent.tools.base import Tool


_TOOL_TEMPLATE = '''\
"""{{name}} — auto‑generated tool for SharanBot."""

from __future__ import annotations

from typing import Any

from sharanbot.agent.tools.base import Tool


class {{class_name}}(Tool):
    name = "{{slug}}"
    description = "{{description}}"
    parameters = {{parameters}}

    async def execute(self, **kwargs: Any) -> str:
        # TODO: Implement tool logic
        return "{{slug}} executed with: " + str(kwargs)
'''


class CreateToolTool(Tool):
    name = "create_tool"
    description = (
        "Create a new SharanBot tool and hot‑register it so it's immediately "
        "available in the current session. Provide a name, description, and "
        "parameter schema."
    )
    parameters = {
        "type": "object",
        "properties": {
            "tool_name": {
                "type": "string",
                "description": "Name for the tool (e.g. 'fetch_weather')",
            },
            "tool_description": {
                "type": "string",
                "description": "What the tool does",
            },
            "tool_parameters": {
                "type": "object",
                "description": "JSON Schema for the tool's parameters",
            },
        },
        "required": ["tool_name", "tool_description"],
    }

    def __init__(self, tool_registry: Any | None = None):
        self._registry = tool_registry

    async def execute(self, **kwargs: Any) -> str:
        name: str = kwargs["tool_name"]
        desc: str = kwargs["tool_description"]
        params: dict = kwargs.get("tool_parameters", {
            "type": "object",
            "properties": {},
            "required": [],
        })

        slug = name.lower().replace(" ", "_").replace("-", "_")
        class_name = "".join(w.capitalize() for w in slug.split("_")) + "Tool"

        tools_dir = Path("sharanbot/agent/tools")
        tools_dir.mkdir(parents=True, exist_ok=True)
        filepath = tools_dir / f"{slug}.py"

        content = (
            _TOOL_TEMPLATE
            .replace("{{name}}", name)
            .replace("{{slug}}", slug)
            .replace("{{class_name}}", class_name)
            .replace("{{description}}", desc)
            .replace("{{parameters}}", repr(params))
        )

        filepath.write_text(content, encoding="utf-8")
        logger.info("Created tool file: {}", filepath)

        # Hot-register: dynamically import and register
        try:
            module_name = f"sharanbot.agent.tools.{slug}"
            spec = importlib.util.spec_from_file_location(module_name, filepath)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = mod
                spec.loader.exec_module(mod)  # type: ignore[union-attr]

                # Find the Tool subclass
                tool_cls = None
                for attr_name in dir(mod):
                    obj = getattr(mod, attr_name)
                    if (
                        isinstance(obj, type)
                        and issubclass(obj, Tool)
                        and obj is not Tool
                    ):
                        tool_cls = obj
                        break

                if tool_cls and self._registry:
                    instance = tool_cls()
                    self._registry.register(instance)
                    logger.info("Hot-registered tool: {}", instance.name)
                    return f"✅ Created and registered tool `{slug}` at `{filepath}`"
                elif tool_cls:
                    return f"✅ Created tool `{slug}` at `{filepath}` (no registry — register manually)"
        except Exception as exc:
            logger.error("Hot-register failed for {}: {}", slug, exc)

        return f"✅ Created tool file at `{filepath}` (import failed — register on next restart)"
