"""Persistent Project Context — multi-project management like a human.

A human juggles multiple projects, remembers context for each, switches
between them, and prioritizes based on deadlines. SharanAGI does the same:

1. **Project Registry** — persistent catalog of all projects with metadata
2. **Per-Project Memory** — each project has its own context, decisions, TODOs
3. **Priority Queue** — deadline-aware prioritization
4. **Auto-Resume** — detect and resume incomplete work per project
5. **Cross-Project Knowledge** — patterns and solutions that transfer
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class Project:
    """A tracked project with persistent context."""

    id: str
    name: str
    path: str  # workspace-relative or absolute path
    description: str = ""
    status: str = "active"  # active, paused, completed, archived
    priority: int = 5  # 1 (highest) to 10 (lowest)
    deadline: str | None = None  # ISO date string
    tags: list[str] = field(default_factory=list)
    context: str = ""  # accumulated project knowledge
    decisions: list[str] = field(default_factory=list)  # key decisions made
    todos: list[str] = field(default_factory=list)  # pending items
    last_worked: float = 0.0
    created_at: float = field(default_factory=time.time)
    total_sessions: int = 0
    tech_stack: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "description": self.description,
            "status": self.status,
            "priority": self.priority,
            "deadline": self.deadline,
            "tags": self.tags,
            "context": self.context,
            "decisions": self.decisions[-50:],
            "todos": self.todos,
            "last_worked": self.last_worked,
            "created_at": self.created_at,
            "total_sessions": self.total_sessions,
            "tech_stack": self.tech_stack,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Project:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class ProjectManager:
    """Manages multiple projects with persistent context.

    Stores project metadata, per-project memory, and provides
    priority-based work suggestions.
    """

    _FILENAME = "projects_registry.json"

    def __init__(self, workspace: Path):
        self._workspace = workspace
        self._path = workspace / self._FILENAME
        self._projects: dict[str, Project] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for pdata in data.get("projects", []):
                proj = Project.from_dict(pdata)
                self._projects[proj.id] = proj
        except Exception as e:
            logger.warning("Failed to load project registry: {}", e)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {"projects": [p.to_dict() for p in self._projects.values()]}
            self._path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning("Failed to save project registry: {}", e)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        path: str,
        description: str = "",
        priority: int = 5,
        deadline: str | None = None,
        tags: list[str] | None = None,
        tech_stack: list[str] | None = None,
    ) -> Project:
        """Register a new project or update existing one."""
        # Generate ID from name
        proj_id = name.lower().replace(" ", "_").replace("-", "_")[:30]
        if proj_id in self._projects:
            proj = self._projects[proj_id]
            proj.description = description or proj.description
            proj.priority = priority
            proj.deadline = deadline or proj.deadline
            if tags:
                proj.tags = tags
            if tech_stack:
                proj.tech_stack = tech_stack
        else:
            proj = Project(
                id=proj_id, name=name, path=path,
                description=description, priority=priority,
                deadline=deadline, tags=tags or [],
                tech_stack=tech_stack or [],
            )
            self._projects[proj_id] = proj
        self._save()
        logger.info("Project registered: {} (priority {})", name, priority)
        return proj

    def get(self, project_id: str) -> Project | None:
        return self._projects.get(project_id)

    def list_all(self, status: str | None = None) -> list[Project]:
        projects = list(self._projects.values())
        if status:
            projects = [p for p in projects if p.status == status]
        return sorted(projects, key=lambda p: (p.priority, -(p.last_worked or 0)))

    def remove(self, project_id: str) -> bool:
        if project_id in self._projects:
            del self._projects[project_id]
            self._save()
            return True
        return False

    # ------------------------------------------------------------------
    # Context management
    # ------------------------------------------------------------------

    def update_context(self, project_id: str, new_context: str) -> bool:
        """Update a project's accumulated context."""
        proj = self._projects.get(project_id)
        if not proj:
            return False
        proj.context = new_context[:10000]  # cap at 10k chars
        proj.last_worked = time.time()
        proj.total_sessions += 1
        self._save()
        return True

    def add_decision(self, project_id: str, decision: str) -> bool:
        """Record a key decision for a project."""
        proj = self._projects.get(project_id)
        if not proj:
            return False
        proj.decisions.append(f"[{time.strftime('%Y-%m-%d')}] {decision}")
        self._save()
        return True

    def add_todo(self, project_id: str, todo: str) -> bool:
        proj = self._projects.get(project_id)
        if not proj:
            return False
        proj.todos.append(todo)
        self._save()
        return True

    def complete_todo(self, project_id: str, index: int) -> bool:
        proj = self._projects.get(project_id)
        if not proj or index < 0 or index >= len(proj.todos):
            return False
        proj.todos.pop(index)
        self._save()
        return True

    def set_status(self, project_id: str, status: str) -> bool:
        proj = self._projects.get(project_id)
        if not proj:
            return False
        proj.status = status
        self._save()
        return True

    # ------------------------------------------------------------------
    # Intelligence
    # ------------------------------------------------------------------

    def get_next_work(self) -> Project | None:
        """Get the highest-priority active project that needs work.

        Priority order:
        1. Projects with deadlines approaching (within 3 days)
        2. Projects with pending TODOs, sorted by priority
        3. Least recently worked active project
        """
        active = [p for p in self._projects.values() if p.status == "active"]
        if not active:
            return None

        # Deadline urgency
        now = time.time()
        three_days = 3 * 86400
        urgent = []
        for p in active:
            if p.deadline:
                try:
                    from datetime import datetime
                    dl = datetime.fromisoformat(p.deadline).timestamp()
                    if 0 < (dl - now) < three_days:
                        urgent.append((dl - now, p))
                except Exception:
                    pass
        if urgent:
            urgent.sort(key=lambda x: x[0])
            return urgent[0][1]

        # Has TODOs, sorted by priority
        with_todos = [p for p in active if p.todos]
        if with_todos:
            return min(with_todos, key=lambda p: p.priority)

        # Least recently worked
        return min(active, key=lambda p: p.last_worked or 0)

    def get_project_context_for_prompt(self, project_id: str) -> str | None:
        """Build a context string for injection into the agent prompt."""
        proj = self._projects.get(project_id)
        if not proj:
            return None
        parts = [f"[Project: {proj.name}]"]
        if proj.description:
            parts.append(f"Description: {proj.description}")
        if proj.tech_stack:
            parts.append(f"Tech: {', '.join(proj.tech_stack)}")
        if proj.context:
            parts.append(f"Context:\n{proj.context[:3000]}")
        if proj.decisions:
            recent = proj.decisions[-5:]
            parts.append("Recent decisions:\n" + "\n".join(f"  - {d}" for d in recent))
        if proj.todos:
            parts.append("TODOs:\n" + "\n".join(f"  - {t}" for t in proj.todos[:10]))
        return "\n".join(parts)

    def find_cross_project_patterns(self, query: str) -> list[dict[str, str]]:
        """Search across all projects for relevant context/decisions."""
        results = []
        query_lower = query.lower()
        for proj in self._projects.values():
            # Search in context
            if query_lower in proj.context.lower():
                results.append({
                    "project": proj.name,
                    "type": "context",
                    "match": proj.context[:500],
                })
            # Search in decisions
            for d in proj.decisions:
                if query_lower in d.lower():
                    results.append({
                        "project": proj.name,
                        "type": "decision",
                        "match": d,
                    })
        return results[:10]

    def get_stats(self) -> dict[str, Any]:
        return {
            "total": len(self._projects),
            "active": sum(1 for p in self._projects.values() if p.status == "active"),
            "with_todos": sum(1 for p in self._projects.values() if p.todos),
            "with_deadlines": sum(1 for p in self._projects.values() if p.deadline),
        }
