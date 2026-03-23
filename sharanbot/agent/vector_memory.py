"""Vector memory store — semantic search over memories and learned patterns.

Uses embeddings for similarity search. Supports litellm embeddings with
a SHA-256 hash fallback for zero-cost operation when no embedding API
is available.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from loguru import logger


@dataclass
class MemoryEntry:
    """A single memory entry with embedding vector."""

    id: str
    text: str
    category: str  # "error_fix", "pattern", "preference", "fact"
    vector: list[float] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    confidence: float = 0.5


def _hash_embed(text: str, dim: int = 256) -> list[float]:
    """Deterministic pseudo-embedding from SHA-256 hash.

    Produces a normalized vector from hash bytes — zero-cost fallback
    when no embedding API is available.
    """
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    # Repeat hash bytes to fill desired dimensions
    raw = []
    idx = 0
    for _ in range(dim):
        raw.append(digest[idx % len(digest)] / 255.0)
        idx += 1
    # Normalize to unit vector
    arr = np.array(raw, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if norm > 0:
        arr = arr / norm
    return arr.tolist()


async def generate_embedding(text: str, model: str = "text-embedding-3-small") -> list[float]:
    """Generate embedding via litellm, falling back to hash embed."""
    try:
        import litellm
        resp = await litellm.aembedding(model=model, input=[text])
        return resp.data[0]["embedding"]
    except Exception:
        return _hash_embed(text)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    dot = np.dot(va, vb)
    norm = np.linalg.norm(va) * np.linalg.norm(vb)
    return float(dot / norm) if norm > 0 else 0.0


class VectorMemoryStore:
    """Semantic vector memory store for similarity search.

    Stores entries with embeddings and supports cosine similarity search.
    Persists to ``<workspace>/Sharan_workplace/memory/vectors/index.json``.
    """

    _DEDUP_THRESHOLD = 0.95  # Near-duplicate detection threshold
    _INDEX_FILE = "index.json"

    def __init__(self, workspace: Path):
        self._dir = workspace / "Sharan_workplace" / "memory" / "vectors"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._entries: dict[str, MemoryEntry] = {}
        self._load()

    def _load(self) -> None:
        path = self._dir / self._INDEX_FILE
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for item in data:
                entry = MemoryEntry(**item)
                self._entries[entry.id] = entry
            logger.debug("Loaded {} vector memory entries", len(self._entries))
        except Exception as e:
            logger.warning("Failed to load vector memory: {}", e)

    def _save(self) -> None:
        path = self._dir / self._INDEX_FILE
        try:
            data = [asdict(e) for e in self._entries.values()]
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to save vector memory: {}", e)

    async def add(self, id: str, text: str, category: str, metadata: dict | None = None,
                  confidence: float = 0.5) -> bool:
        """Add an entry, deduplicating near-duplicates."""
        vector = await generate_embedding(text)

        # Check for near-duplicates
        for existing in self._entries.values():
            if existing.vector and _cosine_similarity(vector, existing.vector) >= self._DEDUP_THRESHOLD:
                # Update confidence instead of creating duplicate
                existing.confidence = min(1.0, existing.confidence + 0.1)
                existing.timestamp = time.time()
                self._save()
                return False

        entry = MemoryEntry(
            id=id, text=text, category=category, vector=vector,
            metadata=metadata or {}, confidence=confidence,
        )
        self._entries[id] = entry
        self._save()
        return True

    def search(self, query_vector: list[float], top_k: int = 5,
               min_score: float = 0.3, category: str | None = None) -> list[tuple[MemoryEntry, float]]:
        """Search for similar entries by vector similarity."""
        results = []
        for entry in self._entries.values():
            if category and entry.category != category:
                continue
            if not entry.vector:
                continue
            score = _cosine_similarity(query_vector, entry.vector)
            if score >= min_score:
                results.append((entry, score))
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    async def search_text(self, query: str, top_k: int = 5,
                          min_score: float = 0.3, category: str | None = None) -> list[tuple[MemoryEntry, float]]:
        """Search by text query (generates embedding first)."""
        vector = await generate_embedding(query)
        return self.search(vector, top_k, min_score, category)

    def remove(self, id: str) -> bool:
        if id in self._entries:
            del self._entries[id]
            self._save()
            return True
        return False

    def get_context(self, max_entries: int = 10) -> str:
        """Get formatted context string for system prompt injection."""
        if not self._entries:
            return ""
        sorted_entries = sorted(
            self._entries.values(),
            key=lambda e: (e.confidence, e.timestamp),
            reverse=True,
        )[:max_entries]
        lines = ["## Remembered Knowledge"]
        for e in sorted_entries:
            lines.append(f"- [{e.category}] {e.text} (confidence: {e.confidence:.1f})")
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._entries)
