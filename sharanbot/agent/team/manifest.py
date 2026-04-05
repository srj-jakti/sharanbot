"""Project Manifest — single source of truth for a project being built by a team.

Every team creates a PROJECT_MANIFEST.json in the project root.
All agents (subagents, GSD workers, autopilot) read and update this manifest
so everyone knows: what's being built, where, by whom, and what decisions
have been made.
"""

from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

_DEFAULT_PORT_RANGE = (8000, 9000)


@dataclass
class AgentClaim:
    """Tracks which agent owns which files/role."""

    agent_id: str
    role: str  # e.g. "backend", "frontend", "tester"
    status: str = "assigned"  # assigned, building, done, failed
    files: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "role": self.role,
            "status": self.status,
            "files": self.files,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentClaim:
        return cls(
            agent_id=data["agent_id"],
            role=data.get("role", "unknown"),
            status=data.get("status", "assigned"),
            files=data.get("files", []),
            started_at=data.get("started_at", 0),
            updated_at=data.get("updated_at", 0),
        )


@dataclass
class Decision:
    """An architecture or design decision recorded in the manifest."""

    what: str
    by: str  # agent_id
    when: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {"what": self.what, "by": self.by, "when": self.when}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Decision:
        return cls(what=data["what"], by=data.get("by", "unknown"), when=data.get("when", 0))


class ProjectManifest:
    """Single source of truth for a project being built by a team.

    Stored as ``PROJECT_MANIFEST.json`` in the project root.
    Thread-safe via file-level read/write (no in-memory caching across agents).
    """

    FILENAME = "PROJECT_MANIFEST.json"

    def __init__(
        self,
        project_name: str,
        project_root: Path,
        architecture: dict[str, str] | None = None,
        ports: dict[str, int] | None = None,
    ):
        self.project_name = project_name
        self.project_root = Path(project_root)
        self.architecture: dict[str, str] = architecture or {}
        self.ports: dict[str, int] = ports or {}
        self.agents: dict[str, AgentClaim] = {}
        self.decisions: list[Decision] = []
        self.created_at: float = time.time()
        self.updated_at: float = time.time()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @property
    def path(self) -> Path:
        return self.project_root / self.FILENAME

    def save(self) -> None:
        """Write manifest to disk."""
        self.updated_at = time.time()
        data = {
            "project_name": self.project_name,
            "project_root": str(self.project_root),
            "architecture": self.architecture,
            "ports": self.ports,
            "agents": {k: v.to_dict() for k, v in self.agents.items()},
            "decisions": [d.to_dict() for d in self.decisions],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        self.project_root.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)
        logger.debug("Manifest saved: {}", self.path)

    @classmethod
    def load(cls, project_root: Path) -> ProjectManifest | None:
        """Load manifest from disk. Returns None if not found."""
        path = project_root / cls.FILENAME
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            m = cls(
                project_name=data["project_name"],
                project_root=Path(data.get("project_root", str(project_root))),
                architecture=data.get("architecture", {}),
                ports=data.get("ports", {}),
            )
            m.agents = {
                k: AgentClaim.from_dict(v) for k, v in data.get("agents", {}).items()
            }
            m.decisions = [Decision.from_dict(d) for d in data.get("decisions", [])]
            m.created_at = data.get("created_at", 0)
            m.updated_at = data.get("updated_at", 0)
            return m
        except Exception as e:
            logger.warning("Failed to load manifest from {}: {}", path, e)
            return None

    # ------------------------------------------------------------------
    # Agent management
    # ------------------------------------------------------------------

    def claim_agent(self, agent_id: str, role: str) -> None:
        """Register an agent as working on a role."""
        self.agents[agent_id] = AgentClaim(agent_id=agent_id, role=role)
        self.save()

    def claim_files(self, agent_id: str, files: list[str]) -> None:
        """Record files owned by an agent."""
        claim = self.agents.get(agent_id)
        if claim:
            for f in files:
                if f not in claim.files:
                    claim.files.append(f)
            claim.updated_at = time.time()
            self.save()

    def update_agent_status(self, agent_id: str, status: str) -> None:
        """Update an agent's status."""
        claim = self.agents.get(agent_id)
        if claim:
            claim.status = status
            claim.updated_at = time.time()
            self.save()

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------

    def record_decision(self, agent_id: str, decision: str) -> None:
        """Record an architecture/design decision."""
        self.decisions.append(Decision(what=decision, by=agent_id))
        self.save()

    # ------------------------------------------------------------------
    # Port management
    # ------------------------------------------------------------------

    def assign_port(self, service: str) -> int:
        """Assign a port for a service. Reuses existing assignment if still free."""
        if service in self.ports:
            # Verify existing assignment is still free
            if self._is_port_free(self.ports[service]):
                return self.ports[service]
            # Port occupied — reassign
            del self.ports[service]

        # Avoid ports already assigned to other services
        used = set(self.ports.values())
        port = self._find_free_port(exclude=used)
        self.ports[service] = port
        self.save()
        return port

    @staticmethod
    def _is_port_free(port: int) -> bool:
        """Check if a TCP port is free on localhost via bind test."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return True
        except OSError:
            return False

    @staticmethod
    def _find_free_port(
        start: int = _DEFAULT_PORT_RANGE[0],
        end: int = _DEFAULT_PORT_RANGE[1],
        exclude: set[int] | None = None,
    ) -> int:
        """Find a free TCP port in the given range."""
        exclude = exclude or set()
        for port in range(start, end):
            if port in exclude:
                continue
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("127.0.0.1", port))
                    return port
            except OSError:
                continue
        # Fallback: let OS pick
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    # ------------------------------------------------------------------
    # Summary for injection into agent prompts
    # ------------------------------------------------------------------

    def summary_for_prompt(self) -> str:
        """Build a concise summary suitable for injecting into an agent's system prompt."""
        lines = [
            f"## Project Manifest: {self.project_name}",
            f"Root: {self.project_root}",
        ]
        if self.architecture:
            lines.append(f"Architecture: {json.dumps(self.architecture)}")
        if self.ports:
            lines.append(f"Ports: {json.dumps(self.ports)}")
        if self.agents:
            lines.append("Agents:")
            for aid, claim in self.agents.items():
                lines.append(f"  - {aid} ({claim.role}): {claim.status}, files={len(claim.files)}")
        if self.decisions:
            lines.append("Decisions:")
            for d in self.decisions[-10:]:
                lines.append(f"  - {d.what} (by {d.by})")
        lines.append(
            "\nIMPORTANT: All file paths must be relative to the project root above. "
            "Do NOT write files outside this directory. "
            "Update the manifest when you create new files or make architecture decisions."
        )
        return "\n".join(lines)

    @classmethod
    def create(cls, project_name: str, project_root: Path) -> ProjectManifest:
        """Create a new manifest and save it to disk."""
        m = cls(project_name=project_name, project_root=project_root)
        m.save()
        logger.info("Created project manifest: {} at {}", project_name, project_root)
        return m
