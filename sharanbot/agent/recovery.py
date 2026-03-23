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
    PERMISSION_ERROR = "permission_error"
    REPEATED_FAILURE = "repeated_failure"
    IMPORT_ERROR = "import_error"


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
    - Auto-install missing packages via package name mapping
    """

    # Common import-name → pip-package mapping for auto-install
    PACKAGE_NAME_MAP: dict[str, str] = {
        "cv2": "opencv-python",
        "PIL": "Pillow",
        "sklearn": "scikit-learn",
        "yaml": "PyYAML",
        "bs4": "beautifulsoup4",
        "attr": "attrs",
        "dateutil": "python-dateutil",
        "dotenv": "python-dotenv",
        "gi": "PyGObject",
        "lxml": "lxml",
        "magic": "python-magic",
        "serial": "pyserial",
        "usb": "pyusb",
        "wx": "wxPython",
        "skimage": "scikit-image",
        "jose": "python-jose",
        "jwt": "PyJWT",
        "Crypto": "pycryptodome",
        "docx": "python-docx",
        "pptx": "python-pptx",
        "xlrd": "xlrd",
        "openpyxl": "openpyxl",
        "bson": "pymongo",
        "pymongo": "pymongo",
        "redis": "redis",
        "celery": "celery",
        "flask": "flask",
        "fastapi": "fastapi",
        "uvicorn": "uvicorn",
        "httpx": "httpx",
        "aiohttp": "aiohttp",
        "numpy": "numpy",
        "pandas": "pandas",
        "matplotlib": "matplotlib",
        "seaborn": "seaborn",
        "torch": "torch",
        "tensorflow": "tensorflow",
        "transformers": "transformers",
        "scipy": "scipy",
        "sympy": "sympy",
    }

    # Pattern → category mapping for error classification (pre-compiled for performance)
    _ERROR_PATTERNS: list[tuple[re.Pattern[str], ErrorCategory]] = [
        (re.compile(r"permission\s*denied|access.*denied|EACCES|EPERM|not permitted|requires elevation|requires admin|run as admin", re.IGNORECASE), ErrorCategory.PERMISSION_ERROR),
        (re.compile(r"ModuleNotFoundError|ImportError|No module named", re.IGNORECASE), ErrorCategory.IMPORT_ERROR),
        (re.compile(r"429|rate.?limit|too many requests|quota", re.IGNORECASE), ErrorCategory.RATE_LIMIT),
        (re.compile(r"timeout|timed?\s*out|deadline exceeded", re.IGNORECASE), ErrorCategory.TIMEOUT),
        (re.compile(r"mcp|model context protocol|server disconnected", re.IGNORECASE), ErrorCategory.MCP_FAILURE),
        (re.compile(r"500|502|503|504|internal server|bad gateway|service unavailable", re.IGNORECASE), ErrorCategory.API_ERROR),
        (re.compile(r"connection\s*(refused|reset|error)|ECONNREFUSED|ECONNRESET", re.IGNORECASE), ErrorCategory.API_ERROR),
        (re.compile(r"stuck|deadlock|not responding|hang", re.IGNORECASE), ErrorCategory.STUCK_AGENT),
        (re.compile(r"worker.*crash|worker.*fail|process.*exit", re.IGNORECASE), ErrorCategory.STUCK_WORKER),
    ]

    # Max failures before circuit opens per category
    _CIRCUIT_THRESHOLDS: dict[ErrorCategory, int] = {
        ErrorCategory.RATE_LIMIT: 5,
        ErrorCategory.API_ERROR: 3,
        ErrorCategory.MCP_FAILURE: 3,
        ErrorCategory.TOOL_ERROR: 5,
        ErrorCategory.STUCK_WORKER: 2,
        ErrorCategory.TIMEOUT: 3,
        ErrorCategory.PERMISSION_ERROR: 2,
        ErrorCategory.REPEATED_FAILURE: 3,
        ErrorCategory.IMPORT_ERROR: 5,
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
        for compiled_pattern, category in self._ERROR_PATTERNS:
            if compiled_pattern.search(text):
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
            # Guard against NaN, Inf, or negative values
            import math
            if not isinstance(learned, (int, float)) or math.isnan(learned) or math.isinf(learned) or learned < 0:
                learned = circuit.cooldown
            # Exponential backoff capped between 1s and 5 minutes
            circuit.cooldown = min(max(learned * 1.5, 1.0), 300)
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
            # Diagnose root cause via LLM (non-blocking best-effort)
            diagnosis = await self.diagnose(error, component, category, context)
            if diagnosis:
                logger.info("Root cause diagnosis for {}: {}", signature, diagnosis)

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
            ErrorCategory.PERMISSION_ERROR: 0.5,
            ErrorCategory.REPEATED_FAILURE: 1.0,
        }.get(category, 3.0)

    async def diagnose(
        self,
        error: str | Exception,
        component: str,
        category: ErrorCategory,
        context: dict[str, Any] | None = None,
    ) -> str | None:
        """Use LLM to diagnose the root cause of a failure.

        Returns a short diagnosis string, or None if unavailable.
        """
        if not self.agent:
            return None

        # Check learning store first — skip LLM if we already know this error
        sig = self.make_error_signature(category, component, error)
        known = self.learning.get_successful_fix(sig)
        if known:
            return f"Known pattern — previous fix: {known}"

        # Build a concise prompt for the LLM
        error_text = str(error)[:500]
        ctx_snippet = ""
        if context:
            ctx_snippet = "\n".join(
                f"  {k}: {str(v)[:120]}" for k, v in list(context.items())[:6]
            )

        prompt = (
            "You are a systems diagnostician. Analyze this error and reply "
            "with ONLY a 1-2 sentence root cause diagnosis.\n\n"
            f"Component: {component}\n"
            f"Category: {category.value}\n"
            f"Error: {error_text}\n"
        )
        if ctx_snippet:
            prompt += f"Context:\n{ctx_snippet}\n"

        try:
            provider = getattr(self.agent, "provider", None)
            if provider is None:
                return None
            response = await provider.chat(
                [{"role": "user", "content": prompt}],
                model=getattr(self.agent, "model", None),
            )
            diagnosis = (response or "").strip()[:300]
            if diagnosis:
                # Persist diagnosis for future look-ups
                self.learning.record(
                    category=category.value,
                    error_signature=sig,
                    description=f"Diagnosis: {diagnosis}",
                    fix_applied="diagnosed",
                    success=False,
                    metadata={"diagnosis": diagnosis, "component": component},
                )
            return diagnosis or None
        except Exception as exc:
            logger.debug("Diagnosis LLM call failed (non-fatal): {}", exc)
            return None

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
        self._register(ErrorCategory.PERMISSION_ERROR, RecoveryAction(
            name="permission_fix",
            handler=self._recover_permission,
            category=ErrorCategory.PERMISSION_ERROR,
            description="Retry with --user flag or elevated permissions",
        ))
        self._register(ErrorCategory.IMPORT_ERROR, RecoveryAction(
            name="auto_install_package",
            handler=self._recover_import_error,
            category=ErrorCategory.IMPORT_ERROR,
            description="Auto-install missing package via pip",
        ))
        # Repetition tracker for detecting stuck command loops
        self._recent_commands: list[str] = []  # rolling window of recent exec commands
        self._REPEAT_WINDOW = 6  # how many recent commands to track
        self._REPEAT_THRESHOLD = 3  # how many similar commands before flagging

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

    async def _recover_permission(
        self, component: str, error: Any, context: dict[str, Any],
    ) -> bool:
        """Handle permission errors: suggest --user flag, avoid requiring admin."""
        logger.info("Permission recovery: suggesting --user install or alternative approach")
        # Record the fix so the learning store knows to use --user next time
        self.learning.record(
            category="permission_error",
            error_signature=f"permission_error:{component}",
            description=str(error)[:200],
            fix_applied="Retry with --user flag for pip, or use venv",
            success=True,
        )
        return True

    async def _recover_import_error(
        self, component: str, error: Any, context: dict[str, Any],
    ) -> bool:
        """Handle ImportError/ModuleNotFoundError: auto-install the missing package."""
        error_str = str(error)
        # Extract module name from "No module named 'xxx'" or "ModuleNotFoundError: xxx"
        match = re.search(r"No module named ['\"]?([a-zA-Z0-9_]+)", error_str)
        if not match:
            match = re.search(r"cannot import name .+ from ['\"]?([a-zA-Z0-9_]+)", error_str)
        if not match:
            return False

        module_name = match.group(1)
        # Look up the pip package name
        pip_name = self.PACKAGE_NAME_MAP.get(module_name, module_name)

        logger.info("Auto-installing missing package: {} (pip: {})", module_name, pip_name)
        try:
            proc = await asyncio.create_subprocess_exec(
                "pip", "install", "--user", pip_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            if proc.returncode == 0:
                self.learning.record(
                    category="import_error",
                    error_signature=f"import_error:{module_name}",
                    description=f"Missing module: {module_name}",
                    fix_applied=f"pip install --user {pip_name}",
                    success=True,
                )
                logger.info("Successfully installed {}", pip_name)
                return True
            else:
                logger.warning("Failed to install {}: {}", pip_name, stderr.decode()[:200])
                return False
        except (asyncio.TimeoutError, FileNotFoundError, OSError) as e:
            logger.warning("Package install error: {}", e)
            return False

    def track_command(self, command: str) -> str | None:
        """Track an exec command and detect repetitive stuck loops.

        Returns a warning message if a repeated-failure pattern is detected,
        or None if the command appears normal.
        """
        # Normalize: strip whitespace, lowercase, remove variable parts (PIDs, ports)
        normalized = re.sub(r"\b\d{3,}\b", "NUM", command.strip().lower())
        normalized = re.sub(r"\s+", " ", normalized)

        self._recent_commands.append(normalized)
        if len(self._recent_commands) > self._REPEAT_WINDOW:
            self._recent_commands = self._recent_commands[-self._REPEAT_WINDOW:]

        # Count how many recent commands share the same base pattern
        # (same first 3 words = likely same approach)
        if len(self._recent_commands) >= self._REPEAT_THRESHOLD:
            base = " ".join(normalized.split()[:3])
            similar = sum(
                1 for c in self._recent_commands
                if " ".join(c.split()[:3]) == base
            )
            if similar >= self._REPEAT_THRESHOLD:
                self.trip_circuit("exec_loop", ErrorCategory.REPEATED_FAILURE)
                logger.warning(
                    "Repeated failure detected: '{}' attempted {}x in {} commands",
                    base, similar, len(self._recent_commands),
                )
                return (
                    f"WARNING: You have attempted a similar command ({base}...) "
                    f"{similar} times without success. STOP this approach and try "
                    f"a fundamentally different strategy. Analyze the root cause "
                    f"before retrying. Consider: different tool, different port, "
                    f"different mode, or checking prerequisites first."
                )
        return None

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
