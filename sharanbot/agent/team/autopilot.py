"""Autopilot — autonomous task complexity analysis and routing.

Auto-detects whether a task should be handled by:
- A single agent (simple tasks)
- A specialist subagent (moderate tasks)
- A multi-agent team (complex tasks)

Also provides proactive decision-making: the main agent uses autopilot
to decide its own course of action without needing user guidance.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from loguru import logger

from sharanbot.providers.base import LLMProvider

_ANALYSIS_PROMPT = """\
Analyze this task and determine the best execution strategy.

Task: {task}

Choose ONE strategy:
1. "direct" — Simple task, answer directly (questions, quick edits, lookups)
2. "single_agent" — Moderate task, spawn one specialist subagent (focused coding, research)
3. "team" — Complex task needing multiple specialists (full features, multi-step projects)

Also determine:
- complexity: "low", "medium", "high"
- suggested_profile: best profile if single_agent (general, coder, researcher, architect, reviewer, tester, debugger, devops, writer, security, planner)
- suggested_pipeline: best pipeline if team (build, deep_research, dev_qa, full_stack, or "auto")
- reasoning: brief explanation

Output ONLY a JSON object:
{{
  "strategy": "direct|single_agent|team",
  "complexity": "low|medium|high",
  "suggested_profile": "profile_name",
  "suggested_pipeline": "pipeline_name",
  "reasoning": "why this strategy"
}}"""


@dataclass
class AutopilotDecision:
    """Result of autopilot analysis."""

    strategy: str  # direct, single_agent, team
    complexity: str  # low, medium, high
    suggested_profile: str
    suggested_pipeline: str
    reasoning: str

    @property
    def needs_team(self) -> bool:
        return self.strategy == "team"

    @property
    def needs_subagent(self) -> bool:
        return self.strategy == "single_agent"


class Autopilot:
    """Autonomous decision engine for SharanBot.

    When autopilot is enabled, the main agent automatically:
    1. Analyzes incoming task complexity
    2. Decides optimal execution strategy
    3. Routes to direct response, subagent, or team
    """

    def __init__(self, provider: LLMProvider, model: str):
        self.provider = provider
        self.model = model
        self.enabled = True

    async def analyze(self, task: str) -> AutopilotDecision:
        """Analyze a task and recommend an execution strategy."""
        # Fast-path: detect obviously simple tasks without LLM call
        quick = self._quick_classify(task)
        if quick:
            return quick

        prompt = _ANALYSIS_PROMPT.format(task=task)
        messages = [
            {"role": "system", "content": "You are a task routing specialist. Output only valid JSON."},
            {"role": "user", "content": prompt},
        ]

        try:
            response = await self.provider.chat_with_retry(
                messages=messages,
                model=self.model,
            )
            return self._parse_decision(response.content or "")
        except Exception as e:
            logger.warning("Autopilot analysis failed, defaulting to direct: {}", e)
            return AutopilotDecision(
                strategy="direct",
                complexity="low",
                suggested_profile="general",
                suggested_pipeline="auto",
                reasoning=f"Analysis failed: {e}",
            )

    def _quick_classify(self, task: str) -> AutopilotDecision | None:
        """Fast heuristic classification without LLM for obvious cases."""
        task_lower = task.lower().strip()
        word_count = len(task.split())

        # Very short messages → direct
        if word_count <= 5:
            return AutopilotDecision(
                strategy="direct", complexity="low",
                suggested_profile="general", suggested_pipeline="auto",
                reasoning="Very short message, handle directly.",
            )

        # Questions → direct
        if task_lower.endswith("?") and word_count <= 20:
            return AutopilotDecision(
                strategy="direct", complexity="low",
                suggested_profile="general", suggested_pipeline="auto",
                reasoning="Simple question, answer directly.",
            )

        # Greetings and simple commands
        greetings = {"hello", "hi", "hey", "thanks", "thank you", "ok", "bye", "status"}
        if task_lower.strip("!., ") in greetings:
            return AutopilotDecision(
                strategy="direct", complexity="low",
                suggested_profile="general", suggested_pipeline="auto",
                reasoning="Greeting or simple command.",
            )

        # Complex indicators → team
        complex_indicators = [
            "build a full", "create a complete", "develop a",
            "implement a system", "build an application",
            "full stack", "frontend and backend",
            "research and implement", "design and build",
            "multiple files", "refactor the entire",
        ]
        if any(ind in task_lower for ind in complex_indicators) and word_count > 15:
            return AutopilotDecision(
                strategy="team", complexity="high",
                suggested_profile="coder", suggested_pipeline="auto",
                reasoning="Complex multi-step task detected by keyword analysis.",
            )

        # No quick classification — need LLM
        return None

    @staticmethod
    def _parse_decision(text: str) -> AutopilotDecision:
        """Parse autopilot decision from LLM response."""
        text = text.strip()
        if "```" in text:
            match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
            if match:
                text = match.group(1).strip()

        try:
            data = json.loads(text)
            return AutopilotDecision(
                strategy=data.get("strategy", "direct"),
                complexity=data.get("complexity", "low"),
                suggested_profile=data.get("suggested_profile", "general"),
                suggested_pipeline=data.get("suggested_pipeline", "auto"),
                reasoning=data.get("reasoning", ""),
            )
        except (json.JSONDecodeError, ValueError):
            return AutopilotDecision(
                strategy="direct", complexity="low",
                suggested_profile="general", suggested_pipeline="auto",
                reasoning="Could not parse analysis, defaulting to direct.",
            )
