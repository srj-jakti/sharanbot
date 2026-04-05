"""Tests for the team orchestration overhaul.

Property-based tests and unit tests for the new orchestration features.
"""

from __future__ import annotations

import pytest

from sharanbot.agent.team.orchestrator import TeamOrchestrator


class TestTaskCheckout:
    """Unit tests for checkout_task / release_task on TeamOrchestrator."""

    def _make_orchestrator(self):
        """Create a minimal TeamOrchestrator with mocked dependencies."""
        from unittest.mock import MagicMock

        return TeamOrchestrator(
            provider=MagicMock(),
            model="test-model",
            profile_registry=MagicMock(),
            subagent_manager=MagicMock(),
            message_bus=MagicMock(),
            workspace="/tmp/test",
        )

    def test_checkout_succeeds_for_new_task(self):
        orch = self._make_orchestrator()
        assert orch.checkout_task("task-1", "agent-a") is True
        assert orch._task_checkouts["task-1"] == "agent-a"

    def test_checkout_same_agent_idempotent(self):
        orch = self._make_orchestrator()
        assert orch.checkout_task("task-1", "agent-a") is True
        assert orch.checkout_task("task-1", "agent-a") is True
        assert orch._task_checkouts["task-1"] == "agent-a"

    def test_checkout_blocked_by_different_agent(self):
        orch = self._make_orchestrator()
        assert orch.checkout_task("task-1", "agent-a") is True
        assert orch.checkout_task("task-1", "agent-b") is False
        # Original mapping unchanged
        assert orch._task_checkouts["task-1"] == "agent-a"

    def test_release_removes_mapping(self):
        orch = self._make_orchestrator()
        orch.checkout_task("task-1", "agent-a")
        orch.release_task("task-1")
        assert "task-1" not in orch._task_checkouts

    def test_release_nonexistent_is_noop(self):
        orch = self._make_orchestrator()
        # Should not raise
        orch.release_task("nonexistent")

    def test_checkout_after_release(self):
        orch = self._make_orchestrator()
        orch.checkout_task("task-1", "agent-a")
        orch.release_task("task-1")
        # Now a different agent can check it out
        assert orch.checkout_task("task-1", "agent-b") is True
        assert orch._task_checkouts["task-1"] == "agent-b"

    def test_multiple_tasks_independent(self):
        orch = self._make_orchestrator()
        assert orch.checkout_task("task-1", "agent-a") is True
        assert orch.checkout_task("task-2", "agent-b") is True
        assert orch._task_checkouts["task-1"] == "agent-a"
        assert orch._task_checkouts["task-2"] == "agent-b"


class TestHeartbeatCheck:
    """Unit tests for _heartbeat_check on TeamOrchestrator."""

    def _make_orchestrator(self):
        """Create a minimal TeamOrchestrator with mocked dependencies."""
        from unittest.mock import MagicMock

        return TeamOrchestrator(
            provider=MagicMock(),
            model="test-model",
            profile_registry=MagicMock(),
            subagent_manager=MagicMock(),
            message_bus=MagicMock(),
            workspace="/tmp/test",
        )

    def _make_session(self, status="running"):
        """Create a minimal TeamSession with the given status."""
        from unittest.mock import MagicMock
        from sharanbot.agent.team.orchestrator import TeamSession

        return TeamSession(
            team_id="test-team",
            task="test task",
            dag=MagicMock(),
            bus=MagicMock(),
            tracer=MagicMock(),
            status=status,
        )

    def _make_task(self, task_id="task-1", comments=None, last_read_index=0):
        """Create a TaskNode with optional comments."""
        from sharanbot.agent.team.dag import TaskNode

        node = TaskNode(id=task_id, description="test", agent_profile="general")
        if comments:
            node.comments = comments
        node._last_read_index = last_read_index
        return node

    @pytest.mark.asyncio
    async def test_exit_on_cancelled_session(self):
        orch = self._make_orchestrator()
        session = self._make_session(status="cancelled")
        task = self._make_task()
        action, msgs = await orch._heartbeat_check(session, task, "agent-a")
        assert action == "exit"
        assert msgs == []

    @pytest.mark.asyncio
    async def test_exit_on_completed_session(self):
        orch = self._make_orchestrator()
        session = self._make_session(status="completed")
        task = self._make_task()
        action, msgs = await orch._heartbeat_check(session, task, "agent-a")
        assert action == "exit"
        assert msgs == []

    @pytest.mark.asyncio
    async def test_exit_on_task_reassignment(self):
        orch = self._make_orchestrator()
        orch._task_checkouts["task-1"] = "agent-a"
        session = self._make_session(status="running")
        task = self._make_task(task_id="task-1")
        # agent-b calls heartbeat but task-1 is checked out by agent-a
        action, msgs = await orch._heartbeat_check(session, task, "agent-b")
        assert action == "exit"
        assert msgs == []

    @pytest.mark.asyncio
    async def test_continue_when_same_agent_holds_checkout(self):
        orch = self._make_orchestrator()
        orch._task_checkouts["task-1"] = "agent-a"
        session = self._make_session(status="running")
        task = self._make_task(task_id="task-1")
        action, msgs = await orch._heartbeat_check(session, task, "agent-a")
        assert action == "continue"
        assert msgs == []

    @pytest.mark.asyncio
    async def test_continue_when_no_checkout_exists(self):
        orch = self._make_orchestrator()
        session = self._make_session(status="running")
        task = self._make_task(task_id="task-1")
        action, msgs = await orch._heartbeat_check(session, task, "agent-a")
        assert action == "continue"
        assert msgs == []

    @pytest.mark.asyncio
    async def test_collects_unread_comments(self):
        orch = self._make_orchestrator()
        session = self._make_session(status="running")
        comments = [
            {"from": "orchestrator", "content": "focus on backend"},
            {"from": "orchestrator", "content": "use postgres"},
        ]
        task = self._make_task(comments=comments, last_read_index=0)
        action, msgs = await orch._heartbeat_check(session, task, "agent-a")
        assert action == "continue"
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert "focus on backend" in msgs[0]["content"]
        assert "use postgres" in msgs[1]["content"]
        assert task._last_read_index == 2

    @pytest.mark.asyncio
    async def test_advances_read_index(self):
        orch = self._make_orchestrator()
        session = self._make_session(status="running")
        comments = [
            {"from": "orchestrator", "content": "msg1"},
            {"from": "orchestrator", "content": "msg2"},
            {"from": "orchestrator", "content": "msg3"},
        ]
        task = self._make_task(comments=comments, last_read_index=1)
        action, msgs = await orch._heartbeat_check(session, task, "agent-a")
        assert action == "continue"
        assert len(msgs) == 2  # only msg2 and msg3
        assert "msg2" in msgs[0]["content"]
        assert "msg3" in msgs[1]["content"]
        assert task._last_read_index == 3

    @pytest.mark.asyncio
    async def test_no_unread_comments_returns_empty(self):
        orch = self._make_orchestrator()
        session = self._make_session(status="running")
        comments = [{"from": "orchestrator", "content": "old msg"}]
        task = self._make_task(comments=comments, last_read_index=1)
        action, msgs = await orch._heartbeat_check(session, task, "agent-a")
        assert action == "continue"
        assert msgs == []
        assert task._last_read_index == 1

    @pytest.mark.asyncio
    async def test_comment_format_includes_from_field(self):
        orch = self._make_orchestrator()
        session = self._make_session(status="running")
        comments = [{"from": "agent-x", "content": "hello"}]
        task = self._make_task(comments=comments, last_read_index=0)
        action, msgs = await orch._heartbeat_check(session, task, "agent-a")
        assert action == "continue"
        assert "[Team Steering from agent-x]" in msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_comment_missing_from_defaults_to_orchestrator(self):
        orch = self._make_orchestrator()
        session = self._make_session(status="running")
        comments = [{"content": "no from field"}]
        task = self._make_task(comments=comments, last_read_index=0)
        action, msgs = await orch._heartbeat_check(session, task, "agent-a")
        assert "[Team Steering from orchestrator]" in msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_cancellation_checked_before_comments(self):
        """Even if there are unread comments, cancelled session returns exit."""
        orch = self._make_orchestrator()
        session = self._make_session(status="cancelled")
        comments = [{"from": "orchestrator", "content": "should not be read"}]
        task = self._make_task(comments=comments, last_read_index=0)
        action, msgs = await orch._heartbeat_check(session, task, "agent-a")
        assert action == "exit"
        assert msgs == []
        # _last_read_index should NOT have advanced
        assert task._last_read_index == 0

    @pytest.mark.asyncio
    async def test_reassignment_checked_before_comments(self):
        """Even if there are unread comments, reassigned task returns exit."""
        orch = self._make_orchestrator()
        orch._task_checkouts["task-1"] = "agent-a"
        session = self._make_session(status="running")
        comments = [{"from": "orchestrator", "content": "should not be read"}]
        task = self._make_task(task_id="task-1", comments=comments, last_read_index=0)
        action, msgs = await orch._heartbeat_check(session, task, "agent-b")
        assert action == "exit"
        assert msgs == []
        # _last_read_index should NOT have advanced
        assert task._last_read_index == 0


