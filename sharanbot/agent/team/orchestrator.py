"""Team orchestrator — creates and drives multi-agent teams."""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
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


COMPLETION_FORMAT = """
When you have completed your task, end your final response with a structured summary:

## Task Completion
- **Files created/modified**: [list of file paths]
- **Architecture decisions**: [key choices made]
- **Summary**: [brief description of what was built]
"""


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
    project_root: Path | None = None  # Unified workspace for all agents
    manifest: Any = None  # ProjectManifest instance
    _task_handles: list[asyncio.Task] = field(default_factory=list)


class TeamOrchestrator:
    """High-level orchestrator that manages multi-agent teams.

    Workflow:
    1. ``create_team(task)`` → decomposes into DAG, returns team_id
    2. ``run_team(team_id)`` → executes DAG tasks in parallel where possible
    3. Quality gates validate outputs before handing off
    4. ``steer_team(team_id, instruction)`` → modify a running team
    """

    # Per-task timeout in seconds — generous to allow complex autonomous work
    DEFAULT_TASK_TIMEOUT = 3600  # 60 minutes
    # Max concurrent LLM-calling tasks to avoid rate limits
    DEFAULT_MAX_CONCURRENT = 3

    def __init__(
        self,
        provider: LLMProvider,
        model: str,
        profile_registry: AgentProfileRegistry,
        subagent_manager: SubagentManager,
        message_bus: MessageBus,
        workspace,
        task_timeout: int = DEFAULT_TASK_TIMEOUT,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    ):
        self.provider = provider
        self.model = model
        self.profile_registry = profile_registry
        self.subagents = subagent_manager
        self.message_bus = message_bus
        self.workspace = workspace
        self.task_timeout = task_timeout
        self.max_concurrent = max_concurrent
        self._concurrency_sem = asyncio.Semaphore(max_concurrent)
        self._recovery_engine = None  # Set via set_recovery_engine()
        self._reaction_engine = None  # Set via set_reaction_engine()

        self.planner = TaskPlanner(provider, model, profile_registry)
        self.quality = QualityGate(provider, model)
        self.teams: dict[str, TeamSession] = {}
        self._task_checkouts: dict[str, str] = {}  # task_id → agent_id

    def set_recovery_engine(self, engine) -> None:
        """Attach a RecoveryEngine for auto-recovery during task execution."""
        self._recovery_engine = engine

    def set_reaction_engine(self, engine) -> None:
        """Attach a ReactionEngine for event-driven reactions."""
        self._reaction_engine = engine

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_team(
        self,
        task: str,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        auto_run: bool = True,
    ) -> str:
        """Decompose a task into a DAG and prepare a team session."""
        from sharanbot.agent.team.manifest import ProjectManifest

        team_id = str(uuid.uuid4())[:8]
        dag = await self.planner.decompose(task)

        bus = TeamBus()
        bus.register_agent("orchestrator")

        tracer = TeamTracer()

        # Derive project name from task for unified workspace
        project_name = self._derive_project_name(task, team_id)
        project_root = Path(self.workspace) / project_name
        project_root.mkdir(parents=True, exist_ok=True)

        # Create project manifest — single source of truth
        manifest = ProjectManifest.create(project_name, project_root)

        # Pre-assign common ports to prevent port-hunting loops
        manifest.assign_port("backend")
        manifest.assign_port("frontend")

        session = TeamSession(
            team_id=team_id,
            task=task,
            dag=dag,
            bus=bus,
            tracer=tracer,
            origin_channel=origin_channel,
            origin_chat_id=origin_chat_id,
            project_root=project_root,
            manifest=manifest,
        )
        self.teams[team_id] = session
        logger.info("Team {} created with {} tasks, project_root={}", team_id, len(dag.tasks), project_root)

        # Auto-launch team execution in background
        if auto_run:
            asyncio.create_task(self.run_team(team_id))

        return team_id

    @staticmethod
    def _derive_project_name(task: str, team_id: str) -> str:
        """Derive a filesystem-safe project name from the task description."""
        import re

        # Strip emoji and unicode symbols before any processing
        task_clean = re.sub(
            r'[\U00010000-\U0010ffff\u2600-\u27bf\u2300-\u23ff\u2b50-\u2bff\u200d\ufe0f\u2014\u2013]',
            '', task,
        )

        # ── Strategy 1: Look for a quoted project name ──
        quoted = re.search(r'["\u201c]([^"\u201d]{2,40})["\u201d]', task_clean)
        if quoted:
            name = quoted.group(1).strip()
            name = re.sub(r"[^\w\s-]", "", name)
            name = re.sub(r"\s+", "-", name).strip("-").lower()
            if name and len(name) >= 2:
                return name[:40]

        # ── Strategy 2: Look for a CamelCase or TitleCase proper noun ──
        # e.g. "TradeArena", "ChatBot", "MyApp"
        camel = re.search(r'\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b', task_clean)
        if camel:
            # Convert CamelCase to kebab-case
            name = re.sub(r'([a-z])([A-Z])', r'\1-\2', camel.group(1)).lower()
            if len(name) >= 3:
                return name[:40]

        task_lower = task_clean.lower().strip()

        # ── Strategy 3: Verb-based pattern with filler-word skipping ──
        # Skip filler phrases like "this as a professional production-grade"
        filler = (
            r"(?:(?:this|it|that|something)"
            r"(?:\s+(?:as|like|into)\s+.*?)?"
            r"\s+)?"
        )
        for pattern in [
            # "build TradeArena" / "create a todo app"
            r"(?:build|create|develop|make|implement)\s+"
            + filler
            + r"(?:a\s+|an\s+|the\s+)?(?:professional\s+|production[\s-]*grade\s+|full[\s-]*stack\s+)*"
            r"(.+?)$",
        ]:
            match = re.search(pattern, task_lower)
            if match:
                name = match.group(1).strip()
                # Remove trailing noise words
                for suffix in [
                    " app", " application", " website", " web app",
                    " project", " system", " tool", " platform",
                    " from scratch", " for me", " please",
                    " using", " with",
                ]:
                    if name.endswith(suffix):
                        name = name[: -len(suffix)].strip()
                # Take first meaningful chunk (before "using", "with", "that", etc.)
                name = re.split(r"\s+(?:using|with|that|which|where|and|for)\s+", name)[0]
                name = name[:40]
                name = re.sub(r"[^\w\s-]", "", name)
                name = re.sub(r"\s+", "-", name).strip("-")
                if name and len(name) >= 2:
                    return name

        # ── Strategy 4: Fallback — extract first 3 significant words ──
        stop_words = {
            "a", "an", "the", "is", "it", "to", "for", "and", "or", "of",
            "in", "on", "at", "by", "with", "from", "me", "my", "please",
            "master", "prompt", "production", "ready", "this", "that",
            "build", "create", "develop", "make", "implement", "ok", "lets",
            "let", "use", "professional", "grade", "full", "stack", "real",
            "time", "as", "like", "want", "need", "should", "can", "will",
        }
        words = re.sub(r"[^\w\s]", "", task_lower).split()
        significant = [w for w in words if w not in stop_words and len(w) > 1][:3]
        if significant:
            name = "-".join(significant)[:40]
            if len(name) >= 2:
                return name

        return f"project-{team_id}"

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
                            # Emit deadlock reaction event
                            if self._reaction_engine:
                                try:
                                    from sharanbot.agi.reactions import EventType as RxEventType
                                    asyncio.create_task(self._reaction_engine.emit(
                                        RxEventType.TEAM_DEADLOCK,
                                        f"Team {team_id}: DAG deadlock, {len(pending)} tasks skipped",
                                        task_id=team_id,
                                    ))
                                except Exception:
                                    pass
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

            # Launch ready tasks concurrently (bounded by semaphore)
            coros = [
                self._execute_task_throttled(session, task_node)
                for task_node in ready
            ]
            await asyncio.gather(*coros, return_exceptions=True)

            # Persist DAG state after each batch for crash recovery
            self.save_team_state(team_id)

        # Finalize
        if dag.has_failures():
            session.status = "failed"
            summary = f"Team {team_id} completed with failures.\n"

            # ── Trigger self-evolution on high failure rate ──
            total_tasks = len(dag.tasks)
            failed_count = sum(1 for t in dag.tasks.values() if t.status == "failed")
            failure_rate = failed_count / total_tasks if total_tasks > 0 else 0

            if failure_rate >= 0.5 and self._reaction_engine:
                # High failure rate — emit event so systems can react
                try:
                    from sharanbot.agi.reactions import EventType as RxEventType
                    failure_details = "; ".join(
                        f"{t.id}: {(t.error or 'unknown')[:80]}"
                        for t in dag.tasks.values() if t.status == "failed"
                    )[:500]
                    asyncio.create_task(self._reaction_engine.emit(
                        RxEventType.TASK_FAILED,
                        f"Team {team_id}: {failed_count}/{total_tasks} tasks failed ({failure_rate:.0%}). "
                        f"Failures: {failure_details}",
                        task_id=team_id,
                    ))
                except Exception:
                    pass

                logger.warning(
                    "Team {}: high failure rate {:.0%} ({}/{}), evolution trigger emitted",
                    team_id, failure_rate, failed_count, total_tasks,
                )
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

        # Consolidated summary from all task completion comments
        consolidated = self._build_consolidated_summary(dag)
        if consolidated:
            summary += "\n\n" + consolidated

        # Announce back to the user
        await self._announce(session, summary)

        # Persist final state
        self.save_team_state(team_id)

        return summary

    async def steer_team(self, team_id: str, instruction: str) -> str:
        """Modify a running team's plan or send guidance."""
        session = self.teams.get(team_id)
        if not session:
            return f"Team {team_id} not found."
        if session.status != "running":
            return f"Team {team_id} is {session.status}, cannot steer."

        # Append comment to all running/pending tasks (primary delivery)
        comment = {
            "from": "orchestrator",
            "content": instruction,
            "timestamp": time.time(),
        }
        targeted = 0
        for task in session.dag.tasks.values():
            if task.status in ("running", "pending"):
                task.comments.append(comment)
                targeted += 1

        # Secondary notification via bus for progress visibility
        try:
            await session.bus.broadcast("orchestrator", "steer", instruction)
        except Exception:
            pass

        return f"Instruction delivered to {targeted} active tasks in team {team_id}."

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

    def checkout_task(self, task_id: str, agent_id: str) -> bool:
        """Atomically claim a task. Returns False if already held by another agent."""
        current = self._task_checkouts.get(task_id)
        if current and current != agent_id:
            logger.warning("Task {} already checked out by {}, {} blocked", task_id, current, agent_id)
            return False
        self._task_checkouts[task_id] = agent_id
        return True

    def release_task(self, task_id: str) -> None:
        """Release a task checkout."""
        self._task_checkouts.pop(task_id, None)

    async def _heartbeat_check(
        self, session: TeamSession, task: TaskNode, agent_id: str
    ) -> tuple[str, list[dict]]:
        """Check parent team status and collect new steering comments.

        Returns:
            ("continue" | "exit", list_of_new_comment_messages)
        """
        # 1. Check team cancellation
        if session.status in ("cancelled", "completed"):
            return "exit", []

        # 2. Check task reassignment via checkout
        checkout_holder = self._task_checkouts.get(task.id)
        if checkout_holder and checkout_holder != agent_id:
            return "exit", []

        # 3. Collect unread comments from TaskNode
        new_messages = []
        unread = task.comments[task._last_read_index:]
        task._last_read_index = len(task.comments)
        for comment in unread:
            new_messages.append({
                "role": "user",
                "content": f"[Team Steering from {comment.get('from', 'orchestrator')}] {comment.get('content', '')}",
            })

        return "continue", new_messages

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _execute_task_throttled(self, session: TeamSession, task: TaskNode) -> None:
        """Rate-limited wrapper around _execute_task using a semaphore."""
        handle = asyncio.current_task()
        if handle:
            session._task_handles.append(handle)
        try:
            async with self._concurrency_sem:
                await self._execute_task(session, task)
        finally:
            if handle and handle in session._task_handles:
                session._task_handles.remove(handle)

    async def _execute_task(self, session: TeamSession, task: TaskNode) -> None:
        """Execute a single task node using a subagent."""
        dag = session.dag
        tracer = session.tracer
        agent_id = f"agent_{task.id}_{str(uuid.uuid4())[:4]}"

        # Atomic task checkout — prevent duplicate execution
        if not self.checkout_task(task.id, agent_id):
            logger.warning("Task {} checkout failed for {}, skipping execution", task.id, agent_id)
            return

        dag.mark_running(task.id, agent_id)
        session.bus.register_agent(agent_id)
        tracer.trace_start(agent_id, task.id, task.description)

        # Register agent in manifest for coordination visibility
        if session.manifest:
            try:
                session.manifest.claim_agent(agent_id, task.agent_profile)
            except Exception as e:
                logger.debug("Manifest claim_agent failed for {}: {}", agent_id, e)

        # Build context from completed dependency results
        context_parts = [task.description]
        for dep_id in task.depends_on:
            dep = dag.get_task(dep_id)
            if dep and dep.result:
                context_parts.append(f"\n--- Output from [{dep_id}] ---\n{dep.result[:2000]}")

        # Lineage context: tell the agent where it sits in the task hierarchy
        if task.lineage:
            hierarchy = []
            for i, desc in enumerate(task.lineage):
                hierarchy.append(f"{'  ' * i}{i}. {desc}")
            hierarchy.append(f"{'  ' * len(task.lineage)}{len(task.lineage)}. {task.description}  <-- (your task)")
            context_parts.append(
                "\n--- Task Hierarchy ---\n"
                + "\n".join(hierarchy)
                + "\n\nStay focused on YOUR specific task. Do not implement functionality "
                "that belongs to other tasks in the hierarchy."
            )

        # Sibling context: tell the agent what parallel tasks exist
        siblings = dag.get_siblings(task.id)
        if siblings:
            sibling_lines = [f"  - {s}" for s in siblings]
            context_parts.append(
                "\n--- Parallel Work ---\n"
                "Sibling tasks being worked on in parallel:\n"
                + "\n".join(sibling_lines)
                + "\n\nDo not duplicate work that sibling tasks handle. "
                "If you need interfaces/types from siblings, define reasonable stubs."
            )

        full_task = "\n".join(context_parts)

        try:
            # All tasks run via subagent (GSD workers removed in v1.0.0)
            result = await asyncio.wait_for(
                self._run_task_as_subagent(
                    task_description=full_task,
                    profile=task.agent_profile,
                    agent_id=agent_id,
                    session=session,
                    task=task,
                ),
                timeout=self.task_timeout,
            )

            # Quality gate check if required
            if task.quality_gate:
                qr = await self.quality.evaluate(task, result)
                tracer.trace_quality_gate(task.id, qr.passed, qr.feedback)

                if not qr.passed:
                    # Lenient handling: if score >= 0.3, accept with warning
                    # This prevents subjective quality gates from blocking progress
                    if qr.score >= 0.3:
                        logger.warning(
                            "Task {} quality gate soft-pass (score={:.2f}): {}",
                            task.id, qr.score, qr.feedback[:100],
                        )
                        result += f"\n\n[Quality Warning: {qr.feedback[:200]}]"
                    else:
                        dag.mark_failed(task.id, f"Quality gate failed: {qr.feedback}")
                        tracer.trace_fail(agent_id, task.id, qr.feedback)
                        # Emit quality gate failure event
                        if self._reaction_engine:
                            try:
                                from sharanbot.agi.reactions import EventType as RxEventType
                                await self._reaction_engine.emit(
                                    RxEventType.QUALITY_GATE_FAILED,
                                    f"Quality gate failed for task {task.id}: {qr.feedback[:100]}",
                                    task_id=task.id,
                                    session_key=session.session_key if hasattr(session, 'session_key') else "",
                                )
                            except Exception:
                                pass

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
            logger.error("Task {} timed out after {}s", task.id, self.task_timeout)

            # Attempt auto-recovery on timeout
            if self._recovery_engine:
                recovered = await self._recovery_engine.handle_error(
                    TimeoutError(error_msg),
                    component=f"team_task_{task.id}",
                    context={"task_id": task.id, "team_id": session.team_id},
                )
                if recovered and task.retry_count < task.max_retries:
                    logger.info("Task {} auto-recovered from timeout, retrying", task.id)
                    dag.tasks[task.id].status = "retrying"
                    tracer.trace_retry(agent_id, task.id, task.retry_count)
                    return

            dag.mark_failed(task.id, error_msg)
            tracer.trace_fail(agent_id, task.id, error_msg)

        except Exception as e:
            error_msg = str(e)
            logger.error("Task {} failed: {}", task.id, error_msg)

            # Attempt auto-recovery on generic errors
            if self._recovery_engine:
                recovered = await self._recovery_engine.handle_error(
                    e,
                    component=f"team_task_{task.id}",
                    context={"task_id": task.id, "team_id": session.team_id},
                )
                if recovered and task.retry_count < task.max_retries:
                    logger.info("Task {} auto-recovered, retrying", task.id)
                    dag.tasks[task.id].status = "retrying"
                    tracer.trace_retry(agent_id, task.id, task.retry_count)
                    return

            dag.mark_failed(task.id, error_msg)
            tracer.trace_fail(agent_id, task.id, error_msg)

        finally:
            self.release_task(task.id)
            session.bus.unregister_agent(agent_id)

    async def _run_task_as_subagent(
        self,
        task_description: str,
        profile: str,
        agent_id: str,
        session: TeamSession | None = None,
        task: TaskNode | None = None,
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

        # Use project_root from session if available, otherwise fall back to workspace
        work_dir = session.project_root if session and session.project_root else self.workspace

        tools = ToolRegistry()
        allowed_dir = work_dir if getattr(self.subagents, "restrict_to_workspace", False) else None
        extra_read = [BUILTIN_SKILLS_DIR] if allowed_dir else None

        tools.register(ReadFileTool(workspace=work_dir, allowed_dir=allowed_dir, extra_allowed_dirs=extra_read))
        tools.register(WriteFileTool(workspace=work_dir, allowed_dir=allowed_dir))
        tools.register(EditFileTool(workspace=work_dir, allowed_dir=allowed_dir))
        tools.register(ListDirTool(workspace=work_dir, allowed_dir=allowed_dir))

        exec_config = self.subagents.exec_config
        tools.register(ExecTool(
            working_dir=str(work_dir),
            timeout=exec_config.timeout,
            restrict_to_workspace=getattr(self.subagents, "restrict_to_workspace", False),
            path_append=exec_config.path_append,
        ))
        tools.register(WebSearchTool(config=self.subagents.web_search_config, proxy=self.subagents.web_proxy))
        tools.register(WebFetchTool(proxy=self.subagents.web_proxy))

        # Wait tool — async sleep
        from sharanbot.agent.tools.wait import WaitTool
        tools.register(WaitTool())

        # Register browser and sandbox tools for richer team capabilities
        try:
            from sharanbot.agent.tools.browser import BrowserTool
            tools.register(BrowserTool(screenshots_dir=work_dir / ".sharanbot_screenshots"))
        except Exception:
            pass  # Playwright not installed — skip
        try:
            from sharanbot.agent.tools.sandbox import SandboxTool
            tools.register(SandboxTool(workspace=work_dir))
        except Exception:
            pass  # Docker not available — skip

        if agent_profile and agent_profile.denied_tools:
            for name in agent_profile.denied_tools:
                tools.unregister(name)

        # Build system prompt with manifest context
        base_prompt = (
            agent_profile.system_prompt
            if agent_profile and agent_profile.system_prompt
            else (
                f"You are a specialist agent ({profile}). Complete the assigned task thoroughly. "
                f"You run autonomously with no iteration limit. "
                f"NEVER claim completion without verification. "
                f"If something fails, report it honestly."
            )
        )

        # Inject manifest context if available
        manifest_context = ""
        if session and session.manifest:
            manifest_context = f"\n\n{session.manifest.summary_for_prompt()}"

        system_prompt = base_prompt + manifest_context + COMPLETION_FORMAT

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task_description},
        ]

        iteration = 0
        final_result = None
        _files_written = 0  # Track file output for productivity nudge
        HARD_ITERATION_CAP = 200  # Safety cap to prevent runaway subagents

        # Autonomous loop with hard safety cap
        while iteration < HARD_ITERATION_CAP:
            iteration += 1

            # Heartbeat check: verify parent team is still active and collect steering
            if session and task:
                action, new_msgs = await self._heartbeat_check(session, task, agent_id)
                if action == "exit":
                    return "Task cancelled via heartbeat check."
                for msg in new_msgs:
                    messages.append(msg)

            # Soft checkpoint every 25 iterations
            if iteration % 25 == 0:
                logger.info("Team task {} checkpoint: iteration {}", agent_id, iteration)

            # Productivity nudge: if 15+ iterations with no files written, push to produce output
            if iteration == 15 and _files_written == 0:
                messages.append({
                    "role": "user",
                    "content": (
                        "[System] You have spent 15 iterations without writing any files. "
                        "STOP planning and START writing code/files immediately. "
                        "Create the project structure and write actual files NOW. "
                        "You can iterate and improve later — get something on disk first."
                    ),
                })
                logger.warning("Team task {}: productivity nudge at iter 15 (0 files written)", agent_id)

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

                    # Update manifest when subagent writes files or makes decisions
                    if session and session.manifest:
                        self._update_manifest_from_tool(
                            session.manifest, agent_id, tool_call.name, tool_call.arguments,
                        )

                    # Track file writes for productivity nudge
                    if tool_call.name in ("write_file", "edit_file"):
                        _files_written += 1

                # Report progress on TeamBus after tool execution
                if session:
                    tool_names = [tc.name for tc in response.tool_calls]
                    try:
                        await session.bus.send(
                            agent_id, "orchestrator", "progress",
                            f"Iteration {iteration}, used: {', '.join(tool_names)}",
                        )
                    except Exception:
                        pass
            else:
                final_result = response.content
                break

        result_text = final_result or (
            f"Task reached iteration cap ({HARD_ITERATION_CAP}). "
            f"Files written: {_files_written}. Agent: {agent_id}. "
            "Task may be incomplete."
            if iteration >= HARD_ITERATION_CAP
            else "Task completed but no final response was generated."
        )

        # Parse structured completion report and update manifest
        if session and session.manifest:
            self._parse_completion_report(result_text, session.manifest, agent_id)

        return result_text

    @staticmethod
    def _update_manifest_from_tool(
        manifest,
        agent_id: str,
        tool_name: str,
        arguments: dict,
    ) -> None:
        """Update the project manifest based on tool calls made by subagents.

        Tracks file ownership and architecture decisions so all agents
        can see what others have done.
        """
        try:
            # Track file writes
            write_tools = {"write_file", "edit_file", "mcp_filesystem_write_file", "mcp_filesystem_edit_file"}
            if tool_name in write_tools:
                filepath = arguments.get("path") or arguments.get("file_path") or ""
                if filepath:
                    manifest.claim_files(agent_id, [filepath])

            # Track architecture decisions from exec commands
            if tool_name == "exec":
                cmd = arguments.get("command", "")
                decision_patterns = [
                    ("npm init", "Initialized npm project"),
                    ("npx create-", "Scaffolded project"),
                    ("pip install", "Installed Python dependencies"),
                    ("npm install", "Installed npm dependencies"),
                    ("cargo init", "Initialized Rust project"),
                    ("go mod init", "Initialized Go module"),
                    ("dotnet new", "Scaffolded .NET project"),
                    ("django-admin startproject", "Created Django project"),
                    ("rails new", "Created Rails project"),
                    ("vite create", "Scaffolded Vite project"),
                ]
                for pattern, desc in decision_patterns:
                    if pattern in cmd:
                        manifest.record_decision(agent_id, f"{desc}: {cmd[:100]}")
                        break

            # Track config file writes as architecture decisions
            if tool_name in write_tools:
                filepath = arguments.get("path") or arguments.get("file_path") or ""
                config_patterns = {
                    "vite.config": "Configured Vite",
                    "tsconfig": "Configured TypeScript",
                    "Dockerfile": "Added Docker configuration",
                    "docker-compose": "Added Docker Compose",
                    ".eslintrc": "Configured ESLint",
                    "webpack.config": "Configured Webpack",
                    "next.config": "Configured Next.js",
                    "tailwind.config": "Configured Tailwind CSS",
                }
                for pattern, desc in config_patterns.items():
                    if pattern in filepath:
                        manifest.record_decision(agent_id, f"{desc}: {filepath}")
                        break
        except Exception as e:
            logger.debug("Manifest update failed for {}: {}", tool_name, e)

    @staticmethod
    def _build_consolidated_summary(dag: TaskDAG) -> str:
        """Build a consolidated summary from all completed task results and comments.

        Collects structured completion data (files, decisions, summaries) from
        each completed task's comments and result, producing a unified report
        for the Autopilot (Mode B) to review.
        """
        task_summaries: list[str] = []
        all_files: list[str] = []
        all_decisions: list[str] = []

        for tid in dag.topological_order():
            t = dag.tasks[tid]
            if t.status != "completed" or not t.result:
                continue

            entry = f"- [{tid}] {t.description}"

            # Extract structured data from completion-type comments
            for comment in t.comments:
                if comment.get("type") == "completion":
                    files = comment.get("files", [])
                    decisions = comment.get("decisions", [])
                    comment_summary = comment.get("summary", "")
                    if files:
                        all_files.extend(files)
                    if decisions:
                        all_decisions.extend(decisions)
                    if comment_summary:
                        entry += f": {comment_summary}"

            # Reference the task result (truncated)
            result_preview = t.result[:200].replace("\n", " ").strip()
            if result_preview:
                entry += f"\n  Result: {result_preview}"

            task_summaries.append(entry)

        if not task_summaries:
            return ""

        parts = ["Consolidated Task Summary:"]
        parts.append("\n".join(task_summaries))

        if all_files:
            unique_files = list(dict.fromkeys(all_files))  # preserve order, dedupe
            parts.append("Files produced: " + ", ".join(unique_files))

        if all_decisions:
            parts.append("Architecture decisions: " + "; ".join(all_decisions))

        return "\n\n".join(parts)

    @staticmethod
    def _parse_completion_report(result: str, manifest, agent_id: str) -> None:
        """Extract structured completion data from subagent response."""
        if "## Task Completion" in result:
            # Parse files
            files_match = re.search(
                r"Files created/modified.*?:\s*(.+?)(?:\n-|\n##|\Z)", result, re.DOTALL,
            )
            if files_match:
                files = re.findall(r'[`"]?([^\s,`"]+\.\w+)[`"]?', files_match.group(1))
                manifest.claim_files(agent_id, files)

            # Parse decisions
            decisions_match = re.search(
                r"Architecture decisions.*?:\s*(.+?)(?:\n-|\n##|\Z)", result, re.DOTALL,
            )
            if decisions_match:
                manifest.record_decision(agent_id, decisions_match.group(1).strip()[:200])

    @staticmethod
    def _build_consolidated_summary(dag: TaskDAG) -> str:
        """Build a consolidated summary from all completed task results and comments.

        Collects structured completion data (files, decisions, summaries) from
        each completed task's comments and result, producing a unified report
        for the Autopilot (Mode B) to review.
        """
        task_summaries: list[str] = []
        all_files: list[str] = []
        all_decisions: list[str] = []

        for tid in dag.topological_order():
            t = dag.tasks[tid]
            if t.status != "completed" or not t.result:
                continue

            entry = f"- [{tid}] {t.description}"

            # Extract structured data from completion-type comments
            for comment in t.comments:
                if comment.get("type") == "completion":
                    files = comment.get("files", [])
                    decisions = comment.get("decisions", [])
                    comment_summary = comment.get("summary", "")
                    if files:
                        all_files.extend(files)
                    if decisions:
                        all_decisions.extend(decisions)
                    if comment_summary:
                        entry += f": {comment_summary}"

            # Reference the task result (truncated)
            result_preview = t.result[:200].replace("\n", " ").strip()
            if result_preview:
                entry += f"\n  Result: {result_preview}"

            task_summaries.append(entry)

        if not task_summaries:
            return ""

        parts = ["Consolidated Task Summary:"]
        parts.append("\n".join(task_summaries))

        if all_files:
            unique_files = list(dict.fromkeys(all_files))  # preserve order, dedupe
            parts.append("Files produced: " + ", ".join(unique_files))

        if all_decisions:
            parts.append("Architecture decisions: " + "; ".join(all_decisions))

        return "\n\n".join(parts)


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
        """Cancel a running team and all its subagent tasks."""
        session = self.teams.get(team_id)
        if not session:
            return f"Team {team_id} not found."
        session.status = "cancelled"
        session.bus.clear()
        # Cancel all running asyncio tasks
        for handle in list(session._task_handles):
            if not handle.done():
                handle.cancel()
        # Release all checkouts for this team
        team_task_ids = set(session.dag.tasks.keys())
        for tid in team_task_ids:
            self._task_checkouts.pop(tid, None)
        logger.info("Team {} cancelled", team_id)
        return f"Team {team_id} cancelled."

    # ------------------------------------------------------------------
    # Persistence — save/restore team state for crash recovery
    # ------------------------------------------------------------------

    def save_team_state(self, team_id: str) -> None:
        """Persist a team's DAG state to disk for crash recovery."""
        session = self.teams.get(team_id)
        if not session:
            return
        state_dir = Path(self.workspace) / ".team_state"
        state_dir.mkdir(parents=True, exist_ok=True)
        state = {
            "team_id": session.team_id,
            "task": session.task,
            "status": session.status,
            "origin_channel": session.origin_channel,
            "origin_chat_id": session.origin_chat_id,
            "created_at": session.created_at.isoformat(),
            "project_root": str(session.project_root) if session.project_root else None,
            "dag": {
                tid: {
                    "id": t.id,
                    "description": t.description,
                    "agent_profile": t.agent_profile,
                    "depends_on": t.depends_on,
                    "status": t.status,
                    "result": t.result,
                    "error": t.error,
                    "retry_count": t.retry_count,
                    "max_retries": t.max_retries,
                    "quality_gate": t.quality_gate,
                }
                for tid, t in session.dag.tasks.items()
            },
        }
        state_file = state_dir / f"{team_id}.json"
        import json
        tmp = state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(state_file)
        logger.debug("Saved team state for {}", team_id)

    def restore_team_state(self, team_id: str) -> TeamSession | None:
        """Restore a team session from disk. Returns None if not found."""
        state_file = Path(self.workspace) / ".team_state" / f"{team_id}.json"
        if not state_file.exists():
            return None
        import json
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            dag = TaskDAG()
            for tid, tdata in data["dag"].items():
                node = TaskNode(
                    id=tdata["id"],
                    description=tdata["description"],
                    agent_profile=tdata["agent_profile"],
                    depends_on=tdata["depends_on"],
                    status=tdata["status"],
                    result=tdata.get("result"),
                    error=tdata.get("error"),
                    retry_count=tdata.get("retry_count", 0),
                    max_retries=tdata.get("max_retries", 3),
                    quality_gate=tdata.get("quality_gate", False),
                )
                dag.tasks[tid] = node
            dag._execution_order = None

            session = TeamSession(
                team_id=data["team_id"],
                task=data["task"],
                dag=dag,
                bus=TeamBus(),
                tracer=TeamTracer(),
                status=data.get("status", "running"),
                origin_channel=data.get("origin_channel", "cli"),
                origin_chat_id=data.get("origin_chat_id", "direct"),
                project_root=Path(data["project_root"]) if data.get("project_root") else None,
            )
            # Restore manifest if project_root exists
            if session.project_root:
                from sharanbot.agent.team.manifest import ProjectManifest
                session.manifest = ProjectManifest.load(session.project_root)
            session.bus.register_agent("orchestrator")
            self.teams[team_id] = session
            logger.info("Restored team {} from disk ({} tasks)", team_id, len(dag.tasks))
            return session
        except Exception as e:
            logger.warning("Failed to restore team {}: {}", team_id, e)
            return None

    def list_saved_teams(self) -> list[str]:
        """List team IDs that have saved state on disk."""
        state_dir = Path(self.workspace) / ".team_state"
        if not state_dir.exists():
            return []
        return [f.stem for f in state_dir.glob("*.json")]
