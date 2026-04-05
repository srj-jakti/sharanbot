"""Human-in-the-Loop Escalation — lets SharanAGI ask for help.

A human knows when to ask a colleague for help. SharanAGI does the same:

1. **CAPTCHA Escalation** — detects CAPTCHAs, sends screenshot to user,
   waits for solution, continues.
2. **2FA/Verification** — detects phone/email verification, asks user to
   complete it, continues.
3. **Judgment Call** — when confidence is low, asks user to confirm before
   proceeding.
4. **Stuck Escalation** — when the agent is going in circles, asks user
   for guidance.

The escalation system works across all channels — it sends the question
to the user's active channel and waits for a response with a timeout.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine

from loguru import logger


class EscalationType(str, Enum):
    CAPTCHA = "captcha"
    VERIFICATION = "verification"
    JUDGMENT = "judgment"
    STUCK = "stuck"
    PERMISSION = "permission"
    UNKNOWN = "unknown"


@dataclass
class EscalationRequest:
    """A request for human help."""

    id: str
    type: EscalationType
    question: str
    context: str = ""
    screenshot_b64: str | None = None
    options: list[str] | None = None  # for multiple-choice
    timeout_s: int = 300  # 5 minutes default
    created_at: float = field(default_factory=time.time)
    resolved: bool = False
    response: str | None = None


class EscalationManager:
    """Manages human-in-the-loop escalation requests.

    When the agent encounters something it can't handle alone (CAPTCHA,
    2FA, ambiguous instructions, stuck loops), it creates an escalation
    request. The request is sent to the user's active channel, and the
    agent waits for a response.

    Usage from tools/agent loop:
        response = await escalation.ask_human(
            type=EscalationType.CAPTCHA,
            question="I need you to solve this CAPTCHA",
            screenshot_b64=screenshot_data,
        )
    """

    def __init__(
        self,
        on_send: Callable[[str, str | None], Coroutine[Any, Any, None]] | None = None,
        default_timeout_s: int = 300,
    ):
        """
        Args:
            on_send: async callback(message, screenshot_b64) to send to user's channel
            default_timeout_s: how long to wait for human response
        """
        self._on_send = on_send
        self._default_timeout = default_timeout_s
        self._pending: dict[str, EscalationRequest] = {}
        self._response_events: dict[str, asyncio.Event] = {}
        self._counter = 0
        self._stats = {
            "total_requests": 0,
            "resolved": 0,
            "timed_out": 0,
            "by_type": {},
        }

    async def ask_human(
        self,
        type: EscalationType,
        question: str,
        context: str = "",
        screenshot_b64: str | None = None,
        options: list[str] | None = None,
        timeout_s: int | None = None,
    ) -> str | None:
        """Ask the human for help and wait for their response.

        Returns the human's response string, or None if timed out.
        """
        self._counter += 1
        req_id = f"esc_{self._counter}_{int(time.time())}"
        timeout = timeout_s or self._default_timeout

        req = EscalationRequest(
            id=req_id,
            type=type,
            question=question,
            context=context,
            screenshot_b64=screenshot_b64,
            options=options,
            timeout_s=timeout,
        )
        self._pending[req_id] = req
        self._response_events[req_id] = asyncio.Event()

        self._stats["total_requests"] += 1
        type_key = type.value
        self._stats["by_type"][type_key] = self._stats["by_type"].get(type_key, 0) + 1

        # Send the question to the user
        message = self._format_escalation_message(req)
        if self._on_send:
            try:
                await self._on_send(message, screenshot_b64)
            except Exception as e:
                logger.error("Escalation: failed to send to user: {}", e)
                self._cleanup(req_id)
                return None

        logger.info("Escalation [{}]: waiting for human response (timeout {}s)", req_id, timeout)

        # Wait for response with timeout
        try:
            await asyncio.wait_for(
                self._response_events[req_id].wait(),
                timeout=timeout,
            )
            response = self._pending[req_id].response
            self._stats["resolved"] += 1
            logger.info("Escalation [{}]: human responded", req_id)
            self._cleanup(req_id)
            return response
        except asyncio.TimeoutError:
            self._stats["timed_out"] += 1
            logger.warning("Escalation [{}]: timed out after {}s", req_id, timeout)
            self._cleanup(req_id)
            return None

    def receive_response(self, text: str) -> bool:
        """Called when the user responds to an escalation.

        The channel handler calls this when it detects the user is
        replying to an escalation (message starts with /answer or
        is received while an escalation is pending).

        Returns True if the response was matched to a pending escalation.
        """
        # Match to the most recent pending escalation
        for req_id in reversed(list(self._pending.keys())):
            req = self._pending[req_id]
            if not req.resolved:
                req.response = text
                req.resolved = True
                event = self._response_events.get(req_id)
                if event:
                    event.set()
                return True
        return False

    def has_pending(self) -> bool:
        """Check if there are any pending escalation requests."""
        return any(not r.resolved for r in self._pending.values())

    def get_pending(self) -> list[EscalationRequest]:
        """Get all pending (unresolved) escalation requests."""
        return [r for r in self._pending.values() if not r.resolved]

    def _format_escalation_message(self, req: EscalationRequest) -> str:
        """Format an escalation request as a user-friendly message."""
        icon = {
            EscalationType.CAPTCHA: "🔐",
            EscalationType.VERIFICATION: "📱",
            EscalationType.JUDGMENT: "🤔",
            EscalationType.STUCK: "🔄",
            EscalationType.PERMISSION: "🔑",
            EscalationType.UNKNOWN: "❓",
        }.get(req.type, "❓")

        parts = [
            f"{icon} **SharanAGI needs your help** [{req.type.value}]",
            "",
            req.question,
        ]
        if req.context:
            parts.append(f"\nContext: {req.context}")
        if req.options:
            parts.append("\nOptions:")
            for i, opt in enumerate(req.options, 1):
                parts.append(f"  {i}. {opt}")
        parts.append(f"\n⏱️ Waiting for your response (timeout: {req.timeout_s}s)")
        parts.append("Reply with your answer to continue.")
        return "\n".join(parts)

    def _cleanup(self, req_id: str) -> None:
        self._pending.pop(req_id, None)
        self._response_events.pop(req_id, None)

    def get_stats(self) -> dict[str, Any]:
        return dict(self._stats)


# ---------------------------------------------------------------------------
# Detection helpers — used by browser/computer tools to detect escalation needs
# ---------------------------------------------------------------------------

_CAPTCHA_INDICATORS = [
    "captcha", "recaptcha", "hcaptcha", "verify you are human",
    "i'm not a robot", "prove you're not a robot", "challenge",
    "security check", "bot detection", "cloudflare",
]

_VERIFICATION_INDICATORS = [
    "verify your phone", "enter the code", "verification code",
    "two-factor", "2fa", "authenticator", "sms code",
    "confirm your identity", "enter otp",
]


def detect_escalation_need(page_text: str) -> EscalationType | None:
    """Analyze page text to detect if human help is needed.

    Called by browser/computer tools after navigating or taking screenshots.
    Returns the escalation type needed, or None if no help needed.
    """
    text_lower = page_text.lower()

    for indicator in _CAPTCHA_INDICATORS:
        if indicator in text_lower:
            return EscalationType.CAPTCHA

    for indicator in _VERIFICATION_INDICATORS:
        if indicator in text_lower:
            return EscalationType.VERIFICATION

    return None
