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
from sharanbot.agent.tools.reload import ReloadTool
from sharanbot.agent.tools.evolve import EvolveTool
from sharanbot.agent.tool_manager import ToolManager
from sharanbot.agent.profiles import AgentProfileRegistry
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
from sharanbot.agent.tools.template_engine import TemplateEngineTool
from sharanbot.agent.tools.git import GitTool
from sharanbot.agent.tools.live_preview import LivePreviewTool
from sharanbot.agent.tools.package_manager import PackageManagerTool
from sharanbot.agent.tools.test_runner import TestRunnerTool
from sharanbot.agent.tools.deploy import DeployTool
from sharanbot.agent.tools.specify_init import SpecifyInitTool
from sharanbot.agent.tools.format_code import FormatTool
from sharanbot.agent.tools.design_to_code import DesignToCodeTool
from sharanbot.agent.tools.database import DatabaseTool
from sharanbot.agent.tools.wait import WaitTool
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
        self._autopilot_active: dict[str, str] = {}  # session_key -> task description
        self._session_write_locks: dict[str, asyncio.Lock] = {}  # protect session writes

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

        # ── SharanAGI: human-like capabilities ──
        from sharanbot.agi.decisions import DecisionStore
        from sharanbot.agi.escalation import EscalationManager
        from sharanbot.agi.knowledge import KnowledgeBase
        from sharanbot.agi.projects import ProjectManager
        from sharanbot.agi.stuck import ConfidenceScorer, StuckDetector
        from sharanbot.agi.reactions import ReactionEngine, EventType as RxEventType, OrchestratorEvent
        from sharanbot.agi.observability import Observer, MetricName

        self.decision_store = DecisionStore(workspace)
        self.project_manager = ProjectManager(workspace)
        self.knowledge_base = KnowledgeBase(workspace)

        from sharanbot.agi.task_ledger import TaskLedger
        self.task_ledger = TaskLedger(workspace)
        self.stuck_detector = StuckDetector()
        self.confidence_scorer = ConfidenceScorer()
        self.escalation = EscalationManager()
        self.observer = Observer(workspace)

        # Reaction engine: event-driven auto-handling with retry + escalation
        self.reaction_engine = ReactionEngine(
            on_notify=self._reaction_notify,
            on_send_to_agent=self._reaction_send_to_agent,
            is_autopilot_active=lambda: bool(self._autopilot_active),
        )

        # Wire reaction engine to team orchestrator
        self.team_orchestrator.set_reaction_engine(self.reaction_engine)

        # ── Self-Evolution Engine: REAL autonomous self-improvement ──
        from sharanbot.agi.evolution import SelfEvolutionEngine
        self.evolution_engine = SelfEvolutionEngine(
            provider=provider,
            model=self.model,
            workspace=workspace,
            learning=self.learning_store,
            observer=self.observer,
            stuck_detector=self.stuck_detector,
            recovery=self.recovery_engine,
            reaction_engine=self.reaction_engine,
        )

        # Wire evolution trigger into reaction engine for auto-healing
        async def _evolve_from_reaction(description: str) -> None:
            from sharanbot.agi.evolution import EvolutionTrigger
            await self.evolution_engine.trigger_from_event(
                EvolutionTrigger.TASK_FAILURE,
                description=description,
            )
        self.reaction_engine._on_evolve = _evolve_from_reaction

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

        # Vibe-coding tools — modern scaffolding, git, packages, preview, tests, deploy
        self.tools.register(TemplateEngineTool())
        self.tools.register(GitTool(workspace=self.workspace))
        self.tools.register(LivePreviewTool(
            screenshots_dir=self.workspace / ".sharanbot_screenshots",
        ))
        self.tools.register(PackageManagerTool())
        self.tools.register(TestRunnerTool())
        self.tools.register(DeployTool())
        self.tools.register(SpecifyInitTool())
        self.tools.register(FormatTool())
        design_tool = DesignToCodeTool(provider=self.provider, model=self.model)
        self.tools.register(design_tool)
        self.tools.register(DatabaseTool())

        # Wait tool — async sleep for Mode A and Mode B
        self.tools.register(WaitTool())

        # Self-extension tools: create plugins, tools, skills, workers at runtime
        self.tools.register(CreatePluginTool())
        self.tools.register(CreateSkillTool())
        self.tools.register(CreateToolTool(tool_registry=self.tools))
        self.tools.register(CreateWorkerTool())
        self.tools.register(SpawnSelfBuildTool())

        # SharanAGI tool — human-like capabilities
        from sharanbot.agent.tools.agi import AGITool
        self.tools.register(AGITool(
            project_manager=self.project_manager,
            knowledge_base=self.knowledge_base,
            escalation=self.escalation,
            stuck_detector=self.stuck_detector,
            evolution_engine=self.evolution_engine,
            decision_store=self.decision_store,
            task_ledger=self.task_ledger,
        ))

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

    async def _reaction_notify(self, event) -> None:
        """Callback for reaction engine: notify user via their active channel."""
        # Route to the most recent active channel
        icon = {"urgent": "🚨", "action": "⚡", "warning": "⚠️", "info": "ℹ️"}.get(
            event.priority.value, "📢"
        )
        content = f"{icon} {event.message}"
        # Try to extract channel info from session_key
        parts = event.session_key.split(":", 1) if event.session_key else []
        channel = parts[0] if len(parts) >= 2 else None
        chat_id = parts[1] if len(parts) >= 2 else None

        # If no valid channel from session_key, find the most recent active session
        if not channel or not chat_id:
            for key in reversed(list(self.sessions._cache.keys())):
                if ":" in key:
                    ch, cid = key.split(":", 1)
                    if ch not in {"cli", "system"}:
                        channel, chat_id = ch, cid
                        break
            if not channel:
                channel, chat_id = "cli", "direct"

        await self.bus.publish_outbound(OutboundMessage(
            channel=channel, chat_id=chat_id, content=content,
        ))

    async def _reaction_send_to_agent(self, session_key: str, message: str) -> bool:
        """Callback for reaction engine: inject a message into the agent's context."""
        # This is used when the reaction engine wants to send instructions
        # to a running agent (e.g., "CI failed, fix it"). We publish it as
        # an inbound system message that the agent loop will pick up.
        msg = InboundMessage(
            channel="system",
            sender_id="reaction_engine",
            chat_id=session_key,
            content=f"[Reaction Engine] {message}",
        )
        await self.bus.publish_inbound(msg)
        return True

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

        base = build_status_content(
            version=__version__, model=self.model,
            start_time=self._start_time, last_usage=self._last_usage,
            context_window_tokens=self.context_window_tokens,
            session_msg_count=len(session.get_history(max_messages=0)),
            context_tokens_estimate=ctx_est,
        )

        # Show background autopilot tasks
        key = msg.session_key
        bg_tasks = self._active_tasks.get(key, [])
        running_bg = [t for t in bg_tasks if not t.done()]
        if running_bg or key in self._autopilot_active:
            task_desc = self._autopilot_active.get(key, "unknown task")
            base += f"\n🔄 Background: {len(running_bg)} task(s) running"
            base += f"\n   └─ {task_desc[:120]}"

        # Show reaction engine status
        rx_stats = self.reaction_engine.get_stats()
        if rx_stats["total_events"] > 0:
            base += f"\n⚡ Reactions: {rx_stats['total_events']} events, {rx_stats['escalations']} escalations"
        if self.reaction_engine.is_paused:
            base += "\n⏸️ PAUSED: Rate limit cooldown active"

        # Show observability summary
        obs = self.observer.get_summary()
        if obs["overall_health"] != "ok":
            base += f"\n🏥 Health: {obs['overall_health']}"

        # Show task ledger summary
        try:
            ledger_stats = self.task_ledger.get_stats()
            if ledger_stats["total_tasks"] > 0:
                base += (
                    f"\n📋 Tasks: {ledger_stats['active']} active, "
                    f"{ledger_stats['completed']} done, {ledger_stats['failed']} failed"
                )
            if ledger_stats["running_services"] > 0:
                base += f"\n🔧 Services: {ledger_stats['running_services']} running"
        except Exception:
            pass

        # Show active decisions
        try:
            active_decisions = self.decision_store.get_active_decisions()
            if active_decisions:
                base += f"\n📌 Decisions: {len(active_decisions)} active"
        except Exception:
            pass

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=base,
            metadata={"render_as": "text"},
        )

    async def _handle_evolve_command(self, msg: InboundMessage, cmd: str) -> OutboundMessage:
        """Handle /evolve slash command."""
        from sharanbot.agi.evolution import EvolutionTrigger

        sub = cmd.replace("/evolve", "").strip()

        if sub == "status":
            status = self.evolution_engine.get_status()
            growth = status["growth"]
            rl = status["rate_limit"]
            lines = [
                "🧬 Self-Evolution Status",
                f"  Evolutions: {growth['total_attempts']} ({growth['success_rate']} success)",
                f"  Capabilities added: {growth['capabilities_added']}",
                f"  Daily cycles: {rl['daily_cycles']}/{rl['max_daily']}",
            ]
            if growth.get("capabilities"):
                lines.append(f"  Recent: {', '.join(growth['capabilities'][-5:])}")
            if status["pending_approval"]:
                lines.append("  ⚠️ Pending approval — say 'approve evolution'")
            content = "\n".join(lines)
        elif sub == "approve":
            result = await self.evolution_engine.approve_pending()
            content = (
                f"✅ Approved: {result.plan_id} — {result.changes_applied} changes"
                if result else "No pending plan to approve"
            )
        elif sub == "reject":
            content = (
                "Rejected pending plan" if self.evolution_engine.reject_pending()
                else "No pending plan to reject"
            )
        else:
            result = await self.evolution_engine.run_cycle(trigger=EvolutionTrigger.MANUAL)
            if not result:
                content = "🧬 No issues found or rate-limited. System is healthy."
            elif result.success:
                content = (
                    f"🧬 Evolution SUCCESS: {result.plan_id}\n"
                    f"  Changes: {result.changes_applied}\n"
                    f"  New: {', '.join(result.new_capabilities[:3]) or 'improvements'}"
                )
            else:
                content = f"🧬 Evolution: {result.error or 'no changes needed'}"

        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content=content, metadata={"render_as": "text"},
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

                    # SharanAGI: record tool call for stuck detection + knowledge
                    is_error = isinstance(result, str) and result.startswith("Error")
                    self.stuck_detector.record(
                        tool_call.name, tool_call.arguments,
                        result if isinstance(result, str) else str(result)[:200],
                        success=not is_error,
                    )

                    # Observability: record tool call metric
                    from sharanbot.agi.observability import MetricName
                    self.observer.record(
                        MetricName.TOOL_ERROR if is_error else MetricName.TOOL_CALL,
                        success=not is_error,
                        component="tool",
                        operation=tool_call.name,
                        reason=str(result)[:100] if is_error else "",
                    )

                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )

                # SharanAGI: check if stuck every 5 iterations
                if iteration % 5 == 0 and iteration > 5:
                    diag = self.stuck_detector.diagnose()
                    if diag.is_stuck:
                        logger.warning("Stuck detected: {} (confidence {:.0%})", diag.reason, diag.confidence)

                        # Emit reaction event for stuck detection
                        from sharanbot.agi.reactions import EventType as RxEventType
                        from sharanbot.agi.observability import MetricName
                        self.observer.record(MetricName.STUCK_DETECTED, success=False, reason=diag.reason)
                        try:
                            result = await self.reaction_engine.emit(
                                RxEventType.TASK_STUCK,
                                f"Agent stuck: {diag.reason}",
                                data={"confidence": diag.confidence, "suggestion": diag.suggestion},
                            )
                            if result and result.escalated:
                                # Reaction engine escalated — ask user for help
                                messages.append({
                                    "role": "user",
                                    "content": (
                                        f"[SharanAGI Escalation]\n"
                                        f"Auto-handling failed. Asking user for guidance.\n"
                                        f"Use the agi tool with action 'ask_human' to get help."
                                    ),
                                })
                                continue
                        except Exception:
                            pass

                        stuck_ctx = self.stuck_detector.build_stuck_context()
                        messages.append({
                            "role": "user",
                            "content": (
                                f"[SharanAGI Stuck Detection]\n"
                                f"You appear to be stuck: {diag.reason}\n"
                                f"Suggestion: {diag.suggestion}\n\n"
                                f"{stuck_ctx}\n\n"
                                f"IMPORTANT: Try a fundamentally different approach. "
                                f"If you've tried multiple approaches, use the agi tool "
                                f"with action 'ask_human' to get guidance from the user."
                            ),
                        })
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
        """Process a message — Boss is ALWAYS responsive, work runs in background.

        Architecture:
        - Simple messages (chat, questions, quick tasks) → handled inline, fast
        - Complex tasks → kicked off as background asyncio.Task, Boss responds immediately
        - Multiple complex tasks can run in parallel (each as its own background task)
        - The Boss is NEVER blocked — it can always chat with the user
        """
        session_key = msg.session_key

        max_recovery_attempts = 2

        for attempt in range(1, max_recovery_attempts + 1):
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

    def _schedule_autopilot_background(
        self,
        task: str,
        decision: Any,
        session: "Session",
        msg: InboundMessage,
        progress_fn: Any,
    ) -> None:
        """Launch autopilot execution as a background task.

        The Boss returns immediately. Autopilot runs in the background.
        When done, the result is reviewed by the Boss and delivered to the user's channel.
        Multiple autopilot tasks can run in parallel for the same user.
        """
        async def _run_autopilot_bg() -> None:
            key = msg.session_key
            self._autopilot_active[key] = task[:200]
            task_entry_id = ""
            try:
                # Auto-create task ledger entry
                try:
                    entry = self.task_ledger.create_task(
                        title=task[:200],
                        description=task[:2000],
                        task_type=decision.complexity if hasattr(decision, 'complexity') else "other",
                        session_key=key,
                        strategy=decision.strategy if hasattr(decision, 'strategy') else "",
                    )
                    task_entry_id = entry.id
                    self.task_ledger.start_task(task_entry_id)
                    logger.info("Task ledger: created {} for background task", task_entry_id)
                except Exception as e:
                    logger.debug("Task ledger auto-create failed: {}", e)
                # Snapshot tool context for this background task so it doesn't
                # get overwritten when the Boss processes other messages.
                # The team_create tool needs the correct origin channel/chat_id.
                self._set_tool_context(msg.channel, msg.chat_id,
                                       msg.metadata.get("message_id") if msg.metadata else None)

                autopilot_result = await self.autopilot.execute(
                    task=task,
                    decision=decision,
                    on_progress=progress_fn,
                )

                # Boss review: craft clean user response
                final_content = await self._boss_review(
                    user_request=task,
                    autopilot_result=autopilot_result,
                    history=session.get_history(max_messages=0),
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                )

                # Save the result to session history (locked to avoid race with inline Boss)
                from datetime import datetime
                lock = self._session_write_locks.setdefault(key, asyncio.Lock())
                async with lock:
                    session.messages.append({
                        "role": "assistant",
                        "content": f"[Background task completed]\n{final_content}",
                        "timestamp": datetime.now().isoformat(),
                    })
                    session.updated_at = datetime.now()
                    self.sessions.save(session)

                # Deliver result to user
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content=f"✅ Done with your task:\n\n{final_content}",
                    metadata={"_background_result": True},
                ))

                # Emit task completion event
                from sharanbot.agi.reactions import EventType as RxEventType
                from sharanbot.agi.observability import MetricName
                self.observer.record(MetricName.TASK_COMPLETED, session_key=key)
                self.reaction_engine.clear_all_trackers(key)
                await self.reaction_engine.emit(
                    RxEventType.TASK_COMPLETED,
                    f"Task completed: {task[:80]}",
                    session_key=key,
                )

                # Auto-complete task ledger entry
                if task_entry_id:
                    try:
                        self.task_ledger.complete_task(
                            task_entry_id,
                            outcome=final_content[:500] if final_content else "Completed",
                        )
                    except Exception:
                        pass

                # Auto-learn from completed task
                try:
                    from sharanbot.agi.knowledge import StudyAgent
                    if final_content and len(final_content) > 100:
                        study = StudyAgent(self.provider, self.model, self.knowledge_base)
                        await study.study_text(final_content[:3000], source="task_completion")
                except Exception:
                    pass

                # Background: subconscious processes the result
                self._schedule_background(
                    self.subconscious.process_transcript(
                        [{"role": "user", "content": task},
                         {"role": "assistant", "content": final_content}],
                        session_key=key,
                    )
                )

            except asyncio.CancelledError:
                logger.info("Background autopilot task cancelled for {}", key)
                # Auto-cancel task ledger entry
                if task_entry_id:
                    try:
                        self.task_ledger.cancel_task(task_entry_id)
                    except Exception:
                        pass
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content="🛑 Task cancelled.",
                    metadata={"_background_result": True},
                ))
            except Exception as exc:
                logger.exception("Background autopilot task failed for {}", key)
                # Auto-fail task ledger entry
                if task_entry_id:
                    try:
                        self.task_ledger.fail_task(task_entry_id, error=str(exc)[:500])
                    except Exception:
                        pass
                # Emit task failure event
                from sharanbot.agi.reactions import EventType as RxEventType
                from sharanbot.agi.observability import MetricName
                self.observer.record(MetricName.TASK_FAILED, success=False, reason=str(exc), session_key=key)
                result = await self.reaction_engine.emit(
                    RxEventType.TASK_FAILED,
                    f"Task failed: {exc}",
                    session_key=key,
                    data={"error": str(exc), "task": task[:200]},
                )
                # If reaction says retry, re-schedule
                if result and result.action == "retry":
                    logger.info("Reaction engine requested retry for {}", key)
                    self._schedule_autopilot_background(task, decision, session, msg, progress_fn)
                    return

                # Trigger self-evolution on repeated failures
                try:
                    from sharanbot.agi.evolution import EvolutionTrigger
                    self._schedule_background(
                        self.evolution_engine.trigger_from_event(
                            EvolutionTrigger.TASK_FAILURE,
                            description=f"Background task failed: {str(exc)[:200]}",
                            evidence=[f"Task: {task[:200]}", f"Error: {str(exc)[:200]}"],
                        )
                    )
                except Exception:
                    pass

                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content=f"❌ Task failed: {exc}",
                    metadata={"_background_result": True},
                ))
            finally:
                self._autopilot_active.pop(key, None)

        bg_task = asyncio.create_task(_run_autopilot_bg())
        self._active_tasks.setdefault(msg.session_key, []).append(bg_task)
        bg_task.add_done_callback(
            lambda t, k=msg.session_key: (
                self._active_tasks.get(k, []).remove(t)
                if t in self._active_tasks.get(k, []) else None
            )
        )

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

            # Suppress reaction engine messages during active autopilot execution.
            # The reaction engine fires events (e.g., quality.gate_failed) that inject
            # system messages into the main loop. During autopilot, these cause the
            # main loop to investigate unrelated projects. Skip them — the autopilot
            # handles its own quality gates within the team orchestrator.
            key_check = f"{channel}:{chat_id}"
            if msg.sender_id == "reaction_engine" and key_check in self._autopilot_active:
                logger.info(
                    "Suppressing reaction engine message during active autopilot: {}",
                    msg.content[:80],
                )
                return OutboundMessage(
                    channel=channel, chat_id=chat_id,
                    content="",  # swallowed
                )
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

        # SharanAGI: check if this is a response to a pending escalation
        if self.escalation.has_pending():
            if self.escalation.receive_response(msg.content):
                logger.info("Message routed to pending escalation")
                return OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content="Got it, continuing with your input.",
                )

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
                "/evolve — Run self-evolution cycle (diagnose + improve)",
                "/evolve status — Show evolution stats",
                "/help — Show available commands",
            ]
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="\n".join(lines),
                metadata={"render_as": "text"},
            )
        if cmd == "/evolve" or cmd.startswith("/evolve "):
            return await self._handle_evolve_command(msg, cmd)
        await self.memory_consolidator.maybe_consolidate_by_tokens(session)

        self._set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"))
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        # SessionStart hook: record session metadata for subconscious
        self.subconscious.on_session_start(session.key, channel=msg.channel)

        # ── Boss Flow: SharanBot decides how to handle ──

        # FAST PATH: If autopilot is already running for this session, handle
        # the new message as a quick direct chat WITHOUT heavy LLM analysis.
        # This ensures the Boss stays responsive while background work continues.
        key_for_ap_check = session_key or msg.session_key
        if key_for_ap_check in self._autopilot_active:
            active_task_desc = self._autopilot_active[key_for_ap_check]
            logger.info("Boss fast-path: autopilot active, handling '{}' as direct chat", msg.content[:60])

            # Use MINIMAL context — only last 4 messages, NOT the full 100K+ history.
            # This is the key to making the fast-path actually fast.
            history = session.get_history(max_messages=0)
            recent_history = history[-4:] if len(history) > 4 else history
            enriched = (
                f"{msg.content}\n\n"
                f"[System Note: You have a background task running: \"{active_task_desc[:150]}\". "
                f"The user is chatting while that runs. Respond to their message naturally. "
                f"If they ask about the task, tell them it's still in progress. "
                f"Keep your response brief and conversational.]"
            )
            messages = self.context.build_messages(
                history=recent_history,
                current_message=enriched,
                media=msg.media if msg.media else None,
                channel=msg.channel, chat_id=msg.chat_id,
            )
            # Use a single LLM call — no tool loop, no intent resolution, no autopilot analysis
            try:
                response = await self.provider.chat_with_retry(
                    messages=messages, model=self.model,
                )
                final_content = response.content or "I'm here — still working on your task in the background."
            except Exception as e:
                logger.warning("Boss fast-path LLM call failed: {}", e)
                final_content = (
                    f"I'm still working on your task in the background. "
                    f"Send /status to check progress, or /stop to cancel."
                )

            logger.info("Boss fast-path response delivered for '{}'", msg.content[:60])

            # Save to session
            all_msgs = messages + [{"role": "assistant", "content": final_content}]
            lock = self._session_write_locks.setdefault(key_for_ap_check, asyncio.Lock())
            async with lock:
                self._save_turn(session, all_msgs, 1 + len(recent_history))
                self.sessions.save(session)

            return OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content=final_content,
            )

        # SharanAGI: Intent Understanding — use LLM + conversation history to
        # understand what the user actually means, especially for vague/natural
        # language messages with typos. This resolved intent is what gets routed.
        resolved_content = msg.content
        try:
            history = session.get_history(max_messages=0)
            if history and len(msg.content.split()) <= 30:
                # Only use intent resolution for shorter messages that might be ambiguous
                # Long detailed messages are usually clear enough
                recent_history = history[-6:]  # last 3 exchanges
                hist_text = "\n".join(
                    f"[{m.get('role', '?')}]: {str(m.get('content', ''))[:200]}"
                    for m in recent_history if m.get('content')
                )
                if hist_text.strip():
                    intent_msgs = [
                        {"role": "system", "content": (
                            "You are an intent resolver. Given recent conversation history and a new user message "
                            "(which may contain typos, abbreviations, or vague language), output a clear version "
                            "of what the user is asking. Preserve the original meaning — just clarify it.\n"
                            "If the message is already clear, return it unchanged.\n"
                            "If it references something from the conversation ('do that', 'fix it', 'the same thing'), "
                            "resolve the reference using history.\n"
                            "Output ONLY the resolved message text, nothing else."
                        )},
                        {"role": "user", "content": (
                            f"Recent conversation:\n{hist_text}\n\n"
                            f"New user message: {msg.content}"
                        )},
                    ]
                    intent_resp = await self.provider.chat_with_retry(
                        messages=intent_msgs, model=self.model,
                    )
                    resolved = (intent_resp.content or "").strip()
                    if resolved and len(resolved) > 5:
                        resolved_content = resolved
                        if resolved_content != msg.content:
                            logger.info("Intent resolved: '{}' → '{}'", msg.content[:80], resolved_content[:80])
        except Exception as e:
            logger.debug("Intent resolution skipped: {}", e)

        # Autopilot: analyze task complexity (using resolved intent)
        decision = None
        if self.autopilot.enabled:
            try:
                decision = await self.autopilot.analyze(resolved_content)
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

        # SharanAGI: proactive memory recall when user references past context
        if not memory_hint:
            try:
                msg_lower = msg.content.lower()
                recall_triggers = [
                    "remember", "we discussed", "we talked", "last time",
                    "earlier", "before", "previously", "you said", "i told you",
                    "as we", "like we", "what about", "continue", "resume",
                    "where were we", "pick up", "back to",
                ]
                if any(trigger in msg_lower for trigger in recall_triggers):
                    mem_content = self.context.memory.read_long_term()
                    if mem_content:
                        memory_hint = (
                            f"\n\n[Memory Recall] The user is referencing past context. "
                            f"Here's your long-term memory:\n{mem_content[:3000]}"
                        )
            except Exception:
                pass

        # Inject subconscious guidance (persistent cross-session context)
        subconscious_hint = ""
        try:
            guidance = self.subconscious.inject_guidance()
            if guidance:
                subconscious_hint = f"\n\n{guidance}"
        except Exception as e:
            logger.debug("Subconscious guidance injection skipped: {}", e)

        # SharanAGI: inject knowledge context for the current task
        knowledge_hint = ""
        try:
            kb_context = self.knowledge_base.build_knowledge_context(msg.content)
            if kb_context:
                knowledge_hint = f"\n\n{kb_context}"
        except Exception as e:
            logger.debug("Knowledge context injection skipped: {}", e)

        # SharanAGI: inject active project context if relevant
        project_hint = ""
        try:
            active_projects = self.project_manager.list_all(status="active")
            if active_projects:
                # Check if user message mentions any registered project
                msg_lower = msg.content.lower()
                matched_project = None
                for proj in active_projects:
                    if proj.name.lower() in msg_lower or proj.id in msg_lower:
                        matched_project = proj
                        break
                if matched_project:
                    ctx = self.project_manager.get_project_context_for_prompt(matched_project.id)
                    if ctx:
                        project_hint = f"\n\n{ctx}"
                    # Update last_worked timestamp
                    self.project_manager.update_context(matched_project.id, matched_project.context)
                elif len(active_projects) <= 3:
                    # If few projects, show a brief summary so the agent is aware
                    proj_lines = []
                    for p in active_projects:
                        todo_count = f", {len(p.todos)} TODOs" if p.todos else ""
                        proj_lines.append(f"  - {p.name} (priority {p.priority}{todo_count})")
                    project_hint = "\n\n[Active Projects]\n" + "\n".join(proj_lines)
        except Exception as e:
            logger.debug("Project context injection skipped: {}", e)

        # SharanAGI: check if study is needed before attempting task
        try:
            domains_to_study = self.knowledge_base.should_study_before_task(msg.content)
            if domains_to_study:
                knowledge_hint += (
                    f"\n\n[Skill Assessment] Low confidence in: {', '.join(domains_to_study)}. "
                    f"Consider using web search to study these topics before proceeding, "
                    f"and use agi(action='knowledge_learn') to store what you learn."
                )
        except Exception:
            pass

        # SharanAGI: reset stuck detector for new task
        self.stuck_detector.reset()

        # SharanAGI: inject active user decisions into context
        decision_hint = ""
        try:
            decision_ctx = self.decision_store.build_context_for_prompt()
            if decision_ctx:
                decision_hint = f"\n\n{decision_ctx}"
        except Exception as e:
            logger.debug("Decision context injection skipped: {}", e)
        self.confidence_scorer.reset()

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

        # Build intent resolution hint if the message was clarified
        intent_hint = ""
        if resolved_content != msg.content:
            intent_hint = f"\n\n[Intent Resolution] User said: \"{msg.content}\"\nUnderstood as: \"{resolved_content}\""

        progress_fn = on_progress or _bus_progress

        if is_complex and decision is not None:
            # ── COMPLEX TASK: Fire off Autopilot in background, Boss responds immediately ──

            # Global pause check: if rate limited, defer the task
            if self.reaction_engine.is_paused:
                return OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content=(
                        "⏸️ I'm currently in a cooldown period (rate limit detected). "
                        "I'll queue this task and start it when the cooldown lifts. "
                        "You can check /status for updates."
                    ),
                )

            logger.info("Boss delegating to Autopilot (background): {}", resolved_content[:80])

            # Session consolidation: aggressively prune before complex tasks
            # to free up context window for the autopilot's own work.
            try:
                estimated, _ = self.memory_consolidator.estimate_session_prompt_tokens(session)
                if estimated > self.context_window_tokens * 0.5:
                    logger.info(
                        "Pre-task consolidation: session at {}/{} tokens ({}%), consolidating",
                        estimated, self.context_window_tokens,
                        int(estimated / self.context_window_tokens * 100),
                    )
                    await self.memory_consolidator.maybe_consolidate_by_tokens(session)
                    history = session.get_history(max_messages=0)
                    logger.info("Post-consolidation: {} messages in history", len(history))
            except Exception as exc:
                logger.debug("Pre-task consolidation skipped: {}", exc)

            # Save the user message to session immediately so history stays consistent
            enriched_content = msg.content + intent_hint + memory_hint + subconscious_hint + knowledge_hint + project_hint + decision_hint
            initial_messages = self.context.build_messages(
                history=history,
                current_message=enriched_content,
                media=msg.media if msg.media else None,
                channel=msg.channel, chat_id=msg.chat_id,
            )
            # Save user turn now (before autopilot runs)
            ack_msgs = initial_messages + [
                {"role": "assistant", "content": "⚡ Working on it in the background..."},
            ]
            lock = self._session_write_locks.setdefault(key, asyncio.Lock())
            async with lock:
                self._save_turn(session, ack_msgs, 1 + len(history))
                self.sessions.save(session)

            # Launch autopilot as a background task — Boss is free immediately
            self._schedule_autopilot_background(
                task=resolved_content,
                decision=decision,
                session=session,
                msg=msg,
                progress_fn=progress_fn,
            )

            # Return immediately — Boss is free to handle next message
            return OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id,
                content=(
                    f"⚡ On it. I've started working on this in the background.\n"
                    f"You can keep chatting — I'll let you know when it's done.\n"
                    f"Send /stop to cancel."
                ),
            )
        else:
            # ── SIMPLE TASK: Boss handles directly ──
            enriched_content = msg.content + intent_hint + memory_hint + subconscious_hint + knowledge_hint + project_hint + decision_hint
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

        # SharanAGI: auto-learn from completed task (both simple and complex)
        try:
            from sharanbot.agi.knowledge import StudyAgent
            if final_content and len(final_content) > 100:
                study = StudyAgent(self.provider, self.model, self.knowledge_base)
                self._schedule_background(
                    study.study_text(final_content[:3000], source="task_completion")
                )
        except Exception:
            pass

        lock = self._session_write_locks.setdefault(key, asyncio.Lock())
        async with lock:
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
        """Process a message directly and return the outbound payload.

        In single-message mode, if the task is complex and gets scheduled as a
        background autopilot task, we wait for it to complete and return the
        final result instead of the "⚡ On it" acknowledgment.
        """
        await self._connect_mcp()
        msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id, content=content)
        response = await self._process_message(msg, session_key=session_key, on_progress=on_progress)

        # If a background autopilot task was scheduled, wait for it to finish
        # so single-message mode doesn't exit before the work is done.
        key = msg.session_key
        bg_tasks = self._active_tasks.get(key, [])
        if bg_tasks:
            running = [t for t in bg_tasks if not t.done()]
            if running:
                if on_progress:
                    try:
                        await on_progress("⚡ Working on this (waiting for completion)...")
                    except Exception:
                        pass
                results = await asyncio.gather(*running, return_exceptions=True)
                # After background task completes, it publishes to bus.
                # In direct mode there's no bus consumer, so pull the final
                # result from session history instead.
                session = self.sessions.get_or_create(key)
                history = session.get_history(max_messages=0)
                if history:
                    last = history[-1]
                    last_content = last.get("content", "")
                    if isinstance(last_content, str) and "[Background task completed]" in last_content:
                        final = last_content.replace("[Background task completed]\n", "")
                        return OutboundMessage(
                            channel=channel, chat_id=chat_id,
                            content=f"✅ {final}",
                        )

        return response
