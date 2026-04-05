"""Stuck Detection + Smart Escalation — knows its limits like a human.

A human knows when they're going in circles. They stop, reassess, and
either try a different approach or ask for help. SharanAGI does the same:

1. **Circular Attempt Detection** — detects when the same tool calls
   are being repeated with similar arguments
2. **Confidence Scoring** — estimates confidence in the current approach
3. **Approach Switching** — when stuck, tries a fundamentally different
   strategy before escalating
4. **Smart Escalation** — when truly stuck, asks the user with context
   about what was tried and what failed
5. **External Resource Search** — searches Stack Overflow, GitHub Issues
   for similar problems before giving up
"""

from __future__ import annotations

import hashlib
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from loguru import logger


@dataclass
class ToolCallRecord:
    """Record of a single tool call for pattern detection."""

    name: str
    args_hash: str
    result_preview: str
    timestamp: float = field(default_factory=time.time)
    success: bool = True


@dataclass
class StuckDiagnosis:
    """Diagnosis of whether the agent is stuck."""

    is_stuck: bool
    confidence: float  # 0.0 to 1.0 — how confident we are it's stuck
    reason: str
    suggestion: str  # what to do about it
    repeated_tools: list[str] = field(default_factory=list)
    attempts: int = 0


class StuckDetector:
    """Monitors tool call patterns to detect when the agent is stuck.

    Plugs into the agent loop — after each tool call, call `record()`.
    Periodically call `diagnose()` to check if the agent is stuck.

    Enhanced with configurable time-based thresholds inspired by
    agent-orchestrator's activity detection system.
    """

    # Thresholds (configurable)
    REPEAT_THRESHOLD = 3  # same tool+args 3 times = likely stuck
    WINDOW_SIZE = 20  # look at last 20 tool calls
    ERROR_STREAK_THRESHOLD = 4  # 4 consecutive errors = stuck
    OSCILLATION_THRESHOLD = 3  # A→B→A→B pattern 3 times = stuck
    IDLE_THRESHOLD_S = 120  # seconds of no activity = idle/stuck
    PROGRESS_STALL_THRESHOLD = 10  # tool calls with no new results = stalled

    def __init__(
        self,
        repeat_threshold: int = REPEAT_THRESHOLD,
        error_streak_threshold: int = ERROR_STREAK_THRESHOLD,
        idle_threshold_s: float = IDLE_THRESHOLD_S,
    ):
        self._history: list[ToolCallRecord] = []
        self._error_streak = 0
        self._total_calls = 0
        self._total_errors = 0
        self._stuck_count = 0  # how many times we've been stuck this session
        self._last_activity: float = time.time()
        self._unique_results: set[str] = set()  # track unique result hashes
        self._progress_stall_count = 0
        # Configurable thresholds
        self._repeat_threshold = repeat_threshold
        self._error_streak_threshold = error_streak_threshold
        self._idle_threshold_s = idle_threshold_s

    def record(self, name: str, arguments: dict[str, Any], result: str, success: bool = True) -> None:
        """Record a tool call for pattern analysis."""
        args_hash = hashlib.md5(
            str(sorted(arguments.items())).encode()
        ).hexdigest()[:12]

        self._history.append(ToolCallRecord(
            name=name,
            args_hash=args_hash,
            result_preview=result[:200] if result else "",
            success=success,
        ))

        self._total_calls += 1
        self._last_activity = time.time()

        if not success:
            self._error_streak += 1
            self._total_errors += 1
        else:
            self._error_streak = 0

        # Track progress: are we getting new/unique results?
        result_hash = hashlib.md5((result or "")[:500].encode()).hexdigest()[:16]
        if result_hash in self._unique_results:
            self._progress_stall_count += 1
        else:
            self._unique_results.add(result_hash)
            self._progress_stall_count = 0

        # Keep history bounded
        if len(self._history) > 100:
            self._history = self._history[-100:]
        if len(self._unique_results) > 500:
            self._unique_results = set(list(self._unique_results)[-200:])

    def diagnose(self) -> StuckDiagnosis:
        """Analyze recent tool calls to determine if the agent is stuck."""
        if len(self._history) < 3:
            return StuckDiagnosis(
                is_stuck=False, confidence=0.0,
                reason="Not enough data", suggestion="Continue working",
            )

        window = self._history[-self.WINDOW_SIZE:]

        # Check 0: Idle timeout — no activity for too long
        idle_s = time.time() - self._last_activity
        if idle_s > self._idle_threshold_s:
            self._stuck_count += 1
            return StuckDiagnosis(
                is_stuck=True,
                confidence=min(0.95, 0.5 + idle_s / (self._idle_threshold_s * 3)),
                reason=f"No activity for {idle_s:.0f}s (threshold: {self._idle_threshold_s}s)",
                suggestion="Agent appears idle. Check if it's waiting for input or has crashed.",
                attempts=0,
            )

        # Check 1: Exact repetition (same tool + same args)
        call_sigs = [f"{r.name}:{r.args_hash}" for r in window]
        sig_counts = Counter(call_sigs)
        most_common_sig, most_common_count = sig_counts.most_common(1)[0]

        if most_common_count >= self._repeat_threshold:
            tool_name = most_common_sig.split(":")[0]
            self._stuck_count += 1
            return StuckDiagnosis(
                is_stuck=True,
                confidence=min(0.95, 0.6 + most_common_count * 0.1),
                reason=f"Repeated '{tool_name}' with same arguments {most_common_count} times",
                suggestion="Try a completely different approach or ask the user for guidance",
                repeated_tools=[tool_name],
                attempts=most_common_count,
            )

        # Check 1b: Same tool name repeated (even with different args) — polling loop
        name_counts = Counter(r.name for r in window)
        most_common_name, name_count = name_counts.most_common(1)[0]
        polling_threshold = self._repeat_threshold + 1  # slightly higher for name-only
        if name_count >= polling_threshold and name_count >= len(window) * 0.6:
            self._stuck_count += 1
            return StuckDiagnosis(
                is_stuck=True,
                confidence=min(0.95, 0.5 + name_count * 0.08),
                reason=f"Polling loop: '{most_common_name}' called {name_count}/{len(window)} times in window",
                suggestion=f"Stop calling '{most_common_name}' repeatedly. It's a read-only status check — use an action tool instead.",
                repeated_tools=[most_common_name],
                attempts=name_count,
            )

        # Check 2: Error streak
        if self._error_streak >= self._error_streak_threshold:
            recent_tools = [r.name for r in window[-self._error_streak:]]
            self._stuck_count += 1
            return StuckDiagnosis(
                is_stuck=True,
                confidence=min(0.9, 0.5 + self._error_streak * 0.1),
                reason=f"{self._error_streak} consecutive errors",
                suggestion="Stop current approach. Analyze the error pattern and try something different",
                repeated_tools=list(set(recent_tools)),
                attempts=self._error_streak,
            )

        # Check 3: Oscillation (A→B→A→B pattern)
        if len(window) >= 6:
            names = [r.name for r in window[-6:]]
            if (names[0] == names[2] == names[4] and
                    names[1] == names[3] == names[5] and
                    names[0] != names[1]):
                self._stuck_count += 1
                return StuckDiagnosis(
                    is_stuck=True,
                    confidence=0.8,
                    reason=f"Oscillating between '{names[0]}' and '{names[1]}'",
                    suggestion="Break the cycle — try a third approach or ask for help",
                    repeated_tools=[names[0], names[1]],
                    attempts=3,
                )

        # Check 3b: Port-hunting loop (port_check → process_start/exec → port_check cycles)
        if len(window) >= 6:
            port_tools = {"port_check", "wait_for_port"}
            start_tools = {"process_start", "process_stop", "exec"}
            recent_names = [r.name for r in window[-12:]]
            port_checks = sum(1 for n in recent_names if n in port_tools)
            starts = sum(1 for n in recent_names if n in start_tools)
            if port_checks >= 3 and starts >= 2 and (port_checks + starts) >= len(recent_names) * 0.6:
                self._stuck_count += 1
                return StuckDiagnosis(
                    is_stuck=True,
                    confidence=0.85,
                    reason=f"Port-hunting loop: {port_checks} port checks + {starts} process starts in last {len(recent_names)} calls",
                    suggestion=(
                        "Stop hunting for ports. Kill ALL processes on the target ports first "
                        "(use process_stop or exec 'kill'), then start the server ONCE on a "
                        "known-free port. Check the project manifest for pre-assigned ports."
                    ),
                    repeated_tools=list(port_tools | start_tools),
                    attempts=port_checks + starts,
                )

        # Check 4: High error rate in window
        error_rate = sum(1 for r in window if not r.success) / len(window)
        if error_rate > 0.6 and len(window) >= 8:
            self._stuck_count += 1
            return StuckDiagnosis(
                is_stuck=True,
                confidence=error_rate,
                reason=f"High error rate: {error_rate:.0%} of recent calls failed",
                suggestion="Current approach is failing. Reassess the problem",
                attempts=int(error_rate * len(window)),
            )

        # Check 5: Progress stall — getting same results repeatedly
        if self._progress_stall_count >= self.PROGRESS_STALL_THRESHOLD:
            self._stuck_count += 1
            return StuckDiagnosis(
                is_stuck=True,
                confidence=0.7,
                reason=f"No new results in {self._progress_stall_count} consecutive calls",
                suggestion="You're getting the same results repeatedly. Try a different strategy.",
                attempts=self._progress_stall_count,
            )

        return StuckDiagnosis(
            is_stuck=False, confidence=0.0,
            reason="No stuck pattern detected", suggestion="Continue working",
        )

    def reset(self) -> None:
        """Reset the detector for a new task."""
        self._history.clear()
        self._error_streak = 0
        self._stuck_count = 0
        self._last_activity = time.time()
        self._unique_results.clear()
        self._progress_stall_count = 0

    def get_stats(self) -> dict[str, Any]:
        return {
            "total_calls": self._total_calls,
            "total_errors": self._total_errors,
            "current_error_streak": self._error_streak,
            "times_stuck": self._stuck_count,
            "history_size": len(self._history),
            "idle_s": round(time.time() - self._last_activity, 1),
            "progress_stall_count": self._progress_stall_count,
        }

    def build_stuck_context(self) -> str:
        """Build context about what was tried, for escalation messages."""
        if not self._history:
            return "No tool calls recorded."

        window = self._history[-10:]
        lines = ["Recent tool calls:"]
        for r in window:
            status = "✓" if r.success else "✗"
            lines.append(f"  {status} {r.name} → {r.result_preview[:100]}")
        return "\n".join(lines)