class TestHeartbeatIntegration:
    """Unit tests for heartbeat integration in _run_task_as_subagent loop."""

    def _make_orchestrator(self, provider=None, tmp_path=None):
        from unittest.mock import MagicMock

        return TeamOrchestrator(
            provider=provider or MagicMock(),
            model="test-model",
            profile_registry=MagicMock(get=MagicMock(return_value=None)),
            subagent_manager=MagicMock(
                restrict_to_workspace=False,
                exec_config=MagicMock(timeout=30, path_append=None),
                web_search_config=None,
                web_proxy=None,
            ),
            message_bus=MagicMock(),
            workspace=tmp_path or "/tmp/test",
        )

    def _make_session(self, status="running", project_root=None):
        from unittest.mock import MagicMock
        from sharanbot.agent.team.orchestrator import TeamSession
        from sharanbot.agent.team.bus import TeamBus
        from sharanbot.agent.team.dag import TaskDAG
        from sharanbot.agent.team.trace import TeamTracer

        bus = TeamBus()
        bus.register_agent("orchestrator")
        return TeamSession(
            team_id="test-team",
            task="test task",
            dag=TaskDAG(),
            bus=bus,
            tracer=TeamTracer(),
            status=status,
            project_root=project_root,
        )

    @pytest.mark.asyncio
    async def test_subagent_exits_on_cancelled_session(self, tmp_path):
        """When session is cancelled, _run_task_as_subagent returns cancellation message."""
        from unittest.mock import AsyncMock
        from sharanbot.agent.team.dag import TaskNode

        provider = AsyncMock()
        orch = self._make_orchestrator(provider=provider, tmp_path=tmp_path)
        session = self._make_session(status="cancelled", project_root=tmp_path)
        task_node = TaskNode(id="task-1", description="test", agent_profile="general")

        result = await orch._run_task_as_subagent(
            task_description="Do something",
            profile="general",
            agent_id="agent-a",
            session=session,
            task=task_node,
        )

        assert "cancelled" in result.lower()
        # Provider should NOT have been called since heartbeat exits immediately
        provider.chat_with_retry.assert_not_called()

    @pytest.mark.asyncio
    async def test_subagent_injects_comments_before_llm_call(self, tmp_path):
        """When task has unread comments, they are injected into LLM messages."""
        from unittest.mock import AsyncMock, MagicMock
        from sharanbot.agent.team.dag import TaskNode

        provider = AsyncMock()
        response = MagicMock()
        response.has_tool_calls = False
        response.content = "Done"
        provider.chat_with_retry = AsyncMock(return_value=response)

        orch = self._make_orchestrator(provider=provider, tmp_path=tmp_path)
        session = self._make_session(status="running", project_root=tmp_path)

        task_node = TaskNode(id="task-1", description="test", agent_profile="general")
        task_node.comments = [
            {"from": "orchestrator", "content": "Use React for frontend"},
        ]
        task_node._last_read_index = 0

        result = await orch._run_task_as_subagent(
            task_description="Build frontend",
            profile="general",
            agent_id="agent-a",
            session=session,
            task=task_node,
        )

        assert result == "Done"
        call_args = provider.chat_with_retry.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages", [])
        steering_msgs = [m for m in messages if "Team Steering" in m.get("content", "")]
        assert len(steering_msgs) == 1
        assert "Use React for frontend" in steering_msgs[0]["content"]
        assert task_node._last_read_index == 1

    @pytest.mark.asyncio
    async def test_subagent_skips_heartbeat_when_no_task(self, tmp_path):
        """When task is None, heartbeat is skipped and subagent runs normally."""
        from unittest.mock import AsyncMock, MagicMock

        provider = AsyncMock()
        response = MagicMock()
        response.has_tool_calls = False
        response.content = "Done without heartbeat"
        provider.chat_with_retry = AsyncMock(return_value=response)

        orch = self._make_orchestrator(provider=provider, tmp_path=tmp_path)
        session = self._make_session(status="running", project_root=tmp_path)

        result = await orch._run_task_as_subagent(
            task_description="Build something",
            profile="general",
            agent_id="agent-a",
            session=session,
            task=None,
        )

        assert result == "Done without heartbeat"
        provider.chat_with_retry.assert_called_once()


