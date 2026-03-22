"""Worker tools — expose GSD workers to the SharanBot agent."""

from typing import TYPE_CHECKING, Any

from sharanbot.agent.tools.base import Tool

if TYPE_CHECKING:
    from sharanbot.agent.worker import WorkerManager


class WorkerStartTool(Tool):
    """Start a GSD worker to build software autonomously."""

    def __init__(self, manager: "WorkerManager"):
        self._manager = manager
        self._origin_channel = "cli"
        self._origin_chat_id = "direct"

    def set_context(self, channel: str, chat_id: str) -> None:
        self._origin_channel = channel
        self._origin_chat_id = chat_id

    @property
    def name(self) -> str:
        return "worker_start"

    @property
    def description(self) -> str:
        return (
            "Start a GSD worker to autonomously build, code, or engineer a software project. "
            "Workers run in the background with full access to files, shell, search, and MCP tools. "
            "Use this for complex engineering tasks: building apps, refactoring codebases, "
            "implementing features, fixing bugs across many files, etc. "
            "Each worker gets its own process and can run for extended periods."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Detailed description of what to build or accomplish",
                },
                "label": {
                    "type": "string",
                    "description": "Short label for the worker (for display)",
                },
                "project_dir": {
                    "type": "string",
                    "description": "Directory to work in (defaults to workspace)",
                },
            },
            "required": ["task"],
        }

    async def execute(self, task: str, label: str | None = None, project_dir: str | None = None, **kwargs: Any) -> str:
        return await self._manager.spawn(
            task=task,
            label=label,
            project_dir=project_dir,
            origin_channel=self._origin_channel,
            origin_chat_id=self._origin_chat_id,
        )


class WorkerStatusTool(Tool):
    """Check status of running workers."""

    def __init__(self, manager: "WorkerManager"):
        self._manager = manager

    @property
    def name(self) -> str:
        return "worker_status"

    @property
    def description(self) -> str:
        return (
            "Check the status of GSD workers. "
            "Without a worker_id, lists all active workers. "
            "With a worker_id, shows detailed status and recent output."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "worker_id": {
                    "type": "string",
                    "description": "Worker ID to check (omit for all workers)",
                },
            },
        }

    async def execute(self, worker_id: str | None = None, **kwargs: Any) -> str:
        return self._manager.status(worker_id)


class WorkerSteerTool(Tool):
    """Send guidance to a running worker."""

    def __init__(self, manager: "WorkerManager"):
        self._manager = manager

    @property
    def name(self) -> str:
        return "worker_steer"

    @property
    def description(self) -> str:
        return (
            "Send a follow-up message or course correction to a running GSD worker. "
            "Use this to provide additional context, change direction, "
            "or give feedback while the worker is executing."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "worker_id": {
                    "type": "string",
                    "description": "The worker ID to steer",
                },
                "message": {
                    "type": "string",
                    "description": "Message to send to the worker",
                },
            },
            "required": ["worker_id", "message"],
        }

    async def execute(self, worker_id: str, message: str, **kwargs: Any) -> str:
        return await self._manager.steer(worker_id, message)


class WorkerStopTool(Tool):
    """Stop a running worker."""

    def __init__(self, manager: "WorkerManager"):
        self._manager = manager

    @property
    def name(self) -> str:
        return "worker_stop"

    @property
    def description(self) -> str:
        return "Stop a running GSD worker process."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "worker_id": {
                    "type": "string",
                    "description": "The worker ID to stop",
                },
            },
            "required": ["worker_id"],
        }

    async def execute(self, worker_id: str, **kwargs: Any) -> str:
        return await self._manager.stop(worker_id)
