"""Branch manager — git worktree isolation for parallel workers.

Each worker gets an isolated git worktree to prevent file conflicts.
Merges happen in dependency order (Infra→Backend→Frontend→Integration)
with CI gating. Provides conflict detection and automatic cleanup.
"""

from __future__ import annotations

import asyncio
import os
import secrets
from pathlib import Path
from typing import Any

from loguru import logger


# Merge order: infrastructure first, then backend, frontend, integration last
MERGE_ORDER = ["infrastructure", "backend", "frontend", "integration"]


class BranchManager:
    """Manages isolated git worktrees for parallel worker execution."""

    def __init__(self, workspace: Path):
        self._workspace = workspace
        self._worktrees: dict[str, Path] = {}  # worker_id → worktree path

    async def _run_git(self, *args: str, cwd: Path | None = None) -> tuple[int, str, str]:
        """Run a git command asynchronously."""
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=str(cwd or self._workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return (
            proc.returncode or 0,
            stdout.decode("utf-8", errors="replace").strip(),
            stderr.decode("utf-8", errors="replace").strip(),
        )

    async def create_worktree(self, worker_id: str, base_branch: str = "main") -> Path:
        """Create an isolated git worktree for a worker."""
        worktree_dir = self._workspace / ".dev-team" / worker_id
        if worktree_dir.exists():
            # Avoid collision
            suffix = secrets.token_hex(4)
            worktree_dir = self._workspace / ".dev-team" / f"{worker_id}-{suffix}"

        branch_name = f"worker/{worker_id}"

        # Create branch from base
        await self._run_git("branch", branch_name, base_branch)

        # Create worktree
        worktree_dir.parent.mkdir(parents=True, exist_ok=True)
        rc, out, err = await self._run_git(
            "worktree", "add", str(worktree_dir), branch_name,
        )

        if rc != 0:
            logger.warning("Failed to create worktree for {}: {}", worker_id, err)
            # Fallback: just use a subdirectory
            worktree_dir.mkdir(parents=True, exist_ok=True)

        self._worktrees[worker_id] = worktree_dir
        logger.info("Created worktree for {} at {}", worker_id, worktree_dir)
        return worktree_dir

    async def merge_worktree(self, worker_id: str,
                             target_branch: str = "main") -> tuple[bool, str]:
        """CI-gated merge of a worker's branch into the target."""
        worktree = self._worktrees.get(worker_id)
        if not worktree:
            return False, f"No worktree found for {worker_id}"

        branch_name = f"worker/{worker_id}"

        # Run CI before merge
        ci_passed, ci_output = await self.run_ci(branch_name, worktree)
        if not ci_passed:
            return False, f"CI failed:\n{ci_output}"

        # Check for conflicts
        conflicts = await self.detect_conflicts(worker_id, target_branch)
        if conflicts:
            return False, f"Merge conflicts detected:\n{conflicts}"

        # Perform merge
        rc, out, err = await self._run_git(
            "merge", "--no-ff", branch_name,
            "-m", f"Merge worker/{worker_id}",
            cwd=self._workspace,
        )

        if rc != 0:
            # Abort failed merge
            await self._run_git("merge", "--abort", cwd=self._workspace)
            return False, f"Merge failed: {err}"

        return True, "Merged successfully"

    async def merge_in_order(self, worker_ids: list[str],
                             dependency_order: list[str] | None = None,
                             target_branch: str = "main") -> list[tuple[str, bool, str]]:
        """Merge workers in dependency order (Infra→Backend→Frontend→Integration)."""
        order = dependency_order or MERGE_ORDER
        results: list[tuple[str, bool, str]] = []

        # Group workers by their category
        categorized: dict[str, list[str]] = {cat: [] for cat in order}
        uncategorized: list[str] = []

        for wid in worker_ids:
            placed = False
            wid_lower = wid.lower()
            for cat in order:
                if cat in wid_lower:
                    categorized[cat].append(wid)
                    placed = True
                    break
            if not placed:
                uncategorized.append(wid)

        # Merge in order
        for cat in order:
            for wid in categorized[cat]:
                success, msg = await self.merge_worktree(wid, target_branch)
                results.append((wid, success, msg))
                if not success:
                    logger.warning("Merge failed for {} ({}): {}", wid, cat, msg)

        # Merge uncategorized last
        for wid in uncategorized:
            success, msg = await self.merge_worktree(wid, target_branch)
            results.append((wid, success, msg))

        return results

    async def run_ci(self, branch: str, workspace: Path) -> tuple[bool, str]:
        """Run 4-step CI: lint → typecheck → test → build."""
        outputs: list[str] = []

        ci_steps = [
            ("lint", ["python", "-m", "flake8", "--max-line-length=120", "--count", "."]),
            ("typecheck", ["python", "-m", "mypy", "--ignore-missing-imports", "."]),
            ("test", ["python", "-m", "pytest", "-x", "--tb=short", "-q"]),
            ("build", ["python", "-m", "py_compile"]),
        ]

        for step_name, cmd in ci_steps:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd, cwd=str(workspace),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
                rc = proc.returncode or 0

                if rc != 0:
                    output = stdout.decode("utf-8", errors="replace")[:2000]
                    err_output = stderr.decode("utf-8", errors="replace")[:1000]
                    outputs.append(f"❌ {step_name} failed:\n{output}\n{err_output}")
                    return False, "\n".join(outputs)
                else:
                    outputs.append(f"✅ {step_name} passed")
            except asyncio.TimeoutError:
                outputs.append(f"❌ {step_name} timed out (120s)")
                return False, "\n".join(outputs)
            except FileNotFoundError:
                outputs.append(f"⏭️ {step_name} skipped (tool not found)")

        return True, "\n".join(outputs)

    async def detect_conflicts(self, worker_id: str, target_branch: str) -> str:
        """Pre-merge conflict detection."""
        branch_name = f"worker/{worker_id}"
        rc, out, err = await self._run_git(
            "merge-tree",
            target_branch, branch_name,
            cwd=self._workspace,
        )
        if "CONFLICT" in out.upper() or "CONFLICT" in err.upper():
            return out or err
        return ""

    async def cleanup_worktree(self, worker_id: str) -> None:
        """Remove worktree and associated branch."""
        worktree = self._worktrees.pop(worker_id, None)
        if worktree:
            await self._run_git("worktree", "remove", str(worktree), "--force")
            branch_name = f"worker/{worker_id}"
            await self._run_git("branch", "-D", branch_name)
            logger.debug("Cleaned up worktree for {}", worker_id)

    async def cleanup_all(self) -> None:
        """Clean up all worktrees."""
        for wid in list(self._worktrees.keys()):
            await self.cleanup_worktree(wid)
