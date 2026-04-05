"""Spec Kit integration — GitHub's Spec-Driven Development toolkit.

Wraps the `specify` CLI from github/spec-kit to initialize projects with
spec-driven development structure. Automatically invoked by TemplateEngineTool
as a post-scaffold hook for new projects.

Install: uvx --from git+https://github.com/github/spec-kit.git specify --help
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any

from loguru import logger

from sharanbot.agent.tools.base import Tool

# The uvx command to run specify without installing globally
_UVX_SPECIFY = "uvx --from git+https://github.com/github/spec-kit.git specify"


def _find_specify() -> str | None:
    """Find the specify CLI — check PATH first, then uvx."""
    if shutil.which("specify"):
        return "specify"
    if shutil.which("uvx"):
        return _UVX_SPECIFY
    return None


class SpecifyInitTool(Tool):
    """Initialize a project with GitHub Spec Kit for Spec-Driven Development."""

    name = "specify_init"
    description = (
        "Initialize a project with GitHub Spec Kit (Spec-Driven Development). "
        "Creates specifications, plans, and AI agent commands. "
        "Requires 'uvx' (from uv) or 'specify' CLI installed. "
        "Use ai_agent to configure for a specific AI assistant."
    )
    parameters = {
        "type": "object",
        "properties": {
            "project_path": {
                "type": "string",
                "description": (
                    "Path to the project directory. Use '.' for current directory. "
                    "If the directory doesn't exist, it will be created."
                ),
            },
            "ai_agent": {
                "type": "string",
                "enum": [
                    "claude", "copilot", "gemini", "codebuddy",
                    "cursor-agent", "windsurf", "vibe", "generic",
                ],
                "description": "AI assistant to configure for the project (default: generic).",
            },
            "offline": {
                "type": "boolean",
                "description": "Use bundled assets, no network required (default: true).",
            },
        },
        "required": ["project_path"],
    }

    async def execute(self, **kwargs: Any) -> str:
        project_path = kwargs.get("project_path", ".")
        ai_agent = kwargs.get("ai_agent", "generic")
        offline = kwargs.get("offline", True)

        # Resolve path
        root = Path(project_path)
        if not root.is_absolute():
            workspace = Path(os.environ.get("SHARANBOT_WORKSPACE", "."))
            root = workspace / root

        # Find the specify CLI
        specify_cmd = _find_specify()
        if not specify_cmd:
            return (
                "Spec Kit CLI not found. Install it with:\n"
                "  pip install uv  (or: curl -LsSf https://astral.sh/uv/install.sh | sh)\n"
                "Then run: uvx --from git+https://github.com/github/spec-kit.git specify --help\n\n"
                "Or install directly: pip install specify-cli"
            )

        # Build the command
        # If project_path is an existing directory, use '.' inside it
        # Otherwise, specify init creates the directory
        if root.exists() and root.is_dir():
            cmd = f"{specify_cmd} init ."
            cwd = str(root)
        else:
            cmd = f"{specify_cmd} init {root.name}"
            cwd = str(root.parent)
            root.parent.mkdir(parents=True, exist_ok=True)

        if offline:
            cmd += " --offline"

        # Set PYTHONIOENCODING to avoid Windows Unicode issues
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

        logger.info("Running Spec Kit init: {} (cwd={})", cmd, cwd)

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(input=b"\n"),
                timeout=120,
            )
            output = stdout.decode("utf-8", errors="replace") if stdout else ""

            if proc.returncode == 0:
                result = f"✅ Spec Kit initialized at {root}\n"
                if output.strip():
                    # Truncate long output
                    clean = output.strip()[-500:]
                    result += f"\nOutput:\n{clean}\n"
                result += (
                    f"\nNext steps:\n"
                    f"  1. Define your constitution: describe project principles\n"
                    f"  2. Create a spec: describe what you want to build\n"
                    f"  3. Generate plan: break spec into implementation tasks\n"
                    f"  4. Execute tasks: implement each task with AI assistance"
                )
                return result
            else:
                return (
                    f"Spec Kit init failed (exit code {proc.returncode}):\n"
                    f"{output[-800:]}\n\n"
                    f"Try running manually: {cmd}"
                )
        except asyncio.TimeoutError:
            return "Spec Kit init timed out after 120s. Try running manually."
        except Exception as e:
            return f"Spec Kit init error: {e}"


async def auto_init_speckit(project_path: str | Path, ai_agent: str = "generic") -> str | None:
    """Auto-initialize Spec Kit in a newly scaffolded project.

    Called by TemplateEngineTool after scaffolding. Returns None if
    specify CLI is not available (graceful skip).
    """
    if not _find_specify():
        logger.debug("Spec Kit CLI not available, skipping auto-init")
        return None

    tool = SpecifyInitTool()
    try:
        result = await tool.execute(
            project_path=str(project_path),
            ai_agent=ai_agent,
            offline=True,
        )
        return result
    except Exception as e:
        logger.warning("Spec Kit auto-init failed: {}", e)
        return None
