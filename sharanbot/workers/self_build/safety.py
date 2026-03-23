"""Safety boundaries for self-modification — prevents the agent from
modifying critical safety, config, and permissions code.

Uses glob-pattern file protection lists. The self-build agent loop
checks every proposed file write against these boundaries.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

from loguru import logger


# ── Protected patterns — NEVER modifiable by self-build ───────────────

PROTECTED_PATTERNS: list[str] = [
    # Safety system itself
    "sharanbot/workers/self_build/safety.py",
    # Security module
    "sharanbot/security/*.py",
    # Configuration schema / validation
    "sharanbot/config/schema.py",
    "sharanbot/config/validation.py",
    # Vault / secrets
    "sharanbot/vault/*.py",
    # Permissions / auth
    "**/permissions.py",
    "**/auth.py",
    "**/auth_*.py",
    # Sandbox / isolation
    "**/sandbox.py",
    "**/sandbox_*.py",
    "sharanbot/tools/exec_sandbox.py",
    # License and legal
    "LICENSE",
    "LICENSE.*",
    # Git internals
    ".git/**",
    # Environment files
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.cert",
]

# ── Restricted patterns — modifiable but with extra caution ───────────

RESTRICTED_PATTERNS: list[str] = [
    # Core agent loop
    "sharanbot/agent/loop.py",
    # Main entry points
    "sharanbot/__main__.py",
    "sharanbot/__init__.py",
    # Tool base class
    "sharanbot/agent/tools/base.py",
    # Test infrastructure
    "conftest.py",
    "**/conftest.py",
    # Build / CI config
    "pyproject.toml",
    "Dockerfile",
    "docker-compose.yml",
    ".github/**",
]


class SafetyBoundary:
    """Enforces file protection rules for self-modification."""

    def __init__(self, workspace: Path | None = None,
                 extra_protected: list[str] | None = None,
                 extra_restricted: list[str] | None = None):
        self._workspace = workspace or Path(".")
        self._protected = PROTECTED_PATTERNS + (extra_protected or [])
        self._restricted = RESTRICTED_PATTERNS + (extra_restricted or [])

    def is_protected(self, file_path: str | Path) -> bool:
        """Check if a file is fully protected (cannot be modified)."""
        rel = self._relative(file_path)
        return self._matches_any(rel, self._protected)

    def is_restricted(self, file_path: str | Path) -> bool:
        """Check if a file requires extra caution."""
        rel = self._relative(file_path)
        return self._matches_any(rel, self._restricted)

    def check_write(self, file_path: str | Path) -> tuple[bool, str]:
        """Check if a file write is allowed.

        Returns:
            (allowed, reason) tuple.
        """
        rel = self._relative(file_path)

        if self.is_protected(rel):
            reason = f"BLOCKED: {rel} is protected by safety boundary"
            logger.warning(reason)
            return False, reason

        if self.is_restricted(rel):
            reason = f"CAUTION: {rel} is restricted — modification allowed but flagged"
            logger.info(reason)
            return True, reason

        return True, ""

    def check_batch(self, file_paths: list[str | Path]) -> dict[str, tuple[bool, str]]:
        """Check multiple file writes at once."""
        return {str(fp): self.check_write(fp) for fp in file_paths}

    def filter_allowed(self, file_paths: list[str | Path]) -> list[str]:
        """Filter to only allowed file paths."""
        return [str(fp) for fp in file_paths if self.check_write(fp)[0]]

    def get_protected_files(self) -> list[str]:
        """Return list of protected patterns for display."""
        return self._protected[:]

    def get_restricted_files(self) -> list[str]:
        """Return list of restricted patterns for display."""
        return self._restricted[:]

    def _relative(self, file_path: str | Path) -> str:
        """Convert to relative path string."""
        p = Path(file_path)
        try:
            return str(p.relative_to(self._workspace)).replace("\\", "/")
        except ValueError:
            return str(p).replace("\\", "/")

    def _matches_any(self, rel_path: str, patterns: list[str]) -> bool:
        """Check if path matches any glob pattern."""
        for pattern in patterns:
            if fnmatch.fnmatch(rel_path, pattern):
                return True
            # Also check with forward slashes normalized
            if fnmatch.fnmatch(rel_path.replace("\\", "/"), pattern):
                return True
        return False
