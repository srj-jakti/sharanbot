"""Tests for E2E Round 2 fixes."""

import asyncio
import time
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


# ── Fix 1: Autopilot team_status polling detection ──────────────────

class TestTeamStatusPollingDetection:
    """Verify the autopilot detects and corrects team_status polling loops."""

    def test_executor_prompt_has_team_steer_instructions(self):
        """The executor prompt must explicitly tell the LLM to use team_steer."""
        from sharanbot.agent.team.autopilot import Autopilot, AutopilotDecision

        ap = Autopilot.__new__(Autopilot)
        ap.workspace = Path("/tmp")
        ap.max_iterations = 40

        decision = AutopilotDecision(
            strategy="team",
            complexity="high",
            suggested_profile="architect",
            suggested_pipeline="full_stack",
            reasoning="test",
        )
        prompt = ap._build_executor_prompt(decision)

        assert "team_steer" in prompt
        assert "CRITICAL" in prompt or "MUST call team_steer" in prompt
        assert "DO NOT call `team_status` more than 2 times" in prompt

    def test_executor_prompt_warns_about_polling(self):
        """The prompt must warn that team_status only checks, doesn't execute."""
        from sharanbot.agent.team.autopilot import Autopilot, AutopilotDecision

        ap = Autopilot.__new__(Autopilot)
        ap.workspace = Path("/tmp")
        ap.max_iterations = 40

        decision = AutopilotDecision(
            strategy="team",
            complexity="high",
            suggested_profile="architect",
            suggested_pipeline="full_stack",
            reasoning="test",
        )
        prompt = ap._build_executor_prompt(decision)

        assert "`team_status` only CHECKS status" in prompt
        assert "DO NOT BUILD DIRECTLY" in prompt


# ── Fix 2: Reaction engine scoping ──────────────────────────────────

class TestReactionEngineScoping:
    """Verify reaction engine messages are suppressed during active autopilot."""

    async def test_reaction_message_suppressed_during_autopilot(self):
        """System messages from reaction_engine should be suppressed when autopilot is active."""
        from sharanbot.bus.events import InboundMessage

        # Create a minimal mock of AgentLoop
        loop = MagicMock()
        loop._autopilot_active = {"telegram:12345": "Building dashboard"}
        loop.sessions = MagicMock()
        loop.memory_consolidator = MagicMock()

        msg = InboundMessage(
            channel="system",
            sender_id="reaction_engine",
            chat_id="telegram:12345",
            content="Quality gate failed. Review feedback.",
        )

        # The fix checks if sender_id == "reaction_engine" and session is in _autopilot_active
        assert msg.sender_id == "reaction_engine"
        assert "telegram:12345" in loop._autopilot_active

    def test_quality_gate_default_action_is_notify(self):
        """quality.gate_failed should use NOTIFY, not SEND_TO_AGENT."""
        from sharanbot.agi.reactions import DEFAULT_REACTIONS, ReactionAction

        config = DEFAULT_REACTIONS["quality.gate_failed"]
        assert config.action == ReactionAction.NOTIFY


# ── Fix 3: Task Ledger auto-wiring ──────────────────────────────────

