"""Team orchestration tools — create, monitor, and steer multi-agent teams."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sharanbot.agent.tools.base import Tool

if TYPE_CHECKING:
    from sharanbot.agent.team.orchestrator import TeamOrchestrator


class TeamCreateTool(Tool):
    """Tool to create and launch a multi-agent team for complex tasks."""

    def __init__(self, orchestrator: "TeamOrchestrator"):
        self._orchestrator = orchestrator
        self._origin_channel = "cli"
        self._origin_chat_id = "direct"

    def set_context(self, channel: str, chat_id: str) -> None:
        self._origin_channel = channel
        self._origin_chat_id = chat_id

    @property
    def name(self) -> str:
        return "team_create"

    @property
    def description(self) -> str:
        return (
            "Create a multi-agent team to handle a complex task. "
            "The task is automatically decomposed into subtasks and assigned to specialist agents. "
            "Use this for tasks requiring multiple skills (e.g., research + code + test) "
            "or tasks that benefit from parallel execution. "
            "Returns a team_id for monitoring progress."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The complex task to decompose and execute with a team.",
                },
                "pipeline": {
                    "type": "string",
                    "description": (
                        "Optional pipeline template: 'build' (research→implement→test→review), "
                        "'deep_research' (parallel search→synthesize), "
                        "'dev_qa' (implement→review→fix), "
                        "'full_stack' (design→backend+frontend→integrate→test). "
                        "If omitted, the LLM auto-plans the DAG."
                    ),
                },
            },
            "required": ["task"],
        }

    async def execute(self, task: str, pipeline: str | None = None, **kwargs: Any) -> str:
        import asyncio
        from sharanbot.agent.team.pipelines import PipelineRegistry

        if pipeline:
            registry = PipelineRegistry()
            dag = registry.get(pipeline, task)
            if dag:
                team_id = await self._orchestrator.create_team(
                    task=task,
                    origin_channel=self._origin_channel,
                    origin_chat_id=self._origin_chat_id,
                )
                # Override the auto-planned DAG with the template
                self._orchestrator.teams[team_id].dag = dag
            else:
                return f"Unknown pipeline '{pipeline}'. Available: {registry.list_templates()}"
        else:
            team_id = await self._orchestrator.create_team(
                task=task,
                origin_channel=self._origin_channel,
                origin_chat_id=self._origin_chat_id,
            )

        # Launch team execution in background
        asyncio.create_task(self._orchestrator.run_team(team_id))

        status = self._orchestrator.get_team_status(team_id)
        return f"Team created and running.\n\n{status}"


class TeamStatusTool(Tool):
    """Tool to check the status of a running team."""

    def __init__(self, orchestrator: "TeamOrchestrator"):
        self._orchestrator = orchestrator

    @property
    def name(self) -> str:
        return "team_status"

    @property
    def description(self) -> str:
        return "Check the status and progress of a multi-agent team."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "team_id": {
                    "type": "string",
                    "description": "The team ID to check. If omitted, lists all teams.",
                },
            },
            "required": [],
        }

    async def execute(self, team_id: str | None = None, **kwargs: Any) -> str:
        if team_id:
            return self._orchestrator.get_team_status(team_id)

        teams = self._orchestrator.list_teams()
        if not teams:
            return "No teams currently active."

        lines = ["Active teams:"]
        for t in teams:
            p = t["progress"]
            lines.append(f"  [{t['team_id']}] {t['task']} — {t['status']} ({p['completed']}/{p['total']})")
        return "\n".join(lines)


class TeamSteerTool(Tool):
    """Tool to send guidance or modifications to a running team."""

    def __init__(self, orchestrator: "TeamOrchestrator"):
        self._orchestrator = orchestrator

    @property
    def name(self) -> str:
        return "team_steer"

    @property
    def description(self) -> str:
        return (
            "Send instructions or guidance to a running team. "
            "Use this to redirect focus, add constraints, or provide feedback."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "team_id": {
                    "type": "string",
                    "description": "The team ID to steer.",
                },
                "instruction": {
                    "type": "string",
                    "description": "Guidance or instruction for the team.",
                },
            },
            "required": ["team_id", "instruction"],
        }

    async def execute(self, team_id: str, instruction: str, **kwargs: Any) -> str:
        return await self._orchestrator.steer_team(team_id, instruction)
