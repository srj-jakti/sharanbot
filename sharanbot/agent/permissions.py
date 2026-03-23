"""Permission manager — classifies and gates dangerous operations.

Classifies commands into safety levels:
- SAFE: No risk (read-only operations, basic shell commands)
- APPROVAL_REQUIRED: Potentially destructive (rm -rf, del /f, format, etc.)
- ELEVATED: Requires privilege escalation (sudo, apt install, npm -g, etc.)
- HARD_BLOCKED: Never allowed (fork bombs, host process kills)

The manager can auto-approve SAFE commands, prompt for APPROVAL_REQUIRED,
wrap ELEVATED commands if configured, and always block HARD_BLOCKED.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

from loguru import logger


class PermissionLevel(Enum):
    SAFE = "safe"
    APPROVAL_REQUIRED = "approval_required"
    ELEVATED = "elevated"
    HARD_BLOCKED = "hard_blocked"


class ApprovalResult(Enum):
    APPROVED = "approved"
    DENIED = "denied"
    TIMEOUT = "timeout"


@dataclass
class PermissionRequest:
    command: str
    level: PermissionLevel
    reason: str
    request_time: float = field(default_factory=time.time)
    request_id: str = ""
    result: ApprovalResult | None = None
    responder: str = ""


# Patterns that require explicit user approval
APPROVAL_PATTERNS: list[re.Pattern] = [
    re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|--force)\b", re.IGNORECASE),
    re.compile(r"\bdel\s+/[fF]\b"),
    re.compile(r"\bformat\s+[a-zA-Z]:", re.IGNORECASE),
    re.compile(r"\bdd\s+.*\bof=\s*/dev/", re.IGNORECASE),
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r"\breboot\b", re.IGNORECASE),
    re.compile(r"\btaskkill\s+/[fF]\b", re.IGNORECASE),
    re.compile(r"\bkill\s+-9\b"),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"\bgit\s+push\s+.*--force\b", re.IGNORECASE),
    re.compile(r"\bgit\s+reset\s+--hard\b", re.IGNORECASE),
    re.compile(r"\bdrop\s+(table|database)\b", re.IGNORECASE),
]

# Patterns that are NEVER allowed
HARD_BLOCK_PATTERNS: list[re.Pattern] = [
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;"),  # fork bomb
    re.compile(r"\bkill\s+-9\s+1\b"),  # kill init
    re.compile(r"\bkillall\b"),
    re.compile(r"\brm\s+-rf\s+/\s*$"),  # rm -rf / (root)
    re.compile(r"\bchmod\s+-R\s+777\s+/\s*$"),  # chmod 777 /
]

# Patterns that need privilege elevation
ELEVATION_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bsudo\b"),
    re.compile(r"\bgsudo\b"),
    re.compile(r"\bsystemctl\b"),
    re.compile(r"\bapt(-get)?\s+(install|remove|purge)\b", re.IGNORECASE),
    re.compile(r"\byum\s+(install|remove)\b", re.IGNORECASE),
    re.compile(r"\bdnf\s+(install|remove)\b", re.IGNORECASE),
    re.compile(r"\bpip\s+install\b", re.IGNORECASE),
    re.compile(r"\bnpm\s+(install|i)\s+(-g|--global)\b", re.IGNORECASE),
    re.compile(r"\bchown\b"),
]


def classify_command(command: str) -> tuple[PermissionLevel, str]:
    """Classify a command by its risk level.

    Returns (level, reason).
    """
    for pat in HARD_BLOCK_PATTERNS:
        if pat.search(command):
            return PermissionLevel.HARD_BLOCKED, f"Blocked pattern: {pat.pattern}"

    for pat in APPROVAL_PATTERNS:
        if pat.search(command):
            return PermissionLevel.APPROVAL_REQUIRED, f"Destructive pattern: {pat.pattern}"

    for pat in ELEVATION_PATTERNS:
        if pat.search(command):
            return PermissionLevel.ELEVATED, f"Elevation required: {pat.pattern}"

    return PermissionLevel.SAFE, "No risky patterns detected"


def wrap_with_elevation(command: str) -> str:
    """Wrap a command with platform-appropriate elevation if needed."""
    import platform
    if platform.system() == "Windows":
        if not command.strip().lower().startswith("gsudo"):
            return f"gsudo {command}"
    else:
        if not command.strip().startswith("sudo"):
            return f"sudo {command}"
    return command


class PermissionManager:
    """Manages command approval workflow.

    Integrates with agent loop to:
    1. Classify commands before execution
    2. Block dangerous patterns
    3. Request user approval for destructive ops
    4. Wrap commands with elevation when needed
    """

    APPROVAL_TIMEOUT_S: float = 120.0

    def __init__(
        self,
        auto_approve_safe: bool = True,
        prompt_callback: Callable[[PermissionRequest], Awaitable[ApprovalResult]] | None = None,
    ):
        self._auto_approve = auto_approve_safe
        self._prompt_callback = prompt_callback
        self._pending: dict[str, PermissionRequest] = {}
        self._history: list[PermissionRequest] = []
        self._counter = 0

    async def check_and_approve(self, command: str) -> tuple[ApprovalResult, str]:
        """Check a command and return (result, possibly-modified command).

        Returns:
            (APPROVED, command) — safe to run (command may be wrapped for elevation)
            (DENIED, reason) — must not run
            (TIMEOUT, reason) — user didn't respond in time
        """
        level, reason = classify_command(command)

        if level == PermissionLevel.HARD_BLOCKED:
            logger.warning("BLOCKED command: {} ({})", command[:80], reason)
            return ApprovalResult.DENIED, f"Hard-blocked: {reason}"

        if level == PermissionLevel.SAFE and self._auto_approve:
            return ApprovalResult.APPROVED, command

        if level == PermissionLevel.ELEVATED:
            command = wrap_with_elevation(command)

        # Need approval
        self._counter += 1
        req = PermissionRequest(
            command=command,
            level=level,
            reason=reason,
            request_id=f"perm-{self._counter}",
        )
        self._pending[req.request_id] = req

        if self._prompt_callback:
            try:
                result = await asyncio.wait_for(
                    self._prompt_callback(req),
                    timeout=self.APPROVAL_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                result = ApprovalResult.TIMEOUT
        else:
            # No callback — auto-approve non-blocked
            result = ApprovalResult.APPROVED

        req.result = result
        self._pending.pop(req.request_id, None)
        self._history.append(req)

        if result == ApprovalResult.APPROVED:
            return ApprovalResult.APPROVED, command
        elif result == ApprovalResult.TIMEOUT:
            return ApprovalResult.TIMEOUT, "Approval timed out"
        else:
            return ApprovalResult.DENIED, "Denied by user"

    def respond(self, request_id: str, approved: bool, responder: str = "") -> bool:
        """Respond to a pending approval request (for async flows)."""
        req = self._pending.get(request_id)
        if req is None:
            return False
        req.result = ApprovalResult.APPROVED if approved else ApprovalResult.DENIED
        req.responder = responder
        return True

    def get_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent permission decisions."""
        return [
            {
                "command": r.command[:100],
                "level": r.level.value,
                "result": r.result.value if r.result else "pending",
                "reason": r.reason,
                "time": r.request_time,
            }
            for r in self._history[-limit:]
        ]