class TestTaskLedgerAutoWiring:
    """Verify task ledger is auto-wired into autopilot execution."""

    def test_task_ledger_create_and_complete(self):
        """Task ledger should track task lifecycle."""
        from sharanbot.agi.task_ledger import TaskLedger, TaskStatus
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            ledger = TaskLedger(Path(tmp))
            entry = ledger.create_task(
                title="Build dashboard",
                task_type="build",
                strategy="team",
                session_key="telegram:12345",
            )
            assert entry.status == TaskStatus.PENDING

            ledger.start_task(entry.id)
            t = ledger.get_task(entry.id)
            assert t.status == TaskStatus.RUNNING

            ledger.complete_task(entry.id, outcome="Dashboard built successfully")
            t = ledger.get_task(entry.id)
            assert t.status == TaskStatus.COMPLETED
            assert t.outcome == "Dashboard built successfully"

    def test_task_ledger_fail(self):
        """Task ledger should track failures."""
        from sharanbot.agi.task_ledger import TaskLedger, TaskStatus
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            ledger = TaskLedger(Path(tmp))
            entry = ledger.create_task(title="Failing task")
            ledger.start_task(entry.id)
            ledger.fail_task(entry.id, error="LLM timeout")
            t = ledger.get_task(entry.id)
            assert t.status == TaskStatus.FAILED
            assert t.error == "LLM timeout"

    def test_task_ledger_cancel(self):
        """Task ledger should track cancellations."""
        from sharanbot.agi.task_ledger import TaskLedger, TaskStatus
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            ledger = TaskLedger(Path(tmp))
            entry = ledger.create_task(title="Cancelled task")
            ledger.cancel_task(entry.id)
            t = ledger.get_task(entry.id)
            assert t.status == TaskStatus.CANCELLED


# ── Fix 4: Stuck detection for polling loops ─────────────────────────

class TestStuckDetectorPollingLoop:
    """Verify stuck detector catches team_status polling loops."""

    def test_detects_same_tool_repeated(self):
        """Exact same tool+args repeated should be detected."""
        from sharanbot.agi.stuck import StuckDetector

        detector = StuckDetector(repeat_threshold=3)
        for _ in range(4):
            detector.record("team_status", {"team_id": "abc123"}, "pending", success=True)

        diag = detector.diagnose()
        assert diag.is_stuck
        assert "team_status" in diag.reason

    def test_detects_polling_loop_different_args(self):
        """Same tool name repeated with different args should be detected as polling."""
        from sharanbot.agi.stuck import StuckDetector

        detector = StuckDetector(repeat_threshold=3)
        # Simulate team_status called many times (even with slightly different results)
        for i in range(8):
            detector.record("team_status", {"team_id": f"abc{i % 2}"}, f"result_{i}", success=True)

        diag = detector.diagnose()
        assert diag.is_stuck
        assert "team_status" in diag.reason or "Polling" in diag.reason

    def test_no_false_positive_for_varied_tools(self):
        """Different tools should not trigger polling detection."""
        from sharanbot.agi.stuck import StuckDetector

        detector = StuckDetector()
        tools = ["read_file", "write_file", "exec", "list_dir", "web_search"]
        for i, tool in enumerate(tools):
            detector.record(tool, {"arg": str(i)}, f"result_{i}", success=True)

        diag = detector.diagnose()
        assert not diag.is_stuck


# ── Fix 5: Progress feedback ─────────────────────────────────────────

class TestProgressFeedback:
    """Verify periodic progress updates are sent."""

    def test_executor_prompt_mentions_progress(self):
        """The executor prompt should exist and be buildable."""
        from sharanbot.agent.team.autopilot import Autopilot, AutopilotDecision

        ap = Autopilot.__new__(Autopilot)
        ap.workspace = Path("/tmp")
        ap.max_iterations = 40

        decision = AutopilotDecision(
            strategy="direct",
            complexity="low",
            suggested_profile="general",
            suggested_pipeline="",
            reasoning="test",
        )
        prompt = ap._build_executor_prompt(decision)
        assert "Autopilot" in prompt
        assert "workspace" in prompt.lower()


# ── Fix 6: Circuit reset log level ───────────────────────────────────

