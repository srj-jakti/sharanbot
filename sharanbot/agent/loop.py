"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from sharanbot import __version__
from sharanbot.agent.context import ContextBuilder
from sharanbot.agent.memory import MemoryConsolidator
from sharanbot.agent.subagent import SubagentManager
from sharanbot.agent.tools.cron import CronTool
from sharanbot.agent.skills import BUILTIN_SKILLS_DIR
from sharanbot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from sharanbot.agent.tools.message import MessageTool
from sharanbot.agent.tools.registry import ToolRegistry
from sharanbot.agent.tools.shell import ExecTool
from sharanbot.agent.tools.spawn import SpawnTool
from sharanbot.agent.tools.web import WebFetchTool, WebSearchTool
from sharanbot.agent.tools.worker import WorkerStartTool, WorkerStatusTool, WorkerSteerTool, WorkerStopTool
from sharanbot.agent.tools.reload import ReloadTool
from sharanbot.agent.tools.evolve import EvolveTool
from sharanbot.agent.tools.dynamic import DynamicToolLoader
from sharanbot.agent.profiles import AgentProfileRegistry
from sharanbot.agent.worker_registry import WorkerTypeRegistry
from sharanbot.agent.team.orchestrator import TeamOrchestrator
from sharanbot.agent.team.tools import TeamCreateTool, TeamStatusTool, TeamSteerTool
from sharanbot.agent.team.autopilot import Autopilot
from sharanbot.agent.tools.import_agent import ImportAgentTool
from sharanbot.agent.tools.browser import BrowserTool
from sharanbot.agent.tools.sandbox import SandboxTool
from sharanbot.agent.learning import LearningStore
from sharanbot.agent.recovery import RecoveryEngine
from sharanbot.bus.events import InboundMessage, OutboundMessage
from sharanbot.utils.helpers import build_status_content
from sharanbot.bus.queue import MessageBus
from sharanbot.providers.base import LLMProvider
from sharanbot.session.manager import Session, SessionManager

