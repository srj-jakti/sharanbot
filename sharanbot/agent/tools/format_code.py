"""FormatTool — auto-detect and run code formatters/linters.

Detects formatter from project config files (.prettierrc, pyproject.toml,
.eslintrc, etc.) and runs it on specified files or the whole project.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

from sharanbot.agent.tools.base import Tool


def detect_formatters(project_path: Path) -> list[dict[str, str]]:
    """Detect available formatters from project config files.

    Returns list of {"name": ..., "command": ..., "config": ...}.
    """
    found: list[dict[str, str]] = []

    # Python formatters
    pyproject = project_path / "pyproject.toml"
    if pyproject.exists():
        content = pyproject.read_text(errors="ignore")
        if "[tool.ruff" in content:
            found.append({"name": "ruff", "command": "ruff format", "lint": "ruff check --fix", "config": "pyproject.toml"})
        elif "[tool.black" in content:
            found.append({"name": "black", "command": "black .", "config": "pyproject.toml"})
        if "[tool.isort" in content:
            found.append({"name": "isort", "command": "isort .", "config": "pyproject.toml"})
    if (project_path / ".flake8").exists() or (project_path / "setup.cfg").exists():
        found.append({"name": "flake8", "command": "flake8", "config": ".flake8"})
    if (project_path / "mypy.ini").exists() or (pyproject.exists() and "[tool.mypy" in pyproject.read_text(errors="ignore")):
        found.append({"name": "mypy", "command": "mypy .", "config": "mypy.ini"})

    # Node/JS formatters
    for prettier_cfg in (".prettierrc", ".prettierrc.json", ".prettierrc.js",
                         ".prettierrc.yaml", ".prettierrc.yml", "prettier.config.js"):
        if (project_path / prettier_cfg).exists():
            found.append({"name": "prettier", "command": "npx prettier --write .", "config": prettier_cfg})
            break
    # Check package.json for prettier
    pkg_json = project_path / "package.json"
    if pkg_json.exists() and not any(f["name"] == "prettier" for f in found):
        try:
            pkg = json.loads(pkg_json.read_text())
            if "prettier" in pkg:
                found.append({"name": "prettier", "command": "npx prettier --write .", "config": "package.json"})
            all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            if "prettier" in all_deps and not any(f["name"] == "prettier" for f in found):
                found.append({"name": "prettier", "command": "npx prettier --write .", "config": "package.json"})
        except Exception:
            pass

    for eslint_cfg in (".eslintrc", ".eslintrc.json", ".eslintrc.js", ".eslintrc.yml",
                       "eslint.config.js", "eslint.config.mjs"):
        if (project_path / eslint_cfg).exists():
            found.append({"name": "eslint", "command": "npx eslint --fix .", "config": eslint_cfg})
            break

    # Rust
    if (project_path / "Cargo.toml").exists():
        found.append({"name": "rustfmt", "command": "cargo fmt", "config": "Cargo.toml"})
        found.append({"name": "clippy", "command": "cargo clippy --fix --allow-dirty", "config": "Cargo.toml"})

    # Go
    if (project_path / "go.mod").exists():
        found.append({"name": "gofmt", "command": "gofmt -w .", "config": "go.mod"})
        found.append({"name": "go vet", "command": "go vet ./...", "config": "go.mod"})

    # Dart/Flutter
    if (project_path / "pubspec.yaml").exists():
        found.append({"name": "dart format", "command": "dart format .", "config": "pubspec.yaml"})
        found.append({"name": "dart analyze", "command": "dart analyze", "config": "pubspec.yaml"})

    # Fallback: if nothing detected but Python files exist
    if not found:
        py_files = list(project_path.glob("*.py")) + list(project_path.glob("**/*.py"))
        if py_files:
            found.append({"name": "ruff", "command": "ruff format", "lint": "ruff check --fix", "config": "(auto-detected Python)"})

    return found


class FormatTool(Tool):
    """Auto-detect and run code formatters and linters."""

    @property
    def name(self) -> str:
        return "format_code"

    @property
    def description(self) -> str:
        return (
            "Format and lint code. Auto-detects formatters from project config "
            "(prettier, eslint, ruff, black, isort, rustfmt, gofmt, dart format). "
            "Actions: format (run formatter), lint (run linter), detect (show detected tools), check (dry-run)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["format", "lint", "detect", "check"],
                    "description": "Action: format, lint, detect, or check (dry-run).",
                },
                "project_path": {
                    "type": "string",
                    "description": "Project directory (defaults to workspace root).",
                },
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific files to format (default: entire project).",
                },
                "formatter": {
                    "type": "string",
                    "description": "Override auto-detected formatter (prettier, ruff, black, eslint, etc.).",
                },
            },
            "required": ["action"],
        }

    async def execute(self, action: str, **kwargs: Any) -> str:
        project_path = kwargs.get("project_path", "")
        root = Path(project_path) if project_path else Path(os.environ.get("SHARANBOT_WORKSPACE", "."))
        if not root.is_absolute():
            root = Path(os.environ.get("SHARANBOT_WORKSPACE", ".")) / root

        formatters = detect_formatters(root)

        if action == "detect":
            if not formatters:
                return f"No formatters detected in {root}."
            lines = [f"Detected formatters in {root}:"]
            for f in formatters:
                lines.append(f"  - {f['name']}: {f['command']} (config: {f['config']})")
            return "\n".join(lines)

        if not formatters:
            return (
                f"No formatters detected in {root}. "
                "Add a .prettierrc, pyproject.toml with [tool.ruff], or similar config file."
            )

        formatter_override = kwargs.get("formatter", "")
        files = kwargs.get("files", [])
        results: list[str] = []

        for fmt in formatters:
            if formatter_override and fmt["name"] != formatter_override:
                continue

            if action == "format":
                cmd = fmt["command"]
                if files:
                    cmd = f"{cmd.split()[0]} {' '.join(files)}"
            elif action == "lint":
                cmd = fmt.get("lint", fmt["command"])
                if files:
                    base = cmd.split()[0]
                    cmd = f"{base} {' '.join(files)}"
            elif action == "check":
                cmd = fmt["command"]
                # Add dry-run flags
                if "prettier" in fmt["name"]:
                    cmd = cmd.replace("--write", "--check")
                elif "ruff format" in cmd:
                    cmd += " --check"
                elif "black" in fmt["name"]:
                    cmd += " --check"
                elif "cargo fmt" in cmd:
                    cmd += " -- --check"
            else:
                return f"Unknown action '{action}'. Use: format, lint, detect, check."

            code, output = await self._run(cmd, str(root))
            status = "✅" if code == 0 else "⚠️"
            results.append(f"{status} [{fmt['name']}] {cmd}\n{output[-1000:]}")

        if not results:
            if formatter_override:
                return f"Formatter '{formatter_override}' not found. Detected: {', '.join(f['name'] for f in formatters)}"
            return "No formatters matched."

        return "\n\n".join(results)

    async def _run(self, cmd: str, cwd: str, timeout: int = 60) -> tuple[int, str]:
        """Run a command and return (returncode, output)."""
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = stdout.decode("utf-8", errors="replace").strip() if stdout else ""
            return proc.returncode or 0, output
        except asyncio.TimeoutError:
            return 1, f"Command timed out after {timeout}s"
        except Exception as e:
            return 1, f"Error: {e}"
