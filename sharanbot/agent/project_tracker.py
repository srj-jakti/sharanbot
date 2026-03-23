"""Project tracker — auto-discovers projects in the workspace.

Scans for project indicator files (package.json, pyproject.toml, etc.)
and maintains a registry of active projects. Injects project awareness
into the system prompt so the agent always knows what exists.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

_PROJECT_INDICATORS = {
    "package.json": "node",
    "pyproject.toml": "python",
    "setup.py": "python",
    "Cargo.toml": "rust",
    "go.mod": "go",
    "pom.xml": "java",
    "build.gradle": "java",
    "Gemfile": "ruby",
    "composer.json": "php",
    "CMakeLists.txt": "cpp",
    "Makefile": "make",
    "docker-compose.yml": "docker",
    "Dockerfile": "docker",
}

_STALE_DAYS = 7


@dataclass
class ProjectState:
    name: str
    path: str
    project_type: str
    status: str = "active"  # "active", "stale", "completed"
    goals: list[str] = field(default_factory=list)
    notes: str = ""
    last_seen: float = field(default_factory=time.time)
    discovered_at: float = field(default_factory=time.time)


class ProjectTracker:
    """Auto-discovers and tracks projects in the workspace."""

    _STATE_FILE = ".sharanbot/projects.json"

    def __init__(self, workspace: Path):
        self._workspace = workspace
        self._projects: dict[str, ProjectState] = {}
        self._load()

    def _load(self) -> None:
        path = self._workspace / self._STATE_FILE
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for k, v in data.items():
                self._projects[k] = ProjectState(**v)
        except Exception as e:
            logger.warning("Failed to load project state: {}", e)

    def _save(self) -> None:
        path = self._workspace / self._STATE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = {k: asdict(v) for k, v in self._projects.items()}
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to save project state: {}", e)

    def scan(self, max_depth: int = 2) -> list[ProjectState]:
        """Scan workspace for project indicator files."""
        found: list[ProjectState] = []
        now = time.time()
        stale_cutoff = now - (_STALE_DAYS * 86400)

        for depth in range(max_depth + 1):
            for indicator, ptype in _PROJECT_INDICATORS.items():
                if depth == 0:
                    glob_pat = indicator
                else:
                    glob_pat = "/".join(["*"] * depth) + "/" + indicator
                for match in self._workspace.glob(glob_pat):
                    project_dir = match.parent
                    key = str(project_dir.relative_to(self._workspace))
                    if key in self._projects:
                        self._projects[key].last_seen = now
                        continue

                    name = project_dir.name
                    state = ProjectState(
                        name=name, path=key, project_type=ptype,
                    )
                    self._projects[key] = state
                    found.append(state)

        # Mark stale projects
        for p in self._projects.values():
            if p.last_seen < stale_cutoff and p.status == "active":
                p.status = "stale"

        self._save()
        return found

    def set_goal(self, project_path: str, goal: str) -> bool:
        state = self._projects.get(project_path)
        if not state:
            return False
        if goal not in state.goals:
            state.goals.append(goal)
        self._save()
        return True

    def set_note(self, project_path: str, note: str) -> bool:
        state = self._projects.get(project_path)
        if not state:
            return False
        state.notes = note
        self._save()
        return True

    def get_context(self, max_projects: int = 10) -> str:
        """Get project awareness context for system prompt injection."""
        active = [p for p in self._projects.values() if p.status == "active"]
        if not active:
            return ""
        active.sort(key=lambda p: p.last_seen, reverse=True)
        lines = ["## Known Projects"]
        for p in active[:max_projects]:
            goals = f" — Goals: {', '.join(p.goals)}" if p.goals else ""
            lines.append(f"- **{p.name}** ({p.project_type}) at `{p.path}`{goals}")
        return "\n".join(lines)

    @property
    def active_projects(self) -> list[ProjectState]:
        return [p for p in self._projects.values() if p.status == "active"]
