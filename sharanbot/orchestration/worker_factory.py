"""Worker factory — creates worker assignments with isolated worktrees.

Generates unique worker IDs, assigns system prompts via the role prompt
library, and creates isolated worktree paths for git isolation.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sharanbot.orchestration.role_prompts import Role, RolePromptLibrary


@dataclass
class WorkerAssignment:
    worker_id: str
    role: Role
    team: str
    task: str
    system_prompt: str
    worktree_path: str = ""
    status: str = "pending"  # "pending", "running", "completed", "failed"
    result: str = ""


class WorkerFactory:
    """Creates WorkerAssignment records with unique IDs and role prompts."""

    def __init__(self, workspace: Path):
        self._workspace = workspace

    def create_worker(
        self,
        role: Role,
        team: str,
        task: str,
        tech_stack: str = "",
        ownership_section: str = "",
        contracts: str = "",
        shared_memory_context: str = "",
    ) -> WorkerAssignment:
        """Create a single worker assignment."""
        worker_id = f"w-{secrets.token_hex(6)}"
        worktree_path = str(self._workspace / ".dev-team" / worker_id)

        # Avoid collision
        wp = Path(worktree_path)
        if wp.exists():
            worker_id = f"w-{secrets.token_hex(6)}-{secrets.token_hex(2)}"
            worktree_path = str(self._workspace / ".dev-team" / worker_id)

        system_prompt = RolePromptLibrary.get_prompt(
            role=role,
            tech_stack=tech_stack,
            ownership_section=ownership_section,
            contracts=contracts,
            shared_memory_context=shared_memory_context,
        )

        return WorkerAssignment(
            worker_id=worker_id,
            role=role,
            team=team,
            task=task,
            system_prompt=system_prompt,
            worktree_path=worktree_path,
        )

    def spawn_team(
        self,
        team_config: list[dict[str, Any]],
        tech_stack: str = "",
        contracts: str = "",
        shared_memory_context: str = "",
    ) -> list[WorkerAssignment]:
        """Create a team of workers from a config list.

        Each config dict should have: role (str), team (str), task (str),
        and optionally ownership_section (str).
        """
        workers = []
        for cfg in team_config:
            role_str = cfg.get("role", "full_stack_dev")
            try:
                role = Role(role_str)
            except ValueError:
                role = Role.FULL_STACK_DEV

            worker = self.create_worker(
                role=role,
                team=cfg.get("team", "default"),
                task=cfg.get("task", ""),
                tech_stack=tech_stack,
                ownership_section=cfg.get("ownership_section", ""),
                contracts=contracts,
                shared_memory_context=shared_memory_context,
            )
            workers.append(worker)
        return workers
