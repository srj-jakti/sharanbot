"""Autopilot — proactive autonomous decision engine with superhuman memory.

Goes beyond reactive task routing:
- Anticipates user needs based on conversation history and learned patterns
- Remembers cross-session context (past tasks, preferences, failures)
- Suggests next actions before the user asks
- Routes tasks optimally using accumulated intelligence
- Adapts behavior based on time-of-day and workload patterns
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from sharanbot.providers.base import LLMProvider

if TYPE_CHECKING:
    from sharanbot.agent.learning import LearningStore

_ANALYSIS_PROMPT = """\
Analyze this task and determine the best execution strategy.

Task: {task}

{memory_context}

Choose ONE strategy:
1. "direct" — Simple task, answer directly (questions, quick edits, lookups)
2. "single_agent" — Moderate task, spawn one specialist subagent (focused coding, research)
3. "team" — Complex task needing multiple specialists (full features, multi-step projects)

Also determine:
- complexity: "low", "medium", "high"
- suggested_profile: best profile if single_agent (general, coder, researcher, architect, reviewer, tester, debugger, devops, writer, security, planner)
- suggested_pipeline: best pipeline if team (build, deep_research, dev_qa, full_stack, or "auto")
- proactive_suggestions: list of 0-3 follow-up actions the user might want next
- reasoning: brief explanation

