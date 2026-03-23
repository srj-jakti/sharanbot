"""Autopilot — SharanBot's autonomous executor and right hand.

The Autopilot is the EXECUTOR that works under SharanBot (the Boss):
- Receives delegated tasks from SharanBot
- Plans execution strategy (direct tools, subagents, or teams)
- Manages ALL workers, subagents, and tools during execution
- Runs a full autonomous agent loop with all tools
- Reports results back to SharanBot for review
- Remembers cross-session patterns for intelligent routing
- Learns from errors to avoid repeating failures

Hierarchy: User <-> SharanBot (Boss) -> Autopilot (Executor) -> Workers/Tools/Teams
"""

from __future__ import annotations

import asyncio
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
    from sharanbot.agent.tools.registry import ToolRegistry
    from sharanbot.agent.subagent import SubagentManager
    from sharanbot.agent.team.orchestrator import TeamOrchestrator

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
    """Autopilot — SharanBot's autonomous executor and right hand.

    The Autopilot is NOT just an advisor — it's a FULL EXECUTOR:
    1. Analyzes tasks and plans execution strategy
    2. Executes tasks autonomously with ALL tools (filesystem, shell, browser, etc.)
    3. Manages subagents, teams, and workers during execution
    4. Reports results back to SharanBot (the Boss) for review
    5. Learns from errors and remembers patterns across sessions
    6. Runs its own autonomous agent loop with a configurable iteration limit

    Hierarchy: SharanBot (Boss) -> Autopilot (Executor) -> Workers/Tools/Teams
    """

    def __init__(
        self,
        provider: LLMProvider,
        model: str,
        workspace: Path | None = None,
        max_iterations: int = 200,
    ):
        self.provider = provider
        self.model = model
        self.enabled = True
        self.workspace = workspace
        self.max_iterations = max_iterations
        self._memory = AutopilotMemory(workspace) if workspace else None
        self._learning: LearningStore | None = None
        # Execution capabilities (set by AgentLoop after init)
        self._tools: ToolRegistry | None = None
        self._subagent_manager: SubagentManager | None = None
        self._team_orchestrator: TeamOrchestrator | None = None
        self._context_window_tokens: int = 65_536
        self._subconscious = None  # Set via set_subconscious()

    def set_learning_store(self, learning: LearningStore) -> None:
        """Connect the learning store for error-aware routing."""
        self._learning = learning

    def set_execution_context(
        self,
        tools: "ToolRegistry",
        subagent_manager: "SubagentManager",
        team_orchestrator: "TeamOrchestrator",
        context_window_tokens: int,
    ) -> None:
        """Give Autopilot full execution capabilities.

        Called by AgentLoop after initialization to grant the Autopilot
        access to all tools, subagents, and team orchestration.
        """
        self._tools = tools
        self._subagent_manager = subagent_manager
        self._team_orchestrator = team_orchestrator
        self._context_window_tokens = context_window_tokens

    def set_subconscious(self, subconscious) -> None:
        """Connect the subconscious for mid-task guidance sync."""
        self._subconscious = subconscious

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

    async def execute(
        self,
        task: str,
        decision: AutopilotDecision,
        on_progress: Any = None,
    ) -> str:
        """Execute a task autonomously with full tool access.

        This is the Autopilot's MAIN capability — it runs a complete agent loop
        with all tools to accomplish the task, then returns the result to SharanBot.
        """
        if not self._tools:
            return f"Autopilot execution not available (tools not connected). Task: {task}"

        logger.info(
            "Autopilot executing: strategy={}, complexity={}, task={}",
            decision.strategy, decision.complexity, task[:80],
        )

        system_prompt = self._build_executor_prompt(decision)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task},
        ]

        iteration = 0
        max_iterations = self.max_iterations
        final_result: str | None = None
        tools_used: list[str] = []

        try:
          while iteration < max_iterations:
            iteration += 1

            # Token management: summarize at 80%
            if iteration % 5 == 0:
                estimated = self._estimate_tokens(messages)
                threshold = int(self._context_window_tokens * 0.80)
                if estimated >= threshold:
                    messages = await self._summarize_execution(messages, iteration)

            # Soft checkpoint
            if iteration % 25 == 0:
                logger.info("Autopilot execution checkpoint: iter={}, tools={}", iteration, len(tools_used))
                if on_progress:
                    try:
                        await on_progress(f"\u26a1 Autopilot working... (step {iteration}, {len(tools_used)} tool calls)")
                    except Exception:
                        pass

            # Mid-task subconscious sync: lightweight diff check (no LLM call)
            # Mirrors claude-subconscious PreToolUse hook — just checks if background
            # processing has updated any memory blocks and injects the diff if so.
            if self._subconscious and iteration % 5 == 0 and iteration > 1:
                try:
                    sync_guidance = self._subconscious.mid_task_sync()
                    if sync_guidance:
                        messages.append({
                            "role": "user",
                            "content": f"[Subconscious mid-task update]\n{sync_guidance}",
                        })
                        logger.debug("Autopilot injected mid-task subconscious sync at iter {}", iteration)
                except Exception as e:
                    logger.debug("Autopilot mid-task sync skipped: {}", e)

            tool_defs = self._tools.get_definitions()

            try:
                response = await self.provider.chat_with_retry(
                    messages=messages,
                    tools=tool_defs,
                    model=self.model,
                )
            except Exception as e:
                logger.error("Autopilot LLM call failed: {}", e)
                if final_result:
                    break
                return f"Autopilot execution failed during LLM call: {e}"

            if response.has_tool_calls:
                if on_progress and response.content:
                    try:
                        await on_progress(response.content[:200])
                    except Exception:
                        pass

                tool_call_dicts = [
                    tc.to_openai_tool_call()
                    for tc in response.tool_calls
                ]
                from sharanbot.agent.context import build_assistant_message
                messages.append(build_assistant_message(
                    response.content or "",
                    tool_calls=tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                ))

                for tool_call in response.tool_calls:
                    tools_used.append(tool_call.name)
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info("Autopilot tool: {}({})", tool_call.name, args_str[:200])
                    result = await self._tools.execute(tool_call.name, tool_call.arguments)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.name,
                        "content": result,
                    })
            else:
                final_result = response.content
                break

          if final_result is None:
            if iteration >= max_iterations:
                final_result = (
                    f"Autopilot reached iteration limit ({max_iterations}). "
                    f"Tools used: {len(tools_used)}. Last tools: {tools_used[-5:]}. "
                    "Task may be incomplete — consider breaking it into smaller subtasks. "
                    "You can increase the limit via agents.defaults.max_tool_iterations in config."
                )
            else:
                final_result = "Autopilot completed execution but produced no final summary."
        finally:
            # Clean up any tools holding external resources (background processes, etc.)
            if self._tools:
                for tool in self._tools._tools.values():
                    close = getattr(tool, "close", None)
                    if close and callable(close):
                        try:
                            result = close()
                            if hasattr(result, "__await__"):
                                await result
                        except Exception:
                            logger.debug("Error closing tool {} in Autopilot", type(tool).__name__)

        logger.info(
            "Autopilot execution complete: {} iterations, {} tool calls",
            iteration, len(tools_used),
        )
        return final_result

    def _build_executor_prompt(self, decision: AutopilotDecision) -> str:
        """Build the system prompt for Autopilot execution mode."""
        workspace_str = str(self.workspace) if self.workspace else "unknown"
        return f"""# Autopilot Executor

You are the Autopilot — SharanBot's autonomous executor and right hand.
SharanBot (the Boss) has delegated a task to you. Execute it completely and report back.

## Your Role
- You are the EXECUTOR. Do the actual work — use tools, write code, run commands, browse the web.
- You have FULL access to all tools: filesystem, shell, browser, spawn, team_create, workers, etc.
- Work autonomously until the task is COMPLETELY done.
- You have an iteration limit of {self.max_iterations} tool calls. Plan efficiently.
- Be thorough and verify your work before reporting.

## Task Context
- Strategy: {decision.strategy} | Complexity: {decision.complexity}
- Suggested profile: {decision.suggested_profile}
- Suggested pipeline: {decision.suggested_pipeline}
- Reasoning: {decision.reasoning}

## Execution Rules
1. DO the work — don't explain how to do it. Use tools directly.
2. For complex tasks, use team_create or spawn to parallelize work.
3. Verify results — run tests, check outputs, take screenshots if needed.
4. NEVER claim something is done without proof.
5. NEVER use mock/fake data when real data is requested.
6. If something fails, report the failure with details — don't hide it.
7. Your final response should be a COMPLETE report of what was done and the results.

## Dev Server & Process Management
Use `exec` for one-shot commands (npm install, npm build, running tests, git commands).
Use `process_start` for long-running processes (npm run dev, flask run, uvicorn, etc.).

### Two Startup Verification Approaches

**Approach A — Pattern matching (faster, use when framework prints a ready message):**
  process_start(command="npm run dev", wait_for="listening", wait_timeout=60)
  → Returns when pattern found in output; process confirmed running.

**Approach B — Port check (more reliable, use when unsure about output patterns):**
  process_start(command="npm run dev")
  wait_for_port(port=3000, timeout=60)
  → Confirms TCP socket is listening.

Pick ONE approach per server, not both.

### Ready Patterns by Framework
- Node/Express: "listening", "Listening on"
- React/Vite: "ready", "VITE v"
- Next.js: "started server", "ready"
- Flask: "Running on"
- Django: "Starting development server"
- If unsure: use Approach B (wait_for_port) instead.

### Timeout Guidance
- Default wait_timeout is 30s. Adjust for slow startups:
  - React/Vite: 45s
  - Next.js / TypeScript monorepos: 60-90s
  - Cold builds: 120s

### Monorepo / Subproject
Always specify working_dir for projects not at workspace root:
  process_start(command="npm run dev", working_dir="./packages/frontend")

### Port & Process Management
- port_check(port) — check if port is available/in-use BEFORE starting a server.
- process_status() — list ALL running background processes.
- process_status(process_id) — check specific process and its recent output.
- process_status(process_id, tail=100) — get more output lines for debugging.
- process_stop(process_id) — stop a background process when done.
- Port checking is restricted to localhost only (127.0.0.1, localhost, 0.0.0.0, ::1).

### Troubleshooting
- **Port in use:** port_check(3000) → "IN USE" → process_status() to find which process → process_stop(pid) → retry.
- **Process exits immediately:** Check error output, fix root cause (missing deps, wrong dir), retry.
- **wait_for timeout:** Process may still be starting. Use process_status(id) to check recent output. Increase wait_timeout if output is still growing.
- **"Command not found":** Check working_dir, ensure dependencies are installed.

### Example: Full Web App Workflow
1. exec: npm install (wait for completion)
2. port_check: 3000 (verify port available)
3. process_start: npm run dev, wait_for="ready", wait_timeout=60
4. Verify the app works (browser, screenshot, or curl)
5. Report success with running server details

## Workspace
{workspace_str}

## HONESTY (NON-NEGOTIABLE)
- Report ALL failures honestly.
- Verify before claiming completion.
- Real data only — no mock substitutes without disclosure."""

    def _estimate_tokens(self, messages: list[dict]) -> int:
        """Estimate token count for messages.

        Uses ~3.5 chars/token as a safer approximation for English text.
        Tool call arguments and structured content tend to have lower
        token density, so counting them at 3.5 avoids underestimation.
        """
        total = 0
        for m in messages:
            content = m.get("content", "")
            if content:
                total += len(str(content))
            # Also count tool call arguments which consume tokens
            for tc in m.get("tool_calls", []):
                func = tc.get("function", {})
                total += len(func.get("name", ""))
                total += len(func.get("arguments", ""))
        return int(total / 3.5)

    async def _summarize_execution(self, messages: list[dict], iteration: int) -> list[dict]:
        """Summarize execution context at 80% token usage."""
        logger.info("Autopilot token window at 80%% (iter {}), summarizing", iteration)
        system_msg = messages[0] if messages and messages[0].get("role") == "system" else None

        summary_messages = [
            {"role": "system", "content": "Summarize this execution so far. Include: what was asked, what has been done, what tools were used, results so far, and what remains."},
            {"role": "user", "content": "\n".join(
                f"[{m.get('role', '?').upper()}]: {str(m.get('content', ''))[:500]}"
                for m in messages[-40:] if m.get('content')
            )},
        ]

        try:
            response = await self.provider.chat_with_retry(messages=summary_messages, model=self.model)
            summary = response.content or "Summary failed."
        except Exception:
            if system_msg:
                return [system_msg] + messages[-8:]
            return messages[-8:]

        new_messages = []
        if system_msg:
            new_messages.append(system_msg)
        new_messages.append({
            "role": "user",
            "content": f"[Execution Summary — continued from iteration {iteration}]\n\n{summary}\n\nContinue execution. Pick up where you left off. Do NOT repeat completed work.",
        })
        return new_messages

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
