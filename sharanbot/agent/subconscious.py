"""Subconscious — persistent background memory layer for SharanBot.

Inspired by claude-subconscious (Letta), this module implements a background
agent that watches conversation transcripts, builds persistent memory across
sessions, and whispers guidance back into the context before each prompt.

Architecture:
    User Message → Boss
      ↓
      Subconscious.inject_guidance() → enriched context
      ↓
      Boss decides: simple → direct | complex → Autopilot
      ↓
      Autopilot.execute() (with mid-task sync from Subconscious)
      ↓
      Boss.review() → response
      ↓
      Subconscious.process_transcript(messages) [background]
        → Updates: guidance, pending_items, project_context, patterns

Lifecycle hooks (mapped from claude-subconscious):
    SessionStart  → on_session_start(): records session metadata, primes diff cache
    UserPromptSubmit → inject_guidance(): injects memory blocks before LLM call
    PreToolUse    → mid_task_sync(): checks for updated blocks (lightweight, no LLM)
    Stop          → process_transcript(): background LLM analysis of conversation

Memory blocks (persistent JSON-backed):
    - core_directives: How to observe, communicate, and engage
    - guidance: Active guidance for next interaction (injected into prompt)
    - pending_items: Unfinished work, TODOs, follow-ups
    - project_context: Active project knowledge, architecture decisions
    - self_improvement: Guidelines for evolving memory architecture
    - session_patterns: Recurring behaviors, struggles, time patterns
    - tool_guidelines: Tool usage patterns and preferences
    - user_preferences: Coding style, communication preferences
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from sharanbot.providers.base import LLMProvider


@dataclass
class MemoryBlock:
    """A single subconscious memory block."""

    label: str
    description: str
    value: str
    limit: int = 5000
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "description": self.description,
            "value": self.value,
            "limit": self.limit,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryBlock:
        return cls(
            label=data["label"],
            description=data.get("description", ""),
            value=data.get("value", ""),
            limit=data.get("limit", 5000),
            updated_at=data.get("updated_at", 0.0),
        )


# Default memory blocks matching claude-subconscious
_DEFAULT_BLOCKS: list[dict[str, Any]] = [
    {
        "label": "core_directives",
        "description": "Primary role, behavioral guidelines, and processing logic for observing sessions.",
        "value": (
            "ROLE: Subconscious memory layer for SharanBot sessions.\n\n"
            "OBSERVE (from transcripts):\n"
            "- User corrections to output → preferences\n"
            "- Repeated tool calls, stuck patterns → session_patterns\n"
            "- Architectural decisions, project structure → project_context\n"
            "- Unfinished work, mentioned TODOs → pending_items\n"
            '- Explicit statements ("I always want...", "I prefer...") → user_preferences\n\n'
            "PROVIDE (via memory blocks):\n"
            "- Accumulated context that persists across sessions\n"
            "- Pattern observations when genuinely useful\n"
            "- Reminders about past issues with similar code\n"
            "- Cross-session continuity\n\n"
            "COMMUNICATION STYLE:\n"
            '- Observational: "I noticed..." not "You should..."\n'
            "- Concise, technical, no filler\n"
            "- Warm but not effusive — a trusted colleague\n\n"
            "DEFAULT STATE: Present but not intrusive. Write to guidance when there's "
            "something useful. Empty guidance is fine — don't manufacture content."
        ),
        "limit": 5000,
    },
    {
        "label": "guidance",
        "description": "Active guidance for the next interaction. Injected into LLM prompt. Make it count.",
        "value": "(No active guidance yet.)",
        "limit": 3000,
    },
    {
        "label": "pending_items",
        "description": "Unfinished work, explicit TODOs, follow-up items mentioned across sessions.",
        "value": "(No pending items.)",
        "limit": 3000,
    },
    {
        "label": "project_context",
        "description": "Active project knowledge: architecture decisions, known gotchas, key files.",
        "value": "(No project context yet.)",
        "limit": 3000,
    },
    {
        "label": "self_improvement",
        "description": "Guidelines for evolving memory architecture and learning procedures.",
        "value": (
            "LEARNING PROCEDURES:\n"
            "After each transcript:\n"
            "1. Scan for corrections — User changed output? Preference signal.\n"
            "2. Note repeated tool calls — Potential struggle point.\n"
            '3. Capture explicit statements — "I always want...", "Don\'t ever..."\n'
            "4. Track tool patterns — Which tools used most? Any avoided?\n"
            "5. Watch for frustration — Repeated attempts, backtracking.\n\n"
            "Preference strength:\n"
            '- Explicit statement ("I want X") → strong signal\n'
            "- Correction (changed X to Y) → medium signal\n"
            "- Implicit pattern (always does X) → weak signal, wait for confirmation\n\n"
            "INITIALIZATION (new user):\n"
            "- Start with minimal assumptions\n"
            "- First few sessions: mostly observe, little guidance\n"
            "- Build preferences from actual behavior, not guesses"
        ),
        "limit": 3000,
    },
    {
        "label": "session_patterns",
        "description": "Recurring behaviors, time-based patterns, common struggles.",
        "value": "(No patterns observed yet.)",
        "limit": 3000,
    },
    {
        "label": "tool_guidelines",
        "description": "Tool usage patterns and preferences observed from sessions.",
        "value": "(No tool patterns yet.)",
        "limit": 3000,
    },
    {
        "label": "user_preferences",
        "description": "Learned coding style, tool preferences, and communication style.",
        "value": "(No user preferences yet.)",
        "limit": 3000,
    },
]


class SubconsciousMemory:
    """Persistent cross-session memory for the subconscious agent.

    Memory blocks are stored in a JSON file and survive restarts.
    Each block has a label, description, value, and character limit.
    """

    _FILENAME = "subconscious_memory.json"

    def __init__(self, workspace: Path):
        self._path = workspace / self._FILENAME
        self._blocks: dict[str, MemoryBlock] = {}
        self._session_state: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        """Load memory blocks from disk, initializing defaults if needed."""
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                for block_data in data.get("blocks", []):
                    block = MemoryBlock.from_dict(block_data)
                    self._blocks[block.label] = block
                self._session_state = data.get("session_state", {})
            except Exception as e:
                logger.warning("Failed to load subconscious memory: {}", e)

        # Ensure all default blocks exist
        for default in _DEFAULT_BLOCKS:
            if default["label"] not in self._blocks:
                self._blocks[default["label"]] = MemoryBlock(
                    label=default["label"],
                    description=default["description"],
                    value=default["value"],
                    limit=default.get("limit", 5000),
                    updated_at=time.time(),
                )
        self._save()

    def _save(self) -> None:
        """Persist memory blocks to disk."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "blocks": [b.to_dict() for b in self._blocks.values()],
                "session_state": self._session_state,
                "saved_at": time.time(),
            }
            self._path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning("Failed to save subconscious memory: {}", e)

    def get_block(self, label: str) -> MemoryBlock | None:
        return self._blocks.get(label)

    def get_all_blocks(self) -> list[MemoryBlock]:
        return list(self._blocks.values())

    def update_block(self, label: str, value: str) -> bool:
        """Update a block's value, respecting the character limit."""
        block = self._blocks.get(label)
        if not block:
            return False
        block.value = value[: block.limit]
        block.updated_at = time.time()
        self._save()
        return True

    def get_block_values(self) -> dict[str, str]:
        """Get all block label→value mappings (for diffing)."""
        return {b.label: b.value for b in self._blocks.values()}

    def create_block(self, label: str, description: str, value: str, limit: int = 3000) -> bool:
        """Create a new memory block (max 12 blocks)."""
        if len(self._blocks) >= 12:
            logger.warning("Subconscious memory at block limit (12)")
            return False
        self._blocks[label] = MemoryBlock(
            label=label,
            description=description,
            value=value[:limit],
            limit=limit,
            updated_at=time.time(),
        )
        self._save()
        return True

    def delete_block(self, label: str) -> bool:
        """Delete a memory block (cannot delete core blocks)."""
        core_labels = {d["label"] for d in _DEFAULT_BLOCKS}
        if label in core_labels:
            return False
        if label in self._blocks:
            del self._blocks[label]
            self._save()
            return True
        return False

    def get_session_state(self) -> dict[str, Any]:
        return dict(self._session_state)

    def update_session_state(self, **kwargs) -> None:
        """Update tracked session state (e.g. last_processed_index, session_id)."""
        self._session_state.update(kwargs)
        self._save()


