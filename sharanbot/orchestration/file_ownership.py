"""File ownership tracker — maps files/directories to worker roles.

Ensures workers only modify files within their ownership boundaries
to prevent cross-worker conflicts during parallel development.
"""

from __future__ import annotations

from fnmatch import fnmatch
from typing import Any

from sharanbot.orchestration.role_prompts import Role


# Default ownership patterns by role category
_DEFAULT_OWNERSHIP: dict[str, list[str]] = {
    "frontend": [
        "src/components/**", "src/pages/**", "src/views/**",
        "src/styles/**", "src/assets/**", "public/**",
        "*.css", "*.scss", "*.tsx", "*.jsx",
    ],
    "backend": [
        "src/api/**", "src/routes/**", "src/controllers/**",
        "src/services/**", "src/models/**", "src/middleware/**",
        "server/**", "api/**",
    ],
    "database": [
        "src/models/**", "src/migrations/**", "src/seeds/**",
        "prisma/**", "migrations/**", "db/**",
    ],
    "infrastructure": [
        "Dockerfile*", "docker-compose*", ".github/**",
        "*.yml", "*.yaml", "terraform/**", "k8s/**",
        ".env.example", "scripts/**",
    ],
    "testing": [
        "tests/**", "test/**", "__tests__/**", "*.test.*",
        "*.spec.*", "cypress/**", "e2e/**",
    ],
    "docs": [
        "*.md", "docs/**", "README*", "CHANGELOG*",
    ],
}

# Role to ownership category mapping
_ROLE_OWNERSHIP_MAP: dict[Role, list[str]] = {
    Role.UI_COMPONENT_DEV: ["frontend"],
    Role.FRONTEND_LOGIC_DEV: ["frontend"],
    Role.RESPONSIVE_MOBILE_SPECIALIST: ["frontend"],
    Role.API_DEV: ["backend"],
    Role.DATABASE_DEV: ["database"],
    Role.AUTH_SECURITY_DEV: ["backend"],
    Role.BUSINESS_LOGIC_DEV: ["backend"],
    Role.DEVOPS_ENGINEER: ["infrastructure"],
    Role.CLOUD_ARCHITECT: ["infrastructure"],
    Role.INTEGRATION_DEV: ["backend", "infrastructure"],
    Role.QA_UNIT_TESTER: ["testing"],
    Role.QA_INTEGRATION_TESTER: ["testing"],
    Role.QA_E2E_TESTER: ["testing"],
    Role.SECURITY_TESTER: ["testing"],
    Role.PERFORMANCE_TESTER: ["testing"],
    Role.TECHNICAL_WRITER: ["docs"],
    Role.DEPLOYMENT_ENGINEER: ["infrastructure"],
}


class FileOwnership:
    """Tracks file ownership by role for conflict prevention."""

    def __init__(self, custom_map: dict[str, list[str]] | None = None):
        self._ownership = dict(_DEFAULT_OWNERSHIP)
        if custom_map:
            self._ownership.update(custom_map)

    def get_ownership_for_role(self, role: Role) -> list[str]:
        """Get the file patterns a role is allowed to modify."""
        categories = _ROLE_OWNERSHIP_MAP.get(role, [])
        patterns = []
        for cat in categories:
            patterns.extend(self._ownership.get(cat, []))
        return patterns

    def get_ownership_section(self, role: Role) -> str:
        """Get a formatted ownership section for prompt injection."""
        patterns = self.get_ownership_for_role(role)
        if not patterns:
            return "No specific file boundaries assigned — coordinate with tech lead."
        lines = ["Your file ownership boundaries:"]
        for p in patterns:
            lines.append(f"  - `{p}`")
        return "\n".join(lines)

    def is_allowed(self, role: Role, file_path: str) -> bool:
        """Check if a role is allowed to modify a file."""
        patterns = self.get_ownership_for_role(role)
        if not patterns:
            return True  # No restrictions if no patterns defined
        return any(fnmatch(file_path, p) for p in patterns)

    def to_map(self) -> dict[str, list[str]]:
        """Export ownership map."""
        return dict(self._ownership)
