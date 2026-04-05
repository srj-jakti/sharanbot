"""Decision Store — structured persistence for user decisions and directives.

When the user says "stop the bitcoin scanner permanently" or "pause monitoring
for 2 hours", that decision needs to be stored as structured data that ALL
subsystems (Watchdog, Autopilot, Reactions, etc.) can query programmatically.

Unlike MEMORY.md (free-text, LLM-summarized), the Decision Store is:
- Machine-readable (JSON)
- Queryable by subject, status, and type
- Respected by Watchdog, Reactions, and Autopilot automatically
- Supports expiry for temporary decisions
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from sharanbot.utils.helpers import atomic_write


class DecisionStatus:
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class DecisionType:
    STOP_PERMANENT = "stop_permanent"
    STOP_TEMPORARY = "stop_temporary"
    PAUSE = "pause"
    RESUME = "resume"
    SUPPRESS_ALERT = "suppress_alert"
    CONFIGURATION = "configuration"
    PREFERENCE = "preference"


@dataclass
class Decision:
    """A single user decision or directive."""

    id: str                          # unique key, e.g. "stop:bitcoin_scanner"
    subject: str                     # what it's about, e.g. "bitcoin_scanner"
    decision_type: str               # from DecisionType
    description: str                 # human-readable, e.g. "User said stop bitcoin scanner permanently"
    status: str = DecisionStatus.ACTIVE
    created_at: float = field(default_factory=time.time)
    expires_at: float | None = None  # None = never expires
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str = "user"             # "user", "system", "watchdog", "autopilot"

    @property
    def is_active(self) -> bool:
        if self.status != DecisionStatus.ACTIVE:
            return False
        if self.expires_at and time.time() > self.expires_at:
            return False
        return True

    @property
    def is_permanent(self) -> bool:
        return self.decision_type == DecisionType.STOP_PERMANENT and self.is_active

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "decision_type": self.decision_type,
            "description": self.description,
            "status": self.status,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "metadata": self.metadata,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Decision:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class DecisionStore:
    """Persistent store for user decisions that all subsystems can query.

    Backed by a JSON file. Loaded into memory on startup.
    Supports:
    - Recording decisions with optional expiry
    - Querying by subject (fuzzy match)
    - Querying by type
    - Auto-expiring temporary decisions
    - Revoking decisions
    """

    _FILENAME = "decisions.json"
    _MAX_DECISIONS = 500

    def __init__(self, workspace: Path):
        self._workspace = workspace
        self._path = workspace / self._FILENAME
        self._decisions: dict[str, Decision] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for ddata in data.get("decisions", []):
                d = Decision.from_dict(ddata)
                self._decisions[d.id] = d
            # Auto-expire old temporary decisions
            self._expire_stale()
        except Exception as e:
            logger.warning("Failed to load decision store: {}", e)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "decisions": [d.to_dict() for d in self._decisions.values()],
                "saved_at": time.time(),
            }
            atomic_write(
                self._path,
                json.dumps(data, ensure_ascii=False, indent=2),
            )
        except Exception as e:
            logger.warning("Failed to save decision store: {}", e)

    def _expire_stale(self) -> None:
        """Mark expired temporary decisions."""
        now = time.time()
        changed = False
        for d in self._decisions.values():
            if d.status == DecisionStatus.ACTIVE and d.expires_at and now > d.expires_at:
                d.status = DecisionStatus.EXPIRED
                changed = True
        if changed:
            self._save()

    # ------------------------------------------------------------------
    # Record decisions
    # ------------------------------------------------------------------

    def record(
        self,
        subject: str,
        decision_type: str,
        description: str,
        expires_at: float | None = None,
        metadata: dict[str, Any] | None = None,
        source: str = "user",
    ) -> Decision:
        """Record a new decision. Overwrites existing decision for same subject+type."""
        decision_id = f"{decision_type}:{subject.lower().replace(' ', '_')}"

        # If there's an existing active decision of opposite type, revoke it
        # e.g., recording "resume" revokes "stop_permanent" for same subject
        self._handle_conflicting_decisions(subject, decision_type)

        decision = Decision(
            id=decision_id,
            subject=subject,
            decision_type=decision_type,
            description=description,
            expires_at=expires_at,
            metadata=metadata or {},
            source=source,
        )
        self._decisions[decision_id] = decision

        # Enforce cap
        if len(self._decisions) > self._MAX_DECISIONS:
            oldest = min(
                self._decisions.values(),
                key=lambda d: d.created_at,
            )
            del self._decisions[oldest.id]

        self._save()
        logger.info("Decision recorded: {} — {}", decision_id, description)
        return decision

    def _handle_conflicting_decisions(self, subject: str, new_type: str) -> None:
        """Revoke conflicting decisions when a new one is recorded."""
        subject_lower = subject.lower()
        resume_types = {DecisionType.RESUME}
        stop_types = {DecisionType.STOP_PERMANENT, DecisionType.STOP_TEMPORARY, DecisionType.PAUSE}

        for d in list(self._decisions.values()):
            if d.subject.lower() != subject_lower or not d.is_active:
                continue
            # Resume revokes stops/pauses
            if new_type in resume_types and d.decision_type in stop_types:
                d.status = DecisionStatus.REVOKED
            # New stop revokes old resume
            elif new_type in stop_types and d.decision_type in resume_types:
                d.status = DecisionStatus.REVOKED

    # ------------------------------------------------------------------
    # Query decisions
    # ------------------------------------------------------------------

    def is_stopped(self, subject: str) -> bool:
        """Check if a subject has an active stop (permanent or temporary)."""
        return self._has_active_decision(
            subject,
            {DecisionType.STOP_PERMANENT, DecisionType.STOP_TEMPORARY, DecisionType.PAUSE},
        )

    def is_permanently_stopped(self, subject: str) -> bool:
        """Check if a subject has been permanently stopped."""
        return self._has_active_decision(subject, {DecisionType.STOP_PERMANENT})

    def is_suppressed(self, subject: str) -> bool:
        """Check if alerts for a subject are suppressed."""
        return self._has_active_decision(
            subject,
            {DecisionType.SUPPRESS_ALERT, DecisionType.STOP_PERMANENT},
        )

    def get_active_decisions(self, subject: str | None = None) -> list[Decision]:
        """Get all active decisions, optionally filtered by subject."""
        self._expire_stale()
        results = []
        for d in self._decisions.values():
            if not d.is_active:
                continue
            if subject and not self._subject_matches(d.subject, subject):
                continue
            results.append(d)
        return results

    def get_decision(self, subject: str, decision_type: str) -> Decision | None:
        """Get a specific decision by subject and type."""
        decision_id = f"{decision_type}:{subject.lower().replace(' ', '_')}"
        d = self._decisions.get(decision_id)
        if d and d.is_active:
            return d
        return None

    def get_all_decisions(self) -> list[Decision]:
        """Get all decisions (including expired/revoked) for status display."""
        return list(self._decisions.values())

    def revoke(self, subject: str, decision_type: str | None = None) -> int:
        """Revoke decisions for a subject. Returns count of revoked decisions."""
        count = 0
        subject_lower = subject.lower()
        for d in self._decisions.values():
            if not d.is_active:
                continue
            if not self._subject_matches(d.subject, subject_lower):
                continue
            if decision_type and d.decision_type != decision_type:
                continue
            d.status = DecisionStatus.REVOKED
            count += 1
        if count:
            self._save()
        return count

    def _has_active_decision(self, subject: str, types: set[str]) -> bool:
        """Check if any active decision matches subject and types."""
        self._expire_stale()
        subject_lower = subject.lower()
        for d in self._decisions.values():
            if not d.is_active:
                continue
            if d.decision_type not in types:
                continue
            if self._subject_matches(d.subject, subject_lower):
                return True
        return False

    @staticmethod
    def _subject_matches(stored: str, query: str) -> bool:
        """Fuzzy match subjects — handles partial matches and common variations."""
        stored_lower = stored.lower().replace("_", " ").replace("-", " ")
        query_lower = query.lower().replace("_", " ").replace("-", " ")

        # Exact match
        if stored_lower == query_lower:
            return True
        # One contains the other
        if stored_lower in query_lower or query_lower in stored_lower:
            return True
        # Word overlap (at least 50% of query words match)
        stored_words = set(stored_lower.split())
        query_words = set(query_lower.split())
        if query_words and len(stored_words & query_words) >= len(query_words) * 0.5:
            return True
        return False

    # ------------------------------------------------------------------
    # Context for prompts
    # ------------------------------------------------------------------

    def build_context_for_prompt(self) -> str:
        """Build a context string of active decisions for system prompt injection."""
        active = self.get_active_decisions()
        if not active:
            return ""

        lines = ["[Active User Decisions — MUST be respected]"]
        for d in sorted(active, key=lambda x: -x.created_at):
            status_icon = {
                DecisionType.STOP_PERMANENT: "🛑",
                DecisionType.STOP_TEMPORARY: "⏸️",
                DecisionType.PAUSE: "⏸️",
                DecisionType.RESUME: "▶️",
                DecisionType.SUPPRESS_ALERT: "🔇",
                DecisionType.CONFIGURATION: "⚙️",
                DecisionType.PREFERENCE: "💡",
            }.get(d.decision_type, "📌")
            expiry = ""
            if d.expires_at:
                remaining = d.expires_at - time.time()
                if remaining > 0:
                    hours = remaining / 3600
                    expiry = f" (expires in {hours:.1f}h)"
            lines.append(f"  {status_icon} {d.subject}: {d.description}{expiry}")
        return "\n".join(lines)

    def format_status(self) -> str:
        """Format a human-readable status of all decisions."""
        all_decisions = self.get_all_decisions()
        if not all_decisions:
            return "No decisions recorded."

        lines = ["📋 Decision Store:"]
        for d in sorted(all_decisions, key=lambda x: -x.created_at)[:20]:
            status_icon = {
                DecisionStatus.ACTIVE: "🟢",
                DecisionStatus.EXPIRED: "⏰",
                DecisionStatus.REVOKED: "❌",
            }.get(d.status, "⚪")
            active = "✓" if d.is_active else "✗"
            lines.append(f"  {status_icon} [{active}] {d.subject} — {d.decision_type}: {d.description}")
        return "\n".join(lines)

    def get_stats(self) -> dict[str, Any]:
        """Get decision store statistics."""
        all_d = self.get_all_decisions()
        active = [d for d in all_d if d.is_active]
        return {
            "total": len(all_d),
            "active": len(active),
            "permanent_stops": sum(1 for d in active if d.decision_type == DecisionType.STOP_PERMANENT),
            "temporary_stops": sum(1 for d in active if d.decision_type == DecisionType.STOP_TEMPORARY),
            "suppressed_alerts": sum(1 for d in active if d.decision_type == DecisionType.SUPPRESS_ALERT),
        }