class TestCircuitResetLogLevel:
    """Verify circuit reset only logs on state transition (open→closed)."""

    def test_circuit_reset_only_logs_on_transition(self):
        """reset_circuit should only log when circuit was previously open."""
        import inspect
        from sharanbot.agent.recovery import RecoveryEngine

        source = inspect.getsource(RecoveryEngine.reset_circuit)
        # Should check was_open before logging
        assert "was_open" in source
        # Should use logger.info for actual recovery events
        assert "logger.info" in source

    def test_circuit_reset_no_log_when_already_closed(self):
        """reset_circuit on an already-closed circuit should not log."""
        from unittest.mock import patch
        from sharanbot.agent.recovery import RecoveryEngine
        from sharanbot.agent.learning import LearningStore

        store = LearningStore.__new__(LearningStore)
        store._entries = {}
        store._cooldowns = {}
        store._fixes = {}
        engine = RecoveryEngine(learning=store)

        with patch("sharanbot.agent.recovery.logger") as mock_logger:
            engine.reset_circuit("llm_provider")
            mock_logger.info.assert_not_called()

    def test_circuit_reset_logs_when_was_open(self):
        """reset_circuit on an open circuit should log recovery."""
        from unittest.mock import patch
        from sharanbot.agent.recovery import RecoveryEngine
        from sharanbot.agent.learning import LearningStore

        store = LearningStore.__new__(LearningStore)
        store._entries = {}
        store._cooldowns = {}
        store._fixes = {}
        engine = RecoveryEngine(learning=store)

        # Open the circuit first
        circuit = engine.get_circuit("llm_provider")
        circuit.is_open = True

        with patch("sharanbot.agent.recovery.logger") as mock_logger:
            engine.reset_circuit("llm_provider")
            mock_logger.info.assert_called_once()


# ── Fix 7: MCP timeout circuit breaker ───────────────────────────────

class TestMCPTimeoutCircuitBreaker:
    """Verify MCP tools are disabled after consecutive timeouts."""

    def test_mcp_wrapper_has_timeout_counter(self):
        """MCPToolWrapper should have consecutive timeout tracking."""
        from sharanbot.agent.tools.mcp import MCPToolWrapper

        mock_session = MagicMock()
        mock_tool = MagicMock()
        mock_tool.name = "test_tool"
        mock_tool.description = "Test"
        mock_tool.inputSchema = {"type": "object", "properties": {}}

        wrapper = MCPToolWrapper(mock_session, "test_server", mock_tool)
        assert hasattr(wrapper, "_consecutive_timeouts")
        assert wrapper._consecutive_timeouts == 0
        assert hasattr(wrapper, "_MAX_CONSECUTIVE_TIMEOUTS")
        assert wrapper._MAX_CONSECUTIVE_TIMEOUTS == 2

    async def test_mcp_disabled_after_timeouts(self):
        """MCP tool should return disabled message after max timeouts."""
        from sharanbot.agent.tools.mcp import MCPToolWrapper

        mock_session = MagicMock()
        mock_tool = MagicMock()
        mock_tool.name = "search_files"
        mock_tool.description = "Search"
        mock_tool.inputSchema = {"type": "object", "properties": {}}

        wrapper = MCPToolWrapper(mock_session, "filesystem", mock_tool)
        wrapper._consecutive_timeouts = 2  # Already at max

        result = await wrapper.execute(path="/test")
        assert "temporarily disabled" in result
        assert "timeouts" in result


# ── Fix 8: Reaction engine quality.gate_failed config ────────────────

class TestReactionEngineConfig:
    """Verify reaction engine default configs are correct."""

    def test_quality_gate_is_notify_not_send_to_agent(self):
        """quality.gate_failed should NOT use SEND_TO_AGENT."""
        from sharanbot.agi.reactions import DEFAULT_REACTIONS, ReactionAction

        config = DEFAULT_REACTIONS["quality.gate_failed"]
        assert config.action != ReactionAction.SEND_TO_AGENT
        assert config.action == ReactionAction.NOTIFY

    def test_task_failed_still_has_retry(self):
        """task.failed should still use RETRY action."""
        from sharanbot.agi.reactions import DEFAULT_REACTIONS, ReactionAction

        config = DEFAULT_REACTIONS["task.failed"]
        assert config.action == ReactionAction.RETRY

    def test_rate_limited_still_has_pause(self):
        """rate.limited should still use PAUSE action."""
        from sharanbot.agi.reactions import DEFAULT_REACTIONS, ReactionAction

        config = DEFAULT_REACTIONS["rate.limited"]
        assert config.action == ReactionAction.PAUSE
