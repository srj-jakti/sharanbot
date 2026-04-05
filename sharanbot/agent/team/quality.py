"""Quality gate — automated validation of task outputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from sharanbot.agent.team.dag import TaskNode
from sharanbot.providers.base import LLMProvider

_QA_PROMPT = """\
You are a quality assurance evaluator. Evaluate the following task output.

Task description: {task_desc}
Task output/result:
{result}

{criteria}

Evaluate and respond with ONLY a JSON object:
{{
  "passed": true/false,
  "score": 0.0 to 1.0,
  "feedback": "specific actionable feedback",
  "evidence": ["what was checked"]
}}

IMPORTANT evaluation rules:
- Pass if the output makes meaningful progress toward the task, even if not perfect.
- Design/architecture tasks pass if they produce a coherent plan or structure.
- Code tasks pass if they produce working code files, even with minor issues.
- Only FAIL if the output is completely off-topic, empty, or fundamentally broken.
- Do NOT fail for style issues, missing edge cases, or incomplete polish.
If it fails, provide specific, actionable feedback for improvement."""


@dataclass
class QualityResult:
    """Result of a quality gate evaluation."""

    passed: bool
    score: float  # 0.0 - 1.0
    feedback: str
    evidence: list[str]


class QualityGate:
    """LLM-powered quality validation for task outputs."""

    def __init__(self, provider: LLMProvider, model: str):
        self.provider = provider
        self.model = model

    async def evaluate(
        self,
        task: TaskNode,
        result: str,
        criteria: str | None = None,
    ) -> QualityResult:
        """Evaluate a task's output against quality criteria."""
        criteria_text = f"Additional criteria:\n{criteria}" if criteria else ""
        prompt = _QA_PROMPT.format(
            task_desc=task.description,
            result=result[:4000],  # truncate very long results
            criteria=criteria_text,
        )

        messages = [
            {"role": "system", "content": "You are a strict QA evaluator. Output only valid JSON."},
            {"role": "user", "content": prompt},
        ]

        try:
            response = await self.provider.chat_with_retry(
                messages=messages,
                model=self.model,
            )
            return self._parse_result(response.content or "")
        except Exception as e:
            logger.error("Quality gate evaluation failed: {}", e)
            return QualityResult(
                passed=False, score=0.0,
                feedback=f"Quality evaluation error: {e}",
                evidence=["evaluation_failed"],
            )

    @staticmethod
    def _parse_result(text: str) -> QualityResult:
        """Parse quality evaluation JSON from LLM response."""
        import json
        import re

        text = text.strip()
        if "```" in text:
            match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
            if match:
                text = match.group(1).strip()

        try:
            data = json.loads(text)
            return QualityResult(
                passed=bool(data.get("passed", False)),
                score=float(data.get("score", 0.0)),
                feedback=str(data.get("feedback", "")),
                evidence=list(data.get("evidence", [])),
            )
        except (json.JSONDecodeError, ValueError):
            # Assume failure if we can't parse
            return QualityResult(
                passed=False, score=0.0,
                feedback="Could not parse quality evaluation",
                evidence=["parse_error"],
            )