class TestSteerTeam:
    """Unit tests for steer_team using TaskNode comments."""

    def _make_orchestrator(self):
        """Create a minimal TeamOrchestrator with mocked dependencies."""
        from unittest.mock import MagicMock

        return TeamOrchestrator(
            provider=MagicMock(),
            model="test-model",
            profile_registry=MagicMock(),
            subagent_manager=MagicMock(),
            message_bus=MagicMock(),
            workspace="/tmp/test",
        )

    def _make_session(self, team_id="test-team", status="running", tasks=None):
        """Create a TeamSession with the given tasks."""
        from unittest.mock import MagicMock
        from sharanbot.agent.team.orchestrator import TeamSession
        from sharanbot.agent.team.dag import TaskDAG, TaskNode

        dag = TaskDAG()
        if tasks:
            for t in tasks:
                dag.tasks[t.id] = t

        session = TeamSession(
            team_id=team_id,
            task="test task",
            dag=dag,
            bus=MagicMock(),
            tracer=MagicMock(),
            status=status,
        )
        return session

    def _make_task(self, task_id, status="pending"):
        from sharanbot.agent.team.dag import TaskNode

        node = TaskNode(id=task_id, description=f"task {task_id}", agent_profile="general")
        node.status = status
        return node

    @pytest.mark.asyncio
    async def test_steer_not_found(self):
        orch = self._make_orchestrator()
        result = await orch.steer_team("nonexistent", "do something")
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_steer_not_running(self):
        orch = self._make_orchestrator()
        session = self._make_session(status="completed")
        orch.teams["t1"] = session
        result = await orch.steer_team("t1", "do something")
        assert "cannot steer" in result.lower()

    @pytest.mark.asyncio
    async def test_steer_appends_to_running_tasks(self):
        orch = self._make_orchestrator()
        t1 = self._make_task("t1", status="running")
        t2 = self._make_task("t2", status="pending")
        session = self._make_session(tasks=[t1, t2])
        orch.teams["team-1"] = session

        result = await orch.steer_team("team-1", "focus on backend")

        assert "2 active tasks" in result
        assert len(t1.comments) == 1
        assert len(t2.comments) == 1
        assert t1.comments[0]["from"] == "orchestrator"
        assert t1.comments[0]["content"] == "focus on backend"
        assert "timestamp" in t1.comments[0]

    @pytest.mark.asyncio
    async def test_steer_skips_completed_and_failed_tasks(self):
        orch = self._make_orchestrator()
        t_running = self._make_task("t1", status="running")
        t_completed = self._make_task("t2", status="completed")
        t_failed = self._make_task("t3", status="failed")
        t_pending = self._make_task("t4", status="pending")
        t_skipped = self._make_task("t5", status="skipped")
        session = self._make_session(tasks=[t_running, t_completed, t_failed, t_pending, t_skipped])
        orch.teams["team-1"] = session

        result = await orch.steer_team("team-1", "change direction")

        assert "2 active tasks" in result
        assert len(t_running.comments) == 1
        assert len(t_pending.comments) == 1
        assert len(t_completed.comments) == 0
        assert len(t_failed.comments) == 0
        assert len(t_skipped.comments) == 0

    @pytest.mark.asyncio
    async def test_steer_returns_zero_when_no_active_tasks(self):
        orch = self._make_orchestrator()
        t1 = self._make_task("t1", status="completed")
        t2 = self._make_task("t2", status="failed")
        session = self._make_session(tasks=[t1, t2])
        orch.teams["team-1"] = session

        result = await orch.steer_team("team-1", "hello")

        assert "0 active tasks" in result
        assert len(t1.comments) == 0
        assert len(t2.comments) == 0

    @pytest.mark.asyncio
    async def test_steer_comment_format(self):
        import time

        orch = self._make_orchestrator()
        t1 = self._make_task("t1", status="running")
        session = self._make_session(tasks=[t1])
        orch.teams["team-1"] = session

        before = time.time()
        await orch.steer_team("team-1", "use postgres")
        after = time.time()

        comment = t1.comments[0]
        assert comment["from"] == "orchestrator"
        assert comment["content"] == "use postgres"
        assert before <= comment["timestamp"] <= after

    @pytest.mark.asyncio
    async def test_steer_multiple_instructions_accumulate(self):
        orch = self._make_orchestrator()
        t1 = self._make_task("t1", status="running")
        session = self._make_session(tasks=[t1])
        orch.teams["team-1"] = session

        await orch.steer_team("team-1", "first instruction")
        await orch.steer_team("team-1", "second instruction")

        assert len(t1.comments) == 2
        assert t1.comments[0]["content"] == "first instruction"
        assert t1.comments[1]["content"] == "second instruction"

    @pytest.mark.asyncio
    async def test_steer_empty_dag(self):
        orch = self._make_orchestrator()
        session = self._make_session(tasks=[])
        orch.teams["team-1"] = session

        result = await orch.steer_team("team-1", "hello")

        assert "0 active tasks" in result


