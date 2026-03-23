"""Project manifest — persistence layer for complete project state.

Stores project_id, description, pipeline state, team configs,
file ownership, contracts, worker assignments, and proof-of-work
summaries. Enables crash recovery and project status queries.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class ProjectManifest:
    """Complete project state, serializable for crash recovery."""

    project_id: str
    description: str = ""
    project_type: str = ""
    pipeline_state: dict[str, Any] = field(default_factory=dict)
    team_configs: dict[str, Any] = field(default_factory=dict)
    file_ownership_map: dict[str, str] = field(default_factory=dict)
    contract_references: list[str] = field(default_factory=list)
    shared_memory_path: str = ""
    worker_assignments: dict[str, dict[str, Any]] = field(default_factory=dict)
    proof_of_work_summaries: dict[str, str] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    tech_stack: str = ""
    complexity: str = "medium"  # "simple", "medium", "complex"

    def update_phase(self, phase: str, status: str) -> None:
        self.pipeline_state[phase] = status
        self.updated_at = time.time()

    def add_worker_summary(self, worker_id: str, summary: str) -> None:
        self.proof_of_work_summaries[worker_id] = summary
        self.updated_at = time.time()

    def get_status_report(self) -> str:
        """Compute progress and generate a status report."""
        phases = self.pipeline_state
        total = len(phases) or 1
        completed = sum(1 for s in phases.values() if s in ("completed", "skipped"))
        pct = completed / total * 100

        # Phase indicators
        icons = {"completed": "✅", "in_progress": "🔄", "pending": "⏳",
                 "failed": "❌", "skipped": "⏭️"}

        lines = [
            f"## Project: {self.project_id}",
            f"Progress: {pct:.0f}% ({completed}/{total} phases)",
            f"Type: {self.project_type} | Complexity: {self.complexity}",
            "",
        ]
        for phase, status in phases.items():
            icon = icons.get(status, "❓")
            lines.append(f"  {icon} {phase}")

        if self.worker_assignments:
            lines.append(f"\nWorkers: {len(self.worker_assignments)}")

        return "\n".join(lines)

    def save(self, workspace: Path) -> None:
        dir_ = workspace / "Sharan_workplace" / ".projects_meta" / self.project_id
        dir_.mkdir(parents=True, exist_ok=True)
        path = dir_ / "manifest.json"
        try:
            path.write_text(
                json.dumps(asdict(self), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Failed to save manifest: {}", e)

    @classmethod
    def load(cls, workspace: Path, project_id: str) -> ProjectManifest | None:
        path = workspace / "Sharan_workplace" / ".projects_meta" / project_id / "manifest.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(**data)
        except Exception as e:
            logger.warning("Failed to load manifest: {}", e)
            return None

    @classmethod
    def list_projects(cls, workspace: Path) -> list[str]:
        dir_ = workspace / "Sharan_workplace" / ".projects_meta"
        if not dir_.exists():
            return []
        return [p.name for p in dir_.iterdir() if p.is_dir()]
