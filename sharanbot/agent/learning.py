"""Self-learning store — persistent memory for errors, fixes, patterns,
project profiles, mistake journals, and user preferences.

Records what went wrong, what fixed it, and avoids repeating mistakes.
Error/pattern data is stored as a JSONL file; project knowledge uses JSON
files in a ``learning/`` subdirectory.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from sharanbot.utils.helpers import atomic_write

MAX_MISTAKES = 200
MAX_GROWTH_LOG = 500
MAX_ENTRIES = 10_000       # hard cap on learning entries
TTL_SECONDS = 90 * 86_400  # 90 days


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


@dataclass
class ProjectProfile:
    """Per-project-type knowledge."""

    project_type: str  # "react", "python", "fastapi", "next.js", etc.
    common_commands: list[str] = field(default_factory=list)
    common_errors: list[str] = field(default_factory=list)
    preferred_tools: list[str] = field(default_factory=list)
    setup_steps: list[str] = field(default_factory=list)
    successful_patterns: list[str] = field(default_factory=list)
    interactions: int = 0


@dataclass
class MistakeEntry:
    """A recorded mistake with context and correction."""

    timestamp: float = field(default_factory=time.time)
    context: str = ""
    mistake: str = ""
    correction: str = ""
    category: str = ""
    severity: str = "low"
    learned_rule: str = ""


@dataclass
class UserPreference:
    """A tracked user preference."""

    key: str
    value: str
    confidence: float = 0.5
    occurrences: int = 1
    last_seen: float = field(default_factory=time.time)


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
        """Load entries from disk, pruning expired (>90 days) and excess entries."""
        if not self._path.exists():
            return
        try:
            now = time.time()
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                entry = LearningEntry(**data)
                # TTL: skip entries older than 90 days
                if now - entry.last_seen > TTL_SECONDS:
                    continue
                self._entries[entry.error_signature] = entry
            # Cap: keep only the most-recently-seen entries
            if len(self._entries) > MAX_ENTRIES:
                sorted_keys = sorted(
                    self._entries, key=lambda k: self._entries[k].last_seen,
                )
                for key in sorted_keys[: len(self._entries) - MAX_ENTRIES]:
                    del self._entries[key]
                self._flush()  # persist pruned state
            logger.debug("Loaded {} learning entries from {}", len(self._entries), self._path)
        except Exception as e:
            logger.warning("Failed to load learning store: {}", e)

    def _flush(self) -> None:
        """Write all entries to disk atomically."""
        try:
            lines = [json.dumps(asdict(e), ensure_ascii=False) for e in self._entries.values()]
            atomic_write(self._path, "\n".join(lines) + "\n")
        except Exception as e:
            logger.warning("Failed to flush learning store: {}", e)

    def _append_entry(self, entry: LearningEntry) -> None:
        """Append a single entry to disk (faster than full rewrite for new entries)."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("Failed to append learning entry: {}", e)

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
            cooldown_seconds=max(cooldown_seconds, 0),
            metadata=metadata or {},
        )
        self._entries[error_signature] = entry
        self._append_entry(entry)
        # Enforce cap
        if len(self._entries) > MAX_ENTRIES:
            oldest_key = min(self._entries, key=lambda k: self._entries[k].last_seen)
            del self._entries[oldest_key]
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

    # ------------------------------------------------------------------
    # Positive Pattern Learning
    # ------------------------------------------------------------------

    def record_success(
        self,
        pattern_name: str,
        tool_chain: list[str],
        description: str = "",
        confidence: float = 0.8,
        metadata: dict[str, Any] | None = None,
    ) -> LearningEntry:
        """Record a successful tool chain / approach as a positive pattern.

        Positive patterns are stored with category='positive' and the
        tool chain in metadata. Repeated successes increase confidence.
        """
        signature = f"positive:{pattern_name}"
        existing = self._entries.get(signature)

        if existing:
            existing.occurrences += 1
            existing.last_seen = time.time()
            existing.success = True
            # Increase confidence with each successful use
            old_conf = existing.metadata.get("confidence", 0.5)
            existing.metadata["confidence"] = min(old_conf + 0.05, 1.0)
            if tool_chain:
                existing.metadata["tool_chain"] = tool_chain
            self._flush()
            return existing

        entry = LearningEntry(
            category="positive",
            error_signature=signature,
            description=description or f"Successful pattern: {pattern_name}",
            fix_applied=", ".join(tool_chain),
            success=True,
            metadata={
                "confidence": confidence,
                "tool_chain": tool_chain,
                **(metadata or {}),
            },
        )
        self._entries[signature] = entry
        self._append_entry(entry)
        logger.info("Positive pattern recorded: {}", pattern_name)
        return entry

    def get_positive_patterns(self, min_confidence: float = 0.5) -> list[LearningEntry]:
        """Get all positive patterns above confidence threshold."""
        return [
            e for e in self._entries.values()
            if e.category == "positive"
            and e.success
            and e.metadata.get("confidence", 0) >= min_confidence
        ]

    def get_best_approach(self, task_type: str) -> list[str] | None:
        """Get the best known tool chain for a task type.

        Returns the tool chain with highest confidence, or None.
        """
        candidates = [
            e for e in self._entries.values()
            if e.category == "positive"
            and task_type.lower() in e.error_signature.lower()
            and e.success
        ]
        if not candidates:
            return None
        best = max(candidates, key=lambda e: (e.metadata.get("confidence", 0), e.occurrences))
        return best.metadata.get("tool_chain")

    def get_context_for_prompt(self) -> str:
        """Get a context string of learned patterns for system prompt injection."""
        positive = self.get_positive_patterns(min_confidence=0.7)
        if not positive:
            return ""

        lines = ["[Learned successful patterns]"]
        for p in sorted(positive, key=lambda e: -e.metadata.get("confidence", 0))[:10]:
            conf = p.metadata.get("confidence", 0)
            chain = p.metadata.get("tool_chain", [])
            lines.append(f"- {p.description} (confidence: {conf:.0%}, tools: {', '.join(chain)})")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Project Knowledge (merged from AdaptiveLearningEngine)
    # ------------------------------------------------------------------

    def _ensure_data_dir(self) -> Path:
        d = self._path.parent / "learning"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _load_json(self, name: str) -> Any:
        path = self._ensure_data_dir() / name
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Failed to load {}: {}", name, e)
            return None

    def _save_json(self, name: str, data: Any) -> None:
        try:
            path = self._ensure_data_dir() / name
            atomic_write(
                path,
                json.dumps(data, ensure_ascii=False, indent=2, default=str),
            )
        except Exception as e:
            logger.warning("Failed to save {}: {}", name, e)

    # -- Project Profiles --

    def _load_profiles(self) -> dict[str, ProjectProfile]:
        data = self._load_json("project_profiles.json")
        if isinstance(data, dict):
            return {k: ProjectProfile(**v) for k, v in data.items()}
        return {}

    def learn_project_type(self, project_type: str, **kwargs: Any) -> None:
        """Create or update a project profile."""
        profiles = self._load_profiles()
        key = project_type.lower().strip()
        profile = profiles.get(key, ProjectProfile(project_type=key))
        profile.interactions += 1

        for list_field in ("common_commands", "common_errors", "preferred_tools",
                           "setup_steps", "successful_patterns"):
            if list_field in kwargs:
                items = kwargs[list_field]
                existing = getattr(profile, list_field)
                for item in (items if isinstance(items, list) else [items]):
                    if item not in existing:
                        existing.append(item)

        profiles[key] = profile
        self._save_json("project_profiles.json", {k: asdict(v) for k, v in profiles.items()})

    def get_project_context(self, project_type: str) -> str:
        """Get context string for a project type."""
        profiles = self._load_profiles()
        profile = profiles.get(project_type.lower().strip())
        if not profile:
            return ""
        lines = [f"## Project Knowledge: {profile.project_type}"]
        if profile.common_commands:
            lines.append(f"Common commands: {', '.join(profile.common_commands[:10])}")
        if profile.common_errors:
            lines.append(f"Known issues: {', '.join(profile.common_errors[:5])}")
        if profile.setup_steps:
            lines.append(f"Setup: {' → '.join(profile.setup_steps[:8])}")
        if profile.successful_patterns:
            lines.append(f"Patterns that work: {', '.join(profile.successful_patterns[:5])}")
        return "\n".join(lines)

    # -- Mistake Journal --

    def _load_mistakes(self) -> list[MistakeEntry]:
        data = self._load_json("mistakes.json")
        if isinstance(data, list):
            return [MistakeEntry(**m) for m in data]
        return []

    def record_mistake(
        self, context: str, mistake: str, correction: str = "",
        category: str = "", severity: str = "low", learned_rule: str = "",
    ) -> None:
        """Record a mistake for future avoidance."""
        mistakes = self._load_mistakes()
        entry = MistakeEntry(
            context=context[:500], mistake=mistake[:500],
            correction=correction[:500], category=category,
            severity=severity, learned_rule=learned_rule,
        )
        mistakes.append(entry)
        if len(mistakes) > MAX_MISTAKES:
            mistakes = mistakes[-MAX_MISTAKES:]
        self._save_json("mistakes.json", [asdict(m) for m in mistakes])

        stats = self._load_json("interaction_stats.json") or {}
        stats["errors_encountered"] = stats.get("errors_encountered", 0) + 1
        self._save_json("interaction_stats.json", stats)

    def get_mistake_context(self, max_entries: int = 10) -> str:
        """Get recent mistakes as context."""
        mistakes = self._load_mistakes()
        if not mistakes:
            return ""
        recent = mistakes[-max_entries:]
        lines = ["## Recent Mistakes to Avoid"]
        for m in recent:
            rule = f" → Rule: {m.learned_rule}" if m.learned_rule else ""
            lines.append(f"- [{m.category}] {m.mistake}{rule}")
        return "\n".join(lines)

    # -- User Preferences --

    def _load_preferences(self) -> dict[str, UserPreference]:
        data = self._load_json("preferences.json")
        if isinstance(data, dict):
            return {k: UserPreference(**v) for k, v in data.items()}
        return {}

    def update_preference(self, key: str, value: str, confidence_boost: float = 0.1) -> None:
        """Track a user preference with confidence scoring."""
        prefs = self._load_preferences()
        existing = prefs.get(key)
        if existing:
            if existing.value == value:
                existing.occurrences += 1
                existing.confidence = min(1.0, existing.confidence + confidence_boost)
            elif confidence_boost > existing.confidence:
                existing.value = value
                existing.confidence = confidence_boost
                existing.occurrences = 1
            existing.last_seen = time.time()
        else:
            prefs[key] = UserPreference(
                key=key, value=value, confidence=0.5 + confidence_boost,
            )
        self._save_json("preferences.json", {k: asdict(v) for k, v in prefs.items()})

    def get_preferences_context(self) -> str:
        """Get preferences as context string."""
        prefs = self._load_preferences()
        if not prefs:
            return ""
        lines = ["## User Preferences"]
        for p in sorted(prefs.values(), key=lambda x: x.confidence, reverse=True):
            if p.confidence >= 0.4:
                lines.append(f"- {p.key}: {p.value} (confidence: {p.confidence:.1f})")
        return "\n".join(lines)

    # -- Stats & Growth --

    def record_interaction(self) -> None:
        stats = self._load_json("interaction_stats.json") or {}
        stats["interactions"] = stats.get("interactions", 0) + 1
        self._save_json("interaction_stats.json", stats)

    def record_task_completed(self) -> None:
        stats = self._load_json("interaction_stats.json") or {}
        stats["tasks_completed"] = stats.get("tasks_completed", 0) + 1
        self._save_json("interaction_stats.json", stats)

    def log_growth(self, event: str) -> None:
        growth = self._load_json("growth_log.json") or []
        growth.append({"timestamp": time.time(), "event": event[:200]})
        if len(growth) > MAX_GROWTH_LOG:
            growth = growth[-MAX_GROWTH_LOG:]
        self._save_json("growth_log.json", growth)

    def get_growth_summary(self) -> str:
        stats = self._load_json("interaction_stats.json") or {}
        profiles = self._load_profiles()
        prefs = self._load_preferences()
        patterns = sum(p.interactions for p in profiles.values())
        return (
            f"Interactions: {stats.get('interactions', 0)} | "
            f"Tasks: {stats.get('tasks_completed', 0)} | "
            f"Errors: {stats.get('errors_encountered', 0)} | "
            f"Project types learned: {len(profiles)} | "
            f"Preferences: {len(prefs)} | "
            f"Patterns: {patterns}"
        )

    # ------------------------------------------------------------------
    # Cross-Project Learning (global store)
    # ------------------------------------------------------------------

    _GLOBAL_DIR = Path.home() / ".sharanbot" / "global_learning"

    def export_high_confidence(self, min_confidence: float = 0.8) -> int:
        """Export high-confidence positive patterns to the global store.

        Returns the number of entries exported.
        """
        patterns = self.get_positive_patterns(min_confidence=min_confidence)
        if not patterns:
            return 0

        self._GLOBAL_DIR.mkdir(parents=True, exist_ok=True)
        global_path = self._GLOBAL_DIR / "shared_patterns.jsonl"

        # Load existing global signatures to avoid duplicates
        existing_sigs: set[str] = set()
        if global_path.exists():
            try:
                for line in global_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        existing_sigs.add(json.loads(line).get("error_signature", ""))
            except Exception:
                pass

        exported = 0
        with open(global_path, "a", encoding="utf-8") as f:
            for p in patterns:
                if p.error_signature in existing_sigs:
                    continue
                f.write(json.dumps(asdict(p), ensure_ascii=False) + "\n")
                exported += 1

        if exported:
            logger.info("Exported {} patterns to global store", exported)
        return exported

    def import_global_patterns(self) -> int:
        """Import shared patterns from the global store into this workspace.

        Returns the number of new entries imported.
        """
        global_path = self._GLOBAL_DIR / "shared_patterns.jsonl"
        if not global_path.exists():
            return 0

        imported = 0
        try:
            for line in global_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                sig = data.get("error_signature", "")
                if sig and sig not in self._entries:
                    entry = LearningEntry(**data)
                    self._entries[sig] = entry
                    imported += 1
        except Exception as e:
            logger.warning("Failed to import global patterns: {}", e)
            return imported

        if imported:
            self._flush()
            logger.info("Imported {} global patterns", imported)
        return imported
