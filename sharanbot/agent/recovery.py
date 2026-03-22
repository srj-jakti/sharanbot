"""Auto-recovery engine — self-healing coordinator for SharanBot.

Detects failures (rate limits, stuck workers, MCP crashes, tool errors)
and applies automated recovery strategies. Integrates with the learning
store to remember what fixes work and avoid repeating mistakes.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Awaitable

from loguru import logger

from sharanbot.agent.learning import LearningStore

if TYPE_CHECKING:
    from sharanbot.agent.loop import AgentLoop


class ErrorCategory(str, Enum):
    RATE_LIMIT = "rate_limit"
    API_ERROR = "api_error"
    TOOL_ERROR = "tool_error"
    MCP_FAILURE = "mcp_failure"
    STUCK_WORKER = "stuck_worker"
    STUCK_AGENT = "stuck_agent"
    GATEWAY_ERROR = "gateway_error"
    TIMEOUT = "timeout"


@dataclass
class RecoveryAction:
    """A recovery action to execute."""

    name: str
    handler: Callable[..., Awaitable[bool]]  # Returns True if recovery succeeded
    category: ErrorCategory
    description: str
    max_retries: int = 3


@dataclass
class CircuitState:
    """Circuit breaker state for a specific component."""

    failures: int = 0
    last_failure: float = 0
    is_open: bool = False  # True = circuit tripped, component disabled
    half_open_at: float = 0  # When to try again
    cooldown: float = 30.0  # Seconds before half-open


class RecoveryEngine:
    """Central coordinator for auto-recovery and self-healing.

    Responsibilities:
    - Detect error types and classify them
    - Apply appropriate recovery strategy (retry, reconnect, restart, backoff)
    - Track circuit breaker state per component
    - Record outcomes in LearningStore for future reference
    - Adapt cooldown/retry strategies based on learned patterns
    """

    # Pattern → category mapping for error classification
    _ERROR_PATTERNS: list[tuple[str, ErrorCategory]] = [
        (r"429|rate.?limit|too many requests|quota", ErrorCategory.RATE_LIMIT),
        (r"timeout|timed?\s*out|deadline exceeded", ErrorCategory.TIMEOUT),
        (r"mcp|model context protocol|server disconnected", ErrorCategory.MCP_FAILURE),
        (r"500|502|503|504|internal server|bad gateway|service unavailable", ErrorCategory.API_ERROR),
        (r"connection\s*(refused|reset|error)|ECONNREFUSED|ECONNRESET", ErrorCategory.API_ERROR),
        (r"stuck|deadlock|not responding|hang", ErrorCategory.STUCK_AGENT),
        (r"worker.*crash|worker.*fail|process.*exit", ErrorCategory.STUCK_WORKER),
    ]

    # Max failures before circuit opens per category
    _CIRCUIT_THRESHOLDS: dict[ErrorCategory, int] = {
        ErrorCategory.RATE_LIMIT: 5,
        ErrorCategory.API_ERROR: 3,
        ErrorCategory.MCP_FAILURE: 3,
        ErrorCategory.TOOL_ERROR: 5,
        ErrorCategory.STUCK_WORKER: 2,
        ErrorCategory.TIMEOUT: 3,
    }

    def __init__(self, learning: LearningStore, agent: AgentLoop | None = None):
        self.learning = learning
        self.agent = agent
        self._circuits: dict[str, CircuitState] = {}  # component_name → state
        self._recovery_handlers: dict[ErrorCategory, list[RecoveryAction]] = {}
        self._active_recoveries: set[str] = set()  # Prevent concurrent recovery
        self._register_default_handlers()

    def set_agent(self, agent: AgentLoop) -> None:
        """Set the agent loop reference (for late binding)."""
        self.agent = agent

    # ------------------------------------------------------------------
    # Error Classification
    # ------------------------------------------------------------------

    def classify_error(self, error: str | Exception) -> ErrorCategory:
        """Classify an error into a recovery category."""
        text = str(error).lower()
        for pattern, category in self._ERROR_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return category
        return ErrorCategory.TOOL_ERROR  # Default

    def make_error_signature(self, category: ErrorCategory, component: str, error: str | Exception) -> str:
        """Create a stable signature for an error type."""
        text = str(error).lower()
        # Extract the most identifying part
        if category == ErrorCategory.RATE_LIMIT:
            return f"{category.value}:{component}"
        if category == ErrorCategory.API_ERROR:
            # Extract HTTP status code if present
            match = re.search(r"(5\d{2})", text)
            code = match.group(1) if match else "unknown"
            return f"{category.value}:{component}:{code}"
        return f"{category.value}:{component}"

    # ------------------------------------------------------------------
    # Circuit Breaker
    # ------------------------------------------------------------------

    def get_circuit(self, component: str) -> CircuitState:
        """Get or create circuit state for a component."""
        if component not in self._circuits:
            self._circuits[component] = CircuitState()
        return self._circuits[component]

    def check_circuit(self, component: str) -> bool:
        """Check if a component's circuit is closed (OK to proceed).

        Returns True if the component is safe to use.
        """
        circuit = self.get_circuit(component)
        if not circuit.is_open:
            return True
        # Check if half-open period reached
        if time.time() >= circuit.half_open_at:
            circuit.is_open = False
            logger.info("Circuit half-open for {}, allowing retry", component)
            return True
        return False

    def trip_circuit(self, component: str, category: ErrorCategory) -> None:
        """Record a failure and potentially trip the circuit breaker."""
        circuit = self.get_circuit(component)
        circuit.failures += 1
        circuit.last_failure = time.time()

        threshold = self._CIRCUIT_THRESHOLDS.get(category, 3)
        if circuit.failures >= threshold:
            circuit.is_open = True
            # Adaptive cooldown: use learned cooldown if available
            learned = self.learning.get_cooldown(
                f"{category.value}:{component}",
                default=circuit.cooldown,
            )
            # Exponential backoff capped at 5 minutes
            circuit.cooldown = min(learned * 1.5, 300)
            circuit.half_open_at = time.time() + circuit.cooldown
            logger.warning(
                "Circuit OPEN for {} ({}x failures), cooldown {:.0f}s",
                component, circuit.failures, circuit.cooldown,
            )

    def reset_circuit(self, component: str) -> None:
        """Reset circuit breaker after successful recovery."""
        circuit = self.get_circuit(component)
        circuit.failures = 0
        circuit.is_open = False
        circuit.cooldown = 30.0
        logger.info("Circuit reset for {}", component)

    # ------------------------------------------------------------------
    # Recovery Execution
    # ------------------------------------------------------------------

    async def handle_error(
        self,
        error: str | Exception,
        component: str = "unknown",
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Main entry point: classify the error and attempt recovery.

        Returns True if recovery succeeded, False otherwise.
        """
        category = self.classify_error(error)
        signature = self.make_error_signature(category, component, error)

        # Check if we're already recovering this component
        recovery_key = f"{component}:{category.value}"
        if recovery_key in self._active_recoveries:
            logger.debug("Recovery already in progress for {}", recovery_key)
            return False

        self._active_recoveries.add(recovery_key)
        try:
            # Check learning store for known fix
            known_fix = self.learning.get_successful_fix(signature)
            if known_fix:
                logger.info("Known fix for {}: {}", signature, known_fix)

            # Record the failure
            self.trip_circuit(component, category)

            # Get learned cooldown for this error type
            cooldown = self.learning.get_cooldown(signature, default=self._default_cooldown(category))

            # Apply cooldown before recovery
            if cooldown > 0:
                logger.info("Applying {:.1f}s cooldown for {} before recovery", cooldown, signature)
                await asyncio.sleep(cooldown)

            # Attempt recovery
            handlers = self._recovery_handlers.get(category, [])
            success = False
            fix_desc = "no handler"

            for action in handlers:
                try:
                    logger.info("Attempting recovery: {} for {}", action.name, component)
                    success = await action.handler(component, error, context or {})
                    fix_desc = action.description
                    if success:
                        self.reset_circuit(component)
                        break
                except Exception as exc:
                    logger.warning("Recovery action {} failed: {}", action.name, exc)
                    continue

            # Record outcome in learning store
            self.learning.record(
                category=category.value,
                error_signature=signature,
                description=str(error)[:200],
                fix_applied=fix_desc,
                success=success,
                cooldown_seconds=cooldown if success else cooldown * 2,
                metadata={"component": component, **(context or {})},
            )

            return success
        finally:
            self._active_recoveries.discard(recovery_key)

    def _default_cooldown(self, category: ErrorCategory) -> float:
        """Default cooldown before recovery attempt."""
        return {
            ErrorCategory.RATE_LIMIT: 10.0,
            ErrorCategory.API_ERROR: 5.0,
            ErrorCategory.MCP_FAILURE: 2.0,
            ErrorCategory.TOOL_ERROR: 1.0,
            ErrorCategory.STUCK_WORKER: 2.0,
            ErrorCategory.STUCK_AGENT: 3.0,
            ErrorCategory.TIMEOUT: 5.0,
            ErrorCategory.GATEWAY_ERROR: 5.0,
        }.get(category, 3.0)

    # ------------------------------------------------------------------
    # Default Recovery Handlers
    # ------------------------------------------------------------------

    def _register_default_handlers(self) -> None:
        """Register built-in recovery strategies."""
        self._register(ErrorCategory.RATE_LIMIT, RecoveryAction(
            name="rate_limit_backoff",
            handler=self._recover_rate_limit,
            category=ErrorCategory.RATE_LIMIT,
            description="Exponential backoff on rate limit",
            max_retries=5,
        ))
        self._register(ErrorCategory.API_ERROR, RecoveryAction(
            name="api_error_retry",
            handler=self._recover_api_error,
            category=ErrorCategory.API_ERROR,
            description="Retry after API error with backoff",
        ))
        self._register(ErrorCategory.MCP_FAILURE, RecoveryAction(
            name="mcp_reconnect",
            handler=self._recover_mcp,
            category=ErrorCategory.MCP_FAILURE,
            description="Reconnect MCP servers",
        ))
        self._register(ErrorCategory.TOOL_ERROR, RecoveryAction(
            name="tool_retry",
            handler=self._recover_tool_error,
            category=ErrorCategory.TOOL_ERROR,
            description="Retry tool after cleanup",
        ))
        self._register(ErrorCategory.STUCK_WORKER, RecoveryAction(
            name="restart_worker",
            handler=self._recover_stuck_worker,
            category=ErrorCategory.STUCK_WORKER,
            description="Kill and restart stuck worker",
        ))
        self._register(ErrorCategory.TIMEOUT, RecoveryAction(
            name="timeout_extend_retry",
            handler=self._recover_timeout,
            category=ErrorCategory.TIMEOUT,
            description="Retry with extended timeout",
        ))

    def _register(self, category: ErrorCategory, action: RecoveryAction) -> None:
        self._recovery_handlers.setdefault(category, []).append(action)

    # --- Individual Handlers ---

    async def _recover_rate_limit(
        self, component: str, error: Any, context: dict[str, Any],
    ) -> bool:
        """Handle rate limit: exponential backoff with learned timing."""
        signature = f"rate_limit:{component}"
        entry = self.learning.lookup(signature)

        # Exponential backoff: start at 10s, max 120s
        base_wait = entry.cooldown_seconds if entry else 10.0
        failures = self.get_circuit(component).failures
        wait = min(base_wait * (2 ** (failures - 1)), 120.0)

        logger.info("Rate limit recovery: waiting {:.1f}s (attempt {})", wait, failures)
        await asyncio.sleep(wait)

        # After waiting, the next API call will test if we're unblocked
        # Record the successful cooldown duration
        self.learning.record(
            category="rate_limit",
            error_signature=signature,
            description=f"Rate limited on {component}",
            fix_applied=f"Waited {wait:.1f}s",
            success=True,
            cooldown_seconds=wait,
        )
        return True

    async def _recover_api_error(
        self, component: str, error: Any, context: dict[str, Any],
    ) -> bool:
        """Handle API error: wait and retry."""
        await asyncio.sleep(5)
        return True  # Signal that retry is safe

    async def _recover_mcp(
        self, component: str, error: Any, context: dict[str, Any],
    ) -> bool:
        """Handle MCP failure: reconnect all MCP servers."""
        if not self.agent:
            return False
        try:
            await self.agent._reconnect_mcp()
            logger.info("MCP recovery: reconnected successfully")
            return True
        except Exception as exc:
            logger.warning("MCP recovery failed: {}", exc)
            return False

    async def _recover_tool_error(
        self, component: str, error: Any, context: dict[str, Any],
    ) -> bool:
        """Handle tool error: check if it's an MCP tool and reconnect if needed."""
        if component.startswith("mcp_") and self.agent:
            try:
                await self.agent._reconnect_mcp()
                return True
            except Exception:
                pass
        # For non-MCP tools, just signal that retry is safe
        return True

    async def _recover_stuck_worker(
        self, component: str, error: Any, context: dict[str, Any],
    ) -> bool:
        """Handle stuck worker: kill and restart."""
        if not self.agent or not self.agent.worker_manager:
            return False
        try:
            worker_id = context.get("worker_id")
            if worker_id:
                await self.agent.worker_manager.stop(worker_id)
                logger.info("Stuck worker {} killed", worker_id)
            return True
        except Exception as exc:
            logger.warning("Worker recovery failed: {}", exc)
            return False

    async def _recover_timeout(
        self, component: str, error: Any, context: dict[str, Any],
    ) -> bool:
        """Handle timeout: signal that retry with extended timeout is OK."""
        return True

    # ------------------------------------------------------------------
    # Health Check
    # ------------------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        """Run a health check on all known components."""
        results: dict[str, Any] = {
            "status": "healthy",
            "circuits": {},
            "learning": self.learning.summary(),
        }
        unhealthy = []
        for name, circuit in self._circuits.items():
            state = "closed"
            if circuit.is_open:
                state = "open"
                unhealthy.append(name)
            results["circuits"][name] = {
                "state": state,
                "failures": circuit.failures,
                "cooldown": circuit.cooldown,
            }
        if unhealthy:
            results["status"] = "degraded"
            results["unhealthy_components"] = unhealthy
        return results

    def get_status_summary(self) -> str:
        """One-line summary for logging/display."""
        open_circuits = [n for n, c in self._circuits.items() if c.is_open]
        total = self.learning.total_entries
        if open_circuits:
            return f"⚠️ {len(open_circuits)} circuit(s) open: {', '.join(open_circuits)} | {total} patterns learned"
        return f"✅ All circuits closed | {total} patterns learned"
