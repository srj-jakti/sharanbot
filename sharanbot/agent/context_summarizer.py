"""Context summarizer — auto-generates conversation summaries.

When the context window approaches the summarization threshold (80%),
this module produces a condensed markdown summary of the conversation so far
and can inject it as a replacement for older messages.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger


@dataclass
class ConversationSummary:
    """A generated conversation summary."""

    summary_text: str
    original_messages: int
    summarized_at: float = field(default_factory=time.time)
    tokens_saved: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class ContextSummarizer:
    """Summarizes conversation history when context window is low.

    Strategies:
    1. **Local summary**: Extract key facts from the oldest N messages
       without calling the LLM (fast, deterministic)
    2. **LLM summary**: Ask the LLM to summarize (more accurate, costs a call)

    This implementation uses strategy 1 (local extraction) by default so
    it can run even when offline.
    """

    MAX_SUMMARY_LENGTH: int = 2000  # characters

    def __init__(self, *, max_summary_length: int | None = None):
        self._max_len = max_summary_length or self.MAX_SUMMARY_LENGTH
        self._summaries: list[ConversationSummary] = []

    def summarize_messages(
        self,
        messages: list[dict[str, Any]],
        keep_recent: int = 5,
    ) -> tuple[list[dict[str, Any]], ConversationSummary]:
        """Summarize older messages and return (new_messages, summary).

        Keeps the most recent ``keep_recent`` messages intact and replaces
        older messages with a summary message.
        """
        if len(messages) <= keep_recent + 1:
            return messages, ConversationSummary(
                summary_text="",
                original_messages=len(messages),
                tokens_saved=0,
            )

        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        to_summarize = non_system[:-keep_recent] if keep_recent > 0 else non_system
        to_keep = non_system[-keep_recent:] if keep_recent > 0 else []

        summary_text = self._extract_summary(to_summarize)

        summary = ConversationSummary(
            summary_text=summary_text,
            original_messages=len(to_summarize),
            tokens_saved=self._estimate_tokens(to_summarize),
        )
        self._summaries.append(summary)

        summary_msg = {
            "role": "system",
            "content": (
                f"[Context Summary — {len(to_summarize)} messages condensed]\n\n"
                f"{summary_text}"
            ),
        }

        new_messages = system_msgs + [summary_msg] + to_keep
        logger.info(
            "Summarized {} messages → {} chars, keeping {} recent",
            len(to_summarize), len(summary_text), len(to_keep),
        )
        return new_messages, summary

    def _extract_summary(self, messages: list[dict[str, Any]]) -> str:
        """Extract a structured summary from messages without LLM."""
        sections: list[str] = []

        # Collect key facts
        user_requests: list[str] = []
        assistant_actions: list[str] = []
        tool_calls: list[str] = []
        errors: list[str] = []

        for msg in messages:
            role = msg.get("role", "")
            content = str(msg.get("content", ""))[:300]

            if role == "user" and content.strip():
                user_requests.append(content.strip()[:100])
            elif role == "assistant" and content.strip():
                first_line = content.strip().split("\n")[0][:100]
                assistant_actions.append(first_line)
            elif role == "tool":
                tool_name = msg.get("name", "unknown")
                tool_calls.append(tool_name)
                if "error" in content.lower() or "failed" in content.lower():
                    errors.append(f"{tool_name}: {content[:80]}")

        if user_requests:
            sections.append("**User requests:**\n" + "\n".join(
                f"- {r}" for r in user_requests[-10:]
            ))

        if assistant_actions:
            sections.append("**Key actions taken:**\n" + "\n".join(
                f"- {a}" for a in assistant_actions[-10:]
            ))

        if tool_calls:
            from collections import Counter
            counts = Counter(tool_calls)
            sections.append("**Tools used:**\n" + "\n".join(
                f"- {name} (×{count})" for name, count in counts.most_common(10)
            ))

        if errors:
            sections.append("**Errors encountered:**\n" + "\n".join(
                f"- {e}" for e in errors[-5:]
            ))

        summary = "\n\n".join(sections)
        if len(summary) > self._max_len:
            summary = summary[:self._max_len] + "\n\n[...truncated]"

        return summary or "No significant conversation content to summarize."

    @staticmethod
    def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
        """Estimate tokens with tiktoken, falling back to char heuristic."""
        try:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
            parts = [str(m.get("content", "")) for m in messages]
            return len(enc.encode("\n".join(parts)))
        except Exception:
            total_chars = sum(len(str(m.get("content", ""))) for m in messages)
            return total_chars // 4

    def get_summaries(self) -> list[dict[str, Any]]:
        return [
            {
                "text": s.summary_text[:200],
                "messages_summarized": s.original_messages,
                "tokens_saved": s.tokens_saved,
                "time": s.summarized_at,
            }
            for s in self._summaries
        ]
