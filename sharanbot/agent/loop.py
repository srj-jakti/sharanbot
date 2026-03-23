"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
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
from sharanbot.agent.tool_manager import ToolManager
from sharanbot.agent.profiles import AgentProfileRegistry
from sharanbot.agent.worker_registry import WorkerTypeRegistry
from sharanbot.agent.team.orchestrator import TeamOrchestrator
from sharanbot.agent.team.tools import TeamCreateTool, TeamStatusTool, TeamSteerTool
from sharanbot.agent.team.autopilot import Autopilot
from sharanbot.agent.subconscious import Subconscious
from sharanbot.agent.tools.import_agent import ImportAgentTool
from sharanbot.agent.tools.browser import BrowserTool
from sharanbot.agent.tools.tandem_browser import TandemBrowserTool
from sharanbot.agent.tools.computer import ComputerTool
from sharanbot.agent.tools.wingman import WingmanTaskTool, WingmanChatTool
from sharanbot.agent.tools.sandbox import SandboxTool
from sharanbot.agent.tools.process import (
    ProcessStartTool,
    ProcessStatusTool,
    ProcessStopTool,
    PortCheckTool,
    WaitForPortTool,
)
from sharanbot.agent.tools.create_software import CreateSoftwareTool
from sharanbot.agent.tools.spawn_dev_team import SpawnDevTeamTool
from sharanbot.agent.tools.create_plugin import CreatePluginTool
from sharanbot.agent.tools.create_skill import CreateSkillTool
from sharanbot.agent.tools.create_tool import CreateToolTool
from sharanbot.agent.tools.create_worker import CreateWorkerTool
from sharanbot.agent.tools.spawn_self_build import SpawnSelfBuildTool
from sharanbot.agent.learning import LearningStore
from sharanbot.agent.recovery import RecoveryEngine
from sharanbot.bus.events import InboundMessage, OutboundMessage
from sharanbot.utils.helpers import build_status_content
from sharanbot.bus.queue import MessageBus
from sharanbot.providers.base import LLMProvider
from sharanbot.session.manager import Session, SessionManager
from sharanbot.vault import Vault
from sharanbot.services.manager import ServiceManager

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
        browser_backend: str = "auto",
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
        self.browser_backend = browser_backend
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
        self._active_tasks: dict[str, list[asyncio.Task]] = {}  # session_key -> tasks
        self._background_tasks: list[asyncio.Task] = []
        self._processing_lock = asyncio.Lock()
        self._session_locks: dict[str, asyncio.Lock] = {}  # per-session message locks
        self._autopilot_active: dict[str, str] = {}  # session_key -> task description

        # Team orchestration
        self.team_orchestrator = TeamOrchestrator(
            provider=provider,
            model=self.model,
            profile_registry=self.agent_profile_registry,
            subagent_manager=self.subagents,
            message_bus=bus,
            workspace=workspace,
        )
        self.autopilot = Autopilot(
            provider=provider,
            model=self.model,
            workspace=workspace,
            max_iterations=max_iterations,
        )

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

        # Proactive autopilot: connect learning store
        self.autopilot.set_learning_store(self.learning_store)

        # Give Autopilot full execution capabilities (Boss→Executor wiring)
        self.autopilot.set_execution_context(
            tools=self.tools,
            subagent_manager=self.subagents,
            team_orchestrator=self.team_orchestrator,
            context_window_tokens=context_window_tokens,
        )

        # Subconscious — persistent background memory layer
        self.subconscious = Subconscious(
            provider=provider,
            model=self.model,
            workspace=workspace,
        )

        # Connect subconscious to autopilot for mid-task sync
        self.autopilot.set_subconscious(self.subconscious)

        # Vault (encrypted credential store)
        self.vault = Vault(workspace / "vault.json")

        # Service manager (autonomous apps, monitors, agents)
        self.service_manager = ServiceManager(workspace)

        self._register_default_tools()

        # Dynamic tools + MCP lifecycle
        self.tool_mgr = ToolManager(
            registry=self.tools,
            workspace=workspace,
            mcp_servers=self._mcp_servers,
        )

        # Scan plugin directories
        self.worker_type_registry.scan_plugins()
        self.agent_profile_registry.scan_plugins()

    def _select_browser_tool(self, backend: str) -> Tool:
        """Select browser tool based on config with automatic fallback."""
        ss_dir = self.workspace / "screenshots"
        if backend == "playwright":
            return BrowserTool(screenshots_dir=ss_dir)
        if backend == "tandem":
            return TandemBrowserTool(screenshots_dir=ss_dir)
        # "auto" — try Tandem first, fall back to Playwright
        tandem = TandemBrowserTool(screenshots_dir=ss_dir)
        if tandem._is_reachable():
            logger.info("Browser backend: Tandem Browser (auto-detected)")
            return tandem
        logger.info("Browser backend: Playwright (Tandem not reachable)")
        return BrowserTool(screenshots_dir=ss_dir)

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

        # Browser tool — select backend based on config with fallback
        browser_tool = self._select_browser_tool(self.browser_backend)
        self.tools.register(browser_tool)

        # Computer tool — coordinate-based mouse, keyboard, and screenshot
        self.tools.register(ComputerTool(
            screenshots_dir=self.workspace / "screenshots",
        ))

        # Wingman tools — task delegation + chat with tandem-browser's AI agent
        self.tools.register(WingmanTaskTool())
        self.tools.register(WingmanChatTool())

        # Sandbox tool (Docker-based isolated execution)
        self.tools.register(SandboxTool(workspace=self.workspace))

        # Background process management tools
        if self.exec_config.enable:
            self.tools.register(ProcessStartTool(
                working_dir=str(self.workspace),
                path_append=self.exec_config.path_append,
            ))
            self.tools.register(ProcessStatusTool())
            self.tools.register(ProcessStopTool())
            self.tools.register(PortCheckTool())
            self.tools.register(WaitForPortTool())

        # Software creation & multi-agent dev team tools
        self.tools.register(CreateSoftwareTool())
        self.tools.register(SpawnDevTeamTool())

        # Self-extension tools: create plugins, tools, skills, workers at runtime
        self.tools.register(CreatePluginTool())
        self.tools.register(CreateSkillTool())
        self.tools.register(CreateToolTool(tool_registry=self.tools))
        self.tools.register(CreateWorkerTool())
        self.tools.register(SpawnSelfBuildTool())

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (delegates to ToolManager)."""
        await self.tool_mgr.connect_mcp(self.recovery_engine, self.learning_store)

    async def handle_reload(self, component: str, target: str | None = None) -> str:
        """Dispatch a reload request for the given component."""
        if component == "tools":
            count = self.tool_mgr.reload_tools()
            return f"Reloaded dynamic tools ({count} loaded)."
        elif component == "mcp":
            await self.tool_mgr.reconnect_mcp()
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
        """Tear down and reconnect all MCP servers (delegates to ToolManager)."""
        await self.tool_mgr.reconnect_mcp()

    def apply_config_reload(self, new_config) -> None:
        """Apply a new Config object to the running agent.

        Handles model, iterations, context window, provider, tools,
        MCP servers, channels, and browser backend.
        """
        from sharanbot.security.sanitizer import validate_config_update

        defaults = new_config.agent_defaults
        # Validate the dynamic fields through the sanitizer
        dynamic_updates = {}
        if defaults.model:
            dynamic_updates["model"] = defaults.model
        if defaults.max_iterations:
            dynamic_updates["max_iterations"] = defaults.max_iterations
        if defaults.context_window:
            dynamic_updates["context_window"] = defaults.context_window
        safe = validate_config_update(dynamic_updates)
        if "model" in safe:
            self.model = safe["model"]
        if "max_iterations" in safe:
            self.max_iterations = safe["max_iterations"]
        if "context_window" in safe:
            self.context_window_tokens = safe["context_window"]

        # Provider: update API key / base if changed
        provider_cfg = getattr(new_config, "providers", None)
        if provider_cfg and hasattr(self.provider, "update_config"):
            self.provider.update_config(provider_cfg)

        # Tool settings: exec, web search, browser backend
        tools_cfg = getattr(new_config, "tools", None)
        if tools_cfg:
            if hasattr(tools_cfg, "exec"):
                self.exec_config = tools_cfg.exec
            if hasattr(tools_cfg, "web") and hasattr(tools_cfg.web, "search"):
                self.web_search_config = tools_cfg.web.search

        # Update MCP servers list for next reconnect
        if new_config.mcp_servers:
            self._mcp_servers = new_config.mcp_servers
            self.tool_mgr.update_mcp_servers(new_config.mcp_servers)

        # Channels config
        channels_cfg = getattr(new_config, "channels", None)
        if channels_cfg:
            self.channels_config = channels_cfg

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

    def _estimate_messages_tokens(self, messages: list[dict]) -> int:
        """Estimate token count for a message list."""
        try:
            from sharanbot.utils.helpers import estimate_prompt_tokens_chain
            est, _ = estimate_prompt_tokens_chain(
                self.provider, self.model, messages,
                self.tools.get_definitions(),
            )
            return est
        except Exception as exc:
            logger.debug("Token estimation via provider failed, using fallback: {}", exc)
            # Rough fallback: ~4 chars per token
            total = sum(len(str(m.get("content", ""))) for m in messages)
            return total // 4

    async def _summarize_and_continue(self, messages: list[dict], iteration: int) -> list[dict]:
        """At 80%% token usage, summarize conversation so far and continue seamlessly."""
        logger.info("Token window at 80%% (iter {}), summarizing context to continue", iteration)

        # Extract system prompt (first message)
        system_msg = messages[0] if messages and messages[0].get("role") == "system" else None

        # Summarize conversation using LLM
        summary_prompt = (
            "Summarize the conversation so far into a concise but complete context. "
            "Include: what the user asked, what has been done so far (files created/edited, "
            "commands run, results obtained), what remains to be done, and any important "
            "decisions or context. Be thorough — this summary replaces the full history."
        )
        summary_messages = [
            {"role": "system", "content": "You are a conversation summarizer. Create a detailed summary that preserves all important context."},
            {"role": "user", "content": summary_prompt + "\n\nConversation:\n" + self._format_messages_for_summary(messages[1:])},
        ]

        try:
            response = await self.provider.chat_with_retry(
                messages=summary_messages,
                model=self.model,
            )
            summary = response.content or "Summary generation failed."
        except Exception as e:
            logger.warning("Summary generation failed: {}, using raw truncation", e)
            # Fallback: keep last N messages
            if system_msg:
                return [system_msg] + messages[-10:]
            return messages[-10:]

        # Rebuild messages with system + summary + continuation instruction
        new_messages = []
        if system_msg:
            new_messages.append(system_msg)
        new_messages.append({
            "role": "user",
            "content": (
                f"[Context Summary — conversation continued from iteration {iteration}]\n\n"
                f"{summary}\n\n"
                "Continue working on the task. Pick up exactly where you left off. "
                "Do NOT repeat work already done. Do NOT claim completion unless you have "
                "actually verified the result."
            ),
        })

        logger.info("Context summarized: {} messages → 2 messages, continuing autonomously", len(messages))
        return new_messages

    @staticmethod
    def _format_messages_for_summary(messages: list[dict]) -> str:
        """Format messages into readable text for summarization."""
        lines = []
        for m in messages[-50:]:  # Last 50 messages max for summary input
            role = m.get("role", "unknown").upper()
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
                )
            if content:
                lines.append(f"[{role}]: {str(content)[:500]}")
        return "\n".join(lines)

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
    ) -> tuple[str | None, list[str], list[dict]]:
        """Run the agent iteration loop — autonomous, no hard iteration cap.

        The loop runs until the LLM produces a final response (no tool calls).
        At 80%% token window usage, context is summarized and the loop continues.
        A soft checkpoint every 50 iterations logs progress but does NOT stop.
        """
        messages = initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []
        consecutive_errors = 0
        _MAX_CONSECUTIVE_ERRORS = 5

        # No hard cap — loop runs until LLM gives final response
        while True:
            iteration += 1

            # Token window management: summarize at 80% to continue seamlessly
            if iteration % 5 == 0:  # Check every 5 iterations to avoid overhead
                estimated_tokens = self._estimate_messages_tokens(messages)
                token_threshold = int(self.context_window_tokens * 0.80)
                if estimated_tokens >= token_threshold:
                    messages = await self._summarize_and_continue(messages, iteration)

            # Soft checkpoint logging every 50 iterations (no stop)
            if iteration % 50 == 0:
                logger.info(
                    "Autonomous loop checkpoint: iteration {}, tools_used={}",
                    iteration, len(tools_used),
                )

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
                consecutive_errors = 0  # successful LLM call resets counter
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
                    consecutive_errors += 1
                    logger.error("LLM returned error ({}/{}): {}", consecutive_errors, _MAX_CONSECUTIVE_ERRORS, (clean or "")[:200])

                    if consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                        logger.error("Max consecutive LLM errors reached, stopping loop")
                        final_content = clean or "Sorry, I encountered a persistent error calling the AI model."
                        break

                    # Auto-recovery: try to recover from LLM errors
                    recovered = await self.recovery_engine.handle_error(
                        clean or "LLM error",
                        component="llm_provider",
                        context={"iteration": iteration},
                    )
                    if recovered:
                        logger.info("LLM error auto-recovered, continuing loop")
                        continue  # Retry the LLM call

                    final_content = clean or "Sorry, I encountered an error calling the AI model."
                    break
                consecutive_errors = 0  # successful LLM call resets counter
                messages = self.context.add_assistant_message(
                    messages, clean, reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )
                final_content = clean
                break

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
        # If the lock is already held (e.g. autopilot running), reply immediately
        # instead of blocking. The user shouldn't have to wait minutes for a response.
        if self._processing_lock.locked():
            active_task = self._autopilot_active.get(msg.session_key)
            if active_task:
                preview = active_task[:200]
                busy_msg = (
                    f"⚡ I'm currently working on your previous request:\n"
                    f"_{preview}_\n\n"
                    f"I'll respond when it's done. Send /stop to cancel."
                )
            else:
                busy_msg = (
                    "I'm still processing your previous message. "
                    "Please wait a moment or send /stop to cancel."
                )
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content=busy_msg,
            ))
            return

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
        if self.tool_mgr._mcp_stack:
            try:
                await self.tool_mgr._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass  # MCP SDK cancel scope cleanup is noisy but harmless
            self.tool_mgr._mcp_stack = None

    def _schedule_background(self, coro) -> None:
        """Schedule a coroutine as a tracked background task (drained on shutdown)."""
        task = asyncio.create_task(coro)
        self._background_tasks.append(task)
        task.add_done_callback(self._background_tasks.remove)

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _boss_review(
        self,
        user_request: str,
        autopilot_result: str,
        history: list[dict],
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> str:
        """SharanBot Boss reviews Autopilot's work and crafts a clean user response.

        This is the quality gate — SharanBot reviews what Autopilot did and
        synthesizes it into a clean, professional response for the user.
        The user never sees the raw Autopilot output, only SharanBot's polished version.
        """
        review_messages = [
            {"role": "system", "content": (
                "You are SharanBot — the Boss. Your Autopilot executor just completed a task. "
                "Review the results and craft a CLEAN, professional response for the user.\n\n"
                "Rules:\n"
                "1. Give the user ONLY the results — no internal details, no JSON dumps, no tool call logs.\n"
                "2. Be concise but complete. The user should know what was done and the outcome.\n"
                "3. If something failed, say so honestly. Don't hide failures.\n"
                "4. If the work is incomplete, say what's done and what remains.\n"
                "5. Use natural language. No technical jargon unless the user is technical.\n"
                "6. If the result includes files created/edited, mention them clearly.\n"
                "7. NEVER say 'Autopilot did X' — you speak as SharanBot. Say 'I did X' or 'Done.'\n"
                "8. Keep it focused — only what matters to the user's original request."
            )},
            {"role": "user", "content": (
                f"**User's original request:**\n{user_request}\n\n"
                f"**Autopilot's execution report:**\n{autopilot_result}\n\n"
                "Now craft the clean response for the user."
            )},
        ]

        try:
            response = await self.provider.chat_with_retry(
                messages=review_messages,
                model=self.model,
            )
            clean_response = response.content
            if clean_response:
                return clean_response
        except Exception as e:
            logger.warning("Boss review LLM call failed: {}, using raw result", e)

        # Fallback: return autopilot result directly if review fails
        return autopilot_result

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

        # SessionStart hook: record session metadata for subconscious
        self.subconscious.on_session_start(session.key, channel=msg.channel)

        # ── Boss Flow: SharanBot decides how to handle ──
        # Autopilot: analyze task complexity
        decision = None
        if self.autopilot.enabled:
            try:
                decision = await self.autopilot.analyze(msg.content)
                logger.info(
                    "Autopilot decision: strategy={}, complexity={}, profile={}, pipeline={}, reason={}",
                    decision.strategy, decision.complexity,
                    decision.suggested_profile, decision.suggested_pipeline,
                    decision.reasoning,
                )
            except Exception as e:
                logger.warning("Autopilot analysis failed, Boss handles directly: {}", e)

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
        except Exception as exc:
            logger.debug("Memory hint generation failed: {}", exc)

        # Inject subconscious guidance (persistent cross-session context)
        subconscious_hint = ""
        try:
            guidance = self.subconscious.inject_guidance()
            if guidance:
                subconscious_hint = f"\n\n{guidance}"
        except Exception as e:
            logger.debug("Subconscious guidance injection skipped: {}", e)

        history = session.get_history(max_messages=0)

        # ── Route: Simple → Boss handles directly | Complex → Autopilot executes ──
        is_complex = (
            decision is not None
            and decision.strategy in ("single_agent", "team")
        )

        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content=content, metadata=meta,
            ))

        progress_fn = on_progress or _bus_progress

        if is_complex and decision is not None:
            # ── COMPLEX TASK: Autopilot executes, then Boss reviews ──
            logger.info("Boss delegating to Autopilot: {}", msg.content[:80])

            # Notify user that work is starting
            await progress_fn("⚡ Working on it...")

            # Autopilot runs full execution loop with all tools
            self._autopilot_active[key] = msg.content[:200]
            try:
                autopilot_result = await self.autopilot.execute(
                    task=msg.content,
                    decision=decision,
                    on_progress=progress_fn,
                )
            finally:
                self._autopilot_active.pop(key, None)

            # Boss review: SharanBot reviews Autopilot's result and crafts clean user response
            final_content = await self._boss_review(
                user_request=msg.content,
                autopilot_result=autopilot_result,
                history=history,
                channel=msg.channel,
                chat_id=msg.chat_id,
            )

            # Save the exchange to session (user msg + boss response)
            enriched_content = msg.content + memory_hint + subconscious_hint
            initial_messages = self.context.build_messages(
                history=history,
                current_message=enriched_content,
                media=msg.media if msg.media else None,
                channel=msg.channel, chat_id=msg.chat_id,
            )
            # Add autopilot result as context and boss response
            all_msgs = initial_messages + [
                {"role": "assistant", "content": final_content},
            ]
        else:
            # ── SIMPLE TASK: Boss handles directly ──
            enriched_content = msg.content + memory_hint + subconscious_hint
            initial_messages = self.context.build_messages(
                history=history,
                current_message=enriched_content,
                media=msg.media if msg.media else None,
                channel=msg.channel, chat_id=msg.chat_id,
            )

            final_content, _, all_msgs = await self._run_agent_loop(
                initial_messages, on_progress=progress_fn,
            )

        if final_content is None:
            final_content = "I've completed processing but have no response to give."

        self._save_turn(session, all_msgs, 1 + len(history))
        self.sessions.save(session)
        self._schedule_background(self.memory_consolidator.maybe_consolidate_by_tokens(session))

        # Background: subconscious processes transcript to update memory blocks
        self._schedule_background(self.subconscious.process_transcript(all_msgs, session_key=session.key))

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
