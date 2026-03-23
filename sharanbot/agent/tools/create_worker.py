"""CreateWorkerTool — scaffold worker modules and their spawn tools.

Generates:
1. A Worker class file that extends BaseWorker
2. A matching SpawnTool so the agent can spawn this worker type on demand
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from sharanbot.agent.tools.base import Tool


_WORKER_TEMPLATE = '''\
"""{{name}} worker for SharanBot."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger


class {{class_name}}Worker:
    """{{description}}

    Lifecycle:
        1. ``start()``  — initialize resources
        2. ``execute(task)`` — process a task
        3. ``stop()``   — cleanup
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {{}}
        self._running = False

    async def start(self) -> None:
        self._running = True
        logger.info("{{name}} worker started")

    async def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        """Execute a single task and return a result dict."""
        logger.info("{{name}} processing task: {{}}", task.get("id", "unknown"))
        # TODO: implement task processing
        return {{"status": "completed", "result": "done"}}

    async def stop(self) -> None:
        self._running = False
        logger.info("{{name}} worker stopped")

    @property
    def is_running(self) -> bool:
        return self._running
'''

_SPAWN_TOOL_TEMPLATE = '''\
"""Spawn{{class_name}} — spawn a {{name}} worker."""

from __future__ import annotations

from typing import Any

from loguru import logger

from sharanbot.agent.tools.base import Tool


class Spawn{{class_name}}Tool(Tool):
    name = "spawn_{{slug}}"
    description = "Spawn a {{name}} worker to handle tasks"
    parameters = {{
        "type": "object",
        "properties": {{
            "task": {{
                "type": "string",
                "description": "Task description for the worker",
            }},
        }},
        "required": ["task"],
    }}

    async def execute(self, **kwargs: Any) -> str:
        task_desc = kwargs["task"]
        logger.info("Spawning {{name}} worker for: {{}}", task_desc[:80])
        # Import the worker dynamically to avoid circular imports
        from sharanbot_worker.{{slug}} import {{class_name}}Worker

        worker = {{class_name}}Worker()
        await worker.start()
        try:
            result = await worker.execute({{"id": "spawn-1", "description": task_desc}})
            return f"✅ {{name}} worker completed: {{result}}"
        finally:
            await worker.stop()
'''


class CreateWorkerTool(Tool):
    name = "create_worker"
    description = (
        "Scaffold a new worker module with a Worker class and matching "
        "SpawnTool so the agent can spawn it on demand."
    )
    parameters = {
        "type": "object",
        "properties": {
            "worker_name": {
                "type": "string",
                "description": "Name for the worker (e.g. 'data_pipeline')",
            },
            "description": {
                "type": "string",
                "description": "What the worker does",
            },
        },
        "required": ["worker_name"],
    }

    async def execute(self, **kwargs: Any) -> str:
        name: str = kwargs["worker_name"]
        desc: str = kwargs.get("description", f"A {name} worker for SharanBot")

        slug = name.lower().replace(" ", "_").replace("-", "_")
        class_name = "".join(w.capitalize() for w in slug.split("_"))

        # 1. Worker file
        worker_dir = Path("sharanbot_worker")
        worker_dir.mkdir(parents=True, exist_ok=True)
        worker_file = worker_dir / f"{slug}.py"
        worker_content = (
            _WORKER_TEMPLATE
            .replace("{{name}}", name)
            .replace("{{slug}}", slug)
            .replace("{{class_name}}", class_name)
            .replace("{{description}}", desc)
        )
        worker_file.write_text(worker_content, encoding="utf-8")

        # 2. Spawn tool file
        tools_dir = Path("sharanbot/agent/tools")
        tools_dir.mkdir(parents=True, exist_ok=True)
        spawn_file = tools_dir / f"spawn_{slug}.py"
        spawn_content = (
            _SPAWN_TOOL_TEMPLATE
            .replace("{{name}}", name)
            .replace("{{slug}}", slug)
            .replace("{{class_name}}", class_name)
        )
        spawn_file.write_text(spawn_content, encoding="utf-8")

        logger.info("Created worker {} + spawn tool {}", worker_file, spawn_file)
        return (
            f"✅ Created worker at `{worker_file}` "
            f"and spawn tool at `{spawn_file}`"
        )
