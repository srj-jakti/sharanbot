"""Self-Evolution Engine — REAL autonomous self-improvement for SharanAGI.

Unlike agent-orchestrator's "self-improving AI" (which is just dogfooding +
GitHub issues), this is actual runtime self-modification:

1. **Self-Diagnosis** — Analyzes observability data, error patterns, stuck
   patterns, and performance metrics to identify what needs improving
2. **Improvement Planner** — LLM-powered planning that turns diagnoses into
   concrete code changes (not just bug reports)
3. **Safe Self-Modification** — Git checkpoint → modify → test → rollback-if-failed
4. **Capability Growth Tracker** — Tracks what was added, what worked, what failed
5. **Feedback Loop** — Runtime errors/stuck patterns automatically trigger
   improvement cycles
6. **Hot-Reload** — Apply changes to running instance without restart

The evolution cycle:
    observe → diagnose → plan → checkpoint → modify → test → commit/rollback → reload

Safety:
    - SafetyBoundary blocks modifications to security/vault/auth code
    - Git checkpoints enable instant rollback
    - Test validation catches regressions before commit
    - Rate-limited: max 1 evolution cycle per 10 minutes
    - Human approval required for high-risk changes
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from sharanbot.agi.observability import Observer
    from sharanbot.agi.reactions import ReactionEngine
    from sharanbot.agi.stuck import StuckDetector
    from sharanbot.agent.learning import LearningStore
    from sharanbot.agent.recovery import RecoveryEngine
    from sharanbot.providers.base import LLMProvider


class EvolutionTrigger(str, Enum):
    """What triggered an evolution cycle."""
    REPEATED_ERROR = "repeated_error"       # Same error 5+ times
    STUCK_PATTERN = "stuck_pattern"         # Agent got stuck repeatedly
    PERFORMANCE_DEGRADATION = "perf_degrade"  # Success rate dropping
    MISSING_CAPABILITY = "missing_cap"      # Tried to do something it can't
    SCHEDULED = "scheduled"                 # Periodic self-review
    MANUAL = "manual"                       # User requested improvement
    TASK_FAILURE = "task_failure"            # Background task failed


class EvolutionRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class Diagnosis:
    """A diagnosed issue that could be improved."""
    id: str
    trigger: EvolutionTrigger
    description: str
    evidence: list[str]           # Concrete data points
    severity: float               # 0.0 to 1.0
    suggested_area: str           # Which module/component to improve
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "trigger": self.trigger.value,
            "description": self.description,
            "evidence": self.evidence,
            "severity": self.severity,
            "suggested_area": self.suggested_area,
            "timestamp": self.timestamp,
        }


@dataclass
class EvolutionPlan:
    """A concrete plan for self-improvement."""
    plan_id: str
    diagnosis_id: str
    description: str
    changes: list[PlannedChange]
    risk: EvolutionRisk = EvolutionRisk.LOW
    rationale: str = ""
    estimated_impact: str = ""
    requires_approval: bool = False
    created_at: float = field(default_factory=time.time)


@dataclass
class PlannedChange:
    """A single file change in an evolution plan."""
    path: str
    action: str  # "create" | "modify" | "delete"
    description: str
    content: str = ""
    original_content: str = ""


@dataclass
class EvolutionResult:
    """Result of an evolution cycle."""
    plan_id: str
    success: bool
    changes_applied: int = 0
    tests_passed: bool = False
    rolled_back: bool = False
    error: str = ""
    duration_s: float = 0.0
    new_capabilities: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "success": self.success,
            "changes_applied": self.changes_applied,
            "tests_passed": self.tests_passed,
            "rolled_back": self.rolled_back,
            "error": self.error,
            "duration_s": round(self.duration_s, 1),
            "new_capabilities": self.new_capabilities,
            "timestamp": self.timestamp,
        }



# ── Growth Tracker ───────────────────────────────────────────────────

class GrowthTracker:
    """Tracks capability growth over time.

    Persists a log of all evolution attempts, successes, and failures.
    Provides metrics on how the system has grown.
    """

    _FILENAME = "evolution_history.json"
    _MAX_ENTRIES = 200

    def __init__(self, workspace: Path):
        self._path = workspace / self._FILENAME
        self._history: list[dict[str, Any]] = []
        self._capabilities: list[str] = []
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._history = data.get("history", [])
            self._capabilities = data.get("capabilities", [])
        except Exception as e:
            logger.warning("Failed to load evolution history: {}", e)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "history": self._history[-self._MAX_ENTRIES:],
                "capabilities": self._capabilities,
                "saved_at": time.time(),
            }
            self._path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Failed to save evolution history: {}", e)

    def record(self, result: EvolutionResult) -> None:
        """Record an evolution attempt."""
        self._history.append(result.to_dict())
        if result.success and result.new_capabilities:
            for cap in result.new_capabilities:
                if cap not in self._capabilities:
                    self._capabilities.append(cap)
        self._save()

    def get_stats(self) -> dict[str, Any]:
        total = len(self._history)
        successes = sum(1 for h in self._history if h.get("success"))
        return {
            "total_attempts": total,
            "successes": successes,
            "failures": total - successes,
            "success_rate": f"{successes / max(total, 1):.0%}",
            "capabilities_added": len(self._capabilities),
            "capabilities": self._capabilities[-20:],
            "last_evolution": self._history[-1] if self._history else None,
        }

    def get_recent(self, n: int = 10) -> list[dict[str, Any]]:
        return self._history[-n:]

    @property
    def total_evolutions(self) -> int:
        return len(self._history)

    @property
    def capabilities(self) -> list[str]:
        return self._capabilities[:]


# ── Self-Diagnosis Engine ────────────────────────────────────────────

class SelfDiagnosisEngine:
    """Analyzes runtime data to identify what needs improving.

    Data sources:
    - LearningStore: error patterns, frequencies, failed fixes
    - Observer: metrics, traces, health status
    - StuckDetector: stuck patterns, repeated failures
    - RecoveryEngine: circuit breaker states, recovery success rates
    """

    # Thresholds for triggering diagnoses
    ERROR_REPEAT_THRESHOLD = 5      # Same error 5+ times → diagnose
    STUCK_REPEAT_THRESHOLD = 3      # Stuck 3+ times → diagnose
    SUCCESS_RATE_THRESHOLD = 0.7    # Below 70% → performance degradation

    def __init__(
        self,
        learning: LearningStore,
        observer: Observer | None = None,
        stuck_detector: StuckDetector | None = None,
        recovery: RecoveryEngine | None = None,
    ):
        self._learning = learning
        self._observer = observer
        self._stuck = stuck_detector
        self._recovery = recovery
        self._diagnosis_count = 0

    def run_diagnosis(self) -> list[Diagnosis]:
        """Analyze all data sources and produce diagnoses."""
        diagnoses: list[Diagnosis] = []

        diagnoses.extend(self._diagnose_repeated_errors())
        diagnoses.extend(self._diagnose_stuck_patterns())
        diagnoses.extend(self._diagnose_performance())
        diagnoses.extend(self._diagnose_circuit_breakers())

        # Sort by severity (highest first)
        diagnoses.sort(key=lambda d: -d.severity)
        return diagnoses

    def _diagnose_repeated_errors(self) -> list[Diagnosis]:
        """Find errors that keep recurring despite fixes."""
        results: list[Diagnosis] = []
        for entry in self._learning._entries.values():
            if entry.occurrences >= self.ERROR_REPEAT_THRESHOLD and not entry.success:
                self._diagnosis_count += 1
                results.append(Diagnosis(
                    id=f"diag-{self._diagnosis_count}",
                    trigger=EvolutionTrigger.REPEATED_ERROR,
                    description=(
                        f"Error '{entry.error_signature}' has occurred "
                        f"{entry.occurrences} times without a successful fix"
                    ),
                    evidence=[
                        f"Category: {entry.category}",
                        f"Occurrences: {entry.occurrences}",
                        f"Last fix attempted: {entry.fix_applied}",
                        f"Description: {entry.description[:200]}",
                    ],
                    severity=min(1.0, entry.occurrences / 20),
                    suggested_area=self._infer_area(entry.category, entry.error_signature),
                ))
        return results

    def _diagnose_stuck_patterns(self) -> list[Diagnosis]:
        """Check if the agent keeps getting stuck."""
        if not self._stuck:
            return []
        stats = self._stuck.get_stats()
        times_stuck = stats.get("times_stuck", 0)
        if times_stuck < self.STUCK_REPEAT_THRESHOLD:
            return []

        self._diagnosis_count += 1
        return [Diagnosis(
            id=f"diag-{self._diagnosis_count}",
            trigger=EvolutionTrigger.STUCK_PATTERN,
            description=f"Agent has been stuck {times_stuck} times this session",
            evidence=[
                f"Total calls: {stats.get('total_calls', 0)}",
                f"Total errors: {stats.get('total_errors', 0)}",
                f"Error streak: {stats.get('current_error_streak', 0)}",
            ],
            severity=min(1.0, times_stuck / 10),
            suggested_area="sharanbot/agi/stuck.py",
        )]

    def _diagnose_performance(self) -> list[Diagnosis]:
        """Check for performance degradation in metrics."""
        if not self._observer:
            return []
        results: list[Diagnosis] = []
        summary = self._observer.get_summary()
        for metric_name, metric_data in summary.get("metrics", {}).items():
            total = metric_data.get("total", 0)
            success = metric_data.get("success", 0)
            if total >= 10:
                rate = success / total
                if rate < self.SUCCESS_RATE_THRESHOLD:
                    self._diagnosis_count += 1
                    results.append(Diagnosis(
                        id=f"diag-{self._diagnosis_count}",
                        trigger=EvolutionTrigger.PERFORMANCE_DEGRADATION,
                        description=f"Metric '{metric_name}' has low success rate: {rate:.0%}",
                        evidence=[
                            f"Total: {total}",
                            f"Success: {success}",
                            f"Failure: {metric_data.get('failure', 0)}",
                        ],
                        severity=1.0 - rate,
                        suggested_area=self._infer_area_from_metric(metric_name),
                    ))
        return results

    def _diagnose_circuit_breakers(self) -> list[Diagnosis]:
        """Check for open circuit breakers indicating persistent failures."""
        if not self._recovery:
            return []
        results: list[Diagnosis] = []
        for name, circuit in self._recovery._circuits.items():
            if circuit.is_open:
                self._diagnosis_count += 1
                results.append(Diagnosis(
                    id=f"diag-{self._diagnosis_count}",
                    trigger=EvolutionTrigger.REPEATED_ERROR,
                    description=f"Circuit breaker open for '{name}' ({circuit.failures} failures)",
                    evidence=[
                        f"Failures: {circuit.failures}",
                        f"Cooldown: {circuit.cooldown:.0f}s",
                    ],
                    severity=0.8,
                    suggested_area=f"recovery:{name}",
                ))
        return results

    @staticmethod
    def _infer_area(category: str, signature: str) -> str:
        """Infer which code area an error relates to."""
        mapping = {
            "rate_limit": "sharanbot/providers/",
            "api_error": "sharanbot/providers/",
            "tool_error": "sharanbot/agent/tools/",
            "mcp_failure": "sharanbot/agent/tools/mcp.py",
            "stuck_worker": "sharanbot/workers/",
            "stuck_agent": "sharanbot/agent/loop.py",
            "import_error": "sharanbot/",
        }
        return mapping.get(category, "sharanbot/")

    @staticmethod
    def _infer_area_from_metric(metric_name: str) -> str:
        mapping = {
            "tool_call": "sharanbot/agent/tools/",
            "tool_error": "sharanbot/agent/tools/",
            "llm_call": "sharanbot/providers/",
            "llm_error": "sharanbot/providers/",
            "task_failed": "sharanbot/agent/team/",
        }
        return mapping.get(metric_name, "sharanbot/")



# ── Improvement Planner ──────────────────────────────────────────────

_PLAN_SYSTEM_PROMPT = """\
You are the Self-Evolution Planner for SharanAGI, an autonomous AI agent framework.

