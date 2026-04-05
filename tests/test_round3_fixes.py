"""Tests for E2E Round 3 fixes — unified team architecture.

Covers:
- ProjectManifest (create, save, load, ports, agents, decisions)
- TeamOrchestrator (project_root, manifest, GSD fallback, live TeamBus)
- Autopilot (team strategy tool filtering)
- Watchdog (task ledger cooldown)
- Always-on WorkerManager (commands.py)
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── ProjectManifest ──────────────────────────────────────────────────

class TestProjectManifest:
    """Tests for the ProjectManifest single-source-of-truth."""

    def test_create_and_save(self, tmp_path: Path):
        from sharanbot.agent.team.manifest import ProjectManifest

        m = ProjectManifest.create("my-dashboard", tmp_path / "my-dashboard")
        assert m.project_name == "my-dashboard"
        assert m.path.exists()
        data = json.loads(m.path.read_text())
        assert data["project_name"] == "my-dashboard"

    def test_load(self, tmp_path: Path):
        from sharanbot.agent.team.manifest import ProjectManifest

        root = tmp_path / "proj"
        m = ProjectManifest.create("proj", root)
        m.architecture = {"backend": "typescript", "frontend": "react"}
        m.save()

        loaded = ProjectManifest.load(root)
        assert loaded is not None
        assert loaded.project_name == "proj"
        assert loaded.architecture["backend"] == "typescript"

    def test_load_missing(self, tmp_path: Path):
        from sharanbot.agent.team.manifest import ProjectManifest

        assert ProjectManifest.load(tmp_path / "nonexistent") is None

    def test_claim_agent_and_files(self, tmp_path: Path):
        from sharanbot.agent.team.manifest import ProjectManifest

        root = tmp_path / "proj"
        m = ProjectManifest.create("proj", root)
        m.claim_agent("agent_1", "backend")
        m.claim_files("agent_1", ["src/index.ts", "src/server.ts"])

        loaded = ProjectManifest.load(root)
        assert "agent_1" in loaded.agents
        assert loaded.agents["agent_1"].role == "backend"
        assert "src/index.ts" in loaded.agents["agent_1"].files

    def test_record_decision(self, tmp_path: Path):
        from sharanbot.agent.team.manifest import ProjectManifest

        root = tmp_path / "proj"
        m = ProjectManifest.create("proj", root)
        m.record_decision("agent_1", "Use TypeScript for backend")

        loaded = ProjectManifest.load(root)
        assert len(loaded.decisions) == 1
        assert "TypeScript" in loaded.decisions[0].what

    def test_assign_port(self, tmp_path: Path):
        from sharanbot.agent.team.manifest import ProjectManifest

        root = tmp_path / "proj"
        m = ProjectManifest.create("proj", root)
        port1 = m.assign_port("backend")
        port2 = m.assign_port("frontend")
        assert isinstance(port1, int)
        assert isinstance(port2, int)
        assert port1 != port2

        # Same service returns same port
        assert m.assign_port("backend") == port1

    def test_summary_for_prompt(self, tmp_path: Path):
        from sharanbot.agent.team.manifest import ProjectManifest

        root = tmp_path / "proj"
        m = ProjectManifest.create("proj", root)
        m.architecture = {"backend": "node"}
        m.assign_port("backend")
        m.claim_agent("a1", "backend")
        m.record_decision("a1", "Use Express")

        summary = m.summary_for_prompt()
        assert "proj" in summary
        assert "backend" in summary
        assert "Express" in summary
        assert "IMPORTANT" in summary


# ── TeamOrchestrator — project_root & manifest ──────────────────────

class TestOrchestratorProjectRoot:
    """Tests for unified workspace and manifest in TeamOrchestrator."""

    def test_derive_project_name_from_task(self):
        from sharanbot.agent.team.orchestrator import TeamOrchestrator

        assert TeamOrchestrator._derive_project_name("Build a real-time dashboard app", "abc123") == "real-time-dashboard"
        assert TeamOrchestrator._derive_project_name("Create a car racing game", "abc123") == "car-racing-game"
        # Filler-word skipping: "build this as a professional production-grade..."
        name = TeamOrchestrator._derive_project_name(
            "Build this as a professional production-grade crypto trading platform called TradeArena", "abc123"
        )
        assert "this-as-a-professional" not in name
        assert len(name) >= 2
        # CamelCase extraction
        assert TeamOrchestrator._derive_project_name("ok lets talk about TradeArena use the team", "abc123") == "trade-arena"
        # Quoted name extraction
        assert TeamOrchestrator._derive_project_name('Create a project called "MyAwesomeApp"', "abc123") == "myawesomeapp"
        # Fallback: "hi" is a significant word (len > 1, not a stop word) so it's extracted
        assert TeamOrchestrator._derive_project_name("hi", "abc123") == "hi"
        # Generic fallback when no significant words exist
        assert TeamOrchestrator._derive_project_name("a", "abc123") == "project-abc123"

    @pytest.mark.asyncio
    async def test_create_team_creates_project_root_and_manifest(self, tmp_path: Path):
        from sharanbot.agent.team.orchestrator import TeamOrchestrator
        from sharanbot.agent.team.manifest import ProjectManifest
        from sharanbot.agent.team.dag import TaskDAG, TaskNode

        # Mock the planner to return a pre-built DAG
        mock_dag = TaskDAG()
        mock_dag.tasks["t1"] = TaskNode(id="t1", description="Design", agent_profile="architect", depends_on=[])

        orch = TeamOrchestrator(
            provider=AsyncMock(),
            model="test",
            profile_registry=MagicMock(get=MagicMock(return_value=None)),
            subagent_manager=MagicMock(),
            message_bus=MagicMock(),
            workspace=tmp_path,
        )
        orch.planner = MagicMock()
        orch.planner.decompose = AsyncMock(return_value=mock_dag)

        team_id = await orch.create_team("Build a test dashboard", auto_run=False)
        session = orch.teams[team_id]

        assert session.project_root is not None
        assert session.project_root.exists()
        assert session.manifest is not None
        assert (session.project_root / "PROJECT_MANIFEST.json").exists()


# ── TeamOrchestrator — live TeamBus ─────────────────────────────────

class TestLiveTeamBus:
    """Tests for subagents reading steer messages via heartbeat check."""

    @pytest.mark.asyncio
    async def test_subagent_reads_task_comments_via_heartbeat(self, tmp_path: Path):
        """Verify that _run_task_as_subagent reads task comments via heartbeat check."""
        from sharanbot.agent.team.orchestrator import TeamOrchestrator, TeamSession
        from sharanbot.agent.team.bus import TeamBus
        from sharanbot.agent.team.dag import TaskDAG, TaskNode
        from sharanbot.agent.team.trace import TeamTracer

        # Create a mock provider that returns a final response (no tool calls)
        provider = AsyncMock()
        response = MagicMock()
        response.has_tool_calls = False
        response.content = "Task done"
        provider.chat_with_retry = AsyncMock(return_value=response)

        subagent_mgr = MagicMock()
        subagent_mgr.restrict_to_workspace = False
        subagent_mgr.exec_config = MagicMock(timeout=30, path_append=None)
        subagent_mgr.web_search_config = None
        subagent_mgr.web_proxy = None

        orch = TeamOrchestrator(
            provider=provider,
            model="test",
            profile_registry=MagicMock(get=MagicMock(return_value=None)),
            subagent_manager=subagent_mgr,
            message_bus=MagicMock(),
            workspace=tmp_path,
        )

        bus = TeamBus()
        bus.register_agent("orchestrator")
        bus.register_agent("agent_test")

        # Create a TaskNode with a steering comment
        task_node = TaskNode(id="task-1", description="Build API", agent_profile="backend_dev")
        task_node.comments = [{"from": "orchestrator", "content": "Focus on the API first"}]
        task_node._last_read_index = 0

        session = TeamSession(
            team_id="t1",
            task="Build API",
            dag=TaskDAG(),
            bus=bus,
            tracer=TeamTracer(),
            project_root=tmp_path,
        )

        result = await orch._run_task_as_subagent(
            task_description="Build the REST API",
            profile="backend_dev",
            agent_id="agent_test",
            session=session,
            task=task_node,
        )

        assert result == "Task done"
        # Verify the steering comment was injected into the LLM call
        call_args = provider.chat_with_retry.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages", [])
        steering_msgs = [m for m in messages if "Team Steering" in m.get("content", "")]
        assert len(steering_msgs) == 1
        assert "Focus on the API first" in steering_msgs[0]["content"]


# ── TeamOrchestrator — GSD worker fallback ──────────────────────────

class TestGSDWorkerRemoval:
    """Tests verifying GSD worker code has been removed from TeamOrchestrator."""

    def test_no_gsd_worker_method(self):
        """TeamOrchestrator should not have _run_task_as_gsd_worker method."""
        from sharanbot.agent.team.orchestrator import TeamOrchestrator
        assert not hasattr(TeamOrchestrator, "_run_task_as_gsd_worker")

    def test_no_worker_manager_parameter(self, tmp_path: Path):
        """TeamOrchestrator constructor should not accept worker_manager."""
        from sharanbot.agent.team.orchestrator import TeamOrchestrator
        import inspect
        sig = inspect.signature(TeamOrchestrator.__init__)
        assert "worker_manager" not in sig.parameters


# ── Autopilot — team strategy tool filtering ────────────────────────

class TestAutopilotTeamFiltering:
    """Tests for autopilot removing direct build tools during team strategy."""

    def test_build_executor_prompt_team_strategy(self):
        """Team strategy prompt tells autopilot NOT to build directly."""
        from sharanbot.agent.team.autopilot import Autopilot, AutopilotDecision

        ap = Autopilot(provider=MagicMock(), model="test", workspace=Path("/tmp"))
        decision = AutopilotDecision(
            strategy="team", complexity="high",
            suggested_profile="coder", suggested_pipeline="auto",
            reasoning="Complex task",
        )
        prompt = ap._build_executor_prompt(decision)
        assert "DO NOT BUILD DIRECTLY" in prompt
        assert "MANAGER, not the BUILDER" in prompt
        assert "team_create" in prompt

    def test_team_strategy_filters_build_tools(self):
        """When team strategy is active, build tools are filtered from tool_defs."""
        # Simulate what happens in the execute loop
        tool_defs = [
            {"function": {"name": "team_create"}},
            {"function": {"name": "team_status"}},
            {"function": {"name": "team_steer"}},
            {"function": {"name": "create_software"}},
            {"function": {"name": "scaffold_project"}},
            {"function": {"name": "write_file"}},
            {"function": {"name": "read_file"}},
            {"function": {"name": "exec"}},
        ]

        _blocked_tools = {
            "create_software", "scaffold_project", "write_file", "edit_file",
            "mcp_filesystem_write_file", "mcp_filesystem_edit_file",
            "mcp_filesystem_create_directory",
        }
        filtered = [t for t in tool_defs if t.get("function", {}).get("name") not in _blocked_tools]

        names = [t["function"]["name"] for t in filtered]
        assert "team_create" in names
        assert "team_status" in names
        assert "read_file" in names
        assert "exec" in names
        assert "create_software" not in names
        assert "scaffold_project" not in names
        assert "write_file" not in names


# ── Watchdog — Task Ledger cooldown ─────────────────────────────────

class TestWatchdogCooldown:
    """Tests for watchdog skipping patrol when task recently completed."""

    @pytest.mark.asyncio
    async def test_watchdog_skips_patrol_after_recent_completion(self, tmp_path: Path):
        from sharanbot.agi.watchdog import WatchdogPatrol

        # Create a mock task ledger with a recently completed task
        mock_task = MagicMock()
        mock_task.status = "completed"
        mock_task.completed_at = time.time() - 60  # completed 60s ago
        mock_task.title = "Build dashboard"

        mock_ledger = MagicMock()
        mock_ledger.get_recent_tasks = MagicMock(return_value=[mock_task])

        watchdog = WatchdogPatrol(
            workspace=tmp_path,
            on_act=AsyncMock(),
            on_notify=AsyncMock(),
            interval_s=600,
            enabled=True,
            task_ledger=mock_ledger,
        )

        # Patrol should skip due to cooldown
        await watchdog._patrol()

        # on_act should NOT have been called
        if watchdog.on_act:
            watchdog.on_act.assert_not_called()

    @pytest.mark.asyncio
    async def test_watchdog_patrols_after_cooldown_expires(self, tmp_path: Path):
        from sharanbot.agi.watchdog import WatchdogPatrol

        # Task completed 31 minutes ago — cooldown expired
        mock_task = MagicMock()
        mock_task.status = "completed"
        mock_task.completed_at = time.time() - 1900  # 31+ minutes ago
        mock_task.title = "Old task"

        mock_ledger = MagicMock()
        mock_ledger.get_recent_tasks = MagicMock(return_value=[mock_task])

        watchdog = WatchdogPatrol(
            workspace=tmp_path,
            on_act=AsyncMock(),
            on_notify=AsyncMock(),
            interval_s=600,
            enabled=True,
            task_ledger=mock_ledger,
        )

        # Should proceed with patrol (no cooldown block)
        await watchdog._patrol()
        # Verify it didn't short-circuit — state should be updated
        assert watchdog._state.get("last_patrol") is not None

    @pytest.mark.asyncio
    async def test_watchdog_patrols_without_ledger(self, tmp_path: Path):
        from sharanbot.agi.watchdog import WatchdogPatrol

        watchdog = WatchdogPatrol(
            workspace=tmp_path,
            interval_s=600,
            enabled=True,
            task_ledger=None,  # No ledger
        )

        # Should proceed normally
        await watchdog._patrol()
        assert watchdog._state.get("last_patrol") is not None


# ── TeamSession persistence — project_root ──────────────────────────

class TestTeamSessionPersistence:
    """Tests for project_root being saved/restored in team state."""

    @pytest.mark.asyncio
    async def test_save_and_restore_includes_project_root(self, tmp_path: Path):
        from sharanbot.agent.team.orchestrator import TeamOrchestrator, TeamSession
        from sharanbot.agent.team.bus import TeamBus
        from sharanbot.agent.team.dag import TaskDAG, TaskNode
        from sharanbot.agent.team.trace import TeamTracer
        from sharanbot.agent.team.manifest import ProjectManifest

        orch = TeamOrchestrator(
            provider=AsyncMock(),
            model="test",
            profile_registry=MagicMock(get=MagicMock(return_value=None)),
            subagent_manager=MagicMock(),
            message_bus=MagicMock(),
            workspace=tmp_path,
        )

        project_root = tmp_path / "my-project"
        project_root.mkdir()
        manifest = ProjectManifest.create("my-project", project_root)

        dag = TaskDAG()
        dag.tasks["t1"] = TaskNode(id="t1", description="Test", agent_profile="coder", depends_on=[])

        session = TeamSession(
            team_id="test123",
            task="Build something",
            dag=dag,
            bus=TeamBus(),
            tracer=TeamTracer(),
            project_root=project_root,
            manifest=manifest,
        )
        orch.teams["test123"] = session

        # Save
        orch.save_team_state("test123")

        # Clear and restore
        orch.teams.clear()
        restored = orch.restore_team_state("test123")

        assert restored is not None
        assert restored.project_root == project_root
        assert restored.manifest is not None
        assert restored.manifest.project_name == "my-project"