if TYPE_CHECKING:
    from sharanbot.agent.worker import WorkerManager
    from sharanbot.channels.manager import ChannelManager
    from sharanbot.config.schema import ChannelsConfig, ExecToolConfig, WebSearchConfig
    from sharanbot.cron.service import CronService


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    _TOOL_RESULT_MAX_CHARS = 16_000

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 40,
        context_window_tokens: int = 65_536,
        web_search_config: WebSearchConfig | None = None,
        web_proxy: str | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
        worker_manager: WorkerManager | None = None,
        worker_type_registry: WorkerTypeRegistry | None = None,
        agent_profile_registry: AgentProfileRegistry | None = None,
        channel_manager: "ChannelManager | None" = None,
    ):
        from sharanbot.config.schema import ExecToolConfig, WebSearchConfig

        self.bus = bus
        self.channels_config = channels_config
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.context_window_tokens = context_window_tokens
        self.web_search_config = web_search_config or WebSearchConfig()
        self.web_proxy = web_proxy
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace
        self.worker_manager = worker_manager
        self.worker_type_registry = worker_type_registry or WorkerTypeRegistry(
            plugins_dir=workspace / "plugins" / "workers"
        )
        self.agent_profile_registry = agent_profile_registry or AgentProfileRegistry(
            plugins_dir=workspace / "plugins" / "agents"
        )
        self.channel_manager: ChannelManager | None = channel_manager
        self._start_time = time.time()
        self._last_usage: dict[str, int] = {}

        self.context = ContextBuilder(workspace)
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            web_search_config=self.web_search_config,
            web_proxy=web_proxy,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
            profile_registry=self.agent_profile_registry,
        )

        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connecting = False
        self._active_tasks: dict[str, list[asyncio.Task]] = {}  # session_key -> tasks
        self._background_tasks: list[asyncio.Task] = []
        self._processing_lock = asyncio.Lock()

        # Team orchestration
        self.team_orchestrator = TeamOrchestrator(
            provider=provider,
            model=self.model,
            profile_registry=self.agent_profile_registry,
            subagent_manager=self.subagents,
            message_bus=bus,
            workspace=workspace,
        )
        self.autopilot = Autopilot(provider=provider, model=self.model)

        self.memory_consolidator = MemoryConsolidator(
            workspace=workspace,
            provider=provider,
            model=self.model,
            sessions=self.sessions,
            context_window_tokens=context_window_tokens,
            build_messages=self.context.build_messages,
            get_tool_definitions=self.tools.get_definitions,
        )

        # Auto-recovery + self-learning
        self.learning_store = LearningStore(workspace)
        self.recovery_engine = RecoveryEngine(learning=self.learning_store, agent=self)
        self.provider.set_recovery_engine(self.recovery_engine)
        self.tools.set_recovery_engine(self.recovery_engine)
        self.team_orchestrator.set_recovery_engine(self.recovery_engine)
        self.subagents.set_recovery_engine(self.recovery_engine)
        if self.worker_manager:
            self.worker_manager.set_recovery_engine(self.recovery_engine)

        self._register_default_tools()

        # Dynamic tools from workspace/plugins/tools/
        self._dynamic_loader = DynamicToolLoader(
            tools_dir=workspace / "plugins" / "tools",
            registry=self.tools,
        )
        self._dynamic_loader.scan_and_load()

        # Scan plugin directories
        self.worker_type_registry.scan_plugins()
        self.agent_profile_registry.scan_plugins()

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        extra_read = [BUILTIN_SKILLS_DIR] if allowed_dir else None
        self.tools.register(ReadFileTool(workspace=self.workspace, allowed_dir=allowed_dir, extra_allowed_dirs=extra_read))
        for cls in (WriteFileTool, EditFileTool, ListDirTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        if self.exec_config.enable:
            self.tools.register(ExecTool(
                working_dir=str(self.workspace),
                timeout=self.exec_config.timeout,
                restrict_to_workspace=self.restrict_to_workspace,
                path_append=self.exec_config.path_append,
            ))
        self.tools.register(WebSearchTool(config=self.web_search_config, proxy=self.web_proxy))
        self.tools.register(WebFetchTool(proxy=self.web_proxy))
        self.tools.register(MessageTool(send_callback=self.bus.publish_outbound))
        self.tools.register(SpawnTool(manager=self.subagents))
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))
        if self.worker_manager:
            self.tools.register(WorkerStartTool(manager=self.worker_manager))
            self.tools.register(WorkerStatusTool(manager=self.worker_manager))
            self.tools.register(WorkerSteerTool(manager=self.worker_manager))
            self.tools.register(WorkerStopTool(manager=self.worker_manager))

        # Meta-tools: reload & evolve (always available)
        self.tools.register(ReloadTool(agent_loop=self))
        self.tools.register(EvolveTool(agent_loop=self))

        # Team orchestration tools
        self.tools.register(TeamCreateTool(orchestrator=self.team_orchestrator))
        self.tools.register(TeamStatusTool(orchestrator=self.team_orchestrator))
        self.tools.register(TeamSteerTool(orchestrator=self.team_orchestrator))

        # Agent import tool
        self.tools.register(ImportAgentTool(
            plugins_dir=self.workspace / "plugins" / "agents",
            agent_loop=self,
        ))

        # Browser tool (Playwright-based visual web interaction)
        self.tools.register(BrowserTool(
            screenshots_dir=self.workspace / "screenshots",
        ))

        # Sandbox tool (Docker-based isolated execution)
        self.tools.register(SandboxTool(workspace=self.workspace))

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy) with auto-recovery."""
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        from sharanbot.agent.tools.mcp import connect_mcp_servers

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                self._mcp_stack = AsyncExitStack()
                await self._mcp_stack.__aenter__()
                await connect_mcp_servers(self._mcp_servers, self.tools, self._mcp_stack)
                self._mcp_connected = True
                if attempt > 1:
                    self.recovery_engine.reset_circuit("mcp")
                    self.learning_store.record(
                        category="mcp_failure",
                        error_signature="mcp_failure:connect",
                        description="MCP connection recovered",
                        fix_applied=f"Retry attempt {attempt} succeeded",
                        success=True,
                    )
                break
            except BaseException as e:
                logger.error("Failed to connect MCP servers (attempt {}/{}): {}", attempt, max_attempts, e)
                if self._mcp_stack:
                    try:
                        await self._mcp_stack.aclose()
                    except Exception:
                        pass
                    self._mcp_stack = None
                if attempt < max_attempts:
                    wait = 2 ** attempt
                    logger.info("Retrying MCP connection in {}s...", wait)
                    self.learning_store.record(
                        category="mcp_failure",
                        error_signature="mcp_failure:connect",
                        description=str(e)[:200],
                        fix_applied=f"Retry with {wait}s backoff",
                        success=False,
                        cooldown_seconds=wait,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error("MCP connection failed after {} attempts, will retry next message", max_attempts)
        self._mcp_connecting = False

    async def handle_reload(self, component: str, target: str | None = None) -> str:
        """Dispatch a reload request for the given component."""
        if component == "tools":
            count = self._dynamic_loader.reload_all()
            return f"Reloaded dynamic tools ({count} loaded)."
        elif component == "mcp":
            await self._reconnect_mcp()
            return "MCP servers reconnected."
        elif component == "config":
            from sharanbot.config.loader import reload_config
            new_cfg = reload_config(self._config_path) if hasattr(self, "_config_path") else None
            if new_cfg:
                self.apply_config_reload(new_cfg)
                return "Configuration reloaded and applied."
            return "No config path set; reload skipped."
        elif component == "workers":
            self.worker_type_registry.reload_plugins()
            return f"Worker types reloaded ({len(self.worker_type_registry.list_types())} available)."
        elif component == "agents":
            self.agent_profile_registry.reload_plugins()
            return f"Agent profiles reloaded ({len(self.agent_profile_registry.list_profiles())} available)."
        elif component == "channels":
            if self.channel_manager and hasattr(self.channel_manager, "reload_all"):
                await self.channel_manager.reload_all()
                return "Channels reloaded."
            return "Channel manager not available."
        else:
            return f"Unknown component: {component}"

    async def _reconnect_mcp(self) -> None:
        """Tear down and reconnect all MCP servers."""
        # Close existing connections
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass  # MCP SDK cancel scope cleanup is noisy but harmless
            except Exception:
                pass
            self._mcp_stack = None
        # Unregister MCP tools
        for name in list(self.tools._tools.keys()):
            if name.startswith("mcp_"):
                self.tools.unregister(name)
        # Reset state and reconnect
        self._mcp_connected = False
        self._mcp_connecting = False
        await self._connect_mcp()

    def apply_config_reload(self, new_config) -> None:
        """Apply a new Config object to the running agent."""
        defaults = new_config.agent_defaults
        if defaults.model:
            self.model = defaults.model
        if defaults.max_iterations:
            self.max_iterations = defaults.max_iterations
        if defaults.context_window:
            self.context_window_tokens = defaults.context_window
        # Update MCP servers list for next reconnect
        if new_config.mcp_servers:
            self._mcp_servers = new_config.mcp_servers

    def _set_tool_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """Update context for all tools that need routing info."""
        for name in ("message", "spawn", "cron", "team_create"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    tool.set_context(channel, chat_id, *([message_id] if name == "message" else []))

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think>…</think> blocks that some models embed in content."""
        if not text:
            return None
        return re.sub(r"<think>[\s\S]*?</think>", "", text).strip() or None

    @staticmethod
    def _tool_hint(tool_calls: list) -> str:
        """Format tool calls as concise hint, e.g. 'web_search("query")'."""
        def _fmt(tc):
            args = (tc.arguments[0] if isinstance(tc.arguments, list) else tc.arguments) or {}
            val = next(iter(args.values()), None) if isinstance(args, dict) else None
            if not isinstance(val, str):
                return tc.name
            return f'{tc.name}("{val[:40]}…")' if len(val) > 40 else f'{tc.name}("{val}")'
        return ", ".join(_fmt(tc) for tc in tool_calls)

    def _status_response(self, msg: InboundMessage, session: Session) -> OutboundMessage:
        """Build an outbound status message for a session."""
        ctx_est = 0
        try:
            ctx_est, _ = self.memory_consolidator.estimate_session_prompt_tokens(session)
        except Exception:
            pass
        if ctx_est <= 0:
            ctx_est = self._last_usage.get("prompt_tokens", 0)
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=build_status_content(
                version=__version__, model=self.model,
                start_time=self._start_time, last_usage=self._last_usage,
                context_window_tokens=self.context_window_tokens,
                session_msg_count=len(session.get_history(max_messages=0)),
                context_tokens_estimate=ctx_est,
            ),
            metadata={"render_as": "text"},
        )

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
    ) -> tuple[str | None, list[str], list[dict]]:
        """Run the agent iteration loop."""
        messages = initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []

        while iteration < self.max_iterations:
            iteration += 1

            tool_defs = self.tools.get_definitions()

            response = await self.provider.chat_with_retry(
                messages=messages,
                tools=tool_defs,
                model=self.model,
            )
            usage = response.usage or {}
            self._last_usage = {
                "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
            }

            if response.has_tool_calls:
                if on_progress:
                    thought = self._strip_think(response.content)
                    if thought:
                        await on_progress(thought)
                    tool_hint = self._tool_hint(response.tool_calls)
                    tool_hint = self._strip_think(tool_hint)
                    await on_progress(tool_hint, tool_hint=True)

                tool_call_dicts = [
                    tc.to_openai_tool_call()
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )

                for tool_call in response.tool_calls:
                    tools_used.append(tool_call.name)
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info("Tool call: {}({})", tool_call.name, args_str[:200])
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
            else:
                clean = self._strip_think(response.content)
                # Don't persist error responses to session history — they can
                # poison the context and cause permanent 400 loops (#1303).
                if response.finish_reason == "error":
                    logger.error("LLM returned error: {}", (clean or "")[:200])

                    # Auto-recovery: try to recover from LLM errors
                    recovered = await self.recovery_engine.handle_error(
                        clean or "LLM error",
                        component="llm_provider",
                        context={"iteration": iteration},
                    )
                    if recovered and iteration < self.max_iterations:
                        logger.info("LLM error auto-recovered, continuing loop")
                        continue  # Retry the LLM call

                    final_content = clean or "Sorry, I encountered an error calling the AI model."
                    break
                messages = self.context.add_assistant_message(
                    messages, clean, reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )
                final_content = clean
                break

        if final_content is None and iteration >= self.max_iterations:
            logger.warning("Max iterations ({}) reached", self.max_iterations)
            final_content = (
                f"I reached the maximum number of tool call iterations ({self.max_iterations}) "
                "without completing the task. You can try breaking the task into smaller steps."
            )

        return final_content, tools_used, messages

    async def run(self) -> None:
        """Run the agent loop, dispatching messages as tasks to stay responsive to /stop."""
        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                # Preserve real task cancellation so shutdown can complete cleanly.
                # Only ignore non-task CancelledError signals that may leak from integrations.
                if not self._running or asyncio.current_task().cancelling():
                    raise
                continue
            except Exception as e:
                logger.warning("Error consuming inbound message: {}, continuing...", e)
                continue

            cmd = msg.content.strip().lower()
            if cmd == "/stop":
                await self._handle_stop(msg)
            elif cmd == "/restart":
                await self._handle_restart(msg)
            elif cmd == "/status":
                session = self.sessions.get_or_create(msg.session_key)
                await self.bus.publish_outbound(self._status_response(msg, session))
            else:
                task = asyncio.create_task(self._dispatch(msg))
                self._active_tasks.setdefault(msg.session_key, []).append(task)
                task.add_done_callback(lambda t, k=msg.session_key: self._active_tasks.get(k, []) and self._active_tasks[k].remove(t) if t in self._active_tasks.get(k, []) else None)

    async def _handle_stop(self, msg: InboundMessage) -> None:
        """Cancel all active tasks and subagents for the session."""
        tasks = self._active_tasks.pop(msg.session_key, [])
        cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        sub_cancelled = await self.subagents.cancel_by_session(msg.session_key)
        total = cancelled + sub_cancelled
        content = f"Stopped {total} task(s)." if total else "No active task to stop."
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=content,
        ))

    async def _handle_restart(self, msg: InboundMessage) -> None:
        """Restart the process in-place via os.execv."""
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content="Restarting...",
        ))

        async def _do_restart():
            await asyncio.sleep(1)
            # Use -m sharanbot instead of sys.argv[0] for Windows compatibility
            # (sys.argv[0] may be just "sharanbot" without full path on Windows)
            os.execv(sys.executable, [sys.executable, "-m", "sharanbot"] + sys.argv[1:])

        asyncio.create_task(_do_restart())

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message under the global lock, with auto-recovery on failure."""
        max_recovery_attempts = 2

        for attempt in range(1, max_recovery_attempts + 1):
            async with self._processing_lock:
                try:
                    response = await self._process_message(msg)
                    if response is not None:
                        await self.bus.publish_outbound(response)
                    elif msg.channel == "cli":
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=msg.channel, chat_id=msg.chat_id,
                            content="", metadata=msg.metadata or {},
                        ))
                    return  # Success
                except asyncio.CancelledError:
                    logger.info("Task cancelled for session {}", msg.session_key)
                    raise
                except Exception as exc:
                    logger.exception("Error processing message for session {} (attempt {})", msg.session_key, attempt)

                    if attempt < max_recovery_attempts:
                        recovered = await self.recovery_engine.handle_error(
                            exc,
                            component="message_dispatch",
                            context={"session": msg.session_key, "attempt": attempt},
                        )
                        if recovered:
                            logger.info("Message dispatch auto-recovered, retrying")
                            continue

                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id,
                        content="Sorry, I encountered an error. Auto-recovery was attempted but failed.",
                    ))
                    return

    async def close_mcp(self) -> None:
        """Drain pending background archives, then close MCP connections."""
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass  # MCP SDK cancel scope cleanup is noisy but harmless
            self._mcp_stack = None

    def _schedule_background(self, coro) -> None:
        """Schedule a coroutine as a tracked background task (drained on shutdown)."""
        task = asyncio.create_task(coro)
        self._background_tasks.append(task)
        task.add_done_callback(self._background_tasks.remove)

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """Process a single inbound message and return the response."""
        # System messages: parse origin from chat_id ("channel:chat_id")
        if msg.channel == "system":
            channel, chat_id = (msg.chat_id.split(":", 1) if ":" in msg.chat_id
                                else ("cli", msg.chat_id))
            logger.info("Processing system message from {}", msg.sender_id)
            key = f"{channel}:{chat_id}"
            session = self.sessions.get_or_create(key)
            await self.memory_consolidator.maybe_consolidate_by_tokens(session)
            self._set_tool_context(channel, chat_id, msg.metadata.get("message_id"))
            history = session.get_history(max_messages=0)
            # Subagent results should be assistant role, other system messages use user role
            current_role = "assistant" if msg.sender_id == "subagent" else "user"
            messages = self.context.build_messages(
                history=history,
                current_message=msg.content, channel=channel, chat_id=chat_id,
                current_role=current_role,
            )
            final_content, _, all_msgs = await self._run_agent_loop(messages)
            self._save_turn(session, all_msgs, 1 + len(history))
            self.sessions.save(session)
            self._schedule_background(self.memory_consolidator.maybe_consolidate_by_tokens(session))
            return OutboundMessage(channel=channel, chat_id=chat_id,
                                  content=final_content or "Background task completed.")

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        key = session_key or msg.session_key
        session = self.sessions.get_or_create(key)

        # Slash commands
        cmd = msg.content.strip().lower()
        if cmd == "/new":
            snapshot = session.messages[session.last_consolidated:]
            session.clear()
            self.sessions.save(session)
            self.sessions.invalidate(session.key)

            if snapshot:
                self._schedule_background(self.memory_consolidator.archive_messages(snapshot))

            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="New session started.")
        if cmd == "/status":
            return self._status_response(msg, session)
        if cmd == "/help":
            lines = [
                "🤖 SharanBot commands:",
                "/new — Start a new conversation",
                "/stop — Stop the current task",
                "/restart — Restart the bot",
                "/status — Show bot status",
                "/help — Show available commands",
            ]
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="\n".join(lines),
                metadata={"render_as": "text"},
            )
        await self.memory_consolidator.maybe_consolidate_by_tokens(session)

        self._set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"))
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        # Autopilot: analyze task complexity and inject routing hint
        autopilot_hint = ""
        if self.autopilot.enabled:
            try:
                decision = await self.autopilot.analyze(msg.content)
                logger.info(
                    "Autopilot decision: strategy={}, complexity={}, profile={}, pipeline={}, reason={}",
                    decision.strategy, decision.complexity,
                    decision.suggested_profile, decision.suggested_pipeline,
                    decision.reasoning,
                )
                if decision.needs_team:
                    autopilot_hint = (
                        f"\n\n[Autopilot Advisory] This task is complex ({decision.complexity}). "
                        f"Recommended: Use team_create with pipeline='{decision.suggested_pipeline}' "
                        f"to handle this efficiently with a multi-agent team. Reason: {decision.reasoning}"
                    )
                elif decision.needs_subagent:
                    autopilot_hint = (
                        f"\n\n[Autopilot Advisory] This task is moderate ({decision.complexity}). "
                        f"Consider using spawn with profile='{decision.suggested_profile}' "
                        f"for focused execution. Reason: {decision.reasoning}"
                    )
            except Exception as e:
                logger.warning("Autopilot analysis failed, proceeding without hint: {}", e)

        # Inject memory context hint when conversation is long
        memory_hint = ""
        try:
            estimated, _ = self.memory_consolidator.estimate_session_prompt_tokens(session)
            if estimated > self.context_window_tokens // 2:
                mem_content = self.context.memory.read_long_term()
                if mem_content:
                    memory_hint = (
                        f"\n\n[Context Reminder] Your conversation is getting long "
                        f"({estimated}/{self.context_window_tokens} tokens). "
                        f"Key context from memory:\n{mem_content[:2000]}"
                    )
        except Exception:
            pass

        # Build enriched user message with autopilot + memory hints
        enriched_content = msg.content + autopilot_hint + memory_hint

        history = session.get_history(max_messages=0)
        initial_messages = self.context.build_messages(
            history=history,
            current_message=enriched_content,
            media=msg.media if msg.media else None,
            channel=msg.channel, chat_id=msg.chat_id,
        )

        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content=content, metadata=meta,
            ))

        final_content, _, all_msgs = await self._run_agent_loop(
            initial_messages, on_progress=on_progress or _bus_progress,
        )

        if final_content is None:
            final_content = "I've completed processing but have no response to give."

        self._save_turn(session, all_msgs, 1 + len(history))
        self.sessions.save(session)
        self._schedule_background(self.memory_consolidator.maybe_consolidate_by_tokens(session))

        if (mt := self.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            return None

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=final_content,
            metadata=msg.metadata or {},
        )

    @staticmethod
    def _image_placeholder(block: dict[str, Any]) -> dict[str, str]:
        """Convert an inline image block into a compact text placeholder."""
        path = (block.get("_meta") or {}).get("path", "")
        return {"type": "text", "text": f"[image: {path}]" if path else "[image]"}

    def _sanitize_persisted_blocks(
        self,
        content: list[dict[str, Any]],
        *,
        truncate_text: bool = False,
        drop_runtime: bool = False,
    ) -> list[dict[str, Any]]:
        """Strip volatile multimodal payloads before writing session history."""
        filtered: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                filtered.append(block)
                continue

            if (
                drop_runtime
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
                and block["text"].startswith(ContextBuilder._RUNTIME_CONTEXT_TAG)
            ):
                continue

            if (
                block.get("type") == "image_url"
                and block.get("image_url", {}).get("url", "").startswith("data:image/")
            ):
                filtered.append(self._image_placeholder(block))
                continue

            if block.get("type") == "text" and isinstance(block.get("text"), str):
                text = block["text"]
                if truncate_text and len(text) > self._TOOL_RESULT_MAX_CHARS:
                    text = text[:self._TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"
                filtered.append({**block, "text": text})
                continue

            filtered.append(block)

        return filtered

    def _save_turn(self, session: Session, messages: list[dict], skip: int) -> None:
        """Save new-turn messages into session, truncating large tool results."""
        from datetime import datetime
        for m in messages[skip:]:
            entry = dict(m)
            role, content = entry.get("role"), entry.get("content")
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue  # skip empty assistant messages — they poison session context
            if role == "tool":
                if isinstance(content, str) and len(content) > self._TOOL_RESULT_MAX_CHARS:
                    entry["content"] = content[:self._TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"
                elif isinstance(content, list):
                    filtered = self._sanitize_persisted_blocks(content, truncate_text=True)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            elif role == "user":
                if isinstance(content, str) and content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                    # Strip the runtime-context prefix, keep only the user text.
                    parts = content.split("\n\n", 1)
                    if len(parts) > 1 and parts[1].strip():
                        entry["content"] = parts[1]
                    else:
                        continue
                if isinstance(content, list):
                    filtered = self._sanitize_persisted_blocks(content, drop_runtime=True)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)
        session.updated_at = datetime.now()

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """Process a message directly and return the outbound payload."""
        await self._connect_mcp()
        msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id, content=content)
        return await self._process_message(msg, session_key=session_key, on_progress=on_progress)