class TestCancelTeam:
    """Unit tests for cancel_team with asyncio task tracking."""

    def _make_orchestrator(self):
        """Create a minimal TeamOrchestrator with mocked dependencies."""
        from unittest.mock import MagicMock

        return TeamOrchestrator(
            provider=MagicMock(),
            model="test-model",
            profile_registry=MagicMock(),
            subagent_manager=MagicMock(),
            message_bus=MagicMock(),
            workspace="/tmp/test",
        )

    def _make_session(self, team_id="test-team", status="running", tasks=None, task_handles=None):
        """Create a TeamSession with the given tasks and task handles."""
        from unittest.mock import MagicMock
        from sharanbot.agent.team.orchestrator import TeamSession
        from sharanbot.agent.team.dag import TaskDAG, TaskNode
        from sharanbot.agent.team.bus import TeamBus

        dag = TaskDAG()
        if tasks:
            for t in tasks:
                dag.tasks[t.id] = t

        session = TeamSession(
            team_id=team_id,
            task="test task",
            dag=dag,
            bus=TeamBus(),
            tracer=MagicMock(),
            status=status,
        )
        if task_handles:
            session._task_handles = task_handles
        return session

    def _make_task(self, task_id, status="pending"):
        from sharanbot.agent.team.dag import TaskNode

        node = TaskNode(id=task_id, description=f"task {task_id}", agent_profile="general")
        node.status = status
        return node

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_team(self):
        orch = self._make_orchestrator()
        result = await orch.cancel_team("nonexistent")
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_cancel_sets_status_to_cancelled(self):
        orch = self._make_orchestrator()
        session = self._make_session()
        orch.teams["t1"] = session
        assert session.status == "running"

        await orch.cancel_team("t1")

        assert session.status == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_clears_bus(self):
        orch = self._make_orchestrator()
        session = self._make_session()
        session.bus.register_agent("agent-a")
        orch.teams["t1"] = session

        await orch.cancel_team("t1")

        # After clear(), mailboxes and history should be empty
        assert session.bus._mailboxes == {}
        assert session.bus._history == []

    @pytest.mark.asyncio
    async def test_cancel_cancels_asyncio_task_handles(self):
        from unittest.mock import MagicMock

        orch = self._make_orchestrator()
        handle1 = MagicMock()
        handle1.done.return_value = False
        handle2 = MagicMock()
        handle2.done.return_value = True  # already done, should not be cancelled
        handle3 = MagicMock()
        handle3.done.return_value = False

        session = self._make_session(task_handles=[handle1, handle2, handle3])
        orch.teams["t1"] = session

        await orch.cancel_team("t1")

        handle1.cancel.assert_called_once()
        handle2.cancel.assert_not_called()
        handle3.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_releases_all_checkouts(self):
        orch = self._make_orchestrator()
        t1 = self._make_task("task-1", status="running")
        t2 = self._make_task("task-2", status="pending")
        session = self._make_session(tasks=[t1, t2])
        orch.teams["team-1"] = session

        # Simulate checkouts
        orch._task_checkouts["task-1"] = "agent-a"
        orch._task_checkouts["task-2"] = "agent-b"
        orch._task_checkouts["task-other"] = "agent-c"  # belongs to another team

        await orch.cancel_team("team-1")

        assert "task-1" not in orch._task_checkouts
        assert "task-2" not in orch._task_checkouts
        # task-other should remain since it's not part of this team
        assert orch._task_checkouts["task-other"] == "agent-c"

    @pytest.mark.asyncio
    async def test_cancel_returns_confirmation_message(self):
        orch = self._make_orchestrator()
        session = self._make_session(team_id="abc123")
        orch.teams["abc123"] = session

        result = await orch.cancel_team("abc123")

        assert "abc123" in result
        assert "cancelled" in result.lower()

    @pytest.mark.asyncio
    async def test_cancel_with_empty_task_handles(self):
        orch = self._make_orchestrator()
        session = self._make_session()
        orch.teams["t1"] = session

        # Should not raise even with no task handles
        result = await orch.cancel_team("t1")
        assert session.status == "cancelled"
        assert "cancelled" in result.lower()

    @pytest.mark.asyncio
    async def test_cancel_with_no_checkouts(self):
        orch = self._make_orchestrator()
        t1 = self._make_task("task-1")
        session = self._make_session(tasks=[t1])
        orch.teams["t1"] = session

        # No checkouts exist — should not raise
        result = await orch.cancel_team("t1")
        assert session.status == "cancelled"

    @pytest.mark.asyncio
    async def test_task_handles_field_exists_on_session(self):
        """Verify _task_handles is a proper field on TeamSession, not just getattr."""
        from sharanbot.agent.team.orchestrator import TeamSession
        from unittest.mock import MagicMock
        from sharanbot.agent.team.dag import TaskDAG

        session = TeamSession(
            team_id="t",
            task="t",
            dag=TaskDAG(),
            bus=MagicMock(),
            tracer=MagicMock(),
        )
        assert hasattr(session, "_task_handles")
        assert isinstance(session._task_handles, list)
        assert session._task_handles == []


