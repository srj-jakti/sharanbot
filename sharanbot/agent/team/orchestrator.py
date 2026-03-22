"""Team orchestrator — creates and drives multi-agent teams."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from loguru import logger

from sharanbot.agent.profiles import AgentProfileRegistry
from sharanbot.agent.subagent import SubagentManager
from sharanbot.agent.team.bus import TeamBus
from sharanbot.agent.team.dag import TaskDAG, TaskNode
from sharanbot.agent.team.planner import TaskPlanner
from sharanbot.agent.team.quality import QualityGate, QualityResult
from sharanbot.agent.team.trace import TeamTracer
from sharanbot.bus.events import InboundMessage
from sharanbot.bus.queue import MessageBus
from sharanbot.providers.base import LLMProvider


@dataclass
class TeamSession:
    """Runtime state for one active team."""

    team_id: str
    task: str
    dag: TaskDAG
    bus: TeamBus
    tracer: TeamTracer
    created_at: datetime = field(default_factory=datetime.now)
    status: str = "running"  # running, completed, failed, cancelled
    origin_channel: str = "cli"
    origin_chat_id: str = "direct"


class TeamOrchestrator:
    """High-level orchestrator that manages multi-agent teams.

    Workflow:
    1. ``create_team(task)`` → decomposes into DAG, returns team_id
    2. ``run_team(team_id)`` → executes DAG tasks in parallel where possible
    3. Quality gates validate outputs before handing off
    4. ``steer_team(team_id, instruction)`` → modify a running team
    """

    # Per-task timeout in seconds — prevents hung subagents from blocking the team
    DEFAULT_TASK_TIMEOUT = 600  # 10 minutes

    def __init__(
        self,
        provider: LLMProvider,
        model: str,
        profile_registry: AgentProfileRegistry,
        subagent_manager: SubagentManager,
        message_bus: MessageBus,
        workspace,
        task_timeout: int = DEFAULT_TASK_TIMEOUT,
    ):
        self.provider = provider
        self.model = model
        self.profile_registry = profile_registry
        self.subagents = subagent_manager
        self.message_bus = message_bus
        self.workspace = workspace
        self.task_timeout = task_timeout

        self.planner = TaskPlanner(provider, model, profile_registry)
        self.quality = QualityGate(provider, model)
        self.teams: dict[str, TeamSession] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_team(
        self,
        task: str,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
    ) -> str:
        """Decompose a task into a DAG and prepare a team session."""
        team_id = str(uuid.uuid4())[:8]
        dag = await self.planner.decompose(task)

        bus = TeamBus()
        bus.register_agent("orchestrator")

        tracer = TeamTracer()

        session = TeamSession(
            team_id=team_id,
            task=task,
            dag=dag,
            bus=bus,
            tracer=tracer,
            origin_channel=origin_channel,
            origin_chat_id=origin_chat_id,
        )
        self.teams[team_id] = session
        logger.info("Team {} created with {} tasks", team_id, len(dag.tasks))
        return team_id

    async def run_team(self, team_id: str) -> str:
        """Execute the team's task DAG to completion."""
        session = self.teams.get(team_id)
        if not session:
            return f"Team {team_id} not found."

        session.tracer.start()
        dag = session.dag
        deadlock_wait_rounds = 0
        max_deadlock_rounds = 3  # Skip blocked tasks after 3 idle rounds

        while not dag.is_complete():
            ready = dag.get_ready_tasks()

            # Handle retrying tasks
            retrying = [t for t in dag.tasks.values() if t.status == "retrying"]
            for t in retrying:
                dag.reset_for_retry(t.id)
            if retrying and not ready:
                ready = dag.get_ready_tasks()

            if not ready:
                # No tasks ready and not complete → deadlock or all running
                running = [t for t in dag.tasks.values() if t.status == "running"]
                if not running:
                    deadlock_wait_rounds += 1
                    if deadlock_wait_rounds >= max_deadlock_rounds:
                        # Skip blocked pending tasks to break the deadlock
                        pending = [t for t in dag.tasks.values() if t.status == "pending"]
                        if pending:
                            logger.warning(
                                "Team {}: DAG deadlock after {} rounds, skipping {} blocked tasks",
                                team_id, deadlock_wait_rounds, len(pending),
                            )
                            for t in pending:
                                dag.mark_failed(t.id, "Skipped: dependency deadlock")
                            break
                        logger.warning("Team {}: DAG deadlock — no ready, running, or pending tasks", team_id)
                        break
                    # Wait briefly before checking again
                    await asyncio.sleep(2)
                    continue
                # Tasks are running, wait for them
                deadlock_wait_rounds = 0
                await asyncio.sleep(1)
                continue

            deadlock_wait_rounds = 0  # Reset on progress

            # Launch ready tasks concurrently
            coros = [
                self._execute_task(session, task_node)
                for task_node in ready
            ]
            await asyncio.gather(*coros, return_exceptions=True)

        # Finalize
        if dag.has_failures():
            session.status = "failed"
            summary = f"Team {team_id} completed with failures.\n"
        else:
            session.status = "completed"
            summary = f"Team {team_id} completed successfully.\n"

        summary += session.tracer.get_summary()

        # Collect results categorized by status
        succeeded = []
        failed_list = []
        skipped_list = []
        for tid in dag.topological_order():
            t = dag.tasks[tid]
            if t.status == "completed" and t.result:
                succeeded.append(f"  [✓ {tid}] {t.result[:300]}")
            elif t.status == "failed":
                failed_list.append(f"  [✗ {tid}] {t.error or 'unknown error'}")
            elif t.status == "skipped":
                skipped_list.append(f"  [⏭ {tid}] {t.error or 'skipped'}")

        summary += "\n\nCompleted:\n" + ("\n".join(succeeded) or "  (none)")
        if failed_list:
            summary += "\n\nFailed:\n" + "\n".join(failed_list)
        if skipped_list:
            summary += "\n\nSkipped:\n" + "\n".join(skipped_list)

        # Announce back to the user
        await self._announce(session, summary)

        return summary

    async def steer_team(self, team_id: str, instruction: str) -> str:
        """Modify a running team's plan or send guidance."""
        session = self.teams.get(team_id)
        if not session:
            return f"Team {team_id} not found."
        if session.status != "running":
            return f"Team {team_id} is {session.status}, cannot steer."

        # Broadcast the instruction to all active agents
        await session.bus.broadcast("orchestrator", "steer", instruction)
        return f"Instruction sent to team {team_id}: {instruction}"

    def get_team_status(self, team_id: str) -> str:
        """Get current status of a team."""
        session = self.teams.get(team_id)
        if not session:
            return f"Team {team_id} not found."

        progress = session.dag.get_progress()
        lines = [
            f"Team: {team_id} ({session.status})",
            f"Task: {session.task[:80]}",
            f"Progress: {progress['completed']}/{progress['total']} tasks",
        ]
        if progress['running'] > 0:
            lines.append(f"Running: {progress['running']}")
        if progress['failed'] > 0:
            lines.append(f"Failed: {progress['failed']}")

        # Per-task detail
        lines.append("\nTasks:")
        for tid in session.dag.topological_order():
            t = session.dag.tasks[tid]
            status_icon = {"pending": "⏳", "running": "🔄", "completed": "✅",
                           "failed": "❌", "retrying": "🔁", "skipped": "⏭️"}.get(t.status, "?")
            lines.append(f"  {status_icon} {tid}: {t.description[:60]} [{t.agent_profile}]")

        return "\n".join(lines)

    def list_teams(self) -> list[dict[str, Any]]:
        """List all teams and their statuses."""
        return [
            {
                "team_id": s.team_id,
                "task": s.task[:60],
                "status": s.status,
                "progress": s.dag.get_progress(),
            }
            for s in self.teams.values()
        ]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _execute_task(self, session: TeamSession, task: TaskNode) -> None:
        """Execute a single task node using a subagent."""
        dag = session.dag
        tracer = session.tracer
        agent_id = f"agent_{task.id}_{str(uuid.uuid4())[:4]}"

        dag.mark_running(task.id, agent_id)
        session.bus.register_agent(agent_id)
        tracer.trace_start(agent_id, task.id, task.description)

        # Build context from completed dependency results
        context_parts = [task.description]
        for dep_id in task.depends_on:
            dep = dag.get_task(dep_id)
            if dep and dep.result:
                context_parts.append(f"\n--- Output from [{dep_id}] ---\n{dep.result[:2000]}")

        full_task = "\n".join(context_parts)

        try:
            # Use SubagentManager to run the task (with timeout to prevent hangs)
            result = await asyncio.wait_for(
                self._run_task_as_subagent(
                    task_description=full_task,
                    profile=task.agent_profile,
                    agent_id=agent_id,
                ),
                timeout=self.task_timeout,
            )

            # Quality gate check if required
            if task.quality_gate:
                qr = await self.quality.evaluate(task, result)
                tracer.trace_quality_gate(task.id, qr.passed, qr.feedback)

                if not qr.passed:
                    dag.mark_failed(task.id, f"Quality gate failed: {qr.feedback}")
                    tracer.trace_fail(agent_id, task.id, qr.feedback)

                    if dag.tasks[task.id].status == "retrying":
                        tracer.trace_retry(agent_id, task.id, task.retry_count)
                    return

            dag.mark_completed(task.id, result)
            tracer.trace_complete(agent_id, task.id, result[:100])

            # Notify subsequent tasks via bus
            await session.bus.send(
                agent_id, "orchestrator", "result",
                f"Task {task.id} completed",
                {"task_id": task.id, "result": result[:2000]},
            )

        except asyncio.TimeoutError:
            error_msg = f"Task timed out after {self.task_timeout}s"
            dag.mark_failed(task.id, error_msg)
            tracer.trace_fail(agent_id, task.id, error_msg)
            logger.error("Task {} timed out after {}s", task.id, self.task_timeout)

        except Exception as e:
            error_msg = str(e)
            dag.mark_failed(task.id, error_msg)
            tracer.trace_fail(agent_id, task.id, error_msg)
            logger.error("Task {} failed: {}", task.id, error_msg)

        finally:
            session.bus.unregister_agent(agent_id)

    async def _run_task_as_subagent(
        self,
        task_description: str,
        profile: str,
        agent_id: str,
    ) -> str:
        """Run a task using a dedicated subagent and capture the result."""
        from sharanbot.agent.tools.filesystem import (
            EditFileTool, ListDirTool, ReadFileTool, WriteFileTool,
        )
        from sharanbot.agent.tools.registry import ToolRegistry
        from sharanbot.agent.tools.shell import ExecTool
        from sharanbot.agent.tools.web import WebFetchTool, WebSearchTool
        from sharanbot.agent.skills import BUILTIN_SKILLS_DIR
        from sharanbot.utils.helpers import build_assistant_message

        import json

        agent_profile = self.profile_registry.get(profile) or self.profile_registry.get("general")

        sub_model = agent_profile.model if agent_profile and agent_profile.model else self.model
        max_iter = agent_profile.max_iterations if agent_profile else 15

        tools = ToolRegistry()
        allowed_dir = self.workspace if getattr(self.subagents, "restrict_to_workspace", False) else None
        extra_read = [BUILTIN_SKILLS_DIR] if allowed_dir else None

        tools.register(ReadFileTool(workspace=self.workspace, allowed_dir=allowed_dir, extra_allowed_dirs=extra_read))
        tools.register(WriteFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
        tools.register(EditFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
        tools.register(ListDirTool(workspace=self.workspace, allowed_dir=allowed_dir))

        exec_config = self.subagents.exec_config
        tools.register(ExecTool(
            working_dir=str(self.workspace),
            timeout=exec_config.timeout,
            restrict_to_workspace=getattr(self.subagents, "restrict_to_workspace", False),
            path_append=exec_config.path_append,
        ))
        tools.register(WebSearchTool(config=self.subagents.web_search_config, proxy=self.subagents.web_proxy))
        tools.register(WebFetchTool(proxy=self.subagents.web_proxy))

        # Register browser and sandbox tools for richer team capabilities
        try:
            from sharanbot.agent.tools.browser import BrowserTool
            tools.register(BrowserTool(screenshots_dir=self.workspace / ".sharanbot_screenshots"))
        except Exception:
            pass  # Playwright not installed — skip
        try:
            from sharanbot.agent.tools.sandbox import SandboxTool
            tools.register(SandboxTool(workspace=self.workspace))
        except Exception:
            pass  # Docker not available — skip

        if agent_profile and agent_profile.denied_tools:
            for name in agent_profile.denied_tools:
                tools.unregister(name)

        system_prompt = (
            agent_profile.system_prompt
            if agent_profile and agent_profile.system_prompt
            else f"You are a specialist agent ({profile}). Complete the assigned task thoroughly."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task_description},
        ]

        iteration = 0
        final_result = None

        while iteration < max_iter:
            iteration += 1
            response = await self.provider.chat_with_retry(
                messages=messages,
                tools=tools.get_definitions(),
                model=sub_model,
            )

            if response.has_tool_calls:
                tool_call_dicts = [tc.to_openai_tool_call() for tc in response.tool_calls]
                messages.append(build_assistant_message(
                    response.content or "",
                    tool_calls=tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                ))

                for tool_call in response.tool_calls:
                    result = await tools.execute(tool_call.name, tool_call.arguments)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.name,
                        "content": result,
                    })
            else:
                final_result = response.content
                break

        return final_result or "Task completed but no final response was generated."

    async def _announce(self, session: TeamSession, summary: str) -> None:
        """Announce team result to the user via the message bus."""
        content = (
            f"[Team Orchestration Result]\n\n{summary}\n\n"
            "Summarize this result naturally for the user. Keep it concise."
        )
        msg = InboundMessage(
            channel="system",
            sender_id="team_orchestrator",
            chat_id=f"{session.origin_channel}:{session.origin_chat_id}",
            content=content,
        )
        await self.message_bus.publish_inbound(msg)

    async def cancel_team(self, team_id: str) -> str:
        """Cancel a running team."""
        session = self.teams.get(team_id)
        if not session:
            return f"Team {team_id} not found."
        session.status = "cancelled"
        session.bus.clear()
        return f"Team {team_id} cancelled."
