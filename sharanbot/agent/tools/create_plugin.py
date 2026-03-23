"""CreatePluginTool — scaffold channel plugins and MCP servers at runtime.

Supports two modes:
- **channel**: Creates a SharanBot channel plugin (init/start/send handlers)
- **mcp_server**: Creates an MCP (Model Context Protocol) server scaffold

The generated code follows SharanBot conventions and can be loaded without restart.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from sharanbot.agent.tools.base import Tool


_CHANNEL_TEMPLATE = '''\
"""{{name}} channel plugin for SharanBot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from sharanbot.channels.base import BaseChannel


@dataclass
class {{class_name}}Config:
    """Configuration for the {{name}} channel."""
    enabled: bool = True
    # Add channel-specific config fields here


class {{class_name}}Channel(BaseChannel):
    """{{description}}"""

    name = "{{slug}}"

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__()
        self.config = {{class_name}}Config(**(config or {{}}))

    async def start(self) -> None:
        """Start listening for messages on this channel."""
        logger.info("{{name}} channel started")
        # TODO: Implement connection / webhook / polling logic

    async def send(self, recipient: str, message: str, **kwargs: Any) -> None:
        """Send a message through this channel."""
        logger.info("{{name}} → {{}}: {{}}", recipient, message[:80])
        # TODO: Implement actual send logic

    async def stop(self) -> None:
        """Gracefully shut down the channel."""
        logger.info("{{name}} channel stopped")
'''

_MCP_SERVER_TEMPLATE = '''\
"""{{name}} — MCP server for SharanBot.

Implements the Model Context Protocol (stdio JSON-RPC) to expose
tools/resources to MCP-compatible clients.
"""

from __future__ import annotations

import json
import sys
from typing import Any


TOOLS = [
    {{
        "name": "{{slug}}_example",
        "description": "An example tool for the {{name}} MCP server",
        "inputSchema": {{
            "type": "object",
            "properties": {{
                "query": {{"type": "string", "description": "A query string"}}
            }},
            "required": ["query"],
        }},
    }},
]


def handle_request(method: str, params: dict[str, Any]) -> Any:
    """Route JSON-RPC requests to the appropriate handler."""
    if method == "tools/list":
        return {{"tools": TOOLS}}
    if method == "tools/call":
        tool_name = params.get("name", "")
        args = params.get("arguments", {{}})
        return call_tool(tool_name, args)
    return {{"error": f"Unknown method: {{method}}"}}


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute a tool by name."""
    if name == "{{slug}}_example":
        return {{"content": [{{"type": "text", "text": f"Echo: {{arguments.get('query', '')}}"}}]}}
    return {{"error": f"Unknown tool: {{name}}"}}


def main() -> None:
    """Stdio JSON-RPC loop."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        result = handle_request(req.get("method", ""), req.get("params", {{}}))
        resp = {{"jsonrpc": "2.0", "id": req.get("id"), "result": result}}
        sys.stdout.write(json.dumps(resp) + "\\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
'''


class CreatePluginTool(Tool):
    name = "create_plugin"
    description = (
        "Scaffold a new SharanBot channel plugin or MCP server. "
        "Provide a name, type ('channel' or 'mcp_server'), and optional description."
    )
    parameters = {
        "type": "object",
        "properties": {
            "plugin_name": {
                "type": "string",
                "description": "Name for the plugin (e.g. 'WhatsApp', 'Jira')",
            },
            "plugin_type": {
                "type": "string",
                "enum": ["channel", "mcp_server"],
                "description": "Type of plugin to create",
            },
            "description": {
                "type": "string",
                "description": "Short description of the plugin's purpose",
            },
        },
        "required": ["plugin_name", "plugin_type"],
    }

    async def execute(self, **kwargs: Any) -> str:
        name: str = kwargs["plugin_name"]
        ptype: str = kwargs["plugin_type"]
        desc: str = kwargs.get("description", f"A {name} plugin for SharanBot")

        slug = name.lower().replace(" ", "_").replace("-", "_")
        class_name = name.replace(" ", "").replace("-", "").replace("_", "")

        if ptype == "channel":
            template = _CHANNEL_TEMPLATE
            target_dir = Path("sharanbot/channels")
        else:
            template = _MCP_SERVER_TEMPLATE
            target_dir = Path("sharanbot/services/mcp")

        target_dir.mkdir(parents=True, exist_ok=True)
        filename = target_dir / f"{slug}.py"

        content = (
            template
            .replace("{{name}}", name)
            .replace("{{slug}}", slug)
            .replace("{{class_name}}", class_name)
            .replace("{{description}}", desc)
        )

        filename.write_text(content, encoding="utf-8")
        logger.info("Created {} plugin: {}", ptype, filename)
        return f"✅ Created {ptype} plugin at `{filename}`"