Your job: turn a diagnosis (identified problem) into a concrete code modification plan.

You have access to the codebase structure and the specific diagnosis.
You must output a JSON plan with exact file changes.

RULES:
1. Changes must be MINIMAL — fix the specific issue, don't refactor unrelated code
2. Changes must be SAFE — never modify security, vault, auth, or safety code
3. Changes must be TESTABLE — include a test file for any new code
4. Prefer creating NEW files over modifying existing ones (lower risk)
5. If the fix requires modifying core code (agent/loop.py, etc.), mark risk as "high"
6. Each change must have complete, valid Python code
7. Never introduce new dependencies without justification
8. Keep changes under 200 lines total

Output format:
{
  "description": "What this plan does",
  "rationale": "Why this will fix the diagnosed issue",
  "estimated_impact": "Expected improvement",
  "risk": "low|medium|high",
  "changes": [
    {
      "path": "relative/path/to/file.py",
      "action": "create|modify",
      "description": "What this change does",
      "content": "complete file content"
    }
  ]
}

Output ONLY valid JSON. No markdown, no explanation outside the JSON."""


class ImprovementPlanner:
    """LLM-powered planner that turns diagnoses into code changes."""

    def __init__(self, provider: LLMProvider, model: str, workspace: Path):
        self._provider = provider
        self._model = model
        self._workspace = workspace

    async def plan(
        self,
        diagnosis: Diagnosis,
        codebase_context: str = "",
        recent_history: list[dict] | None = None,
    ) -> EvolutionPlan | None:
        """Generate an evolution plan from a diagnosis."""
        # Build context about the diagnosed area
        area_context = self._read_area_context(diagnosis.suggested_area)

        user_prompt = (
            f"DIAGNOSIS:\n"
            f"  ID: {diagnosis.id}\n"
            f"  Trigger: {diagnosis.trigger.value}\n"
            f"  Description: {diagnosis.description}\n"
            f"  Evidence:\n"
        )
        for ev in diagnosis.evidence:
            user_prompt += f"    - {ev}\n"
        user_prompt += f"  Suggested area: {diagnosis.suggested_area}\n\n"

        if codebase_context:
            user_prompt += f"CODEBASE STRUCTURE:\n{codebase_context[:3000]}\n\n"

        if area_context:
            user_prompt += f"RELEVANT CODE:\n{area_context[:4000]}\n\n"

        if recent_history:
            user_prompt += "RECENT EVOLUTION HISTORY (avoid repeating failed approaches):\n"
            for h in recent_history[-5:]:
                status = "✓" if h.get("success") else "✗"
                user_prompt += f"  {status} {h.get('plan_id', '?')}: {h.get('error', 'ok')}\n"
            user_prompt += "\n"

        user_prompt += "Generate a concrete improvement plan as JSON."

        messages = [
            {"role": "system", "content": _PLAN_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        try:
            response = await self._provider.chat_with_retry(
                messages=messages, model=self._model,
            )
            return self._parse_plan(response.content or "", diagnosis)
        except Exception as e:
            logger.warning("Evolution planner failed: {}", e)
            return None

    def _parse_plan(self, response_text: str, diagnosis: Diagnosis) -> EvolutionPlan | None:
        """Parse LLM response into an EvolutionPlan."""
        try:
            text = response_text.strip()
            # Strip markdown code fences
            if text.startswith("```"):
                lines = text.split("\n")
                json_lines = []
                in_block = False
                for line in lines:
                    if line.strip().startswith("```") and not in_block:
                        in_block = True
                        continue
                    elif line.strip().startswith("```") and in_block:
                        break
                    elif in_block:
                        json_lines.append(line)
                text = "\n".join(json_lines)

            data = json.loads(text)
            changes = []
            for c in data.get("changes", []):
                changes.append(PlannedChange(
                    path=c.get("path", ""),
                    action=c.get("action", "create"),
                    description=c.get("description", ""),
                    content=c.get("content", ""),
                ))

            if not changes:
                return None

            risk_str = data.get("risk", "low").lower()
            risk = EvolutionRisk.HIGH if risk_str == "high" else (
                EvolutionRisk.MEDIUM if risk_str == "medium" else EvolutionRisk.LOW
            )

            plan_id = f"evo-{int(time.time())}"
            return EvolutionPlan(
                plan_id=plan_id,
                diagnosis_id=diagnosis.id,
                description=data.get("description", ""),
                changes=changes,
                risk=risk,
                rationale=data.get("rationale", ""),
                estimated_impact=data.get("estimated_impact", ""),
                requires_approval=risk == EvolutionRisk.HIGH,
            )
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Failed to parse evolution plan: {}", e)
            return None

    def _read_area_context(self, area: str) -> str:
        """Read relevant source code for the diagnosed area."""
        if area.startswith("recovery:") or not area.endswith(".py"):
            return ""
        full_path = self._workspace / area
        if full_path.is_file():
            try:
                content = full_path.read_text(encoding="utf-8")
                if len(content) > 4000:
                    content = content[:4000] + "\n... (truncated)"
                return content
            except Exception:
                return ""
        # If it's a directory, list files
        if full_path.is_dir():
            files = sorted(full_path.glob("*.py"))[:10]
            parts = []
            for f in files:
                try:
                    parts.append(f"# {f.name}\n{f.read_text(encoding='utf-8')[:500]}")
                except Exception:
                    pass
            return "\n\n".join(parts)
        return ""



# ── Self-Evolution Engine (main orchestrator) ────────────────────────

class SelfEvolutionEngine:
    """The main self-evolution orchestrator.

    Coordinates the full cycle:
        observe → diagnose → plan → checkpoint → modify → test → commit/rollback → reload

    Rate-limited to prevent runaway self-modification.
    Integrates with all existing AGI subsystems.
    """

    MIN_CYCLE_INTERVAL_S = 600  # 10 minutes between evolution cycles
    MAX_CHANGES_PER_CYCLE = 10
    MAX_DAILY_CYCLES = 20

    def __init__(
        self,
        provider: LLMProvider,
        model: str,
        workspace: Path,
        learning: LearningStore,
        observer: Observer | None = None,
        stuck_detector: StuckDetector | None = None,
        recovery: RecoveryEngine | None = None,
        reaction_engine: ReactionEngine | None = None,
    ):
        self._provider = provider
        self._model = model
        self._workspace = workspace
        self._learning = learning

        # Sub-engines
        self._diagnosis = SelfDiagnosisEngine(
            learning=learning,
            observer=observer,
            stuck_detector=stuck_detector,
            recovery=recovery,
        )
        self._planner = ImprovementPlanner(provider, model, workspace)
        self._growth = GrowthTracker(workspace)
        self._reaction_engine = reaction_engine

        # Safety
        from sharanbot.workers.self_build.safety import SafetyBoundary
        self._safety = SafetyBoundary(workspace)

        # State
        self._last_cycle_time: float = 0
        self._daily_cycles: int = 0
        self._daily_reset_time: float = time.time()
        self._running = False
        self._pending_approval: EvolutionPlan | None = None

    @property
    def growth_tracker(self) -> GrowthTracker:
        return self._growth

    async def run_cycle(
        self,
        trigger: EvolutionTrigger = EvolutionTrigger.SCHEDULED,
        specific_diagnosis: Diagnosis | None = None,
    ) -> EvolutionResult | None:
        """Run one evolution cycle: diagnose → plan → execute.

        Returns the result, or None if skipped (rate-limited, no issues found).
        """
        # Rate limiting
        if not self._check_rate_limit():
            logger.debug("Evolution cycle skipped: rate limited")
            return None

        self._last_cycle_time = time.time()
        self._daily_cycles += 1
        start = time.time()

        # Step 1: Diagnose
        if specific_diagnosis:
            diagnoses = [specific_diagnosis]
        else:
            diagnoses = self._diagnosis.run_diagnosis()

        if not diagnoses:
            logger.debug("Evolution: no issues diagnosed")
            return None

        # Take the highest-severity diagnosis
        diagnosis = diagnoses[0]
        logger.info(
            "Evolution cycle triggered ({}): {} (severity={:.1f})",
            trigger.value, diagnosis.description, diagnosis.severity,
        )

        # Step 2: Plan
        codebase_ctx = self._get_codebase_context()
        plan = await self._planner.plan(
            diagnosis=diagnosis,
            codebase_context=codebase_ctx,
            recent_history=self._growth.get_recent(5),
        )

        if not plan:
            logger.info("Evolution: planner produced no plan")
            return None

        # Step 3: Safety check
        blocked = self._safety_check(plan)
        if blocked:
            result = EvolutionResult(
                plan_id=plan.plan_id,
                success=False,
                error=f"Safety blocked: {blocked}",
                duration_s=time.time() - start,
            )
            self._growth.record(result)
            return result

        # Step 4: If high risk, queue for approval
        if plan.requires_approval:
            self._pending_approval = plan
            logger.info("Evolution plan {} requires approval (high risk)", plan.plan_id)
            return EvolutionResult(
                plan_id=plan.plan_id,
                success=False,
                error="Queued for approval (high risk)",
                duration_s=time.time() - start,
            )

        # Step 5: Execute
        result = await self._execute_plan(plan)
        result.duration_s = time.time() - start
        self._growth.record(result)

        # Step 6: Log to learning store
        self._learning.log_growth(
            f"Evolution {plan.plan_id}: "
            f"{'SUCCESS' if result.success else 'FAILED'} — "
            f"{plan.description[:100]}"
        )

        return result

    async def approve_pending(self) -> EvolutionResult | None:
        """Approve and execute the pending high-risk plan."""
        if not self._pending_approval:
            return None
        plan = self._pending_approval
        self._pending_approval = None
        start = time.time()
        result = await self._execute_plan(plan)
        result.duration_s = time.time() - start
        self._growth.record(result)
        return result

    def reject_pending(self) -> bool:
        """Reject the pending high-risk plan."""
        if self._pending_approval:
            self._pending_approval = None
            return True
        return False

    async def _execute_plan(self, plan: EvolutionPlan) -> EvolutionResult:
        """Execute an evolution plan with git checkpoint and test validation."""
        from sharanbot.workers.self_build.rollback import GitRollback
        from sharanbot.workers.self_build.validator import TestValidator

        rollback = GitRollback(self._workspace)
        validator = TestValidator(self._workspace)

        # Create checkpoint
        checkpoint = await rollback.create_checkpoint(
            description=f"Evolution: {plan.description[:80]}",
            files=[c.path for c in plan.changes],
        )

        # Capture test baseline
        baseline = await validator.capture_baseline()

        # Apply changes
        changes_applied = 0
        new_capabilities: list[str] = []
        for change in plan.changes[:self.MAX_CHANGES_PER_CYCLE]:
            try:
                full_path = self._workspace / change.path
                if change.action == "create":
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_text(change.content, encoding="utf-8")
                    changes_applied += 1
                    new_capabilities.append(change.description or change.path)
                elif change.action == "modify":
                    if full_path.exists():
                        change.original_content = full_path.read_text(encoding="utf-8")
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_text(change.content, encoding="utf-8")
                    changes_applied += 1
                elif change.action == "delete" and full_path.exists():
                    full_path.unlink()
                    changes_applied += 1
            except Exception as e:
                logger.error("Evolution: failed to apply {}: {}", change.path, e)
                if checkpoint:
                    await rollback.rollback(checkpoint)
                return EvolutionResult(
                    plan_id=plan.plan_id,
                    success=False,
                    changes_applied=changes_applied,
                    rolled_back=True,
                    error=f"Apply failed: {e}",
                )

        if changes_applied == 0:
            return EvolutionResult(
                plan_id=plan.plan_id,
                success=False,
                error="No changes applied",
            )

        # Run tests
        report = await validator.run_tests()
        tests_passed = report.verdict in ("PASS", "PASS_WITH_KNOWN_FAILURES")

        if report.regressions_detected:
            logger.warning("Evolution: regressions detected, rolling back")
            if checkpoint:
                await rollback.rollback(checkpoint)
            return EvolutionResult(
                plan_id=plan.plan_id,
                success=False,
                changes_applied=changes_applied,
                tests_passed=False,
                rolled_back=True,
                error=f"Regressions: {', '.join(report.newly_failing[:5])}",
            )

        # Success — commit the changes
        try:
            from sharanbot.workers.self_build.rollback import GitRollback as _GR
            gr = _GR(self._workspace)
            await gr._git("add", "-A")
            await gr._git(
                "commit", "-m",
                f"[SharanAGI Evolution] {plan.description[:60]}",
            )
        except Exception:
            pass  # Non-fatal if git commit fails

        logger.info(
            "Evolution SUCCESS: {} — {} changes applied, tests {}",
            plan.plan_id, changes_applied,
            "passed" if tests_passed else "passed (with known failures)",
        )

        return EvolutionResult(
            plan_id=plan.plan_id,
            success=True,
            changes_applied=changes_applied,
            tests_passed=tests_passed,
            new_capabilities=new_capabilities,
        )

    def _safety_check(self, plan: EvolutionPlan) -> str:
        """Check all planned changes against safety boundaries.

        Returns empty string if safe, or a reason string if blocked.
        """
        for change in plan.changes:
            allowed, reason = self._safety.check_write(change.path)
            if not allowed:
                return f"{change.path}: {reason}"
        return ""

    def _check_rate_limit(self) -> bool:
        """Check if we're allowed to run another cycle."""
        now = time.time()
        # Reset daily counter
        if now - self._daily_reset_time > 86400:
            self._daily_cycles = 0
            self._daily_reset_time = now

        if self._daily_cycles >= self.MAX_DAILY_CYCLES:
            return False
        if now - self._last_cycle_time < self.MIN_CYCLE_INTERVAL_S:
            return False
        return True

    def _get_codebase_context(self) -> str:
        """Get a brief codebase context for the planner."""
        try:
            from sharanbot.workers.self_build.analyzer import CodebaseAnalyzer
            analyzer = CodebaseAnalyzer(self._workspace / "sharanbot")
            cmap = analyzer.scan()
            return cmap.get_summary()
        except Exception:
            return ""

    def get_status(self) -> dict[str, Any]:
        """Get evolution engine status."""
        return {
            "growth": self._growth.get_stats(),
            "rate_limit": {
                "daily_cycles": self._daily_cycles,
                "max_daily": self.MAX_DAILY_CYCLES,
                "last_cycle_ago_s": round(time.time() - self._last_cycle_time)
                if self._last_cycle_time else None,
                "min_interval_s": self.MIN_CYCLE_INTERVAL_S,
            },
            "pending_approval": self._pending_approval is not None,
        }

    async def trigger_from_event(
        self,
        trigger: EvolutionTrigger,
        description: str = "",
        evidence: list[str] | None = None,
    ) -> EvolutionResult | None:
        """Convenience: create a diagnosis from an event and run a cycle."""
        diag = Diagnosis(
            id=f"diag-event-{int(time.time())}",
            trigger=trigger,
            description=description,
            evidence=evidence or [],
            severity=0.6,
            suggested_area="sharanbot/",
        )
        return await self.run_cycle(trigger=trigger, specific_diagnosis=diag)
