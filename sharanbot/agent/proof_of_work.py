"""Proof of work — auditable evidence tracker for agent/worker actions.

Records timestamped entries of tool calls, file writes, phase completions,
and errors. Provides skeptical verification before accepting success claims.
Persists to ``.sharanbot/proof-of-work/<task_id>.json``.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class WorkEntry:
    timestamp: float = field(default_factory=time.time)
    action: str = ""  # "tool_call", "file_write", "file_modify", "phase", "error", "complete"
    detail: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProofOfWork:
    """Auditable evidence tracker for a task."""

    task_id: str
    task_description: str = ""
    entries: list[WorkEntry] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    phases_completed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    status: str = "in_progress"  # "in_progress", "completed", "failed", "verified"
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None

    def record(self, action: str, detail: str, metadata: dict | None = None) -> None:
        """Record a work entry and auto-populate tracking lists."""
        entry = WorkEntry(action=action, detail=detail[:200], metadata=metadata or {})
        self.entries.append(entry)

        if action == "tool_call":
            tool = metadata.get("tool", detail.split("(")[0]) if metadata else detail
            if tool and tool not in self.tools_used:
                self.tools_used.append(tool)
        elif action == "file_write":
            if detail not in self.files_created:
                self.files_created.append(detail)
        elif action == "file_modify":
            if detail not in self.files_modified:
                self.files_modified.append(detail)
        elif action == "phase":
            if detail not in self.phases_completed:
                self.phases_completed.append(detail)
        elif action == "error":
            self.errors.append(detail[:200])

    def complete(self, success: bool = True) -> bool:
        """Mark as complete, but verify evidence first."""
        self.completed_at = time.time()
        if success:
            verified = self._verify_result()
            self.status = "verified" if verified else "completed"
            return verified
        self.status = "failed"
        return False

    def _verify_result(self) -> bool:
        """Skeptical verification — check actual evidence exists."""
        # Fail if last entry was an error
        if self.entries and self.entries[-1].action == "error":
            return False
        # Fail if zero tool calls or file changes
        if not self.tools_used and not self.files_created and not self.files_modified:
            return False
        return True

    @property
    def summary(self) -> str:
        icon = {"verified": "✅", "completed": "⚠️", "failed": "❌", "in_progress": "🔄"}.get(self.status, "❓")
        duration = (self.completed_at or time.time()) - self.started_at
        return (
            f"{icon} {self.task_id}: {len(self.tools_used)} tools, "
            f"{len(self.files_created)} created, {len(self.files_modified)} modified, "
            f"{len(self.errors)} errors ({duration:.0f}s)"
        )

    def save(self, workspace: Path) -> None:
        dir_ = workspace / ".sharanbot" / "proof-of-work"
        dir_.mkdir(parents=True, exist_ok=True)
        path = dir_ / f"{self.task_id}.json"
        data = asdict(self)
        # Truncate metadata values
        for entry in data.get("entries", []):
            meta = entry.get("metadata", {})
            for k, v in list(meta.items()):
                meta[k] = str(v)[:100]
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to save proof of work: {}", e)


def load_proof_of_work(workspace: Path, task_id: str) -> ProofOfWork | None:
    path = workspace / ".sharanbot" / "proof-of-work" / f"{task_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = [WorkEntry(**e) for e in data.pop("entries", [])]
        pow_ = ProofOfWork(**data)
        pow_.entries = entries
        return pow_
    except Exception:
        return None


def list_proofs(workspace: Path) -> list[str]:
    dir_ = workspace / ".sharanbot" / "proof-of-work"
    if not dir_.exists():
        return []
    return [p.stem for p in dir_.glob("*.json")]
