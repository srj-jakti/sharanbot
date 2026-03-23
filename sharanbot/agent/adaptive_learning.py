"""Adaptive learning engine — DEPRECATED, merged into learning.py.

This module re-exports `LearningStore` as `AdaptiveLearningEngine`
for backward compatibility.  All project-profile, mistake-journal,
preference, and growth features now live in
`sharanbot.agent.learning.LearningStore`.
"""

from __future__ import annotations

import warnings
from pathlib import Path

from sharanbot.agent.learning import (  # noqa: F401 - re-exports
    LearningStore,
    MistakeEntry,
    ProjectProfile,
    UserPreference,
)


class AdaptiveLearningEngine(LearningStore):
    """Backward-compatible wrapper - delegates to LearningStore."""

    def __init__(self, workspace: Path, **kwargs):
        warnings.warn(
            "AdaptiveLearningEngine is deprecated; use LearningStore directly.",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__(workspace, **kwargs)
