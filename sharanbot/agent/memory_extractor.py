"""Regex-based memory extraction — fast fact extraction from conversations.

Extracts memory-worthy facts without LLM calls. Supports explicit
commands ("remember X", "forget X") and implicit extraction of personal
facts, preferences, and ownership statements.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class GuardLevel(Enum):
    STRICT = 0.85
    STANDARD = 0.65
    RELAXED = 0.50


@dataclass
class ExtractedMemory:
    action: str  # "add" or "delete"
    text: str
    confidence: float
    is_explicit: bool
    reason: str


# Explicit memory commands
_EXPLICIT_ADD = re.compile(
    r"(?:remember|note|save|record|keep in mind|store|memorize)\s+(?:that\s+)?(.+)",
    re.IGNORECASE,
)
_EXPLICIT_DELETE = re.compile(
    r"(?:forget|remove|delete|erase|clear)\s+(?:that\s+|about\s+)?(.+)",
    re.IGNORECASE,
)

# Implicit patterns with confidence scores
_IMPLICIT_PATTERNS: list[tuple[re.Pattern, float, str]] = [
    # Personal-profile (highest confidence)
    (re.compile(r"(?:my name is|i'?m called|call me)\s+(\S+)", re.I), 0.93, "personal_name"),
    (re.compile(r"i (?:am|'m) (?:a |an )?(\w+(?:\s+\w+)?)\s+(?:developer|engineer|designer|manager|student)", re.I), 0.93, "personal_role"),
    (re.compile(r"i (?:live|reside|stay) (?:in|at)\s+(.+?)(?:\.|$)", re.I), 0.90, "personal_location"),
    (re.compile(r"i (?:speak|know|use)\s+([\w\s,]+?)\s+(?:language|programming)", re.I), 0.88, "language"),
    # Ownership
    (re.compile(r"(?:my|our)\s+(?:company|team|org(?:anization)?)\s+(?:is|called|named)\s+(.+?)(?:\.|$)", re.I), 0.90, "ownership"),
    (re.compile(r"(?:my|our)\s+(?:project|app|product)\s+(?:is|called|named)\s+(.+?)(?:\.|$)", re.I), 0.90, "ownership"),
    # Preferences
    (re.compile(r"i (?:prefer|like|always use|love)\s+(.+?)(?:\s+(?:for|over|instead)|\.|\s*$)", re.I), 0.88, "preference"),
    (re.compile(r"(?:always|never)\s+(.+?)(?:\.|$)", re.I), 0.85, "preference"),
    (re.compile(r"(?:my|our)\s+(?:coding |development )?(?:style|convention|standard)\s+(?:is|uses)\s+(.+?)(?:\.|$)", re.I), 0.88, "preference"),
    # Assistant preferences
    (re.compile(r"(?:respond|reply|answer|write)\s+(?:in|with|using)\s+(.+?)(?:\.|$)", re.I), 0.86, "assistant_pref"),
    (re.compile(r"(?:use|don'?t use)\s+(tabs|spaces|semicolons|single quotes|double quotes)", re.I), 0.88, "assistant_pref"),
    # Technical facts
    (re.compile(r"(?:we|i|our team)\s+(?:deploy|host|run)\s+(?:on|with)\s+(.+?)(?:\.|$)", re.I), 0.85, "tech_fact"),
    (re.compile(r"(?:our|my)\s+(?:stack|tech stack|infrastructure)\s+(?:is|includes|uses)\s+(.+?)(?:\.|$)", re.I), 0.85, "tech_fact"),
]

# Filters
_QUESTION_MARKERS = re.compile(r"(?:^|\s)(?:what|how|why|where|when|who|can you|could you|would you|is there)\b", re.I)
_SMALL_TALK = re.compile(r"^(?:hi|hello|hey|thanks|thank you|ok|okay|sure|great|nice|cool|good|bye|goodbye)\b", re.I)
_PROCEDURAL = re.compile(r"(?:run|execute|install|create|build|deploy|start|stop|restart|open|close|save|delete)\s", re.I)
_CODE_BLOCK = re.compile(r"```[\s\S]*?```")


def _is_question(text: str) -> bool:
    return bool(_QUESTION_MARKERS.search(text)) or text.rstrip().endswith("?")


def _is_small_talk(text: str) -> bool:
    return bool(_SMALL_TALK.match(text.strip()))


def _is_procedural(text: str) -> bool:
    return bool(_PROCEDURAL.match(text.strip()))


def _sanitize(text: str) -> str:
    """Strip code blocks and normalize whitespace."""
    text = _CODE_BLOCK.sub("", text)
    return " ".join(text.split()).strip()


def extract_turn_memories(
    user_text: str,
    assistant_text: str = "",
    guard_level: GuardLevel = GuardLevel.STANDARD,
    max_implicit: int = 2,
) -> list[ExtractedMemory]:
    """Extract memories from a user↔assistant turn.

    Returns explicit + implicit memories, filtered by guard level.
    Implicit memories are capped at ``max_implicit`` per turn.
    """
    results: list[ExtractedMemory] = []
    clean_user = _sanitize(user_text)

    if not clean_user or len(clean_user) < 5:
        return results

    # 1. Explicit commands (always honored)
    m = _EXPLICIT_ADD.search(clean_user)
    if m:
        results.append(ExtractedMemory(
            action="add", text=m.group(1).strip(),
            confidence=0.99, is_explicit=True, reason="explicit_add",
        ))

    m = _EXPLICIT_DELETE.search(clean_user)
    if m:
        results.append(ExtractedMemory(
            action="delete", text=m.group(1).strip(),
            confidence=0.99, is_explicit=True, reason="explicit_delete",
        ))

    # 2. Skip implicit extraction for questions, small talk, procedural
    if _is_question(clean_user) or _is_small_talk(clean_user) or _is_procedural(clean_user):
        return results

    # 3. Implicit extraction
    implicit: list[ExtractedMemory] = []
    threshold = guard_level.value

    for pattern, confidence, reason in _IMPLICIT_PATTERNS:
        if confidence < threshold:
            continue
        match = pattern.search(clean_user)
        if match:
            text = match.group(1).strip() if match.lastindex else match.group(0).strip()
            if len(text) < 3 or len(text) > 200:
                continue
            implicit.append(ExtractedMemory(
                action="add", text=f"{reason}: {text}",
                confidence=confidence, is_explicit=False, reason=reason,
            ))

    # Sort by confidence, take top N
    implicit.sort(key=lambda e: e.confidence, reverse=True)
    results.extend(implicit[:max_implicit])

    return results


def detect_user_correction(text: str) -> bool:
    """Detect if the user is correcting the assistant."""
    correction_patterns = [
        r"no[,.]?\s+(?:do|use|try|make|it)",
        r"that'?s (?:wrong|incorrect|not right|not what)",
        r"i (?:said|meant|wanted)",
        r"(?:actually|instead)[,.]",
        r"not (?:that|this|like)",
        r"you (?:should|need to|must)",
        r"(?:wrong|incorrect|bad|broken)",
    ]
    return any(re.search(p, text, re.I) for p in correction_patterns)


def detect_positive_feedback(text: str) -> bool:
    """Detect positive user feedback."""
    positive_patterns = [
        r"\b(?:good|great|perfect|excellent|nice|awesome|wonderful|amazing|correct|right)\b",
        r"\bthank(?:s| you)\b",
        r"\blooks? good\b",
        r"\bwell done\b",
        r"\b(?:that'?s|this is) (?:it|correct|right|perfect|great)\b",
    ]
    return any(re.search(p, text, re.I) for p in positive_patterns)
