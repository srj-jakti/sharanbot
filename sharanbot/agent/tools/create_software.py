"""CreateSoftwareTool — scaffold complete projects from a file map.

Accepts a dict of {path: content} entries and writes them all to disk,
creating the full directory structure. Can also generate common boilerplate
(package.json, pyproject.toml, Dockerfile, etc.) from project type hints.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from loguru import logger

from sharanbot.agent.tools.base import Tool


# ── Boilerplate templates ─────────────────────────────────────────────

_GITIGNORE_PYTHON = """\
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
.venv/
venv/
.env
*.sqlite3
.pytest_cache/
.mypy_cache/
"""

_GITIGNORE_NODE = """\
node_modules/
dist/
build/
.env
*.log
coverage/
"""

_PYPROJECT_TEMPLATE = """\
[project]
name = "{name}"
version = "0.1.0"
description = "{description}"
requires-python = ">=3.11"
dependencies = []

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.backends._legacy:_Backend"
"""

_PACKAGE_JSON_TEMPLATE = """\
{{
  "name": "{name}",
  "version": "0.1.0",
  "description": "{description}",
  "main": "index.js",
  "scripts": {{
    "start": "node index.js",
    "dev": "node --watch index.js",
    "test": "echo \\"no tests yet\\""
  }},
  "keywords": [],
  "license": "MIT"
}}
"""

_README_TEMPLATE = """\
# {name}

{description}

## Getting Started

```bash
# Install dependencies
{install_cmd}

# Run
{run_cmd}
```
"""


class CreateSoftwareTool(Tool):
    """Tool for scaffolding complete software projects."""

    @property
    def name(self) -> str:
        return "create_software"

    @property
    def description(self) -> str:
        return (
            "Create a complete software project by writing multiple files at once. "
            "Provide a files dict mapping relative paths to file contents. "
            "Optionally specify project_type for auto-generated boilerplate. "
            "Returns summary of created files."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "project_path": {
                    "type": "string",
                    "description": "Root directory for the project (relative to workspace).",
                },
                "files": {
                    "type": "object",
                    "description": (
                        "Dict mapping relative file paths to their content. "
                        "Example: {\"src/main.py\": \"print('hello')\", \"README.md\": \"# My App\"}"
                    ),
                },
                "project_type": {
                    "type": "string",
                    "enum": ["python", "node", "react", "fastapi", "express", "static", ""],
                    "description": "Optional project type for auto-generating boilerplate.",
                },
                "project_name": {
                    "type": "string",
                    "description": "Project name (defaults to directory name).",
                },
                "project_description": {
                    "type": "string",
                    "description": "Short project description.",
                },
            },
            "required": ["project_path", "files"],
        }

    async def execute(self, **kwargs: Any) -> str:
        project_path = kwargs.get("project_path", "")
        files: dict[str, str] = kwargs.get("files", {})
        project_type = kwargs.get("project_type", "")
        project_name = kwargs.get("project_name", "") or Path(project_path).name
        project_description = kwargs.get("project_description", "")

        if not project_path:
            return "Error: project_path is required"
        if not files:
            return "Error: files dict is required (at least one file)"

        # Resolve project root
        root = Path(project_path)
        if not root.is_absolute():
            workspace = Path(os.environ.get("SHARANBOT_WORKSPACE", "."))
            root = workspace / root

        # Security: prevent path traversal
        try:
            root.resolve()
        except (ValueError, OSError):
            return "Error: invalid project path"

        # Add boilerplate if project_type specified
        if project_type:
            boilerplate = self._generate_boilerplate(
                project_type, project_name, project_description,
            )
            # Don't overwrite user-specified files
            for bp_path, bp_content in boilerplate.items():
                if bp_path not in files:
                    files[bp_path] = bp_content

        # Create all files
        created: list[str] = []
        errors: list[str] = []

        for rel_path, content in sorted(files.items()):
            # Security: prevent path traversal in file paths
            clean_path = Path(rel_path)
            if ".." in clean_path.parts:
                errors.append(f"Skipped (path traversal): {rel_path}")
                continue

            full_path = root / clean_path
            try:
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text(content, encoding="utf-8")
                created.append(rel_path)
            except (OSError, PermissionError) as e:
                errors.append(f"{rel_path}: {e}")

        # Build summary
        lines = [f"Created {len(created)} files in {project_path}:"]
        for f in created:
            lines.append(f"  ✓ {f}")
        if errors:
            lines.append(f"\n{len(errors)} errors:")
            for e in errors:
                lines.append(f"  ✗ {e}")

        logger.info("CreateSoftware: {} files in {}", len(created), project_path)
        return "\n".join(lines)

    def _generate_boilerplate(self, project_type: str, name: str,
                               description: str) -> dict[str, str]:
        """Generate boilerplate files for a project type."""
        desc = description or f"A {project_type} project"
        bp: dict[str, str] = {}

        if project_type in ("python", "fastapi"):
            bp[".gitignore"] = _GITIGNORE_PYTHON
            bp["pyproject.toml"] = _PYPROJECT_TEMPLATE.format(
                name=name, description=desc,
            )
            bp["README.md"] = _README_TEMPLATE.format(
                name=name, description=desc,
                install_cmd="pip install -e .",
                run_cmd="python -m " + name,
            )
            if project_type == "fastapi":
                bp["requirements.txt"] = "fastapi>=0.104\nuvicorn[standard]>=0.24\n"
                if "main.py" not in bp:
                    bp["main.py"] = (
                        'from fastapi import FastAPI\n\n'
                        'app = FastAPI(title="{name}")\n\n'
                        '@app.get("/health")\n'
                        'def health():\n'
                        '    return {{"status": "ok"}}\n'
                    ).format(name=name)

        elif project_type in ("node", "express", "react"):
            bp[".gitignore"] = _GITIGNORE_NODE
            bp["package.json"] = _PACKAGE_JSON_TEMPLATE.format(
                name=name, description=desc,
            )
            install_cmd = "npm install"
            run_cmd = "npm start"
            if project_type == "react":
                run_cmd = "npm run dev"
            bp["README.md"] = _README_TEMPLATE.format(
                name=name, description=desc,
                install_cmd=install_cmd, run_cmd=run_cmd,
            )

        elif project_type == "static":
            bp[".gitignore"] = ".env\n"
            bp["README.md"] = _README_TEMPLATE.format(
                name=name, description=desc,
                install_cmd="# No dependencies",
                run_cmd="# Open index.html in browser",
            )

        return bp
