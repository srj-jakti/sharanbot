"""Backup manager — automated workspace backups.

Creates timestamped tar.gz archives of the workspace and manages retention
policy (keep last N backups, rotate by age).
"""

from __future__ import annotations

import shutil
import tarfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger


class BackupManager:
    """Creates and manages workspace backups.

    Parameters
    ----------
    workspace:
        Root directory to back up.
    backup_dir:
        Where to store backup archives. Defaults to ``workspace/.backups``.
    max_backups:
        Maximum number of backup archives to retain.
    exclude_patterns:
        Glob patterns to exclude from backups (e.g. ``__pycache__``, ``.git``).
    """

    DEFAULT_EXCLUDES: list[str] = [
        "__pycache__",
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "*.pyc",
        ".pytest_cache",
        ".mypy_cache",
        "dist",
        "build",
        ".backups",
    ]

    def __init__(
        self,
        workspace: Path,
        backup_dir: Path | None = None,
        max_backups: int = 10,
        exclude_patterns: list[str] | None = None,
    ):
        self._workspace = workspace
        self._backup_dir = backup_dir or (workspace / ".backups")
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        self._max_backups = max_backups
        self._excludes = exclude_patterns or self.DEFAULT_EXCLUDES

    def _should_exclude(self, path: Path) -> bool:
        name = path.name
        for pat in self._excludes:
            if "*" in pat:
                if path.match(pat):
                    return True
            elif name == pat:
                return True
        return False

    def create_backup(self, label: str = "") -> Path:
        """Create a compressed backup of the workspace.

        Returns the path to the archive file.
        """
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = f"_{label}" if label else ""
        archive_name = f"backup_{ts}{suffix}.tar.gz"
        archive_path = self._backup_dir / archive_name

        logger.info("Creating backup: {}", archive_path)
        start = time.time()

        with tarfile.open(archive_path, "w:gz") as tar:
            for item in self._workspace.rglob("*"):
                if self._should_exclude(item):
                    continue
                # Don't back up the backups directory itself
                try:
                    rel = item.relative_to(self._backup_dir)
                    continue
                except ValueError:
                    pass
                try:
                    arcname = str(item.relative_to(self._workspace))
                    tar.add(item, arcname=arcname)
                except (PermissionError, OSError) as exc:
                    logger.debug("Skipping {}: {}", item, exc)

        elapsed = time.time() - start
        size_mb = archive_path.stat().st_size / (1024 * 1024)
        logger.info("Backup created: {:.1f} MB in {:.1f}s", size_mb, elapsed)

        self._enforce_retention()
        return archive_path

    def list_backups(self) -> list[dict[str, Any]]:
        """List available backup archives."""
        backups = sorted(self._backup_dir.glob("backup_*.tar.gz"))
        return [
            {
                "path": str(b),
                "name": b.name,
                "size_mb": round(b.stat().st_size / (1024 * 1024), 2),
                "created": datetime.fromtimestamp(b.stat().st_mtime).isoformat(),
            }
            for b in backups
        ]

    def restore_backup(self, archive_path: Path, target: Path | None = None) -> Path:
        """Restore a backup archive to the target directory.

        If target is None, restores to a sibling directory of workspace.
        """
        target = target or self._workspace.parent / f"{self._workspace.name}_restored"
        target.mkdir(parents=True, exist_ok=True)

        logger.info("Restoring backup {} → {}", archive_path, target)
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(target, filter="data")

        return target

    def _enforce_retention(self) -> None:
        """Remove oldest backups that exceed max_backups."""
        backups = sorted(
            self._backup_dir.glob("backup_*.tar.gz"),
            key=lambda p: p.stat().st_mtime,
        )
        while len(backups) > self._max_backups:
            oldest = backups.pop(0)
            oldest.unlink()
            logger.debug("Removed old backup: {}", oldest.name)

    def cleanup_old_backups(self, max_age_days: int = 30) -> int:
        """Remove backups older than max_age_days. Returns count removed."""
        cutoff = time.time() - (max_age_days * 86400)
        removed = 0
        for b in self._backup_dir.glob("backup_*.tar.gz"):
            if b.stat().st_mtime < cutoff:
                b.unlink()
                removed += 1
        return removed
