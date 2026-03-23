"""Self-build agent loop — LLM-driven self-modification with safety.

Orchestrates the full self-modification cycle:
  1. Analyze current codebase (AST)
  2. Plan modifications
  3. Create safety checkpoint
  4. Apply changes (with safety boundary enforcement)
  5. Validate (tests + regression detection)
  6. Rollback if validation fails
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from sharanbot.workers.self_build.analyzer import (
    CodebaseAnalyzer,
    CodebaseMap,
    generate_test_skeleton,
)
from sharanbot.workers.self_build.rollback import GitRollback
from sharanbot.workers.self_build.safety import SafetyBoundary
from sharanbot.workers.self_build.validator import TestValidator, ValidationReport


@dataclass
class ModificationPlan:
    """A planned set of self-modifications."""
    plan_id: str
    description: str
    changes: list[FileChange] = field(default_factory=list)
    rationale: str = ""
    estimated_risk: str = "low"  # low | medium | high
    approved: bool = False


@dataclass
class FileChange:
    """A single file modification."""
    path: str
    action: str  # "create" | "modify" | "delete"
    content: str = ""
    original_content: str = ""
    description: str = ""


@dataclass
class SelfBuildResult:
    """Result of a self-build cycle."""
    success: bool
    plan_id: str = ""
    changes_applied: int = 0
    tests_passed: bool = False
    rolled_back: bool = False
    validation_report: ValidationReport | None = None
    error: str = ""
    duration: float = 0.0

    def summary(self) -> str:
        status = "SUCCESS" if self.success else "FAILED"
        lines = [
            f"Self-build {self.plan_id}: {status}",
            f"  Changes: {self.changes_applied}",
            f"  Tests: {'PASS' if self.tests_passed else 'FAIL'}",
        ]
        if self.rolled_back:
            lines.append("  Rolled back: YES")
        if self.error:
            lines.append(f"  Error: {self.error}")
        if self.validation_report:
            lines.append(f"  {self.validation_report.summary()}")
        return "\n".join(lines)


class SelfBuildLoop:
    """LLM-driven self-modification loop with safety guardrails."""

    MAX_RETRIES = 2
    MAX_CHANGES_PER_CYCLE = 20

    def __init__(self, workspace: Path | None = None):
        self._workspace = workspace or Path(".")
        self._analyzer = CodebaseAnalyzer(self._workspace / "sharanbot")
        self._safety = SafetyBoundary(self._workspace)
        self._rollback = GitRollback(self._workspace)
        self._validator = TestValidator(self._workspace)
        self._codebase_map: CodebaseMap | None = None
        self._history: list[SelfBuildResult] = []

    async def analyze(self) -> CodebaseMap:
        """Scan and analyze the current codebase."""
        self._codebase_map = self._analyzer.scan()
        logger.info("Analyzed codebase:\n{}", self._codebase_map.get_summary())
        return self._codebase_map

    def create_plan(self, description: str,
                    changes: list[dict[str, str]]) -> ModificationPlan:
        """Create a modification plan from a list of changes.

        Each change dict: {"path": "...", "action": "create|modify|delete",
                           "content": "...", "description": "..."}
        """
        plan_id = f"plan-{int(time.time())}"
        file_changes: list[FileChange] = []

        for change in changes[:self.MAX_CHANGES_PER_CYCLE]:
            fc = FileChange(
                path=change.get("path", ""),
                action=change.get("action", "modify"),
                content=change.get("content", ""),
                description=change.get("description", ""),
            )
            # Read original content for modify/delete
            full_path = self._workspace / fc.path
            if full_path.exists() and fc.action in ("modify", "delete"):
                try:
                    fc.original_content = full_path.read_text(encoding="utf-8")
                except Exception:
                    pass
            file_changes.append(fc)

        # Assess risk
        risk = self._assess_risk(file_changes)

        plan = ModificationPlan(
            plan_id=plan_id,
            description=description,
            changes=file_changes,
            estimated_risk=risk,
        )
        return plan

    async def execute_plan(self, plan: ModificationPlan,
                           auto_rollback: bool = True) -> SelfBuildResult:
        """Execute a modification plan with full safety cycle."""
        start = time.time()

        # 1. Safety check all files
        blocked = []
        for change in plan.changes:
            allowed, reason = self._safety.check_write(change.path)
            if not allowed:
                blocked.append(f"{change.path}: {reason}")

        if blocked:
            return SelfBuildResult(
                success=False,
                plan_id=plan.plan_id,
                error=f"Safety blocked: {'; '.join(blocked)}",
                duration=time.time() - start,
            )

        # 2. Create checkpoint
        checkpoint = await self._rollback.create_checkpoint(
            description=plan.description,
            files=[c.path for c in plan.changes],
        )

        # 3. Capture test baseline
        baseline = await self._validator.capture_baseline()

        # 4. Apply changes
        changes_applied = 0
        for change in plan.changes:
            try:
                if self._apply_change(change):
                    changes_applied += 1
            except Exception as e:
                logger.error("Failed to apply {}: {}", change.path, e)
                if auto_rollback and checkpoint:
                    await self._rollback.rollback(checkpoint)
                return SelfBuildResult(
                    success=False,
                    plan_id=plan.plan_id,
                    changes_applied=changes_applied,
                    rolled_back=True,
                    error=str(e),
                    duration=time.time() - start,
                )

        # 4b. Auto-generate tests for changed Python files
        generated = self._generate_tests_for_changes(plan.changes)
        if generated:
            logger.info("Auto-generated {} test file(s)", len(generated))

        # 5. Validate
        report = await self._validator.run_tests()

        # 6. Check for regressions
        if report.regressions_detected and auto_rollback and checkpoint:
            logger.warning("Regressions detected — rolling back")
            await self._rollback.rollback(checkpoint)
            result = SelfBuildResult(
                success=False,
                plan_id=plan.plan_id,
                changes_applied=changes_applied,
                tests_passed=False,
                rolled_back=True,
                validation_report=report,
                error="Regressions detected",
                duration=time.time() - start,
            )
            self._history.append(result)
            return result

        # Success!
        result = SelfBuildResult(
            success=True,
            plan_id=plan.plan_id,
            changes_applied=changes_applied,
            tests_passed=report.verdict in ("PASS", "PASS_WITH_KNOWN_FAILURES"),
            validation_report=report,
            duration=time.time() - start,
        )
        self._history.append(result)

        # Cleanup old checkpoints
        if checkpoint:
            await self._rollback.cleanup_checkpoints()

        logger.info("Self-build complete:\n{}", result.summary())
        return result

    def _apply_change(self, change: FileChange) -> bool:
        """Apply a single file change."""
        full_path = self._workspace / change.path

        if change.action == "create":
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(change.content, encoding="utf-8")
            logger.debug("Created: {}", change.path)
            return True

        elif change.action == "modify":
            if not full_path.exists():
                logger.warning("Cannot modify non-existent file: {}", change.path)
                return False
            full_path.write_text(change.content, encoding="utf-8")
            logger.debug("Modified: {}", change.path)
            return True

        elif change.action == "delete":
            if full_path.exists():
                full_path.unlink()
                logger.debug("Deleted: {}", change.path)
                return True
            return False

        logger.warning("Unknown action: {}", change.action)
        return False

    def _generate_tests_for_changes(
        self, changes: list[FileChange],
    ) -> list[str]:
        """Generate test skeletons for created/modified Python files."""
        generated: list[str] = []
        analyzer = CodebaseAnalyzer()

        for change in changes:
            if change.action not in ("create", "modify"):
                continue
            if not change.path.endswith(".py"):
                continue
            # Skip files already in tests/
            if change.path.startswith("tests/") or change.path.startswith("tests\\"):
                continue
            if not change.content:
                continue

            mod_info = analyzer.analyze_source(change.content, change.path)
            if mod_info.errors or (not mod_info.classes and not mod_info.functions):
                continue

            # Build import path: sharanbot/utils/helpers.py -> sharanbot.utils.helpers
            import_path = (
                change.path.replace("/", ".").replace("\\", ".").removesuffix(".py")
            )

            skeleton = generate_test_skeleton(mod_info, import_path)

            # Determine test file path
            base = Path(change.path).stem
            test_path = f"tests/test_{base}.py"
            full_test = self._workspace / test_path
            if full_test.exists():
                continue  # don't overwrite existing tests

            full_test.parent.mkdir(parents=True, exist_ok=True)
            full_test.write_text(skeleton, encoding="utf-8")
            generated.append(test_path)
            logger.debug("Generated test skeleton: {}", test_path)

        return generated

    def _assess_risk(self, changes: list[FileChange]) -> str:
        """Assess overall risk of a set of changes."""
        restricted_count = sum(
            1 for c in changes if self._safety.is_restricted(c.path)
        )
        delete_count = sum(1 for c in changes if c.action == "delete")
        core_count = sum(
            1 for c in changes
            if "agent/" in c.path or "loop" in c.path
        )

        if restricted_count > 0 or delete_count > 2 or core_count > 3:
            return "high"
        if len(changes) > 10 or delete_count > 0 or core_count > 0:
            return "medium"
        return "low"

    @property
    def history(self) -> list[SelfBuildResult]:
        return self._history[:]

    def get_codebase_context(self) -> str:
        """Get codebase context string for LLM prompts."""
        if not self._codebase_map:
            self.analyze_sync()
        if not self._codebase_map:
            return "Codebase not yet analyzed."
        return self._codebase_map.get_summary()

    def analyze_sync(self) -> CodebaseMap:
        """Synchronous version of analyze."""
        self._codebase_map = self._analyzer.scan()
        return self._codebase_map
