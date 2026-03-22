"""Task planner — LLM-powered task decomposition into a DAG."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from sharanbot.agent.profiles import AgentProfileRegistry
from sharanbot.agent.team.dag import TaskDAG
from sharanbot.providers.base import LLMProvider

_DECOMPOSE_PROMPT = """\
You are a task planner. Decompose the following complex task into subtasks.

For each subtask, output a JSON object with:
- "id": short unique identifier (e.g. "research", "implement", "test")
- "description": what needs to be done
- "profile": which agent profile should handle it (from the available list)
- "depends_on": list of task IDs this depends on (empty list = can start immediately)
- "quality_gate": boolean, true if this task's output must be validated before dependent tasks proceed
- "max_retries": integer, how many times to retry on failure (default 3)
- "on_fail": optional task ID to retry when quality gate fails

Available agent profiles:
{profiles}

IMPORTANT RULES:
- Tasks that CAN run in parallel SHOULD have no dependency on each other
- Keep the number of tasks reasonable (3-8 for most projects)
- Use the most appropriate specialist profile for each task
- Always include a final review/integration task
- Output ONLY a JSON array, no other text

Task to decompose:
{task}"""


class TaskPlanner:
    """LLM-powered task decomposition into a DAG."""

    def __init__(
        self,
        provider: LLMProvider,
        model: str,
        profile_registry: AgentProfileRegistry,
    ):
        self.provider = provider
        self.model = model
        self.profile_registry = profile_registry

    async def decompose(self, task: str) -> TaskDAG:
        """Break a complex task into a DAG of subtasks using the LLM."""
        profiles_desc = self._format_profiles()
        prompt = _DECOMPOSE_PROMPT.format(profiles=profiles_desc, task=task)

        messages = [
            {"role": "system", "content": "You are a task planning assistant. Output only valid JSON."},
            {"role": "user", "content": prompt},
        ]

        response = await self.provider.chat_with_retry(
            messages=messages,
            model=self.model,
        )

        plan = self._parse_plan(response.content or "[]")
        dag = TaskDAG.from_plan(plan)
        logger.info("Task decomposed into {} subtasks", len(dag.tasks))
        return dag

    async def replan(self, dag: TaskDAG, failed_task_id: str, feedback: str) -> TaskDAG:
        """Adjust the plan after a task failure."""
        current_state = dag.to_summary()
        prompt = (
            f"The following task plan had a failure.\n\n"
            f"Current state:\n{current_state}\n\n"
            f"Failed task: {failed_task_id}\n"
            f"Feedback: {feedback}\n\n"
            f"Suggest an updated plan. Output ONLY a JSON array of task objects "
            f"with the same fields (id, description, profile, depends_on, quality_gate, max_retries).\n"
            f"Keep completed tasks and adjust the remaining ones."
        )

        messages = [
            {"role": "system", "content": "You are a task replanning assistant. Output only valid JSON."},
            {"role": "user", "content": prompt},
        ]

        response = await self.provider.chat_with_retry(
            messages=messages,
            model=self.model,
        )

        plan = self._parse_plan(response.content or "[]")
        new_dag = TaskDAG.from_plan(plan)

        # Preserve completed task results
        for tid, task in dag.tasks.items():
            if task.status == "completed" and tid in new_dag.tasks:
                new_dag.tasks[tid].status = "completed"
                new_dag.tasks[tid].result = task.result

        return new_dag

    def _format_profiles(self) -> str:
        """Format available profiles for the LLM prompt."""
        lines = []
        for p in self.profile_registry.list_profiles():
            lines.append(f"- {p.name}: {p.description or p.display_name}")
        return "\n".join(lines) if lines else "- general: Default agent"

    @staticmethod
    def _parse_plan(text: str) -> list[dict[str, Any]]:
        """Extract JSON array from LLM response."""
        text = text.strip()
        # Try to extract JSON from markdown code blocks
        if "```" in text:
            import re
            match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
            if match:
                text = match.group(1).strip()

        try:
            data = json.loads(text)
            if isinstance(data, list):
                return data
            return [data]
        except json.JSONDecodeError:
            logger.error("Failed to parse task plan from LLM response")
            # Fallback: single task
            return [{"id": "task_1", "description": "Complete the full task", "profile": "general", "depends_on": []}]