class TestUpdateManifestFromTool:
    """Unit tests for the enhanced _update_manifest_from_tool method."""

    def _make_manifest(self, tmp_path):
        from sharanbot.agent.team.manifest import ProjectManifest

        proj_dir = tmp_path / "test-proj"
        proj_dir.mkdir(parents=True, exist_ok=True)
        manifest = ProjectManifest.create("test-proj", proj_dir)
        manifest.claim_agent("agent-1", "backend")
        return manifest

    # -- File write tracking --

    def test_write_file_claims_path(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "write_file", {"path": "src/main.py"},
        )
        assert "src/main.py" in manifest.agents["agent-1"].files

    def test_edit_file_claims_path(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "edit_file", {"path": "src/utils.py"},
        )
        assert "src/utils.py" in manifest.agents["agent-1"].files

    def test_mcp_write_file_claims_file_path_arg(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "mcp_filesystem_write_file", {"file_path": "index.js"},
        )
        assert "index.js" in manifest.agents["agent-1"].files

    def test_mcp_edit_file_claims_file_path_arg(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "mcp_filesystem_edit_file", {"file_path": "app.ts"},
        )
        assert "app.ts" in manifest.agents["agent-1"].files

    def test_empty_path_not_claimed(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "write_file", {"path": ""},
        )
        assert manifest.agents["agent-1"].files == []

    # -- Exec architecture decision patterns --

    def test_exec_npm_init(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "exec", {"command": "npm init -y"},
        )
        assert len(manifest.decisions) == 1
        assert "Initialized npm project" in manifest.decisions[0].what

    def test_exec_npx_create(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "exec", {"command": "npx create-react-app my-app"},
        )
        assert len(manifest.decisions) == 1
        assert "Scaffolded project" in manifest.decisions[0].what

    def test_exec_pip_install(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "exec", {"command": "pip install flask sqlalchemy"},
        )
        assert len(manifest.decisions) == 1
        assert "Python dependencies" in manifest.decisions[0].what

    def test_exec_cargo_init(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "exec", {"command": "cargo init my-project"},
        )
        assert len(manifest.decisions) == 1
        assert "Rust" in manifest.decisions[0].what

    def test_exec_go_mod_init(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "exec", {"command": "go mod init github.com/user/proj"},
        )
        assert len(manifest.decisions) == 1
        assert "Go module" in manifest.decisions[0].what

    def test_exec_dotnet_new(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "exec", {"command": "dotnet new webapi -n MyApi"},
        )
        assert len(manifest.decisions) == 1
        assert ".NET" in manifest.decisions[0].what

    def test_exec_django_admin_startproject(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "exec", {"command": "django-admin startproject mysite"},
        )
        assert len(manifest.decisions) == 1
        assert "Django" in manifest.decisions[0].what

    def test_exec_rails_new(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "exec", {"command": "rails new blog --api"},
        )
        assert len(manifest.decisions) == 1
        assert "Rails" in manifest.decisions[0].what

    def test_exec_vite_create(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "exec", {"command": "vite create my-app --template react"},
        )
        assert len(manifest.decisions) == 1
        assert "Vite" in manifest.decisions[0].what

    def test_exec_npm_install(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "exec", {"command": "npm install express"},
        )
        assert len(manifest.decisions) == 1
        assert "npm dependencies" in manifest.decisions[0].what

    def test_exec_unrecognized_command_no_decision(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "exec", {"command": "ls -la"},
        )
        assert len(manifest.decisions) == 0

    def test_exec_command_truncated_at_100_chars(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        long_cmd = "npm install " + "a" * 200
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "exec", {"command": long_cmd},
        )
        assert len(manifest.decisions) == 1
        # The decision should contain the truncated command
        assert len(manifest.decisions[0].what) <= 200  # desc + truncated cmd

    # -- Config file write tracking --

    def test_write_vite_config_records_decision(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "write_file", {"path": "vite.config.js"},
        )
        decisions = [d.what for d in manifest.decisions]
        assert any("Configured Vite" in d for d in decisions)

    def test_write_tsconfig_records_decision(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "write_file", {"path": "tsconfig.json"},
        )
        decisions = [d.what for d in manifest.decisions]
        assert any("Configured TypeScript" in d for d in decisions)

    def test_write_dockerfile_records_decision(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "write_file", {"path": "Dockerfile"},
        )
        decisions = [d.what for d in manifest.decisions]
        assert any("Docker configuration" in d for d in decisions)

    def test_write_docker_compose_records_decision(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "write_file", {"path": "docker-compose.yml"},
        )
        decisions = [d.what for d in manifest.decisions]
        assert any("Docker Compose" in d for d in decisions)

    def test_write_eslintrc_records_decision(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "write_file", {"path": ".eslintrc.json"},
        )
        decisions = [d.what for d in manifest.decisions]
        assert any("Configured ESLint" in d for d in decisions)

    def test_write_webpack_config_records_decision(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "write_file", {"path": "webpack.config.js"},
        )
        decisions = [d.what for d in manifest.decisions]
        assert any("Configured Webpack" in d for d in decisions)

    def test_write_next_config_records_decision(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "write_file", {"path": "next.config.js"},
        )
        decisions = [d.what for d in manifest.decisions]
        assert any("Configured Next.js" in d for d in decisions)

    def test_write_tailwind_config_records_decision(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "write_file", {"path": "tailwind.config.js"},
        )
        decisions = [d.what for d in manifest.decisions]
        assert any("Configured Tailwind CSS" in d for d in decisions)

    def test_config_write_also_claims_file(self, tmp_path):
        """Config file writes should both claim the file AND record a decision."""
        manifest = self._make_manifest(tmp_path)
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "write_file", {"path": "vite.config.ts"},
        )
        assert "vite.config.ts" in manifest.agents["agent-1"].files
        decisions = [d.what for d in manifest.decisions]
        assert any("Configured Vite" in d for d in decisions)

    def test_non_config_write_no_decision(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "write_file", {"path": "src/app.js"},
        )
        assert "src/app.js" in manifest.agents["agent-1"].files
        assert len(manifest.decisions) == 0

    def test_mcp_write_config_records_decision(self, tmp_path):
        """MCP write tools should also trigger config detection."""
        manifest = self._make_manifest(tmp_path)
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "mcp_filesystem_write_file", {"file_path": "tsconfig.json"},
        )
        decisions = [d.what for d in manifest.decisions]
        assert any("Configured TypeScript" in d for d in decisions)

    # -- Error handling --

    def test_exception_does_not_propagate(self, tmp_path):
        """If manifest methods raise, the exception is caught and logged."""
        from unittest.mock import MagicMock

        manifest = MagicMock()
        manifest.claim_files.side_effect = RuntimeError("boom")
        # Should not raise
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "write_file", {"path": "foo.txt"},
        )

    def test_non_write_non_exec_tool_no_effect(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        TeamOrchestrator._update_manifest_from_tool(
            manifest, "agent-1", "read_file", {"path": "README.md"},
        )
        assert len(manifest.agents["agent-1"].files) == 0
        assert len(manifest.decisions) == 0


class TestParseCompletionReport:
    """Unit tests for _parse_completion_report and COMPLETION_FORMAT integration."""

    def _make_manifest(self, tmp_path):
        from sharanbot.agent.team.manifest import ProjectManifest

        proj_dir = tmp_path / "test-proj"
        proj_dir.mkdir(parents=True, exist_ok=True)
        manifest = ProjectManifest.create("test-proj", proj_dir)
        manifest.claim_agent("agent-1", "backend")
        return manifest

    def test_completion_format_constant_exists(self):
        from sharanbot.agent.team.orchestrator import COMPLETION_FORMAT

        assert "## Task Completion" in COMPLETION_FORMAT
        assert "Files created/modified" in COMPLETION_FORMAT
        assert "Architecture decisions" in COMPLETION_FORMAT

    def test_parses_files_from_completion_report(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        result = (
            "I built the backend.\n\n"
            "## Task Completion\n"
            "- **Files created/modified**: `src/main.py`, `src/utils.py`, `requirements.txt`\n"
            "- **Architecture decisions**: Used Flask for the web framework\n"
            "- **Summary**: Built the REST API\n"
        )
        TeamOrchestrator._parse_completion_report(result, manifest, "agent-1")
        files = manifest.agents["agent-1"].files
        assert "src/main.py" in files
        assert "src/utils.py" in files
        assert "requirements.txt" in files

    def test_parses_decisions_from_completion_report(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        result = (
            "## Task Completion\n"
            "- **Files created/modified**: `app.ts`\n"
            "- **Architecture decisions**: Chose Next.js with TypeScript for SSR\n"
            "- **Summary**: Set up the frontend\n"
        )
        TeamOrchestrator._parse_completion_report(result, manifest, "agent-1")
        decisions = [d.what for d in manifest.decisions]
        assert any("Next.js" in d for d in decisions)

    def test_no_task_completion_section_is_noop(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        result = "I finished the task. Everything works great."
        TeamOrchestrator._parse_completion_report(result, manifest, "agent-1")
        assert len(manifest.agents["agent-1"].files) == 0
        assert len(manifest.decisions) == 0

    def test_truncates_long_decisions(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        long_decision = "x" * 300
        result = (
            "## Task Completion\n"
            f"- **Files created/modified**: `a.py`\n"
            f"- **Architecture decisions**: {long_decision}\n"
            "- **Summary**: done\n"
        )
        TeamOrchestrator._parse_completion_report(result, manifest, "agent-1")
        decisions = [d.what for d in manifest.decisions]
        assert len(decisions) == 1
        assert len(decisions[0]) <= 200

    def test_parses_files_without_backticks(self, tmp_path):
        manifest = self._make_manifest(tmp_path)
        result = (
            "## Task Completion\n"
            "- **Files created/modified**: src/index.js, package.json\n"
            "- **Architecture decisions**: none\n"
            "- **Summary**: done\n"
        )
        TeamOrchestrator._parse_completion_report(result, manifest, "agent-1")
        files = manifest.agents["agent-1"].files
        assert "src/index.js" in files
        assert "package.json" in files

    def test_completion_at_end_of_string(self, tmp_path):
        """Handles case where completion section is at the very end with no trailing newline."""
        manifest = self._make_manifest(tmp_path)
        result = (
            "## Task Completion\n"
            "- **Files created/modified**: `server.py`\n"
            "- **Architecture decisions**: Used FastAPI"
        )
        TeamOrchestrator._parse_completion_report(result, manifest, "agent-1")
        assert "server.py" in manifest.agents["agent-1"].files


class TestBuildConsolidatedSummary:
    """Unit tests for _build_consolidated_summary in TeamOrchestrator."""

    def _make_dag_with_tasks(self, tasks_data):
        """Create a TaskDAG with the given task data.

        tasks_data: list of dicts with keys: id, description, status, result, comments
        """
        from sharanbot.agent.team.dag import TaskDAG, TaskNode

        dag = TaskDAG()
        for td in tasks_data:
            node = TaskNode(
                id=td["id"],
                description=td.get("description", f"task {td['id']}"),
                agent_profile=td.get("agent_profile", "general"),
            )
            node.status = td.get("status", "completed")
            node.result = td.get("result")
            node.comments = td.get("comments", [])
            dag.tasks[node.id] = node
        return dag

    def test_empty_dag_returns_empty_string(self):
        from sharanbot.agent.team.dag import TaskDAG

        dag = TaskDAG()
        result = TeamOrchestrator._build_consolidated_summary(dag)
        assert result == ""

    def test_no_completed_tasks_returns_empty(self):
        dag = self._make_dag_with_tasks([
            {"id": "t1", "status": "failed", "result": None},
            {"id": "t2", "status": "pending", "result": None},
        ])
        result = TeamOrchestrator._build_consolidated_summary(dag)
        assert result == ""

    def test_completed_tasks_without_result_excluded(self):
        dag = self._make_dag_with_tasks([
            {"id": "t1", "status": "completed", "result": None},
        ])
        result = TeamOrchestrator._build_consolidated_summary(dag)
        assert result == ""

    def test_single_completed_task_included(self):
        dag = self._make_dag_with_tasks([
            {"id": "t1", "description": "Build API", "status": "completed", "result": "Built REST API with Flask"},
        ])
        result = TeamOrchestrator._build_consolidated_summary(dag)
        assert "Consolidated Task Summary:" in result
        assert "[t1]" in result
        assert "Build API" in result
        assert "Built REST API with Flask" in result

    def test_multiple_completed_tasks_all_referenced(self):
        dag = self._make_dag_with_tasks([
            {"id": "t1", "description": "Backend", "status": "completed", "result": "Built backend"},
            {"id": "t2", "description": "Frontend", "status": "completed", "result": "Built frontend"},
            {"id": "t3", "description": "Tests", "status": "completed", "result": "Wrote tests"},
        ])
        result = TeamOrchestrator._build_consolidated_summary(dag)
        assert "[t1]" in result
        assert "[t2]" in result
        assert "[t3]" in result
        assert "Built backend" in result
        assert "Built frontend" in result
        assert "Wrote tests" in result

    def test_mixed_statuses_only_completed_included(self):
        dag = self._make_dag_with_tasks([
            {"id": "t1", "description": "Backend", "status": "completed", "result": "Done backend"},
            {"id": "t2", "description": "Frontend", "status": "failed", "result": None},
            {"id": "t3", "description": "Deploy", "status": "skipped", "result": None},
        ])
        result = TeamOrchestrator._build_consolidated_summary(dag)
        assert "[t1]" in result
        assert "[t2]" not in result
        assert "[t3]" not in result

    def test_completion_comments_files_collected(self):
        dag = self._make_dag_with_tasks([
            {
                "id": "t1",
                "description": "Backend",
                "status": "completed",
                "result": "Done",
                "comments": [
                    {
                        "type": "completion",
                        "from": "agent-1",
                        "files": ["src/main.py", "src/utils.py"],
                        "decisions": [],
                        "summary": "Built the API",
                    },
                ],
            },
        ])
        result = TeamOrchestrator._build_consolidated_summary(dag)
        assert "Files produced:" in result
        assert "src/main.py" in result
        assert "src/utils.py" in result

    def test_completion_comments_decisions_collected(self):
        dag = self._make_dag_with_tasks([
            {
                "id": "t1",
                "description": "Backend",
                "status": "completed",
                "result": "Done",
                "comments": [
                    {
                        "type": "completion",
                        "from": "agent-1",
                        "files": [],
                        "decisions": ["Used Flask", "PostgreSQL for DB"],
                        "summary": "",
                    },
                ],
            },
        ])
        result = TeamOrchestrator._build_consolidated_summary(dag)
        assert "Architecture decisions:" in result
        assert "Used Flask" in result
        assert "PostgreSQL for DB" in result

    def test_completion_comment_summary_appended_to_entry(self):
        dag = self._make_dag_with_tasks([
            {
                "id": "t1",
                "description": "Backend",
                "status": "completed",
                "result": "Done",
                "comments": [
                    {
                        "type": "completion",
                        "from": "agent-1",
                        "files": [],
                        "decisions": [],
                        "summary": "Built REST endpoints",
                    },
                ],
            },
        ])
        result = TeamOrchestrator._build_consolidated_summary(dag)
        assert "Built REST endpoints" in result

    def test_non_completion_comments_ignored(self):
        dag = self._make_dag_with_tasks([
            {
                "id": "t1",
                "description": "Backend",
                "status": "completed",
                "result": "Done",
                "comments": [
                    {"type": "steer", "from": "orchestrator", "content": "focus on backend"},
                    {"from": "orchestrator", "content": "no type field"},
                ],
            },
        ])
        result = TeamOrchestrator._build_consolidated_summary(dag)
        assert "Files produced:" not in result
        assert "Architecture decisions:" not in result

    def test_result_truncated_at_200_chars(self):
        long_result = "x" * 500
        dag = self._make_dag_with_tasks([
            {"id": "t1", "description": "Task", "status": "completed", "result": long_result},
        ])
        result = TeamOrchestrator._build_consolidated_summary(dag)
        # The result preview should be truncated
        assert "x" * 200 in result
        assert "x" * 201 not in result

    def test_files_deduplicated(self):
        dag = self._make_dag_with_tasks([
            {
                "id": "t1",
                "status": "completed",
                "result": "Done",
                "comments": [
                    {"type": "completion", "files": ["shared.py"], "decisions": [], "summary": ""},
                ],
            },
            {
                "id": "t2",
                "status": "completed",
                "result": "Done",
                "comments": [
                    {"type": "completion", "files": ["shared.py", "other.py"], "decisions": [], "summary": ""},
                ],
            },
        ])
        result = TeamOrchestrator._build_consolidated_summary(dag)
        # shared.py should appear only once in the files list
        files_section = result.split("Files produced:")[1].split("\n")[0] if "Files produced:" in result else ""
        assert files_section.count("shared.py") == 1
        assert "other.py" in result


class TestWorkspaceResolution:
    """Unit tests for workspace resolution in Autopilot.execute() (Req 14)."""

    def _make_autopilot(self, tmp_path):
        """Create a minimal Autopilot with mocked provider and tools."""
        from unittest.mock import MagicMock

        from sharanbot.agent.team.autopilot import Autopilot

        provider = MagicMock()
        ap = Autopilot(
            provider=provider,
            model="test-model",
            workspace=tmp_path,
            max_iterations=1,
        )
        return ap

    def _make_decision(self, strategy="direct"):
        from sharanbot.agent.team.autopilot import AutopilotDecision

        return AutopilotDecision(
            strategy=strategy,
            complexity="low",
            suggested_profile="general",
            suggested_pipeline="auto",
            reasoning="test",
        )

    @pytest.mark.asyncio
    async def test_mode_a_creates_workspace_folder(self, tmp_path):
        """Mode A should create a workspace folder derived from the task name."""
        from unittest.mock import AsyncMock, MagicMock

        from sharanbot.agent.team.autopilot import Autopilot

        provider = MagicMock()
        # Make chat_with_retry return a response with no tool calls
        mock_response = MagicMock()
        mock_response.has_tool_calls = False
        mock_response.content = "Done"
        mock_response.tool_calls = []
        provider.chat_with_retry = AsyncMock(return_value=mock_response)

        ap = Autopilot(provider=provider, model="test", workspace=tmp_path, max_iterations=1)

        # Set up a minimal tool registry with a mock filesystem tool
        from sharanbot.agent.tools.registry import ToolRegistry

        tools = ToolRegistry()
        mock_tool = MagicMock()
        mock_tool.name = "write_file"
        mock_tool._workspace = tmp_path
        mock_tool.to_schema.return_value = {"type": "function", "function": {"name": "write_file", "parameters": {}}}
        tools.register(mock_tool)

        ap.set_execution_context(
            tools=tools,
            subagent_manager=MagicMock(),
            team_orchestrator=MagicMock(),
            context_window_tokens=65536,
        )

        decision = self._make_decision("direct")
        await ap.execute("Build a todo app", decision)

        # Workspace folder should have been created
        expected_dir = tmp_path / "todo"
        assert expected_dir.exists()
        assert expected_dir.is_dir()

        # Tool workspace should have been updated
        assert mock_tool._workspace == expected_dir

    @pytest.mark.asyncio
    async def test_mode_a_updates_exec_tool_working_dir(self, tmp_path):
        """Mode A should update ExecTool's working_dir to the new workspace."""
        from unittest.mock import AsyncMock, MagicMock

        from sharanbot.agent.team.autopilot import Autopilot

        provider = MagicMock()
        mock_response = MagicMock()
        mock_response.has_tool_calls = False
        mock_response.content = "Done"
        mock_response.tool_calls = []
        provider.chat_with_retry = AsyncMock(return_value=mock_response)

        ap = Autopilot(provider=provider, model="test", workspace=tmp_path, max_iterations=1)

        from sharanbot.agent.tools.registry import ToolRegistry

        tools = ToolRegistry()
        mock_exec = MagicMock()
        mock_exec.name = "exec"
        mock_exec.working_dir = str(tmp_path)
        mock_exec.to_schema.return_value = {"type": "function", "function": {"name": "exec", "parameters": {}}}
        tools.register(mock_exec)

        ap.set_execution_context(
            tools=tools,
            subagent_manager=MagicMock(),
            team_orchestrator=MagicMock(),
            context_window_tokens=65536,
        )

        decision = self._make_decision("single_agent")
        await ap.execute("Create a weather dashboard", decision)

        expected_dir = tmp_path / "weather-dashboard"
        assert expected_dir.exists()
        assert mock_exec.working_dir == str(expected_dir)

    @pytest.mark.asyncio
    async def test_mode_b_does_not_create_workspace(self, tmp_path):
        """Mode B should NOT create a workspace folder — team_create handles it."""
        from unittest.mock import AsyncMock, MagicMock

        from sharanbot.agent.team.autopilot import Autopilot

        provider = MagicMock()
        mock_response = MagicMock()
        mock_response.has_tool_calls = False
        mock_response.content = "Done"
        mock_response.tool_calls = []
        provider.chat_with_retry = AsyncMock(return_value=mock_response)

        ap = Autopilot(provider=provider, model="test", workspace=tmp_path, max_iterations=1)

        from sharanbot.agent.tools.registry import ToolRegistry

        tools = ToolRegistry()
        mock_tool = MagicMock()
        mock_tool.name = "team_create"
        mock_tool._workspace = tmp_path
        mock_tool.to_schema.return_value = {"type": "function", "function": {"name": "team_create", "parameters": {}}}
        tools.register(mock_tool)

        ap.set_execution_context(
            tools=tools,
            subagent_manager=MagicMock(),
            team_orchestrator=MagicMock(),
            context_window_tokens=65536,
        )

        decision = self._make_decision("team")
        await ap.execute("Build a complex e-commerce platform", decision)

        # No project subfolder should have been created
        subdirs = [d for d in tmp_path.iterdir() if d.is_dir()]
        # Only autopilot_memory.json might be created, no project dirs
        project_dirs = [d for d in subdirs if d.name != "__pycache__"]
        assert len(project_dirs) == 0
        # Tool workspace should NOT have been changed
        assert mock_tool._workspace == tmp_path

    @pytest.mark.asyncio
    async def test_mode_a_workspace_resolved_before_first_tool_call(self, tmp_path):
        """Workspace should be resolved before the first tool call in Mode A."""
        from unittest.mock import AsyncMock, MagicMock

        from sharanbot.agent.team.autopilot import Autopilot

        provider = MagicMock()

        # First call returns a tool call, second returns final response
        tool_call_response = MagicMock()
        tool_call_response.has_tool_calls = True
        tool_call_response.content = ""
        tool_call_response.reasoning_content = None
        tool_call_response.thinking_blocks = None
        tc = MagicMock()
        tc.name = "write_file"
        tc.id = "tc1"
        tc.arguments = {"path": "index.html", "content": "<html></html>"}
        tc.to_openai_tool_call.return_value = {
            "id": "tc1", "type": "function",
            "function": {"name": "write_file", "arguments": '{"path":"index.html","content":"<html></html>"}'},
        }
        tool_call_response.tool_calls = [tc]

        final_response = MagicMock()
        final_response.has_tool_calls = False
        final_response.content = "Done"
        final_response.tool_calls = []

        provider.chat_with_retry = AsyncMock(side_effect=[tool_call_response, final_response])

        ap = Autopilot(provider=provider, model="test", workspace=tmp_path, max_iterations=5)

        from sharanbot.agent.tools.registry import ToolRegistry

        tools = ToolRegistry()

        # Track what workspace was set when execute was called
        workspace_at_execute_time = []

        mock_tool = MagicMock()
        mock_tool.name = "write_file"
        mock_tool._workspace = tmp_path
        mock_tool.to_schema.return_value = {"type": "function", "function": {"name": "write_file", "parameters": {}}}
        mock_tool.cast_params.side_effect = lambda p: p
        mock_tool.validate_params.return_value = []

        async def capture_workspace(**kwargs):
            workspace_at_execute_time.append(mock_tool._workspace)
            return "OK"

        mock_tool.execute = capture_workspace
        tools.register(mock_tool)

        ap.set_execution_context(
            tools=tools,
            subagent_manager=MagicMock(),
            team_orchestrator=MagicMock(),
            context_window_tokens=65536,
        )

        decision = self._make_decision("direct")
        await ap.execute("Build a landing page", decision)

        # The workspace should have been updated BEFORE the tool was called
        expected_dir = tmp_path / "landing-page"
        assert expected_dir.exists()
        assert len(workspace_at_execute_time) == 1
        assert workspace_at_execute_time[0] == expected_dir


# ── Round 5 Fixes ─────────────────────────────────────────────────────


class TestWaitToolCap:
    """Verify WaitTool caps duration at 120 seconds."""

    @pytest.mark.asyncio
    async def test_wait_capped_at_120(self):
        from unittest.mock import AsyncMock, patch
        from sharanbot.agent.tools.wait import WaitTool

        tool = WaitTool()
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await tool.execute(seconds=200)
            # Should be capped at 120
            mock_sleep.assert_awaited_once_with(120)
            assert "120.0" in result

    @pytest.mark.asyncio
    async def test_wait_normal_value_unchanged(self):
        from sharanbot.agent.tools.wait import WaitTool

        tool = WaitTool()
        result = await tool.execute(seconds=0.01)
        assert "0.0" in result

    def test_wait_description_says_120(self):
        from sharanbot.agent.tools.wait import WaitTool

        tool = WaitTool()
        assert "120" in tool.description
        assert "120" in tool.parameters["properties"]["seconds"]["description"]


class TestDeriveProjectNameRound5:
    """Extended tests for smarter project name derivation."""

    def test_filler_word_skipping(self):
        """'Build this as a professional...' should not produce 'this-as-a-professional'."""
        name = TeamOrchestrator._derive_project_name(
            "Build this as a professional production-grade crypto trading platform", "t1"
        )
        assert "this-as-a-professional" not in name
        assert "production" not in name

    def test_camelcase_extraction(self):
        """CamelCase proper nouns like TradeArena should be extracted."""
        name = TeamOrchestrator._derive_project_name(
            "ok lets talk about TradeArena use the team to build it", "t1"
        )
        assert name == "trade-arena"

    def test_quoted_name_extraction(self):
        """Quoted project names should be extracted."""
        name = TeamOrchestrator._derive_project_name(
            'Create a project called "SuperApp"', "t1"
        )
        assert name == "superapp"

    def test_simple_build_still_works(self):
        """Simple 'Build a todo app' should still work."""
        name = TeamOrchestrator._derive_project_name("Build a todo app", "t1")
        assert "todo" in name

    def test_create_car_racing_game(self):
        """'Create a car racing game' should still work."""
        name = TeamOrchestrator._derive_project_name("Create a car racing game", "t1")
        assert "car" in name and "racing" in name

    def test_fallback_still_works(self):
        """Generic fallback when no significant words exist."""
        assert TeamOrchestrator._derive_project_name("a", "abc") == "project-abc"


class TestSubagentIterationCap:
    """Verify subagent loop has a hard iteration cap and productivity nudge."""

    def test_hard_cap_constant_in_source(self):
        """_run_task_as_subagent should have HARD_ITERATION_CAP."""
        import inspect
        source = inspect.getsource(TeamOrchestrator._run_task_as_subagent)
        assert "HARD_ITERATION_CAP" in source

    def test_productivity_nudge_in_source(self):
        """_run_task_as_subagent should nudge subagents to write files."""
        import inspect
        source = inspect.getsource(TeamOrchestrator._run_task_as_subagent)
        assert "_files_written" in source
        assert "START writing" in source

    def test_file_write_tracking_in_source(self):
        """_run_task_as_subagent should track write_file and edit_file calls."""
        import inspect
        source = inspect.getsource(TeamOrchestrator._run_task_as_subagent)
        assert 'write_file' in source
        assert '_files_written += 1' in source