Output ONLY a JSON object:
{{
  "strategy": "direct|single_agent|team",
  "complexity": "low|medium|high",
  "suggested_profile": "profile_name",
  "suggested_pipeline": "pipeline_name",
  "proactive_suggestions": ["suggestion1", "suggestion2"],
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
    proactive_suggestions: list[str] = field(default_factory=list)

    @property
    def needs_team(self) -> bool:
        return self.strategy == "team"

    @property
    def needs_subagent(self) -> bool:
        return self.strategy == "single_agent"


@dataclass
class UserProfile:
    """Accumulated intelligence about a user's patterns and preferences."""

    total_tasks: int = 0
    tasks_by_strategy: dict[str, int] = field(default_factory=dict)
    preferred_profiles: dict[str, int] = field(default_factory=dict)
    recent_topics: list[str] = field(default_factory=list)  # last N task summaries
    failure_patterns: list[str] = field(default_factory=list)
    avg_complexity: float = 0.0
    last_active: float = 0.0
    peak_hours: dict[int, int] = field(default_factory=dict)  # hour -> task count


class AutopilotMemory:
    """Persistent cross-session memory for the autopilot.

    Stores user interaction patterns, task history, and learned preferences
    in a JSON file that survives restarts.
    """

    _FILENAME = "autopilot_memory.json"
    _MAX_RECENT_TOPICS = 50
    _MAX_TASK_HISTORY = 200

    def __init__(self, workspace: Path):
        self._path = workspace / self._FILENAME
        self._profiles: dict[str, UserProfile] = {}
        self._task_history: list[dict[str, Any]] = []
        self._pattern_cache: dict[str, str] = {}  # task_hash -> strategy
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for uid, pdata in data.get("profiles", {}).items():
                self._profiles[uid] = UserProfile(**pdata)
            self._task_history = data.get("task_history", [])[-self._MAX_TASK_HISTORY:]
            self._pattern_cache = data.get("pattern_cache", {})
        except Exception as e:
            logger.warning("Failed to load autopilot memory: {}", e)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "profiles": {},
                "task_history": self._task_history[-self._MAX_TASK_HISTORY:],
                "pattern_cache": dict(list(self._pattern_cache.items())[-500:]),
            }
            for uid, prof in self._profiles.items():
                data["profiles"][uid] = {
                    "total_tasks": prof.total_tasks,
                    "tasks_by_strategy": prof.tasks_by_strategy,
                    "preferred_profiles": prof.preferred_profiles,
                    "recent_topics": prof.recent_topics[-self._MAX_RECENT_TOPICS:],
                    "failure_patterns": prof.failure_patterns[-20:],
                    "avg_complexity": prof.avg_complexity,
                    "last_active": prof.last_active,
                    "peak_hours": prof.peak_hours,
                }
            self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to save autopilot memory: {}", e)

    def get_profile(self, user_id: str = "default") -> UserProfile:
        if user_id not in self._profiles:
            self._profiles[user_id] = UserProfile()
        return self._profiles[user_id]

    def record_task(self, task: str, decision: AutopilotDecision, user_id: str = "default") -> None:
        """Record a completed task decision for future pattern learning."""
        profile = self.get_profile(user_id)
        profile.total_tasks += 1
        profile.tasks_by_strategy[decision.strategy] = (
            profile.tasks_by_strategy.get(decision.strategy, 0) + 1
        )
        if decision.suggested_profile:
            profile.preferred_profiles[decision.suggested_profile] = (
                profile.preferred_profiles.get(decision.suggested_profile, 0) + 1
            )

        # Track topic
        summary = task[:100].strip()
        profile.recent_topics.append(summary)
        if len(profile.recent_topics) > self._MAX_RECENT_TOPICS:
            profile.recent_topics = profile.recent_topics[-self._MAX_RECENT_TOPICS:]

        # Update complexity average (exponential moving average)
        complexity_val = {"low": 1, "medium": 2, "high": 3}.get(decision.complexity, 1)
        profile.avg_complexity = profile.avg_complexity * 0.8 + complexity_val * 0.2

        # Track peak hours
        hour = time.localtime().tm_hour
        profile.peak_hours[str(hour)] = profile.peak_hours.get(str(hour), 0) + 1
        profile.last_active = time.time()

        # Record in task history
        self._task_history.append({
            "task": summary,
            "strategy": decision.strategy,
            "profile": decision.suggested_profile,
            "complexity": decision.complexity,
            "timestamp": time.time(),
            "user": user_id,
        })

        # Cache the pattern for fast lookup
        task_key = self._task_signature(task)
        self._pattern_cache[task_key] = decision.strategy

        self._save()

    def get_cached_strategy(self, task: str) -> str | None:
        """Look up if we've seen a very similar task before."""
        return self._pattern_cache.get(self._task_signature(task))

    def get_recent_context(self, user_id: str = "default", n: int = 5) -> list[dict]:
        """Get the N most recent tasks for context injection."""
        user_tasks = [t for t in self._task_history if t.get("user") == user_id]
        return user_tasks[-n:]

    def build_memory_context(self, user_id: str = "default") -> str:
        """Build a memory context string for LLM prompt injection."""
        profile = self.get_profile(user_id)
        if profile.total_tasks == 0:
            return ""

        lines = ["Context from memory (user's patterns):"]

        # Preferred strategy
        if profile.tasks_by_strategy:
            top_strategy = max(profile.tasks_by_strategy, key=profile.tasks_by_strategy.get)
            lines.append(f"- User most often needs: {top_strategy} ({profile.tasks_by_strategy[top_strategy]}/{profile.total_tasks} tasks)")

        # Recent topics
        recent = profile.recent_topics[-3:]
        if recent:
            lines.append(f"- Recent topics: {', '.join(recent)}")

        # Complexity tendency
        if profile.avg_complexity > 2.2:
            lines.append("- User tends to request complex tasks (lean toward team)")
        elif profile.avg_complexity < 1.4:
            lines.append("- User tends to request simple tasks (lean toward direct)")

        # Preferred profiles
        if profile.preferred_profiles:
            top_prof = max(profile.preferred_profiles, key=profile.preferred_profiles.get)
            lines.append(f"- Most used specialist: {top_prof}")

        return "\n".join(lines)

    @staticmethod
    def _task_signature(task: str) -> str:
        """Create a normalized signature for pattern matching."""
        # Normalize: lowercase, collapse whitespace, remove punctuation
        t = re.sub(r"[^\w\s]", "", task.lower().strip())
        words = t.split()[:10]  # first 10 words
        return " ".join(words)


class Autopilot:
    """Proactive autonomous decision engine for SharanBot.

    Beyond reactive routing, the autopilot:
    1. Remembers user patterns across sessions (superhuman memory)
    2. Anticipates needs based on recent activity and learning store
    3. Suggests follow-up actions proactively
    4. Adapts routing based on accumulated intelligence
    5. Uses learned error patterns to avoid known-bad strategies
    """

    def __init__(self, provider: LLMProvider, model: str, workspace: Path | None = None):
        self.provider = provider
        self.model = model
        self.enabled = True
        self._memory = AutopilotMemory(workspace) if workspace else None
        self._learning: LearningStore | None = None

    def set_learning_store(self, learning: LearningStore) -> None:
        """Connect the learning store for error-aware routing."""
        self._learning = learning

    async def analyze(self, task: str, user_id: str = "default") -> AutopilotDecision:
        """Analyze a task with full memory and pattern awareness."""
        # Fast-path: detect obviously simple tasks without LLM call
        quick = self._quick_classify(task)
        if quick:
            self._record(task, quick, user_id)
            return quick

        # Check memory for cached similar tasks
        if self._memory:
            cached = self._memory.get_cached_strategy(task)
            if cached:
                logger.debug("Autopilot cache hit: {} → {}", task[:50], cached)
                decision = AutopilotDecision(
                    strategy=cached, complexity="medium",
                    suggested_profile=self._guess_profile(task),
                    suggested_pipeline="auto",
                    reasoning="Matched similar past task from memory.",
                )
                self._record(task, decision, user_id)
                return decision

        # Build memory context for the LLM
        memory_context = ""
        if self._memory:
            memory_context = self._memory.build_memory_context(user_id)

        # Add learning-store awareness
        if self._learning and self._learning.total_entries > 0:
            if memory_context:
                memory_context += "\n"
            memory_context += f"Note: {self._learning.total_entries} error patterns learned. Avoid strategies that previously failed."

        prompt = _ANALYSIS_PROMPT.format(task=task, memory_context=memory_context)
        messages = [
            {"role": "system", "content": "You are a proactive task routing specialist with superhuman pattern recognition. Output only valid JSON."},
            {"role": "user", "content": prompt},
        ]

        try:
            response = await self.provider.chat_with_retry(
                messages=messages,
                model=self.model,
            )
            decision = self._parse_decision(response.content or "")
            self._record(task, decision, user_id)
            return decision
        except Exception as e:
            logger.warning("Autopilot analysis failed, defaulting to direct: {}", e)
            decision = AutopilotDecision(
                strategy="direct",
                complexity="low",
                suggested_profile="general",
                suggested_pipeline="auto",
                reasoning=f"Analysis failed: {e}",
            )
            self._record(task, decision, user_id)
            return decision

    def get_proactive_suggestions(self, user_id: str = "default") -> list[str]:
        """Generate proactive suggestions based on recent activity and patterns."""
        if not self._memory:
            return []

        suggestions = []
        profile = self._memory.get_profile(user_id)
        recent = self._memory.get_recent_context(user_id, n=3)

        if not recent:
            return []

        # Suggest based on recent activity patterns
        last_task = recent[-1] if recent else None
        if last_task:
            strategy = last_task.get("strategy", "")
            if strategy == "team":
                suggestions.append("Review the team's output and run tests")
            elif strategy == "single_agent" and last_task.get("profile") == "coder":
                suggestions.append("Run tests on the latest changes")

        # Suggest based on failure patterns
        if self._learning:
            stuck_count = len(self._learning.find_by_category("stuck_agent"))
            if stuck_count > 2:
                suggestions.append("Review and clear stuck agent patterns")

        # Time-based suggestions
        hour = time.localtime().tm_hour
        if hour >= 22 or hour < 6:
            suggestions.append("Schedule remaining tasks for tomorrow via cron")

        # If user has been doing lots of complex tasks, suggest a break
        if profile.avg_complexity > 2.5 and profile.total_tasks > 10:
            suggestions.append("Consider breaking this into smaller phased tasks")

        return suggestions[:3]

    def _record(self, task: str, decision: AutopilotDecision, user_id: str) -> None:
        """Record the decision in memory for future reference."""
        if self._memory:
            self._memory.record_task(task, decision, user_id)

    def _guess_profile(self, task: str) -> str:
        """Heuristic profile guess based on keywords."""
        task_lower = task.lower()
        profile_keywords = {
            "coder": ["code", "implement", "function", "class", "bug", "fix", "write"],
            "researcher": ["research", "find", "search", "look up", "investigate"],
            "architect": ["design", "architecture", "structure", "plan"],
            "tester": ["test", "spec", "coverage", "assert"],
            "debugger": ["debug", "error", "crash", "traceback"],
            "devops": ["deploy", "docker", "ci", "pipeline", "server"],
            "writer": ["document", "readme", "write docs", "explain"],
            "security": ["security", "vulnerability", "auth", "permission"],
        }
        for profile, keywords in profile_keywords.items():
            if any(kw in task_lower for kw in keywords):
                return profile
        return "general"

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
                proactive_suggestions=data.get("proactive_suggestions", []),
            )
        except (json.JSONDecodeError, ValueError):
            return AutopilotDecision(
                strategy="direct", complexity="low",
                suggested_profile="general", suggested_pipeline="auto",
                reasoning="Could not parse analysis, defaulting to direct.",
            )
