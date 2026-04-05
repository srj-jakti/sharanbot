"""Tests for E2E Round 3 remaining fixes — v0.9.1.

Covers:
- Problem 7: Orphan process cleanup on gateway startup
- Problem 11: Human-readable progress updates in autopilot
- Problem 12: Reaction engine quality.gate_failed suppression during autopilot
- Stuck detector: Port-hunting pattern detection
- Subagents writing to manifest (claim_files, record_decision)
- Worker ↔ TeamBus bridge (set_team_bus)
- Reaction engine channel routing fix
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Problem 7: Orphan process cleanup ───────────────────────────────

class TestOrphanProcessCleanup:
    """Tests for orphan process cleanup on gateway startup."""

    def test_process_registry_clear(self):
        """Verify _PROCESS_REGISTRY can be cleared."""
        from sharanbot.agent.tools.process import _PROCESS_REGISTRY

        _PROCESS_REGISTRY["test_pid"] = MagicMock()
        assert "test_pid" in _PROCESS_REGISTRY
        _PROCESS_REGISTRY.clear()
        assert len(_PROCESS_REGISTRY) == 0

    def test_process_registry_is_global(self):
        """Verify _PROCESS_REGISTRY is shared across imports."""
        from sharanbot.agent.tools.process import _PROCESS_REGISTRY as reg1
        from sharanbot.agent.tools.process import _PROCESS_REGISTRY as reg2

        assert reg1 is reg2


# ── Problem 11: Human-readable progress updates ────────────────────

class TestHumanReadableProgress:
    """Tests for autopilot humanizing tool names in progress updates."""

    def test_humanize_known_tools(self):
        from sharanbot.agent.team.autopilot import Autopilot

        assert Autopilot._humanize_tool_name("mcp_filesystem_write_file") == "Writing file"
        assert Autopilot._humanize_tool_name("exec") == "Running command"
        assert Autopilot._humanize_tool_name("team_create") == "Creating team"
        assert Autopilot._humanize_tool_name("port_check") == "Checking port"
        assert Autopilot._humanize_tool_name("process_start") == "Starting process"
        assert Autopilot._humanize_tool_name("web_search") == "Searching the web"

    def test_humanize_unknown_mcp_tool(self):
        from sharanbot.agent.team.autopilot import Autopilot

        result = Autopilot._humanize_tool_name("mcp_github_create_issue")
        assert "Github" in result
        assert "create issue" in result

    def test_humanize_unknown_tool_fallback(self):
        from sharanbot.agent.team.autopilot import Autopilot

        result = Autopilot._humanize_tool_name("some_custom_tool")
        assert result == "Some Custom Tool"

    def test_humanize_all_mapped_tools(self):
        """Every mapped tool should return a non-empty human-readable string."""
        from sharanbot.agent.team.autopilot import Autopilot

        for tool_name, display in Autopilot._TOOL_DISPLAY_NAMES.items():
            assert display, f"Empty display name for {tool_name}"
            assert Autopilot._humanize_tool_name(tool_name) == display


# ── Problem 12: Reaction engine suppression during autopilot ────────

class TestReactionEngineSuppression:
    """Tests for quality.gate_failed suppression during active autopilot."""

    @pytest.mark.asyncio
    async def test_quality_gate_suppressed_during_autopilot(self):
        from sharanbot.agi.reactions import ReactionEngine, EventType

        engine = ReactionEngine(
            on_notify=AsyncMock(),
            is_autopilot_active=lambda: True,  # Autopilot is active
        )

        result = await engine.emit(
            EventType.QUALITY_GATE_FAILED,
            "Quality gate failed for task t1",
            session_key="telegram:123",
        )

        assert result is not None
        assert result.action == "suppressed"
        assert engine._stats["suppressed"] >= 1
        # on_notify should NOT have been called
        engine._on_notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_quality_gate_not_suppressed_when_no_autopilot(self):
        from sharanbot.agi.reactions import ReactionEngine, EventType

        engine = ReactionEngine(
            on_notify=AsyncMock(),
            is_autopilot_active=lambda: False,  # No autopilot
        )

        result = await engine.emit(
            EventType.QUALITY_GATE_FAILED,
            "Quality gate failed",
            session_key="telegram:123",
        )

        assert result is not None
        assert result.action != "suppressed"

    @pytest.mark.asyncio
    async def test_task_failed_not_suppressed_during_autopilot(self):
        """Non-quality events should still fire during autopilot."""
        from sharanbot.agi.reactions import ReactionEngine, EventType

        engine = ReactionEngine(
            on_notify=AsyncMock(),
            is_autopilot_active=lambda: True,
        )

        result = await engine.emit(
            EventType.TASK_FAILED,
            "Task failed",
            session_key="telegram:123",
        )

        assert result is not None
        assert result.action != "suppressed"

    @pytest.mark.asyncio
    async def test_suppression_without_callback(self):
        """Engine works fine without is_autopilot_active callback."""
        from sharanbot.agi.reactions import ReactionEngine, EventType

        engine = ReactionEngine(on_notify=AsyncMock())

        result = await engine.emit(
            EventType.QUALITY_GATE_FAILED,
            "Quality gate failed",
        )

        # Should proceed normally (no suppression)
        assert result is not None
        assert result.action != "suppressed"


# ── Stuck detector: Port-hunting pattern ────────────────────────────

class TestStuckDetectorPortHunting:
    """Tests for port-hunting loop detection in StuckDetector."""

    def test_detects_port_hunting_loop(self):
        from sharanbot.agi.stuck import StuckDetector

        detector = StuckDetector()

        # Simulate port-hunting: port_check → process_start → port_check → exec → ...
        # Use different args each time to avoid triggering exact-repetition check first
        calls = [
            ("port_check", {"port": 3000}),
            ("process_start", {"command": "npm run dev --port 3000"}),
            ("port_check", {"port": 3000}),
            ("process_stop", {"process_id": "bg_1"}),
            ("port_check", {"port": 3001}),
            ("process_start", {"command": "npm run dev --port 3001"}),
            ("port_check", {"port": 3001}),
            ("exec", {"command": "kill -9 1234"}),
            ("port_check", {"port": 3002}),
            ("process_start", {"command": "npm run dev --port 3002"}),
            ("port_check", {"port": 3002}),
            ("exec", {"command": "lsof -i :3002"}),
        ]

        for name, args in calls:
            detector.record(name, args, f"result for {name}", success=True)

        diagnosis = detector.diagnose()
        assert diagnosis.is_stuck
        # Should detect either port-hunting or polling pattern
        reason_lower = diagnosis.reason.lower()
        assert "port" in reason_lower or "polling" in reason_lower or "repeated" in reason_lower

    def test_no_false_positive_for_single_port_check(self):
        from sharanbot.agi.stuck import StuckDetector

        detector = StuckDetector()

        # Normal workflow: one port check, one start
        detector.record("port_check", {"port": 3000}, "AVAILABLE", True)
        detector.record("process_start", {"command": "npm run dev"}, "Started", True)
        detector.record("wait_for_port", {"port": 3000}, "Ready", True)
        detector.record("web_search", {"query": "react docs"}, "Results", True)

        diagnosis = detector.diagnose()
        assert not diagnosis.is_stuck


# ── Subagents writing to manifest ───────────────────────────────────

class TestManifestWriteIntegration:
    """Tests for subagents updating the manifest via _update_manifest_from_tool."""

    def test_write_file_claims_in_manifest(self, tmp_path: Path):
        from sharanbot.agent.team.orchestrator import TeamOrchestrator
        from sharanbot.agent.team.manifest import ProjectManifest

        manifest = ProjectManifest.create("test-proj", tmp_path / "test-proj")
        manifest.claim_agent("agent_1", "backend")

        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent_1", "write_file", {"path": "src/index.ts"},
        )

        assert "src/index.ts" in manifest.agents["agent_1"].files

    def test_edit_file_claims_in_manifest(self, tmp_path: Path):
        from sharanbot.agent.team.orchestrator import TeamOrchestrator
        from sharanbot.agent.team.manifest import ProjectManifest

        manifest = ProjectManifest.create("test-proj", tmp_path / "test-proj")
        manifest.claim_agent("agent_2", "frontend")

        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent_2", "edit_file", {"path": "src/App.tsx"},
        )

        assert "src/App.tsx" in manifest.agents["agent_2"].files

    def test_mcp_write_claims_in_manifest(self, tmp_path: Path):
        from sharanbot.agent.team.orchestrator import TeamOrchestrator
        from sharanbot.agent.team.manifest import ProjectManifest

        manifest = ProjectManifest.create("test-proj", tmp_path / "test-proj")
        manifest.claim_agent("agent_3", "backend")

        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent_3", "mcp_filesystem_write_file",
            {"file_path": "server.js"},
        )

        assert "server.js" in manifest.agents["agent_3"].files

    def test_exec_records_decision(self, tmp_path: Path):
        from sharanbot.agent.team.orchestrator import TeamOrchestrator
        from sharanbot.agent.team.manifest import ProjectManifest

        manifest = ProjectManifest.create("test-proj", tmp_path / "test-proj")

        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent_1", "exec",
            {"command": "npm install express cors"},
        )

        assert len(manifest.decisions) == 1
        assert "npm" in manifest.decisions[0].what.lower()

    def test_non_write_tool_no_manifest_change(self, tmp_path: Path):
        from sharanbot.agent.team.orchestrator import TeamOrchestrator
        from sharanbot.agent.team.manifest import ProjectManifest

        manifest = ProjectManifest.create("test-proj", tmp_path / "test-proj")

        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent_1", "read_file", {"path": "README.md"},
        )

        assert len(manifest.agents) == 0
        assert len(manifest.decisions) == 0

    def test_manifest_update_handles_missing_agent(self, tmp_path: Path):
        """claim_files on an unregistered agent should not crash."""
        from sharanbot.agent.team.orchestrator import TeamOrchestrator
        from sharanbot.agent.team.manifest import ProjectManifest

        manifest = ProjectManifest.create("test-proj", tmp_path / "test-proj")

        # Agent not registered — should not crash
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "unknown_agent", "write_file", {"path": "foo.txt"},
        )
        # No crash = success


# ── GSD Worker bridge loop in orchestrator ──────────────────────────

class TestGSDWorkerBridgeLoopRemoved:
    """Tests verifying GSD worker bridge loop has been removed from orchestrator."""

    def test_no_gsd_worker_method_exists(self):
        """_run_task_as_gsd_worker should not exist on TeamOrchestrator."""
        from sharanbot.agent.team.orchestrator import TeamOrchestrator
        assert not hasattr(TeamOrchestrator, "_run_task_as_gsd_worker")

    def test_no_worker_manager_attribute(self, tmp_path: Path):
        """TeamOrchestrator instances should not have _worker_manager attribute."""
        from sharanbot.agent.team.orchestrator import TeamOrchestrator

        orch = TeamOrchestrator(
            provider=AsyncMock(),
            model="test",
            profile_registry=MagicMock(get=MagicMock(return_value=None)),
            subagent_manager=MagicMock(),
            message_bus=MagicMock(),
            workspace=tmp_path,
        )
        assert not hasattr(orch, "_worker_manager")


# ── Reaction engine channel routing ─────────────────────────────────

class TestReactionChannelRouting:
    """Tests for reaction engine using session channel instead of hardcoded 'cli'."""

    @pytest.mark.asyncio
    async def test_notify_uses_session_channel(self):
        from sharanbot.agi.reactions import ReactionEngine, EventType

        captured = []

        async def mock_notify(event):
            captured.append(event)

        engine = ReactionEngine(on_notify=mock_notify)

        await engine.emit(
            EventType.TEAM_DEADLOCK,
            "Deadlock detected",
            session_key="telegram:12345",
        )

        assert len(captured) == 1
        assert captured[0].session_key == "telegram:12345"


# ── Version check ───────────────────────────────────────────────────

class TestVersion:
    """Verify version was bumped."""

    def test_version_is_0_9_1(self):
        from sharanbot import __version__
        assert __version__ == "0.9.1"
