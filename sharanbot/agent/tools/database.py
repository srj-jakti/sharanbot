"""DatabaseTool — schema management and migration operations.

Auto-detects ORM/migration tool from project files and provides
migrate, seed, reset, status, and generate operations.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

from sharanbot.agent.tools.base import Tool


def detect_database_tool(project_path: Path) -> tuple[str, str]:
    """Detect database/ORM tool from project files.

    Returns (tool_name, ecosystem) e.g. ("prisma", "node"), ("alembic", "python").
    """
    # Prisma (Node)
    if (project_path / "prisma" / "schema.prisma").exists():
        return "prisma", "node"

    # Drizzle (Node)
    for cfg in ("drizzle.config.ts", "drizzle.config.js"):
        if (project_path / cfg).exists():
            return "drizzle", "node"

    # TypeORM (Node)
    for cfg in ("ormconfig.json", "ormconfig.js", "ormconfig.ts"):
        if (project_path / cfg).exists():
            return "typeorm", "node"

    # Check package.json deps
    pkg_json = project_path / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text())
            all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            if "prisma" in all_deps:
                return "prisma", "node"
            if "drizzle-orm" in all_deps:
                return "drizzle", "node"
            if "typeorm" in all_deps:
                return "typeorm", "node"
            if "knex" in all_deps:
                return "knex", "node"
            if "sequelize" in all_deps:
                return "sequelize", "node"
        except Exception:
            pass

    # Django (Python)
    manage_py = project_path / "manage.py"
    if manage_py.exists():
        content = manage_py.read_text(errors="ignore")
        if "DJANGO_SETTINGS_MODULE" in content:
            return "django", "python"

    # Alembic (Python)
    if (project_path / "alembic.ini").exists() or (project_path / "alembic").is_dir():
        return "alembic", "python"

    # SQLAlchemy check via pyproject.toml
    pyproject = project_path / "pyproject.toml"
    if pyproject.exists():
        content = pyproject.read_text(errors="ignore")
        if "sqlalchemy" in content.lower() or "alembic" in content.lower():
            return "alembic", "python"

    # Tortoise ORM (Python)
    if pyproject.exists() and "tortoise" in pyproject.read_text(errors="ignore").lower():
        return "tortoise", "python"

    # Flutter/Dart — drift or sqflite
    pubspec = project_path / "pubspec.yaml"
    if pubspec.exists():
        content = pubspec.read_text(errors="ignore")
        if "drift" in content:
            return "drift", "dart"
        if "sqflite" in content:
            return "sqflite", "dart"

    # Rust — diesel or sqlx
    cargo = project_path / "Cargo.toml"
    if cargo.exists():
        content = cargo.read_text(errors="ignore")
        if "diesel" in content:
            return "diesel", "rust"
        if "sqlx" in content:
            return "sqlx", "rust"

    # Go — goose or golang-migrate
    if (project_path / "go.mod").exists():
        content = (project_path / "go.mod").read_text(errors="ignore")
        if "goose" in content:
            return "goose", "go"
        if "golang-migrate" in content or "migrate" in content:
            return "golang-migrate", "go"

    return "unknown", "unknown"


# Command templates per database tool
_DB_COMMANDS: dict[str, dict[str, str]] = {
    "prisma": {
        "migrate": "npx prisma migrate dev --name {name}",
        "migrate_deploy": "npx prisma migrate deploy",
        "generate": "npx prisma generate",
        "seed": "npx prisma db seed",
        "reset": "npx prisma migrate reset --force",
        "status": "npx prisma migrate status",
        "studio": "npx prisma studio",
        "push": "npx prisma db push",
        "pull": "npx prisma db pull",
        "format": "npx prisma format",
    },
    "drizzle": {
        "migrate": "npx drizzle-kit generate",
        "migrate_deploy": "npx drizzle-kit migrate",
        "push": "npx drizzle-kit push",
        "pull": "npx drizzle-kit introspect",
        "status": "npx drizzle-kit check",
        "studio": "npx drizzle-kit studio",
    },
    "django": {
        "migrate": "python manage.py makemigrations && python manage.py migrate",
        "migrate_deploy": "python manage.py migrate",
        "seed": "python manage.py loaddata {name}",
        "reset": "python manage.py flush --no-input",
        "status": "python manage.py showmigrations",
        "shell": "python manage.py dbshell",
        "create_superuser": "python manage.py createsuperuser --noinput",
    },
    "alembic": {
        "migrate": 'alembic revision --autogenerate -m "{name}"',
        "migrate_deploy": "alembic upgrade head",
        "status": "alembic current",
        "history": "alembic history",
        "reset": "alembic downgrade base",
        "downgrade": "alembic downgrade -1",
    },
    "typeorm": {
        "migrate": "npx typeorm migration:generate -n {name}",
        "migrate_deploy": "npx typeorm migration:run",
        "status": "npx typeorm migration:show",
        "reset": "npx typeorm migration:revert",
    },
    "knex": {
        "migrate": "npx knex migrate:make {name}",
        "migrate_deploy": "npx knex migrate:latest",
        "seed": "npx knex seed:run",
        "status": "npx knex migrate:status",
        "reset": "npx knex migrate:rollback --all",
    },
    "sequelize": {
        "migrate": "npx sequelize-cli migration:generate --name {name}",
        "migrate_deploy": "npx sequelize-cli db:migrate",
        "seed": "npx sequelize-cli db:seed:all",
        "status": "npx sequelize-cli db:migrate:status",
        "reset": "npx sequelize-cli db:migrate:undo:all",
    },
    "diesel": {
        "migrate": "diesel migration generate {name}",
        "migrate_deploy": "diesel migration run",
        "status": "diesel migration list",
        "reset": "diesel migration revert",
        "setup": "diesel setup",
    },
    "goose": {
        "migrate": "goose create {name} sql",
        "migrate_deploy": "goose up",
        "status": "goose status",
        "reset": "goose reset",
        "downgrade": "goose down",
    },
    "golang-migrate": {
        "migrate": "migrate create -ext sql -dir migrations {name}",
        "migrate_deploy": "migrate up",
        "status": "migrate version",
        "reset": "migrate down",
    },
}


class DatabaseTool(Tool):
    """Database schema management — migrations, seeds, resets, status."""

    @property
    def name(self) -> str:
        return "database"

    @property
    def description(self) -> str:
        return (
            "Manage database schema and migrations. Auto-detects ORM/migration tool "
            "(Prisma, Drizzle, Django, Alembic, TypeORM, Knex, Sequelize, Diesel, Goose). "
            "Actions: migrate (create migration), deploy (run migrations), seed, reset, "
            "status, detect, push, pull, generate."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["migrate", "deploy", "seed", "reset", "status",
                             "detect", "push", "pull", "generate", "studio"],
                    "description": "Database action to perform.",
                },
                "project_path": {
                    "type": "string",
                    "description": "Project directory (defaults to workspace root).",
                },
                "migration_name": {
                    "type": "string",
                    "description": "Name for the migration (for 'migrate' action).",
                },
                "tool": {
                    "type": "string",
                    "description": "Override auto-detected database tool.",
                },
            },
            "required": ["action"],
        }

    async def execute(self, action: str, **kwargs: Any) -> str:
        project_path = kwargs.get("project_path", "")
        root = Path(project_path) if project_path else Path(os.environ.get("SHARANBOT_WORKSPACE", "."))
        if not root.is_absolute():
            root = Path(os.environ.get("SHARANBOT_WORKSPACE", ".")) / root

        tool_override = kwargs.get("tool", "")
        if tool_override:
            db_tool, ecosystem = tool_override, "override"
        else:
            db_tool, ecosystem = detect_database_tool(root)

        if action == "detect":
            if db_tool == "unknown":
                return f"No database tool detected in {root}."
            return f"Detected: {db_tool} ({ecosystem}) in {root}"

        if db_tool == "unknown":
            return (
                "Could not detect database/ORM tool. No prisma/schema.prisma, "
                "alembic.ini, manage.py (Django), Cargo.toml with diesel, or "
                "similar config found. Specify tool= manually."
            )

        commands = _DB_COMMANDS.get(db_tool, {})
        if not commands:
            return f"No command templates for database tool '{db_tool}'."

        migration_name = kwargs.get("migration_name", "migration")

        # Map action to command
        action_map = {
            "migrate": "migrate",
            "deploy": "migrate_deploy",
            "seed": "seed",
            "reset": "reset",
            "status": "status",
            "push": "push",
            "pull": "pull",
            "generate": "generate",
            "studio": "studio",
        }

        cmd_key = action_map.get(action, action)
        cmd_template = commands.get(cmd_key)
        if not cmd_template:
            available = ", ".join(commands.keys())
            return f"Action '{action}' not available for {db_tool}. Available: {available}"

        cmd = cmd_template.format(name=migration_name)

        logger.info("Database: [{}] {}", db_tool, cmd)

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(root),
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
            output = stdout.decode("utf-8", errors="replace").strip() if stdout else ""

            if len(output) > 5000:
                output = output[:2500] + "\n\n... (truncated) ...\n\n" + output[-2500:]

            status = "✅" if proc.returncode == 0 else "❌"
            return f"{status} [{db_tool}] {cmd}\n\n{output}\n\nExit code: {proc.returncode}"
        except asyncio.TimeoutError:
            return f"❌ Command timed out after 120s: {cmd}"
        except Exception as e:
            return f"❌ Error: {e}"
