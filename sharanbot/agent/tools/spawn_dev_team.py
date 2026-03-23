"""SpawnDevTeamTool — trigger multi-agent development pipeline from chat.

This is the chat-facing entry point to the orchestration system.
When a user says "build me a full-stack app", this tool spins up
the TechLeadOrchestrator pipeline with the right configuration.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from loguru import logger

from sharanbot.agent.tools.base import Tool


class SpawnDevTeamTool(Tool):
    """Tool that spawns a multi-agent development team for building software."""

    @property
    def name(self) -> str:
        return "spawn_dev_team"

    @property
    def description(self) -> str:
        return (
            "Spawn a multi-agent development team to build a complete software project. "
            "Provide a description of what to build. The team will go through requirements, "
            "architecture, sprint planning, parallel development, testing, and deployment. "
            "Use this for complex multi-file projects that need structured development."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Detailed description of what software to build.",
                },
                "project_path": {
                    "type": "string",
                    "description": "Directory where the project should be created.",
                },
                "tech_stack": {
                    "type": "string",
                    "description": (
                        "Preferred tech stack (e.g., 'Python FastAPI + React + PostgreSQL'). "
                        "Leave empty for auto-detection."
                    ),
                },
                "parallel_workers": {
                    "type": "integer",
                    "description": "Number of parallel development workers (1-10, default 5).",
                },
                "skip_phases": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Phases to skip. Options: requirements, architecture, "
                        "sprint_planning, development, testing, review, deployment"
                    ),
                },
            },
            "required": ["description"],
        }

    async def execute(self, **kwargs: Any) -> str:
        description = kwargs.get("description", "")
        project_path = kwargs.get("project_path", "")
        tech_stack = kwargs.get("tech_stack", "")
        parallel_workers = kwargs.get("parallel_workers", 5)
        skip_phases_raw = kwargs.get("skip_phases", [])

        if not description:
            return "Error: description is required"

        # Import here to avoid circular imports
        from sharanbot.orchestration.pipeline import Phase
        from sharanbot.orchestration.tech_lead import TechLeadOrchestrator

        # Resolve workspace
        workspace = Path(project_path) if project_path else Path(".")
        if not workspace.is_absolute():
            import os
            ws_root = Path(os.environ.get("SHARANBOT_WORKSPACE", "."))
            workspace = ws_root / workspace

        workspace.mkdir(parents=True, exist_ok=True)

        # Parse skip phases
        skip_phases: list[Phase] = []
        phase_map = {p.value: p for p in Phase}
        for sp in skip_phases_raw:
            if sp in phase_map:
                skip_phases.append(phase_map[sp])

        # Clamp parallel workers
        parallel_workers = min(max(int(parallel_workers or 5), 1), 10)

        # Create orchestrator
        orchestrator = TechLeadOrchestrator(
            workspace=workspace,
            parallel_limit=parallel_workers,
        )

        # Start project
        manifest = orchestrator.start_project(
            description=description,
            tech_stack=tech_stack,
            skip_phases=skip_phases,
        )

        # Build status message
        lines = [
            f"🏗️ Development team initialized!",
            f"  Project: {manifest.project_id}",
            f"  Type: {manifest.project_type}",
            f"  Complexity: {manifest.complexity}",
            f"  Workers: {parallel_workers}",
            f"  Path: {workspace}",
        ]
        if tech_stack:
            lines.append(f"  Stack: {tech_stack}")
        if skip_phases:
            lines.append(f"  Skipping: {', '.join(sp.value for sp in skip_phases)}")

        lines.append("")
        lines.append("Pipeline phases:")
        from sharanbot.orchestration.pipeline import PHASE_ORDER
        for phase in PHASE_ORDER:
            if phase in skip_phases:
                lines.append(f"  ⏭️ {phase.value} (skipped)")
            else:
                lines.append(f"  ⏳ {phase.value}")

        # Note: actual pipeline execution needs the agent loop
        if not orchestrator.has_execution_backend:
            lines.append("")
            lines.append(
                "Note: Pipeline initialized but requires agent loop for execution. "
                "The orchestrator will coordinate work as tasks come in."
            )
        else:
            lines.append("")
            lines.append("Starting pipeline execution...")
            try:
                results = await orchestrator.run_pipeline()
                lines.append(f"Pipeline complete! Results: {json.dumps(results, indent=2)}")
            except Exception as e:
                lines.append(f"Pipeline error: {e}")

        return "\n".join(lines)
