"""Context window tracker and summarizer — prevents token overflow.

Tracks token usage per turn, auto-summarizes old context when approaching
the model's context limit, and maintains a rolling summary for continuity.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from loguru import logger


def _estimate_tokens(text: str) -> int:
    """Estimate tokens using tiktoken, falling back to char heuristic."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(len(text) // 4, 1)


@dataclass
class TurnRecord:
    """A single conversation turn."""
    role: str  # "user" | "assistant" | "system" | "tool"
    content: str
    token_estimate: int = 0
    timestamp: float = 0.0
    turn_number: int = 0
    is_summarized: bool = False

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()
        if not self.token_estimate:
            self.token_estimate = _estimate_tokens(self.content)


@dataclass
class ContextSnapshot:
    """Serializable context state for crash recovery."""
    rolling_summary: str = ""
    total_turns: int = 0
    summarized_up_to: int = 0
    token_budget: int = 128_000
    token_usage: int = 0
    session_id: str = ""


class ContextManager:
    """Tracks and manages conversation context within token budgets.

    Maintains a rolling summary of older turns and keeps recent turns
    in full. When approaching the token limit, oldest unsummarized turns
    are compressed into the rolling summary.
    """

    # Reserve tokens for system prompt, tools, and response
    RESERVED_TOKENS = 8_000
    # Summarize when usage exceeds this fraction of budget
    SUMMARIZE_THRESHOLD = 0.75
    # How many recent turns to always keep in full
    KEEP_RECENT = 10

    def __init__(self, token_budget: int = 128_000,
                 session_id: str = "",
                 persist_path: Path | None = None):
        self._budget = token_budget
        self._usable = token_budget - self.RESERVED_TOKENS
        self._session_id = session_id
        self._persist_path = persist_path

        self._turns: list[TurnRecord] = []
        self._rolling_summary: str = ""
        self._summarized_up_to: int = 0
        self._total_turns: int = 0

        if persist_path and persist_path.exists():
            self._load(persist_path)

    @property
    def token_usage(self) -> int:
        """Current estimated token usage."""
        summary_tokens = _estimate_tokens(self._rolling_summary) if self._rolling_summary else 0
        turn_tokens = sum(t.token_estimate for t in self._turns if not t.is_summarized)
        return summary_tokens + turn_tokens

    @property
    def usage_fraction(self) -> float:
        """Fraction of usable budget consumed."""
        return self.token_usage / max(self._usable, 1)

    @property
    def needs_summarization(self) -> bool:
        """Whether context should be compressed."""
        return self.usage_fraction >= self.SUMMARIZE_THRESHOLD

    def add_turn(self, role: str, content: str, token_count: int = 0) -> TurnRecord:
        """Record a new conversation turn."""
        self._total_turns += 1
        turn = TurnRecord(
            role=role,
            content=content,
            token_estimate=token_count or _estimate_tokens(content),
            turn_number=self._total_turns,
        )
        self._turns.append(turn)

        if self.needs_summarization:
            self._compress()

        return turn

    def get_context_messages(self) -> list[dict[str, str]]:
        """Build message list for LLM: rolling summary + recent turns."""
        messages: list[dict[str, str]] = []

        if self._rolling_summary:
            messages.append({
                "role": "system",
                "content": f"[Previous conversation summary]\n{self._rolling_summary}",
            })

        for turn in self._turns:
            if not turn.is_summarized:
                messages.append({
                    "role": turn.role,
                    "content": turn.content,
                })

        return messages

    def get_stats(self) -> dict[str, Any]:
        """Get context usage statistics."""
        return {
            "total_turns": self._total_turns,
            "active_turns": sum(1 for t in self._turns if not t.is_summarized),
            "summarized_turns": self._summarized_up_to,
            "token_usage": self.token_usage,
            "token_budget": self._budget,
            "usage_percent": round(self.usage_fraction * 100, 1),
            "has_summary": bool(self._rolling_summary),
        }

    def force_summarize(self) -> str:
        """Force summarization of older turns."""
        return self._compress()

    def _compress(self) -> str:
        """Compress older turns into rolling summary."""
        active = [t for t in self._turns if not t.is_summarized]
        if len(active) <= self.KEEP_RECENT:
            return self._rolling_summary

        # Mark all but recent turns as summarized
        to_summarize = active[:-self.KEEP_RECENT]
        summary_parts = []

        if self._rolling_summary:
            summary_parts.append(self._rolling_summary)

        for turn in to_summarize:
            # Compress each turn to key points
            compressed = self._compress_turn(turn)
            if compressed:
                summary_parts.append(compressed)
            turn.is_summarized = True
            self._summarized_up_to = max(self._summarized_up_to, turn.turn_number)

        self._rolling_summary = "\n".join(summary_parts)

        # Trim summary if it's getting too long (keep last 2000 chars)
        max_summary_tokens = self._usable // 4
        max_summary_chars = max_summary_tokens * 4
        if len(self._rolling_summary) > max_summary_chars:
            self._rolling_summary = "..." + self._rolling_summary[-max_summary_chars:]

        if self._persist_path:
            self._save()

        logger.debug(
            "Compressed {} turns, summary now {} tokens",
            len(to_summarize),
            _estimate_tokens(self._rolling_summary) if self._rolling_summary else 0,
        )
        return self._rolling_summary

    def _compress_turn(self, turn: TurnRecord) -> str:
        """Compress a single turn to a brief summary line."""
        content = turn.content
        # User messages: keep first 200 chars
        if turn.role == "user":
            truncated = content[:200].strip()
            if len(content) > 200:
                truncated += "..."
            return f"User (turn {turn.turn_number}): {truncated}"
        # Assistant: keep first 300 chars
        elif turn.role == "assistant":
            truncated = content[:300].strip()
            if len(content) > 300:
                truncated += "..."
            return f"Assistant (turn {turn.turn_number}): {truncated}"
        # Tool: keep minimal
        elif turn.role == "tool":
            truncated = content[:100].strip()
            return f"Tool (turn {turn.turn_number}): {truncated}..."
        return ""

    def _save(self):
        """Save context state for crash recovery."""
        if not self._persist_path:
            return
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot = ContextSnapshot(
            rolling_summary=self._rolling_summary,
            total_turns=self._total_turns,
            summarized_up_to=self._summarized_up_to,
            token_budget=self._budget,
            token_usage=self.token_usage,
            session_id=self._session_id,
        )
        self._persist_path.write_text(
            json.dumps(asdict(snapshot), indent=2),
            encoding="utf-8",
        )

    def _load(self, path: Path):
        """Load context state from disk."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._rolling_summary = data.get("rolling_summary", "")
            self._total_turns = data.get("total_turns", 0)
            self._summarized_up_to = data.get("summarized_up_to", 0)
            self._session_id = data.get("session_id", "")
            logger.debug("Loaded context state: {} turns", self._total_turns)
        except (json.JSONDecodeError, KeyError):
            logger.warning("Failed to load context state from {}", path)
