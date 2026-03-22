"""Tool registry for dynamic tool management."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from sharanbot.agent.tools.base import Tool

if TYPE_CHECKING:
    from sharanbot.agent.recovery import RecoveryEngine


class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.
    Integrates with RecoveryEngine for automatic retry on transient failures.
    """

    _MAX_TOOL_RETRIES = 2  # Max auto-retries per tool execution

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._recovery_engine: RecoveryEngine | None = None

    def set_recovery_engine(self, engine: RecoveryEngine) -> None:
        """Attach recovery engine for auto-retry on tool failures."""
        self._recovery_engine = engine

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get all tool definitions in OpenAI format."""
        return [tool.to_schema() for tool in self._tools.values()]

    async def execute(self, name: str, params: dict[str, Any]) -> Any:
        """Execute a tool by name with given parameters.

        On transient failures (MCP disconnects, timeouts), attempts
        auto-recovery via the RecoveryEngine before retrying.
        """
        _HINT = "\n\n[Analyze the error above and try a different approach.]"

        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found. Available: {', '.join(self.tool_names)}"

        # Repetition detection for exec commands
        if name == "exec" and self._recovery_engine:
            cmd = params.get("command", "")
            if cmd:
                loop_warning = self._recovery_engine.track_command(cmd)
                if loop_warning:
                    return loop_warning

        retries_left = self._MAX_TOOL_RETRIES if self._recovery_engine else 0

        for attempt in range(1 + retries_left):
            try:
                # Attempt to cast parameters to match schema types
                params_cast = tool.cast_params(params)

                # Validate parameters
                errors = tool.validate_params(params_cast)
                if errors:
                    return f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors) + _HINT
                result = await tool.execute(**params_cast)

                # Check for error string responses that indicate recoverable failures
                if (
                    isinstance(result, str)
                    and result.startswith("Error")
                    and self._recovery_engine
                    and attempt < retries_left
                    and self._is_recoverable_tool_error(name, result)
                ):
                    recovered = await self._recovery_engine.handle_error(
                        result, component=name, context={"attempt": attempt + 1},
                    )
                    if recovered:
                        logger.info("Tool {} auto-recovered, retrying (attempt {})", name, attempt + 2)
                        continue

                if isinstance(result, str) and result.startswith("Error"):
                    return result + _HINT
                return result

            except Exception as e:
                if self._recovery_engine and attempt < retries_left:
                    recovered = await self._recovery_engine.handle_error(
                        e, component=name, context={"attempt": attempt + 1},
                    )
                    if recovered:
                        logger.info("Tool {} auto-recovered from exception, retrying", name)
                        continue
                return f"Error executing {name}: {str(e)}" + _HINT

        # Should not reach here, but safety net
        return f"Error: Tool '{name}' failed after {retries_left + 1} attempts" + _HINT

    def _is_recoverable_tool_error(self, name: str, error: str) -> bool:
        """Check if a tool error is likely recoverable via retry."""
        err_lower = error.lower()
        recoverable_markers = (
            "timeout", "timed out", "connection", "disconnected",
            "server error", "temporarily", "mcp", "unavailable",
            "refused", "reset", "broken pipe", "permission denied",
            "access denied", "EACCES",
        )
        return any(m in err_lower for m in recoverable_markers)

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
