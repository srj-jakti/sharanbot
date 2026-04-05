"""PackageManagerTool — unified dependency management.

Auto-detects package manager from project files and provides install, add,
remove, list, outdated, and audit operations.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

from sharanbot.agent.tools.base import Tool


def detect_package_manager(project_path: Path) -> tuple[str, str]:
    """Detect package manager and ecosystem from project files.

    Returns (manager_name, ecosystem) e.g. ("npm", "node"), ("pip", "python").
    """
    # Node ecosystem
    if (project_path / "pnpm-lock.yaml").exists():
        return "pnpm", "node"
    if (project_path / "yarn.lock").exists():
        return "yarn", "node"
    if (project_path / "bun.lockb").exists():
        return "bun", "node"
    if (project_path / "package-lock.json").exists() or (project_path / "package.json").exists():
        return "npm", "node"

    # Python ecosystem
    if (project_path / "uv.lock").exists():
        return "uv", "python"
    if (project_path / "poetry.lock").exists() or (project_path / "pyproject.toml").exists():
        # Check if it's poetry or pip/uv
        pyproject = project_path / "pyproject.toml"
        if pyproject.exists():
            content = pyproject.read_text(errors="ignore")
            if "[tool.poetry]" in content:
                return "poetry", "python"
        return "pip", "python"
    if (project_path / "requirements.txt").exists():
        return "pip", "python"

    # Flutter/Dart
    if (project_path / "pubspec.yaml").exists():
        return "flutter", "dart"

    # Rust
    if (project_path / "Cargo.toml").exists():
        return "cargo", "rust"

    # Go
    if (project_path / "go.mod").exists():
        return "go", "go"

    return "unknown", "unknown"


# Command templates per package manager
_COMMANDS: dict[str, dict[str, str]] = {
    "npm": {
        "install": "npm install",
        "add": "npm install {packages}",
        "add_dev": "npm install --save-dev {packages}",
        "remove": "npm uninstall {packages}",
        "list": "npm list --depth=0",
        "outdated": "npm outdated",
        "audit": "npm audit",
        "run": "npm run {script}",
    },
    "yarn": {
        "install": "yarn install",
        "add": "yarn add {packages}",
        "add_dev": "yarn add --dev {packages}",
        "remove": "yarn remove {packages}",
        "list": "yarn list --depth=0",
        "outdated": "yarn outdated",
        "audit": "yarn audit",
        "run": "yarn {script}",
    },
    "pnpm": {
        "install": "pnpm install",
        "add": "pnpm add {packages}",
        "add_dev": "pnpm add -D {packages}",
        "remove": "pnpm remove {packages}",
        "list": "pnpm list --depth=0",
        "outdated": "pnpm outdated",
        "audit": "pnpm audit",
        "run": "pnpm {script}",
    },
    "bun": {
        "install": "bun install",
        "add": "bun add {packages}",
        "add_dev": "bun add -d {packages}",
        "remove": "bun remove {packages}",
        "list": "bun pm ls",
        "outdated": "bun outdated",
        "audit": "echo 'bun audit not available'",
        "run": "bun run {script}",
    },
    "pip": {
        "install": "pip install -r requirements.txt",
        "add": "pip install {packages}",
        "add_dev": "pip install {packages}",
        "remove": "pip uninstall -y {packages}",
        "list": "pip list",
        "outdated": "pip list --outdated",
        "audit": "pip audit 2>/dev/null || echo 'pip-audit not installed'",
        "run": "python -m {script}",
    },
    "uv": {
        "install": "uv sync",
        "add": "uv add {packages}",
        "add_dev": "uv add --dev {packages}",
        "remove": "uv remove {packages}",
        "list": "uv pip list",
        "outdated": "uv pip list --outdated",
        "audit": "echo 'uv audit not available yet'",
        "run": "uv run {script}",
    },
    "poetry": {
        "install": "poetry install",
        "add": "poetry add {packages}",
        "add_dev": "poetry add --group dev {packages}",
        "remove": "poetry remove {packages}",
        "list": "poetry show",
        "outdated": "poetry show --outdated",
        "audit": "echo 'poetry audit not available'",
        "run": "poetry run {script}",
    },
    "flutter": {
        "install": "flutter pub get",
        "add": "flutter pub add {packages}",
        "add_dev": "flutter pub add --dev {packages}",
        "remove": "flutter pub remove {packages}",
        "list": "flutter pub deps",
        "outdated": "flutter pub outdated",
        "audit": "echo 'flutter audit not available'",
        "run": "flutter run",
    },
    "cargo": {
        "install": "cargo build",
        "add": "cargo add {packages}",
        "add_dev": "cargo add --dev {packages}",
        "remove": "cargo remove {packages}",
        "list": "cargo tree --depth 1",
        "outdated": "cargo outdated 2>/dev/null || echo 'cargo-outdated not installed'",
        "audit": "cargo audit 2>/dev/null || echo 'cargo-audit not installed'",
        "run": "cargo run",
    },
    "go": {
        "install": "go mod download",
        "add": "go get {packages}",
        "add_dev": "go get {packages}",
        "remove": "go mod edit -droprequire {packages} && go mod tidy",
        "list": "go list -m all",
        "outdated": "go list -u -m all",
        "audit": "govulncheck ./... 2>/dev/null || echo 'govulncheck not installed'",
        "run": "go run .",
    },
}


class PackageManagerTool(Tool):
    """Unified dependency management — auto-detects npm/yarn/pnpm/pip/uv/poetry/flutter/cargo/go."""

    @property
    def name(self) -> str:
        return "package_manager"

    @property
    def description(self) -> str:
        return (
            "Manage project dependencies. Auto-detects the package manager "
            "(npm, yarn, pnpm, bun, pip, uv, poetry, flutter, cargo, go). "
            "Actions: install, add, remove, list, outdated, audit, run."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["install", "add", "remove", "list", "outdated", "audit", "run", "detect"],
                    "description": "Package manager action.",
                },
                "packages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Package names (for add/remove). E.g. ['react', 'tailwindcss'].",
                },
                "dev": {
                    "type": "boolean",
                    "description": "Install as dev dependency (default: false).",
                },
                "project_path": {
                    "type": "string",
                    "description": "Project directory (defaults to workspace root).",
                },
                "script": {
                    "type": "string",
                    "description": "Script name to run (for 'run' action).",
                },
                "manager": {
                    "type": "string",
                    "description": "Override auto-detected package manager.",
                },
            },
            "required": ["action"],
        }

    async def execute(self, action: str, **kwargs: Any) -> str:
        project_path = kwargs.get("project_path", "")
        root = Path(project_path) if project_path else Path(os.environ.get("SHARANBOT_WORKSPACE", "."))
        if not root.is_absolute():
            workspace = Path(os.environ.get("SHARANBOT_WORKSPACE", "."))
            root = workspace / root

        # Detect or override package manager
        manager_override = kwargs.get("manager", "")
        if manager_override:
            manager, ecosystem = manager_override, "override"
        else:
            manager, ecosystem = detect_package_manager(root)

        if action == "detect":
            return f"Detected: {manager} ({ecosystem}) in {root}"

        if manager == "unknown":
            return (
                f"Could not detect package manager in {root}. "
                "No package.json, pyproject.toml, pubspec.yaml, Cargo.toml, or go.mod found. "
                "Specify manager= manually."
            )

        commands = _COMMANDS.get(manager)
        if not commands:
            return f"No command templates for package manager '{manager}'."

        packages = kwargs.get("packages", [])
        dev = kwargs.get("dev", False)
        script = kwargs.get("script", "")

        # Build command
        if action == "add":
            if not packages:
                return "Error: packages list is required for 'add' action."
            pkg_str = " ".join(packages)
            cmd_key = "add_dev" if dev else "add"
            cmd = commands[cmd_key].format(packages=pkg_str)
        elif action == "remove":
            if not packages:
                return "Error: packages list is required for 'remove' action."
            pkg_str = " ".join(packages)
            cmd = commands["remove"].format(packages=pkg_str)
        elif action == "run":
            if not script:
                return "Error: script name is required for 'run' action."
            cmd = commands["run"].format(script=script)
        elif action in commands:
            cmd = commands[action]
        else:
            return f"Unknown action '{action}' for {manager}."

        # Execute
        logger.info("PackageManager: {} → {}", manager, cmd)
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(root),
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=180)
            output = stdout.decode("utf-8", errors="replace").strip() if stdout else ""

            # Truncate long output
            if len(output) > 5000:
                output = output[:2500] + f"\n\n... ({len(output) - 5000} chars truncated) ...\n\n" + output[-2500:]

            status = "✅" if proc.returncode == 0 else "❌"
            return f"{status} [{manager}] {cmd}\n\n{output}\n\nExit code: {proc.returncode}"
        except asyncio.TimeoutError:
            return f"❌ Command timed out after 180s: {cmd}"
        except Exception as e:
            return f"❌ Error: {e}"
