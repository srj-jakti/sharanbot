"""Git-based automatic rollback for self-build modifications.

Creates a checkpoint branch before modifications, runs validation,
and automatically rolls back to the checkpoint if tests fail.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class RollbackCheckpoint:
    """A saved point we can roll back to."""
    checkpoint_id: str
    branch_name: str
    commit_hash: str
    timestamp: float = 0.0
    description: str = ""
    files_modified: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()


class GitRollback:
    """Manages git-based rollback for self-modification safety."""

    CHECKPOINT_PREFIX = "sharanbot-checkpoint"

    def __init__(self, workspace: Path | None = None):
        self._workspace = workspace or Path(".")
        self._checkpoints: list[RollbackCheckpoint] = []
        self._current_branch: str = ""

    async def create_checkpoint(self, description: str = "",
                                 files: list[str] | None = None) -> RollbackCheckpoint | None:
        """Create a checkpoint before modifications."""
        # Get current branch
        self._current_branch = await self._git("rev-parse", "--abbrev-ref", "HEAD")
        if not self._current_branch:
            logger.warning("Not in a git repository")
            return None

        # Get current commit
        commit = await self._git("rev-parse", "HEAD")
        if not commit:
            return None

        # Stage and commit any uncommitted changes
        status = await self._git("status", "--porcelain")
        if status.strip():
            await self._git("add", "-A")
            await self._git("commit", "-m", f"[sharanbot] checkpoint: {description or 'pre-modification'}")
            commit = await self._git("rev-parse", "HEAD")

        checkpoint_id = f"{self.CHECKPOINT_PREFIX}-{int(time.time())}"
        branch_name = f"{self.CHECKPOINT_PREFIX}/{checkpoint_id}"

        # Create checkpoint branch (just a tag/branch at current commit)
        await self._git("branch", branch_name, commit)

        checkpoint = RollbackCheckpoint(
            checkpoint_id=checkpoint_id,
            branch_name=branch_name,
            commit_hash=commit.strip(),
            description=description,
            files_modified=files or [],
        )
        self._checkpoints.append(checkpoint)
        logger.info("Created checkpoint: {} at {}", checkpoint_id, commit[:8])
        return checkpoint

    async def rollback(self, checkpoint: RollbackCheckpoint | None = None) -> bool:
        """Roll back to a checkpoint."""
        if checkpoint is None:
            if not self._checkpoints:
                logger.warning("No checkpoints to roll back to")
                return False
            checkpoint = self._checkpoints[-1]

        logger.warning("Rolling back to checkpoint: {}", checkpoint.checkpoint_id)

        # Reset to checkpoint commit
        result = await self._git("reset", "--hard", checkpoint.commit_hash)
        if "HEAD is now at" not in result and not result.strip():
            # Verify HEAD matches
            head = await self._git("rev-parse", "HEAD")
            if head.strip() != checkpoint.commit_hash.strip():
                logger.error("Rollback verification failed")
                return False

        # Clean untracked files created after checkpoint
        await self._git("clean", "-fd")

        logger.info("Rolled back to {}", checkpoint.commit_hash[:8])
        return True

    async def rollback_if_tests_fail(self, checkpoint: RollbackCheckpoint,
                                      test_command: str = "python -m pytest tests/ -q --tb=no") -> bool:
        """Run tests and rollback if they fail."""
        try:
            proc = await asyncio.create_subprocess_shell(
                test_command,
                cwd=str(self._workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)

            if proc.returncode == 0:
                logger.info("Tests passed — keeping modifications")
                return True

            logger.warning("Tests failed — rolling back")
            await self.rollback(checkpoint)
            return False

        except asyncio.TimeoutError:
            logger.warning("Tests timed out — rolling back")
            await self.rollback(checkpoint)
            return False

    async def cleanup_checkpoints(self, keep_last: int = 5):
        """Remove old checkpoint branches."""
        if len(self._checkpoints) <= keep_last:
            return

        old = self._checkpoints[:-keep_last]
        for cp in old:
            await self._git("branch", "-D", cp.branch_name)
            logger.debug("Removed checkpoint branch: {}", cp.branch_name)

        self._checkpoints = self._checkpoints[-keep_last:]

    @property
    def latest_checkpoint(self) -> RollbackCheckpoint | None:
        return self._checkpoints[-1] if self._checkpoints else None

    async def _git(self, *args: str) -> str:
        """Run a git command and return stdout."""
        cmd = ["git"] + list(args)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(self._workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            return stdout.decode(errors="replace").strip()
        except (asyncio.TimeoutError, FileNotFoundError, OSError) as e:
            logger.debug("git {} failed: {}", args[0], e)
            return ""