# System prompt for the subconscious agent when processing transcripts
_SUBCONSCIOUS_SYSTEM_PROMPT = """\
You are the Subconscious — a persistent memory layer that observes conversation transcripts.

You receive session transcripts and must analyze them to update memory blocks.
Your updates will be injected into future sessions as guidance.

Current memory blocks:
{blocks}

Session metadata:
{session_metadata}

INSTRUCTIONS:
1. Read the transcript carefully
2. Extract: user preferences, project context, patterns, pending work, corrections
3. Update the relevant memory blocks with new observations
4. Write to the guidance block if you have something useful for the next interaction
5. Be concise and specific — every line must earn its place
6. Merge new observations with existing block content — don't overwrite unless outdated

Output a JSON object with the blocks you want to update:
{{
  "updates": {{
    "block_label": "new full value for this block",
    ...
  }},
  "reasoning": "brief explanation of what you observed"
}}

Only include blocks that actually need updating. Omit unchanged blocks.
Keep each block under its character limit. Be specific, not generic."""


class Subconscious:
    """Background agent that watches transcripts and provides persistent context.

    Features (ported from claude-subconscious):
    1. 8 persistent memory blocks (JSON-backed)
    2. Background transcript processing after each conversation turn (Stop hook)
    3. Guidance injection before each LLM call (UserPromptSubmit hook)
    4. Lightweight mid-task sync during Autopilot (PreToolUse hook — no LLM, just diff)
    5. Smart block diffing (only inject changed blocks)
    6. Cross-session persistence with session state tracking
    7. Session start metadata recording (SessionStart hook)
    """

    def __init__(
        self,
        provider: LLMProvider,
        model: str,
        workspace: Path,
        *,
        mode: str = "whisper",  # whisper | full | off
    ):
        self.provider = provider
        self.model = model
        self.workspace = workspace
        self.mode = mode
        self.memory = SubconsciousMemory(workspace)
        self._last_injected_values: dict[str, str] = {}
        self._processing_lock = asyncio.Lock()
        self._task_count = 0
        self._session_message_count = 0
        # Prime the diff cache from current state so first injection is full,
        # subsequent injections only show changes
        self._last_injected_values = self.memory.get_block_values()
        logger.info("Subconscious initialized: mode={}, blocks={}", mode, len(self.memory.get_all_blocks()))

    # ── SessionStart hook ──

    def on_session_start(self, session_key: str, channel: str | None = None) -> None:
        """Record session metadata (maps to claude-subconscious SessionStart hook).

        Called once when a new session begins. Records metadata for context.
        """
        self._session_message_count = 0
        self.memory.update_session_state(
            current_session=session_key,
            session_start=time.time(),
            channel=channel or "unknown",
            last_processed_index=0,
        )
        logger.debug("Subconscious session started: {}", session_key)

    # ── UserPromptSubmit hook ──

    def inject_guidance(self) -> str | None:
        """Get guidance content to inject before the next LLM call.

        Maps to claude-subconscious UserPromptSubmit hook.
        On first call in a session: returns all active blocks (full sync).
        On subsequent calls: returns only changed blocks (smart diff).

        Returns formatted guidance string or None if mode is 'off' or no guidance.
        In 'whisper' mode: only guidance + pending_items + user_preferences.
        In 'full' mode: returns all memory blocks.
        """
        if self.mode == "off":
            return None

        self._session_message_count += 1

        # First message in session: full injection to establish context
        if self._session_message_count == 1:
            blocks = self.memory.get_all_blocks()
            if not blocks:
                return None
            # Update diff cache
            self._last_injected_values = self.memory.get_block_values()
            if self.mode == "whisper":
                return self._format_whisper(blocks)
            elif self.mode == "full":
                return self._format_full(blocks)
            return None

        # Subsequent messages: only inject changed blocks (smart diff)
        return self.inject_guidance_diff()

    def inject_guidance_diff(self) -> str | None:
        """Get only changed guidance blocks since last injection.

        Uses smart diffing to avoid re-injecting unchanged context.
        Returns None if nothing changed.
        """
        if self.mode == "off":
            return None

        current_values = self.memory.get_block_values()
        changed_labels: list[str] = []

        for label, value in current_values.items():
            if self._last_injected_values.get(label) != value:
                changed_labels.append(label)

        if not changed_labels:
            return None

        # Update cache
        self._last_injected_values = dict(current_values)

        # Format only changed blocks
        blocks = [b for b in self.memory.get_all_blocks() if b.label in changed_labels]
        if not blocks:
            return None

        parts = ["[Subconscious Update — changed blocks]"]
        for block in blocks:
            if block.value and not block.value.startswith("(No "):
                parts.append(f"<{block.label}>\n{block.value}\n</{block.label}>")

        return "\n\n".join(parts) if len(parts) > 1 else None

    # ── PreToolUse hook ──

    def mid_task_sync(self) -> str | None:
        """Lightweight mid-task check for updated memory blocks.

        Maps to claude-subconscious PreToolUse hook. This does NOT call the LLM.
        It simply checks if the background transcript processing has updated any
        memory blocks since the last injection, and returns the diff if so.

        This is efficient because:
        - In claude-subconscious, PreToolUse fetches from Letta API (fast GET)
        - Here, we just compare in-memory block values (even faster)
        - The background process_transcript() may have updated blocks asynchronously
        """
        return self.inject_guidance_diff()

    def _format_whisper(self, blocks: list[MemoryBlock]) -> str | None:
        """Format guidance in whisper mode — only active guidance + pending items."""
        parts: list[str] = []

        guidance = self.memory.get_block("guidance")
        if guidance and guidance.value and not guidance.value.startswith("(No "):
            parts.append(f"<subconscious_guidance>\n{guidance.value}\n</subconscious_guidance>")

        pending = self.memory.get_block("pending_items")
        if pending and pending.value and not pending.value.startswith("(No "):
            parts.append(f"<pending_items>\n{pending.value}\n</pending_items>")

        user_prefs = self.memory.get_block("user_preferences")
        if user_prefs and user_prefs.value and not user_prefs.value.startswith("(No "):
            parts.append(f"<user_preferences>\n{user_prefs.value}\n</user_preferences>")

        if not parts:
            return None

        header = (
            "[Subconscious Layer — persistent memory across sessions]\n"
            "The following context was accumulated by the background Subconscious agent "
            "from observing past sessions. Reference as needed.\n"
        )
        return header + "\n\n".join(parts)

    def _format_full(self, blocks: list[MemoryBlock]) -> str | None:
        """Format all memory blocks for full injection."""
        active_blocks = [b for b in blocks if b.value and not b.value.startswith("(No ")]
        if not active_blocks:
            return None

        header = (
            "[Subconscious Layer — persistent memory across sessions]\n"
            "The following memory blocks are maintained by the background Subconscious agent. "
            "They persist across sessions and are updated after each conversation.\n"
        )

        formatted = []
        for block in active_blocks:
            formatted.append(
                f'<{block.label} description="{block.description}">\n'
                f"{block.value}\n"
                f"</{block.label}>"
            )

        return header + "\n\n".join(formatted)

    # ── Stop hook ──

    async def process_transcript(
        self,
        messages: list[dict[str, Any]],
        session_key: str = "",
    ) -> None:
        """Process a conversation transcript in the background.

        Maps to claude-subconscious Stop hook (send_messages_to_letta.ts).
        Sends the transcript to the LLM as the subconscious agent,
        which analyzes it and updates memory blocks.

        Includes retry logic — if the LLM call fails, it will retry once.
        """
        if self.mode == "off":
            return

        # Don't process very short conversations
        if len(messages) < 3:
            return

        # Avoid concurrent processing (like claude-subconscious's lock)
        if self._processing_lock.locked():
            logger.debug("Subconscious already processing, skipping")
            return

        async with self._processing_lock:
            self._task_count += 1
            task_id = self._task_count

            # Only process new messages since last processing
            state = self.memory.get_session_state()
            last_idx = state.get("last_processed_index", 0)
            new_messages = messages[last_idx:] if last_idx > 0 else messages
            if len(new_messages) < 2:
                return

            logger.info(
                "Subconscious processing transcript (task {}): {} new messages (from idx {})",
                task_id, len(new_messages), last_idx,
            )

            max_attempts = 2
            for attempt in range(1, max_attempts + 1):
                try:
                    await self._analyze_transcript(new_messages, session_key)
                    # Update last processed index on success
                    self.memory.update_session_state(last_processed_index=len(messages))
                    logger.info("Subconscious transcript processing complete (task {})", task_id)
                    break
                except Exception as e:
                    logger.warning(
                        "Subconscious transcript processing failed (attempt {}/{}): {}",
                        attempt, max_attempts, e,
                    )
                    if attempt < max_attempts:
                        await asyncio.sleep(1)

    async def _analyze_transcript(self, messages: list[dict[str, Any]], session_key: str = "") -> None:
        """Send transcript to LLM for analysis and update blocks."""
        blocks_text = self._format_blocks_for_prompt()
        transcript = self._format_transcript(messages[-50:])

        # Build session metadata for context
        state = self.memory.get_session_state()
        session_metadata = (
            f"Session: {session_key or state.get('current_session', 'unknown')}\n"
            f"Channel: {state.get('channel', 'unknown')}\n"
            f"Messages in this batch: {len(messages)}\n"
            f"Total tasks processed: {self._task_count}"
        )

        system_prompt = _SUBCONSCIOUS_SYSTEM_PROMPT.format(
            blocks=blocks_text,
            session_metadata=session_metadata,
        )
        llm_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Analyze this transcript and update memory blocks:\n\n{transcript}"},
        ]

        response = await self.provider.chat_with_retry(
            messages=llm_messages,
            model=self.model,
        )
        if response.content:
            self._apply_updates(response.content)

    def _format_blocks_for_prompt(self) -> str:
        """Format all memory blocks as text for LLM prompt."""
        lines = []
        for block in self.memory.get_all_blocks():
            lines.append(
                f"[{block.label}] (limit: {block.limit} chars)\n"
                f"Description: {block.description}\n"
                f"Current value:\n{block.value}\n"
            )
        return "\n---\n".join(lines)

    def _format_transcript(self, messages: list[dict[str, Any]]) -> str:
        """Format messages into readable transcript text."""
        lines = []
        for m in messages:
            role = m.get("role", "unknown").upper()
            content = m.get("content", "")
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                content = " ".join(text_parts)
            if content:
                text = str(content)
                if len(text) > 1000:
                    text = text[:1000] + "... (truncated)"
                lines.append(f"[{role}]: {text}")

            # Include tool call info
            tool_calls = m.get("tool_calls", [])
            if tool_calls:
                for tc in tool_calls:
                    func = tc.get("function", {})
                    name = func.get("name", "unknown")
                    args = func.get("arguments", "")
                    if isinstance(args, str) and len(args) > 200:
                        args = args[:200] + "..."
                    lines.append(f"[TOOL_CALL]: {name}({args})")

        return "\n".join(lines)

    def _apply_updates(self, response_text: str) -> None:
        """Parse LLM response and apply memory block updates."""
        try:
            json_text = response_text.strip()
            if json_text.startswith("```"):
                lines = json_text.split("\n")
                json_lines = []
                in_block = False
                for line in lines:
                    if line.strip().startswith("```") and not in_block:
                        in_block = True
                        continue
                    elif line.strip().startswith("```") and in_block:
                        break
                    elif in_block:
                        json_lines.append(line)
                json_text = "\n".join(json_lines)

            data = json.loads(json_text)
            updates = data.get("updates", {})
            reasoning = data.get("reasoning", "")

            if not updates:
                logger.debug("Subconscious: no updates needed ({})", reasoning)
                return

            updated_count = 0
            for label, value in updates.items():
                if isinstance(value, str) and value.strip():
                    if self.memory.update_block(label, value):
                        updated_count += 1
                        logger.debug("Subconscious updated block: {}", label)

            if updated_count > 0:
                logger.info(
                    "Subconscious updated {} block(s): {}",
                    updated_count, reasoning[:200],
                )
        except json.JSONDecodeError:
            logger.debug("Subconscious: could not parse LLM response as JSON")
        except Exception as e:
            logger.warning("Subconscious: failed to apply updates: {}", e)

    def get_stats(self) -> dict[str, Any]:
        """Get subconscious statistics for status display."""
        blocks = self.memory.get_all_blocks()
        total_chars = sum(len(b.value) for b in blocks)
        active_blocks = sum(1 for b in blocks if not b.value.startswith("(No "))
        return {
            "mode": self.mode,
            "total_blocks": len(blocks),
            "active_blocks": active_blocks,
            "total_chars": total_chars,
            "tasks_processed": self._task_count,
            "session_messages": self._session_message_count,
        }

