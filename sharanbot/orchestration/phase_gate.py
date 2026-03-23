"""Phase gate validator — artifact-based quality checks between pipeline phases.

Validates that each phase produced its required output artifacts before
allowing advancement. Includes deep content checks for critical artifacts
and optional LLM-powered quality evaluation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from sharanbot.orchestration.pipeline import Phase


@dataclass
class GateResult:
    passed: bool
    phase: Phase
    missing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    details: str = ""


# Required artifacts per phase
REQUIRED_ARTIFACTS: dict[Phase, list[str]] = {
    Phase.DISCOVERY: ["product_vision_doc", "brd"],
    Phase.ARCHITECTURE: ["api_spec", "db_schema", "architecture_doc", "file_ownership_map"],
    Phase.SPRINT_PLANNING: ["project_plan"],
    Phase.DEVELOPMENT: ["team_branches_committed"],
    Phase.QA_SECURITY: ["test_results"],
    Phase.DEPLOYMENT: ["deployment_configs"],
    Phase.POST_LAUNCH: ["handoff_document"],
}

# BRD required sections
_BRD_REQUIRED_SECTIONS = [
    "functional_requirements", "non_functional_requirements",
    "user_journey", "feasibility", "risk",
]


class PhaseGateValidator:
    """Validates phase outputs before allowing pipeline advancement."""

    def validate(self, phase: Phase, workspace: Path,
                 artifacts: dict[str, Any]) -> GateResult:
        """Validate all required artifacts for a phase."""
        required = REQUIRED_ARTIFACTS.get(phase, [])
        missing = [a for a in required if a not in artifacts]

        if missing:
            return GateResult(
                passed=False, phase=phase, missing=missing,
                details=f"Missing artifacts: {', '.join(missing)}",
            )

        warnings: list[str] = []

        # Deep checks per phase
        if phase == Phase.DISCOVERY:
            warnings.extend(self._check_brd(artifacts.get("brd", "")))
            warnings.extend(self._check_content_length(artifacts, phase))
        elif phase == Phase.ARCHITECTURE:
            warnings.extend(self._check_content_length(artifacts, phase))
        elif phase == Phase.SPRINT_PLANNING:
            cycle_warning = self._check_circular_deps(artifacts.get("project_plan", {}))
            if cycle_warning:
                return GateResult(
                    passed=False, phase=phase,
                    details=f"Circular dependency detected: {cycle_warning}",
                )
        elif phase == Phase.QA_SECURITY:
            test_warning = self._check_test_results(artifacts.get("test_results", {}))
            if test_warning:
                return GateResult(
                    passed=False, phase=phase,
                    details=test_warning,
                )

        return GateResult(passed=True, phase=phase, warnings=warnings)

    def _check_brd(self, brd_content: str) -> list[str]:
        """Check BRD has required sections."""
        warnings = []
        if isinstance(brd_content, str):
            content_lower = brd_content.lower()
            for section in _BRD_REQUIRED_SECTIONS:
                if section.replace("_", " ") not in content_lower and section not in content_lower:
                    warnings.append(f"BRD missing section: {section}")
        return warnings

    def _check_circular_deps(self, plan: Any) -> str:
        """Check sprint plan for circular dependencies using DFS."""
        if not isinstance(plan, dict):
            return ""

        tasks = plan.get("tasks", plan.get("sprints", []))
        if not isinstance(tasks, (list, dict)):
            return ""

        # Build adjacency list
        graph: dict[str, list[str]] = {}
        if isinstance(tasks, list):
            for task in tasks:
                if isinstance(task, dict):
                    tid = task.get("id", task.get("name", ""))
                    deps = task.get("dependencies", task.get("depends_on", []))
                    graph[tid] = deps if isinstance(deps, list) else []
        elif isinstance(tasks, dict):
            for tid, task in tasks.items():
                deps = task.get("dependencies", []) if isinstance(task, dict) else []
                graph[tid] = deps if isinstance(deps, list) else []

        if not graph:
            return ""

        # DFS cycle detection
        visited: set[str] = set()
        in_stack: set[str] = set()

        def dfs(node: str) -> str:
            if node in in_stack:
                return node
            if node in visited:
                return ""
            visited.add(node)
            in_stack.add(node)
            for dep in graph.get(node, []):
                cycle = dfs(dep)
                if cycle:
                    return f"{node} → {cycle}"
            in_stack.discard(node)
            return ""

        for node in graph:
            result = dfs(node)
            if result:
                return result
        return ""

    def _check_test_results(self, results: Any) -> str:
        """Check test results meet minimum pass rate (80%)."""
        if not isinstance(results, dict):
            return ""

        passed = results.get("passed", 0)
        failed = results.get("failed", 0)
        total = passed + failed

        if total == 0:
            return "No test results found"

        pass_rate = passed / total
        if pass_rate < 0.80:
            return f"Test pass rate {pass_rate:.0%} < 80% minimum ({passed}/{total})"

        return ""

    # -- Content quality checks ----------------------------------------

    # Minimum content lengths (chars) per artifact type
    _MIN_CONTENT_LENGTHS: dict[str, int] = {
        "brd": 200,
        "product_vision_doc": 100,
        "architecture_doc": 200,
        "api_spec": 100,
        "db_schema": 50,
        "project_plan": 100,
        "handoff_document": 100,
    }

    def _check_content_length(
        self, artifacts: dict[str, Any], phase: Phase,
    ) -> list[str]:
        """Warn when string artifacts are too short to be meaningful."""
        warnings: list[str] = []
        for name, value in artifacts.items():
            min_len = self._MIN_CONTENT_LENGTHS.get(name)
            if min_len is None or not isinstance(value, str):
                continue
            if len(value.strip()) < min_len:
                warnings.append(
                    f"Artifact '{name}' is only {len(value.strip())} chars "
                    f"(minimum {min_len}) — may be incomplete",
                )
        return warnings

    async def evaluate_quality(
        self,
        phase: Phase,
        artifacts: dict[str, Any],
        provider: Any,
        model: str | None = None,
    ) -> GateResult:
        """LLM-powered content quality evaluation.

        Runs the standard :meth:`validate` first, then asks the LLM to
        score each text artifact.  Returns a combined :class:`GateResult`.
        """
        base = self.validate(phase, Path("."), artifacts)
        if not base.passed:
            return base

        text_artifacts = {
            k: v for k, v in artifacts.items()
            if isinstance(v, str) and len(v.strip()) > 20
        }
        if not text_artifacts or provider is None:
            return base

        prompt = (
            "You are a quality auditor. Rate each artifact on completeness "
            "and clarity (1-10). List issues found. Reply as plain text.\n\n"
        )
        for name, content in text_artifacts.items():
            # Limit content sent to LLM to avoid token waste
            snippet = content[:2000]
            prompt += f"### {name}\n{snippet}\n\n"

        try:
            chat_fn = getattr(provider, "chat", None)
            if not callable(chat_fn):
                return base
            response = await chat_fn(
                [{"role": "user", "content": prompt}],
                model=model,
            )
            assessment = ""
            if hasattr(response, "content"):
                assessment = response.content or ""
            elif isinstance(response, dict):
                assessment = response.get("content", "")

            if assessment:
                base.details = (base.details + "\n" if base.details else "") + assessment.strip()
                # Check for low scores
                low_scores = re.findall(r"(\d)/10", assessment)
                if low_scores and any(int(s) < 5 for s in low_scores):
                    base.warnings.append(
                        "LLM quality check found artifacts scoring below 5/10"
                    )
        except Exception as exc:
            logger.debug("LLM quality evaluation skipped: {}", exc)

        return base
