"""Shared memory store — importance-weighted cross-worker memory.

Workers use this to share architecture decisions, technology choices,
contracts, and coordination notes. High-importance entries persist
while low-importance entries are evicted when the store grows too large.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


class MemoryCategory:
    ARCHITECTURE = "architecture"
    TECHNOLOGY = "technology"
    CONTRACT = "contract"
    COORDINATION = "coordination"
    DECISION = "decision"


@dataclass
class SharedMemoryEntry:
    key: str
    value: str
    importance: int = 5  # 1-10
    category: str = MemoryCategory.COORDINATION
    timestamp: float = field(default_factory=time.time)
    source_worker_id: str = ""


class SharedMemoryStore:
    """Cross-worker shared memory with importance-weighted eviction."""

    MAX_ENTRIES = 100
    EVICTION_THRESHOLD = 4  # Evict entries with importance < this

    def __init__(self, path: Path | None = None):
        self._entries: dict[str, SharedMemoryEntry] = {}
        self._path = path
        if path and path.exists():
            self._load(path)

    def _load(self, path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for item in data:
                entry = SharedMemoryEntry(**item)
                self._entries[entry.key] = entry
        except Exception as e:
            logger.warning("Failed to load shared memory: {}", e)

    def add(self, key: str, value: str, importance: int = 5,
            category: str = MemoryCategory.COORDINATION,
            source_worker_id: str = "") -> None:
        """Add or update a shared memory entry."""
        importance = max(1, min(10, importance))
        self._entries[key] = SharedMemoryEntry(
            key=key, value=value[:500], importance=importance,
            category=category, source_worker_id=source_worker_id,
        )
        if len(self._entries) > self.MAX_ENTRIES:
            self.evict()

    def get(self, key: str) -> str | None:
        entry = self._entries.get(key)
        return entry.value if entry else None

    def get_relevant(self, categories: list[str] | None = None,
                     min_importance: int = 1) -> list[SharedMemoryEntry]:
        """Get entries filtered by category and minimum importance."""
        result = []
        for entry in self._entries.values():
            if categories and entry.category not in categories:
                continue
            if entry.importance < min_importance:
                continue
            result.append(entry)
        result.sort(key=lambda e: e.importance, reverse=True)
        return result

    def evict(self) -> int:
        """Remove low-importance entries when store is full."""
        before = len(self._entries)
        self._entries = {
            k: v for k, v in self._entries.items()
            if v.importance >= self.EVICTION_THRESHOLD
        }
        removed = before - len(self._entries)
        if removed:
            logger.debug("Evicted {} low-importance shared memory entries", removed)
        return removed

    def to_context_string(self, max_entries: int = 20) -> str:
        """Format top entries as markdown for system prompt injection."""
        if not self._entries:
            return ""
        sorted_entries = sorted(
            self._entries.values(),
            key=lambda e: (e.importance, e.timestamp),
            reverse=True,
        )[:max_entries]

        lines = ["## Team Shared Memory"]
        for e in sorted_entries:
            lines.append(f"- **[{e.category}]** {e.key}: {e.value}")
        return "\n".join(lines)

    def save(self, path: Path | None = None) -> None:
        target = path or self._path
        if not target:
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            data = [asdict(e) for e in self._entries.values()]
            target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to save shared memory: {}", e)

    @classmethod
    def load(cls, path: Path) -> SharedMemoryStore:
        store = cls(path=path)
        return store

    def __len__(self) -> int:
        return len(self._entries)
