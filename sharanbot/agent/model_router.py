"""Cost-aware model router — routes tasks to optimal LLM tier.

Classifies incoming tasks by complexity (simple/moderate/complex) using
regex patterns and text-length heuristics, then selects the appropriate
model tier to minimize cost while maximizing accuracy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from loguru import logger


@dataclass
class ModelTier:
    name: str
    model: str
    cost_per_1k: float = 0.0
    max_complexity: str = "complex"  # "simple", "moderate", "complex"


# Complexity indicators
_COMPLEX_PATTERNS = re.compile(
    r"(?:architect|design|refactor|debug|optimize|security|deploy|pipeline|orchestrat|"
    r"full[- ]?stack|production|database schema|api design|microservice|system design|"
    r"build (?:a|an|the) (?:complete|full|entire)|from scratch|end[- ]?to[- ]?end)",
    re.IGNORECASE,
)

_MODERATE_PATTERNS = re.compile(
    r"(?:implement|create|build|write|fix|update|modify|test|review|explain|analyze|"
    r"function|class|component|endpoint|migration|configuration)",
    re.IGNORECASE,
)

_SIMPLE_PATTERNS = re.compile(
    r"(?:^(?:what|how|where|when|why|who|which)\b|"
    r"(?:translate|summarize|format|convert|list|count|calculate|check)|"
    r"^(?:hi|hello|hey|thanks|ok|yes|no)\b)",
    re.IGNORECASE,
)


class ModelRouter:
    """Routes tasks to appropriate model tier based on complexity."""

    def __init__(self, tiers: list[ModelTier] | None = None):
        self._tiers = tiers or []
        self._stats: dict[str, int] = {"simple": 0, "moderate": 0, "complex": 0}

    def add_tier(self, name: str, model: str, cost_per_1k: float = 0.0,
                 max_complexity: str = "complex") -> None:
        self._tiers.append(ModelTier(name=name, model=model,
                                      cost_per_1k=cost_per_1k,
                                      max_complexity=max_complexity))

    def route(self, task_text: str, force_tier: str | None = None) -> str | None:
        """Route a task to the best model. Returns model identifier."""
        if not self._tiers:
            return None

        if force_tier:
            for tier in self._tiers:
                if tier.name == force_tier:
                    return tier.model
            return None

        complexity = self._assess_complexity(task_text)
        self._stats[complexity] = self._stats.get(complexity, 0) + 1

        # Find cheapest tier that can handle this complexity
        complexity_order = {"simple": 0, "moderate": 1, "complex": 2}
        target = complexity_order[complexity]

        eligible = [
            t for t in self._tiers
            if complexity_order.get(t.max_complexity, 2) >= target
        ]

        if not eligible:
            return self._tiers[-1].model  # Fallback to last (most capable)

        # Sort by cost, pick cheapest
        eligible.sort(key=lambda t: t.cost_per_1k)
        selected = eligible[0]
        logger.debug("Routed {} task to {} ({})", complexity, selected.name, selected.model)
        return selected.model

    def _assess_complexity(self, text: str) -> str:
        """Classify task complexity via regex + heuristics."""
        if _COMPLEX_PATTERNS.search(text):
            return "complex"
        if len(text) > 500:
            return "complex"
        if _MODERATE_PATTERNS.search(text):
            return "moderate"
        if _SIMPLE_PATTERNS.search(text) or len(text) < 50:
            return "simple"
        return "moderate"

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)
