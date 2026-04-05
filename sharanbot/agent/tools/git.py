"""GitTool — structured git operations for version control.

Provides a dedicated tool for git operations instead of relying on raw exec().
Supports init, status, diff, add, commit, branch, checkout, push, pull, log,
and PR creation stubs.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any

from loguru import logger

from sharanbot.agent.tools.base import Tool


class GitTool(Tool):
    """Structured git operations — init, status, diff, add, commit, branch, log, push, pull."""

    def __init__(self, workspace: Path | None = None):
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "git"

    @property
    def description(self) -> str:
        return (
            "Perform git operations. Actions: init, status, diff, add, commit, "
            "branch, checkout, log, push, pull, stash, stash_pop, reset, tag. "
            "Use this instead of exec for all git operations."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "init", "status", "diff", "add", "commit",
                        "branch", "checkout", "log", "push", "pull",
                        "stash", "stash_pop", "reset", "tag",
                    ],
                    "description": "Git action to perform.",
                },
                "message": {
                    "type": "string",
                    "description": "Commit message (for 'commit' action) or tag message (for 'tag').",
                },
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Files to add/stage (for 'add' action). Use ['.'] for all.",
                },
                "branch_name": {
                    "type": "string",
                    "description": "Branch name (for 'branch', 'checkout' actions).",
                },
                "remote": {
                    "type": "string",
                    "description": "Remote name (default: origin) for push/pull.",
                },
                "lines": {
                    "type": "integer",
                    "description": "Number of log entries to show (default: 10).",
                },
                "working_dir": {
                    "type": "string",
                    "description": "Working directory (defaults to workspace root).",
                },
                "tag_name": {
                    "type": "string",
                    "description": "Tag name (for 'tag' action).",
                },
            },
            "required": ["action"],
        }

    async def execute(self, action: str, **kwargs: Any) -> str:
        cwd = kwargs.get("working_dir") or (str(self._workspace) if self._workspace else os.getcwd())

        dispatch = {
            "init": self._init,
            "status": self._status,
            "diff": self._diff,
            "add": self._add,
            "commit": self._commit,
            "branch": self._branch,
            "checkout": self._checkout,
            "log": self._log,
            "push": self._push,
            "pull": self._pull,
            "stash": self._stash,
            "stash_pop": self._stash_pop,
            "reset": self._reset,
            "tag": self._tag,
        }

        handler = dispatch.get(action)
        if not handler:
            return f"Unknown git action '{action}'. Available: {', '.join(dispatch)}"

        try:
            return await handler(cwd=cwd, **kwargs)
        except Exception as e:
            return f"Git error ({action}): {e}"

    async def _run(self, cmd: str, cwd: str, timeout: int = 30) -> tuple[int, str]:
        """Run a git command and return (returncode, output)."""
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode("utf-8", errors="replace").strip() if stdout else ""
        return proc.returncode or 0, output

    async def _init(self, cwd: str, **_: Any) -> str:
        code, out = await self._run("git init", cwd)
        if code != 0:
            return f"git init failed: {out}"
        # Set default branch and initial config
        await self._run('git config user.email "sharanbot@local"', cwd)
        await self._run('git config user.name "SharanBot"', cwd)
        return f"Initialized git repository in {cwd}\n{out}"

    async def _status(self, cwd: str, **_: Any) -> str:
        code, out = await self._run("git status --short --branch", cwd)
        if code != 0:
            return f"git status failed: {out}"
        if not out.strip():
            return "Working tree clean, nothing to commit."
        return f"Git status:\n{out}"

    async def _diff(self, cwd: str, files: list[str] | None = None, **_: Any) -> str:
        file_args = " ".join(f'"{f}"' for f in files) if files else ""
        code, out = await self._run(f"git diff {file_args}", cwd)
        if not out.strip():
            # Try staged diff
            code2, out2 = await self._run(f"git diff --staged {file_args}", cwd)
            if out2.strip():
                return f"Staged changes:\n{out2[:5000]}"
            return "No changes detected."
        result = out[:5000]
        if len(out) > 5000:
            result += f"\n\n... ({len(out) - 5000} chars truncated)"
        return result

    async def _add(self, cwd: str, files: list[str] | None = None, **_: Any) -> str:
        if not files:
            files = ["."]
        file_args = " ".join(f'"{f}"' for f in files)
        code, out = await self._run(f"git add {file_args}", cwd)
        if code != 0:
            return f"git add failed: {out}"
        return f"Staged: {', '.join(files)}"

    async def _commit(self, cwd: str, message: str = "", **_: Any) -> str:
        if not message:
            return "Error: commit message is required."
        # Sanitize message
        safe_msg = message.replace('"', '\\"').replace("\n", "\\n")
        code, out = await self._run(f'git commit -m "{safe_msg}"', cwd)
        if code != 0:
            return f"git commit failed: {out}"
        return f"Committed: {out}"

    async def _branch(self, cwd: str, branch_name: str = "", **_: Any) -> str:
        if not branch_name:
            # List branches
            code, out = await self._run("git branch -a", cwd)
            return f"Branches:\n{out}" if out else "No branches yet."
        code, out = await self._run(f'git branch "{branch_name}"', cwd)
        if code != 0:
            return f"git branch failed: {out}"
        return f"Created branch: {branch_name}"

    async def _checkout(self, cwd: str, branch_name: str = "", **_: Any) -> str:
        if not branch_name:
            return "Error: branch_name is required for checkout."
        code, out = await self._run(f'git checkout "{branch_name}"', cwd)
        if code != 0:
            # Try creating new branch
            code2, out2 = await self._run(f'git checkout -b "{branch_name}"', cwd)
            if code2 != 0:
                return f"git checkout failed: {out}\n{out2}"
            return f"Created and switched to new branch: {branch_name}"
        return f"Switched to branch: {branch_name}\n{out}"

    async def _log(self, cwd: str, lines: int = 10, **_: Any) -> str:
        n = min(max(lines, 1), 50)
        code, out = await self._run(
            f'git log --oneline --graph --decorate -n {n}', cwd,
        )
        if code != 0:
            return f"git log failed: {out}"
        return f"Recent commits:\n{out}" if out else "No commits yet."

    async def _push(self, cwd: str, remote: str = "origin", branch_name: str = "", **_: Any) -> str:
        branch = branch_name or ""
        if not branch:
            _, branch = await self._run("git branch --show-current", cwd)
            branch = branch.strip()
        code, out = await self._run(f'git push {remote} {branch}', cwd, timeout=60)
        if code != 0:
            return f"git push failed: {out}"
        return f"Pushed to {remote}/{branch}\n{out}"

    async def _pull(self, cwd: str, remote: str = "origin", branch_name: str = "", **_: Any) -> str:
        branch = branch_name or ""
        cmd = f"git pull {remote} {branch}".strip()
        code, out = await self._run(cmd, cwd, timeout=60)
        if code != 0:
            return f"git pull failed: {out}"
        return f"Pulled from {remote}\n{out}"

    async def _stash(self, cwd: str, message: str = "", **_: Any) -> str:
        cmd = "git stash"
        if message:
            cmd += f' push -m "{message}"'
        code, out = await self._run(cmd, cwd)
        return out or "Stashed changes."

    async def _stash_pop(self, cwd: str, **_: Any) -> str:
        code, out = await self._run("git stash pop", cwd)
        return out or "Popped stash."

    async def _reset(self, cwd: str, files: list[str] | None = None, **_: Any) -> str:
        if files:
            file_args = " ".join(f'"{f}"' for f in files)
            code, out = await self._run(f"git reset HEAD {file_args}", cwd)
        else:
            code, out = await self._run("git reset HEAD", cwd)
        return out or "Reset staging area."

    async def _tag(self, cwd: str, tag_name: str = "", message: str = "", **_: Any) -> str:
        if not tag_name:
            code, out = await self._run("git tag -l", cwd)
            return f"Tags:\n{out}" if out else "No tags."
        cmd = f'git tag "{tag_name}"'
        if message:
            cmd = f'git tag -a "{tag_name}" -m "{message}"'
        code, out = await self._run(cmd, cwd)
        if code != 0:
            return f"git tag failed: {out}"
        return f"Created tag: {tag_name}"
