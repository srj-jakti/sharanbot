"""Self-learning store — persistent memory for errors, fixes, and patterns.

Records what went wrong, what fixed it, and avoids repeating mistakes.
Data is stored as a JSONL file in the workspace so it survives restarts.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class LearningEntry:
    """A single learned pattern or fix."""

    category: str  # "rate_limit", "tool_error", "mcp_failure", "stuck", "api_error", ...
    error_signature: str  # Unique key for this error type (e.g. "429:kimi-for-coding")
    description: str  # Human-readable description of what happened
    fix_applied: str  # What recovery action was taken
    success: bool  # Whether the fix worked
    occurrences: int = 1  # How many times this was seen
    last_seen: float = field(default_factory=time.time)
    first_seen: float = field(default_factory=time.time)
    cooldown_seconds: float = 0  # Learned optimal wait time for this error
    metadata: dict[str, Any] = field(default_factory=dict)


class LearningStore:
    """Persistent store for learned error patterns and recovery strategies.

    Backed by a JSONL file in the workspace directory. Loaded into memory
    on startup for fast lookup. Flushed to disk after every update.
    """

    _FILENAME = "learning_store.jsonl"

    def __init__(self, workspace: Path):
        self._path = workspace / self._FILENAME
        self._entries: dict[str, LearningEntry] = {}  # keyed by error_signature
        self._load()

    def _load(self) -> None:
        """Load entries from disk."""
        if not self._path.exists():
            return
        try:
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                entry = LearningEntry(**data)
                self._entries[entry.error_signature] = entry
            logger.debug("Loaded {} learning entries from {}", len(self._entries), self._path)
        except Exception as e:
            logger.warning("Failed to load learning store: {}", e)

    def _flush(self) -> None:
        """Write all entries to disk (full rewrite for consistency)."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            lines = [json.dumps(asdict(e), ensure_ascii=False) for e in self._entries.values()]
            self._path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to flush learning store: {}", e)

    def record(
        self,
        category: str,
        error_signature: str,
        description: str,
        fix_applied: str,
        success: bool,
        cooldown_seconds: float = 0,
        metadata: dict[str, Any] | None = None,
    ) -> LearningEntry:
        """Record a new error and its fix, or update an existing entry."""
        existing = self._entries.get(error_signature)
        if existing:
            existing.occurrences += 1
            existing.last_seen = time.time()
            existing.success = success
            if success and fix_applied:
                existing.fix_applied = fix_applied
            if cooldown_seconds > 0:
                # Learn optimal cooldown: exponential moving average
                existing.cooldown_seconds = (
                    existing.cooldown_seconds * 0.7 + cooldown_seconds * 0.3
                )
            if metadata:
                existing.metadata.update(metadata)
            self._flush()
            logger.info(
                "Learning updated [{}]: {} (seen {}x)",
                category, error_signature, existing.occurrences,
            )
            return existing

        entry = LearningEntry(
            category=category,
            error_signature=error_signature,
            description=description,
            fix_applied=fix_applied,
            success=success,
            cooldown_seconds=cooldown_seconds,
            metadata=metadata or {},
        )
        self._entries[error_signature] = entry
        self._flush()
        logger.info("Learning recorded [{}]: {}", category, error_signature)
        return entry

    def lookup(self, error_signature: str) -> LearningEntry | None:
        """Look up a previously seen error pattern."""
        return self._entries.get(error_signature)

    def find_by_category(self, category: str) -> list[LearningEntry]:
        """Find all entries matching a category."""
        return [e for e in self._entries.values() if e.category == category]

    def get_cooldown(self, error_signature: str, default: float = 1.0) -> float:
        """Get the learned cooldown for an error type, or the default."""
        entry = self._entries.get(error_signature)
        if entry and entry.cooldown_seconds > 0:
            return entry.cooldown_seconds
        return default

    def has_seen(self, error_signature: str) -> bool:
        """Check if this error has been seen before."""
        return error_signature in self._entries

    def get_successful_fix(self, error_signature: str) -> str | None:
        """Get the last successful fix for an error, if any."""
        entry = self._entries.get(error_signature)
        if entry and entry.success:
            return entry.fix_applied
        return None

    def summary(self) -> str:
        """Return a human-readable summary of learned patterns."""
        if not self._entries:
            return "No patterns learned yet."
        lines = [f"Learned patterns ({len(self._entries)} total):"]
        by_cat: dict[str, list[LearningEntry]] = {}
        for e in self._entries.values():
            by_cat.setdefault(e.category, []).append(e)
        for cat, entries in sorted(by_cat.items()):
            lines.append(f"\n  [{cat}] ({len(entries)} patterns)")
            for e in sorted(entries, key=lambda x: -x.occurrences)[:5]:
                status = "✅" if e.success else "❌"
                lines.append(f"    {status} {e.error_signature} (seen {e.occurrences}x)")
        return "\n".join(lines)

    @property
    def total_entries(self) -> int:
        return len(self._entries)
