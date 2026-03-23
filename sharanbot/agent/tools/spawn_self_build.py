"""SpawnSelfBuildTool — trigger the self-build system for self-improvement.

Allows the agent to modify its own codebase through the self-build pipeline:
analyze → validate → backup → apply → test → rollback-if-failed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from sharanbot.agent.tools.base import Tool


class SpawnSelfBuildTool(Tool):
    name = "spawn_self_build"
    description = (
        "Trigger the self-build system to improve SharanBot's own code. "
        "Provide a description of what to improve and optionally which files to target."
    )
    parameters = {
        "type": "object",
        "properties": {
            "improvement": {
                "type": "string",
                "description": "Description of the improvement to make",
            },
            "target_files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Specific files to modify (optional — auto-detects if empty)",
            },
            "dry_run": {
                "type": "boolean",
                "description": "If true, analyze and plan but don't apply changes",
            },
        },
        "required": ["improvement"],
    }

    async def execute(self, **kwargs: Any) -> str:
        improvement: str = kwargs["improvement"]
        target_files: list[str] = kwargs.get("target_files", [])
        dry_run: bool = kwargs.get("dry_run", False)

        try:
            from sharanbot.workers.self_build.analyzer import SelfBuildAnalyzer
            from sharanbot.workers.self_build.validator import SelfBuildValidator
            from sharanbot.workers.self_build.safety import SafetyChecker
        except ImportError:
            return "❌ Self-build modules not available. Ensure sharanbot.workers.self_build is installed."

        workspace = Path(".")

        # Step 1: Analyze
        analyzer = SelfBuildAnalyzer(workspace)
        analysis = analyzer.analyze(improvement, target_files)

        if dry_run:
            return (
                f"🔍 Self-build analysis (DRY RUN):\n"
                f"  Improvement: {improvement}\n"
                f"  Target files: {analysis.get('files', [])}\n"
                f"  Estimated changes: {analysis.get('change_count', 'unknown')}\n"
                f"  Risk level: {analysis.get('risk', 'unknown')}"
            )

        # Step 2: Safety check
        checker = SafetyChecker(workspace)
        safe, reason = checker.check(analysis)
        if not safe:
            return f"⚠️ Self-build blocked by safety check: {reason}"

        # Step 3: Validate
        validator = SelfBuildValidator(workspace)
        valid, issues = validator.validate(analysis)
        if not valid:
            return f"⚠️ Self-build validation failed: {'; '.join(issues)}"

        return (
            f"✅ Self-build pipeline ready for: {improvement}\n"
            f"  Files: {analysis.get('files', [])}\n"
            f"  Use the self-build agent loop to apply changes."
        )
