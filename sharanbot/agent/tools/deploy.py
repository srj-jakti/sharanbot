"""DeployTool — one-command deployment for built applications."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from loguru import logger

from sharanbot.agent.tools.base import Tool


def detect_deploy_target(project_path: Path) -> str | None:
    """Auto-detect the best deployment target from project files."""
    if (project_path / "vercel.json").exists():
        return "vercel"
    if (project_path / "netlify.toml").exists():
        return "netlify"
    if (project_path / "Dockerfile").exists():
        return "docker"
    for n in ("docker-compose.yml", "docker-compose.yaml"):
        if (project_path / n).exists():
            return "docker-compose"
    for n in ("next.config.js", "next.config.mjs"):
        if (project_path / n).exists():
            return "vercel"
    for n in ("vite.config.ts", "vite.config.js"):
        if (project_path / n).exists():
            return "vercel"
    if (project_path / "index.html").exists():
        return "netlify"
    return None


class DeployTool(Tool):
    """Deploy applications via Vercel, Netlify, Docker, or static hosting."""

    @property
    def name(self) -> str:
        return "deploy"

    @property
    def description(self) -> str:
        return (
            "Deploy a project. Auto-detects deployment target (Vercel, Netlify, Docker). "
            "Actions: deploy (push to production), preview (deploy preview/staging), "
            "status (check deployment status), detect (show detected target), build."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["deploy", "preview", "status", "detect", "build"],
                    "description": "Deployment action.",
                },
                "project_path": {
                    "type": "string",
                    "description": "Project directory (defaults to workspace root).",
                },
                "target": {
                    "type": "string",
                    "enum": ["vercel", "netlify", "docker", "docker-compose"],
                    "description": "Override auto-detected deployment target.",
                },
                "production": {
                    "type": "boolean",
                    "description": "Deploy to production (default: false).",
                },
            },
            "required": ["action"],
        }

    async def execute(self, action: str, **kwargs: Any) -> str:
        project_path = kwargs.get("project_path", "")
        root = Path(project_path) if project_path else Path(
            os.environ.get("SHARANBOT_WORKSPACE", "."))
        if not root.is_absolute():
            root = Path(os.environ.get("SHARANBOT_WORKSPACE", ".")) / root

        target = kwargs.get("target", "") or detect_deploy_target(root)
        production = kwargs.get("production", False)

        if action == "detect":
            t = target or "none"
            return f"Detected deployment target: {t}\nProject: {root}"

        if action == "build":
            return await self._build(root)

        if not target:
            return (
                "Could not detect deployment target. No vercel.json, netlify.toml, "
                "Dockerfile, or docker-compose.yml found. Specify target= manually."
            )

        if action in ("deploy", "preview"):
            prod = production if action == "deploy" else False
            return await self._do_deploy(root, target, prod)
        elif action == "status":
            return await self._status(root, target)
        return "Unknown action. Use: deploy, preview, status, detect, build."

    async def _run(self, cmd: str, cwd: str, timeout: int = 120) -> tuple[int, str]:
        """Run a shell command and return (returncode, output)."""
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode("utf-8", errors="replace").strip() if stdout else ""
        return proc.returncode or 0, output

    async def _do_deploy(self, root: Path, target: str, production: bool) -> str:
        """Execute deployment for the given target."""
        if target == "vercel":
            cmd = "npx vercel --yes"
            if production:
                cmd += " --prod"
            code, out = await self._run(cmd, str(root), 180)
            status = "OK" if code == 0 else "FAILED"
            env = "Production" if production else "Preview"
            return f"Vercel deploy {status} ({env})\n{out[-500:]}"

        if target == "netlify":
            cmd = "npx netlify deploy"
            if production:
                cmd += " --prod"
            code, out = await self._run(cmd, str(root), 180)
            status = "OK" if code == 0 else "FAILED"
            return f"Netlify deploy {status}\n{out[-500:]}"

        if target == "docker":
            name = root.name.lower().replace(" ", "-")
            code, out = await self._run(f"docker build -t {name} .", str(root), 300)
            status = "OK" if code == 0 else "FAILED"
            result = f"Docker build {status}\n{out[-300:]}"
            if code == 0:
                result += f"\n\nRun with: docker run -p 3000:3000 {name}"
            return result

        if target == "docker-compose":
            code, out = await self._run("docker compose up -d --build", str(root), 300)
            status = "OK" if code == 0 else "FAILED"
            return f"Docker Compose {status}\n{out[-300:]}"

        return f"Unsupported target: {target}"

    async def _build(self, root: Path) -> str:
        """Run the project build command."""
        if (root / "package.json").exists():
            code, out = await self._run("npm run build", str(root), 180)
            status = "OK" if code == 0 else "FAILED"
            return f"Build {status} (exit code: {code})\n{out[-500:]}"

        if (root / "pyproject.toml").exists():
            code, out = await self._run("python -m build", str(root), 120)
            status = "OK" if code == 0 else "FAILED"
            return f"Python build {status} (exit code: {code})\n{out[-500:]}"

        return "No build configuration found (no package.json or pyproject.toml)."

    async def _status(self, root: Path, target: str) -> str:
        """Check deployment status."""
        if target == "vercel":
            code, out = await self._run("npx vercel ls --limit 5", str(root))
            if code == 0:
                return f"Vercel deployments:\n{out}"
            return f"Could not get status: {out}"

        if target in ("docker", "docker-compose"):
            code, out = await self._run("docker ps", str(root))
            if code == 0:
                return f"Running containers:\n{out}"
            return f"Could not get status: {out}"

        return f"Status check not implemented for {target}."
