"""Tests for team state persistence and rate limiting."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from sharanbot.agent.team.dag import TaskDAG, TaskNode
from sharanbot.agent.team.orchestrator import TeamOrchestrator, TeamSession
from sharanbot.agent.team.bus import TeamBus
from sharanbot.agent.team.trace import TeamTracer


def _make_orchestrator(tmp_path: Path, max_concurrent: int = 3) -> TeamOrchestrator:
    provider = MagicMock()
    profile_registry = MagicMock()
    profile_registry.get.return_value = None
    profile_registry.list_profiles.return_value = []
    subagent_manager = MagicMock()
    message_bus = MagicMock()
    return TeamOrchestrator(
        provider=provider,
        model="test-model",
        profile_registry=profile_registry,
        subagent_manager=subagent_manager,
        message_bus=message_bus,
        workspace=tmp_path,
        max_concurrent=max_concurrent,
    )


def _make_session(team_id: str = "test-team") -> TeamSession:
    dag = TaskDAG()
    dag.add_task(TaskNode(id="t1", description="Task 1", agent_profile="general"))
    dag.add_task(TaskNode(
        id="t2", description="Task 2", agent_profile="coder",
        depends_on=["t1"],
    ))
    dag.tasks["t1"].status = "completed"
    dag.tasks["t1"].result = "Done with task 1"
    return TeamSession(
        team_id=team_id,
        task="Build something",
        dag=dag,
        bus=TeamBus(),
        tracer=TeamTracer(),
        origin_channel="telegram",
        origin_chat_id="12345",
    )


class TestTeamPersistence:
    def test_save_and_restore_team_state(self, tmp_path: Path):
        orch = _make_orchestrator(tmp_path)
        session = _make_session("persist-test")
        orch.teams["persist-test"] = session

        orch.save_team_state("persist-test")

        state_file = tmp_path / ".team_state" / "persist-test.json"
        assert state_file.exists()

        # Clear in-memory state
        orch.teams.clear()

        # Restore
        restored = orch.restore_team_state("persist-test")
        assert restored is not None
        assert restored.team_id == "persist-test"
        assert restored.task == "Build something"
        assert restored.origin_channel == "telegram"
        assert len(restored.dag.tasks) == 2
        assert restored.dag.tasks["t1"].status == "completed"
        assert restored.dag.tasks["t1"].result == "Done with task 1"
        assert restored.dag.tasks["t2"].status == "pending"

    def test_restore_nonexistent_team_returns_none(self, tmp_path: Path):
        orch = _make_orchestrator(tmp_path)
        assert orch.restore_team_state("nonexistent") is None

    def test_list_saved_teams(self, tmp_path: Path):
        orch = _make_orchestrator(tmp_path)

        # No saved teams initially
        assert orch.list_saved_teams() == []

        # Save two teams
        orch.teams["team-a"] = _make_session("team-a")
        orch.teams["team-b"] = _make_session("team-b")
        orch.save_team_state("team-a")
        orch.save_team_state("team-b")

        saved = orch.list_saved_teams()
        assert sorted(saved) == ["team-a", "team-b"]

    def test_save_uses_atomic_write(self, tmp_path: Path):
        """State file should be written atomically (tmp + rename)."""
        orch = _make_orchestrator(tmp_path)
        session = _make_session("atomic-test")
        orch.teams["atomic-test"] = session

        orch.save_team_state("atomic-test")

        # No .tmp file should remain
        state_dir = tmp_path / ".team_state"
        tmp_files = list(state_dir.glob("*.tmp"))
        assert len(tmp_files) == 0


class TestConcurrencyLimiting:
    def test_semaphore_initialized_with_max_concurrent(self, tmp_path: Path):
        orch = _make_orchestrator(tmp_path, max_concurrent=5)
        assert orch._concurrency_sem._value == 5

    def test_default_max_concurrent_is_3(self, tmp_path: Path):
        orch = _make_orchestrator(tmp_path)
        assert orch.max_concurrent == 3
        assert orch._concurrency_sem._value == 3