class ConfidenceScorer:
    """Estimates confidence in the current approach.

    Factors:
    - Tool success rate
    - Whether we've seen similar tasks succeed before
    - How many retries we've needed
    - Whether the LLM is hedging in its responses
    """

    _HEDGE_PHRASES = [
        "i'm not sure", "i think", "might work", "could try",
        "not certain", "possibly", "maybe", "let me try",
        "i'll attempt", "hopefully", "should work",
    ]

    def __init__(self):
        self._scores: list[float] = []

    def score_response(self, response_text: str, tool_success_rate: float) -> float:
        """Score confidence based on response text and tool outcomes."""
        score = 0.7  # base confidence

        # Adjust for tool success rate
        score += (tool_success_rate - 0.5) * 0.4  # ±0.2

        # Check for hedging language
        text_lower = response_text.lower()
        hedge_count = sum(1 for phrase in self._HEDGE_PHRASES if phrase in text_lower)
        score -= hedge_count * 0.05

        # Check for explicit uncertainty
        if "error" in text_lower or "failed" in text_lower:
            score -= 0.15
        if "successfully" in text_lower or "completed" in text_lower:
            score += 0.1

        score = max(0.0, min(1.0, score))
        self._scores.append(score)
        return score

    def get_average(self) -> float:
        if not self._scores:
            return 0.7
        return sum(self._scores) / len(self._scores)

    def should_escalate(self, threshold: float = 0.35) -> bool:
        """Check if confidence is low enough to escalate to human."""
        if len(self._scores) < 2:
            return False
        recent = self._scores[-3:]
        avg = sum(recent) / len(recent)
        return avg < threshold

    def reset(self) -> None:
        self._scores.clear()
