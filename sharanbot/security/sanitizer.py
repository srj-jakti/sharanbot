"""Input sanitizer — strips prompt‑injection markers and dangerous patterns.

Provides two primary utilities:
1. ``sanitize_search_query`` — clean up user search input
2. ``strip_prompt_injection_markers`` — remove known LLM system‑prompt markers
3. ``validate_config_update`` — restrict config changes to known safe keys
"""

from __future__ import annotations

import re

from loguru import logger

# Shell metacharacters that can enable command injection
_SHELL_METACHARACTERS = re.compile(r"[;&|`$(){}]")

# Known prompt‑injection delimiters used by various LLMs
_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"<\|system\|>", re.IGNORECASE),
    re.compile(r"<\|user\|>", re.IGNORECASE),
    re.compile(r"<\|assistant\|>", re.IGNORECASE),
    re.compile(r"\[INST\]", re.IGNORECASE),
    re.compile(r"\[/INST\]", re.IGNORECASE),
    re.compile(r"###\s*(System|User|Assistant)\s*:", re.IGNORECASE),
    re.compile(r"<<SYS>>", re.IGNORECASE),
    re.compile(r"<</SYS>>", re.IGNORECASE),
    re.compile(r"<\|im_start\|>", re.IGNORECASE),
    re.compile(r"<\|im_end\|>", re.IGNORECASE),
    re.compile(r"<\|endoftext\|>", re.IGNORECASE),
    re.compile(r"IGNORE PREVIOUS INSTRUCTIONS", re.IGNORECASE),
    re.compile(r"YOU ARE NOW", re.IGNORECASE),
]


def sanitize_search_query(query: str, *, max_length: int = 500) -> str:
    """Clean a user-supplied search query.

    - Strips prompt‑injection markers
    - Removes shell metacharacters
    - Truncates to max_length
    """
    text = strip_prompt_injection_markers(query)
    text = _SHELL_METACHARACTERS.sub("", text)
    text = " ".join(text.split())  # collapse whitespace
    if len(text) > max_length:
        text = text[:max_length]
    return text


def strip_prompt_injection_markers(text: str) -> str:
    """Remove all known LLM prompt‑injection delimiters from *text*."""
    cleaned = text
    for pat in _INJECTION_PATTERNS:
        cleaned = pat.sub("", cleaned)
    if cleaned != text:
        logger.debug("Stripped prompt injection markers from input ({}→{} chars)", len(text), len(cleaned))
    return cleaned


# Safe config keys that may be updated via dynamic config changes
_SAFE_CONFIG_KEYS: frozenset[str] = frozenset({
    "model",
    "temperature",
    "max_tokens",
    "max_iterations",
    "context_window",
    "system_prompt",
    "auto_approve",
    "debug",
    "log_level",
})


def validate_config_update(updates: dict) -> dict:
    """Filter a config‑update dict to only allow known safe keys.

    Returns the filtered dict. Unknown keys are logged and discarded.
    """
    safe: dict = {}
    for key, value in updates.items():
        if key in _SAFE_CONFIG_KEYS:
            safe[key] = value
        else:
            logger.warning("Rejected unknown config key: {}", key)
    return safe
