"""Tests for team orchestration components."""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime


# ── TeamBus ──────────────────────────────────────────────────────────

class TestTeamBus:
    def test_register_and_unregister(self):
        from sharanbot.agent.team.bus import TeamBus
        bus = TeamBus()
        bus.register_agent("agent_1")
        assert "agent_1" in bus._mailboxes
        bus.unregister_agent("agent_1")
        assert "agent_1" not in bus._mailboxes

    @pytest.mark.asyncio
    async def test_send_and_receive(self):
        from sharanbot.agent.team.bus import TeamBus
        bus = TeamBus()
        bus.register_agent("a")
        bus.register_agent("b")
        await bus.send("a", "b", "result", "hello from a")
        msg = await bus.receive("b", timeout=1.0)
        assert msg is not None
        assert msg.from_agent == "a"
        assert msg.content == "hello from a"

    @pytest.mark.asyncio
    async def test_broadcast(self):
        from sharanbot.agent.team.bus import TeamBus
        bus = TeamBus()
        bus.register_agent("a")
        bus.register_agent("b")
        bus.register_agent("c")
        await bus.broadcast("a", "status", "update")
        # b and c should get it, not a
        msg_b = await bus.receive("b", timeout=1.0)
        msg_c = await bus.receive("c", timeout=1.0)
        assert msg_b is not None
        assert msg_c is not None
        msg_a = await bus.receive("a", timeout=0.1)
        assert msg_a is None

    @pytest.mark.asyncio
    async def test_history(self):
        from sharanbot.agent.team.bus import TeamBus
        bus = TeamBus()
        bus.register_agent("a")
        bus.register_agent("b")
        await bus.send("a", "b", "result", "msg1")
        await bus.send("b", "a", "result", "msg2")
        history = bus.get_history()
        assert len(history) == 2

    def test_get_pending_empty(self):
        from sharanbot.agent.team.bus import TeamBus
        bus = TeamBus()
        bus.register_agent("a")
        assert bus.get_pending("a") == []

    def test_clear(self):
        from sharanbot.agent.team.bus import TeamBus
        bus = TeamBus()
        bus.register_agent("a")
        bus.clear()
        assert len(bus._mailboxes) == 0


# ── TaskDAG ──────────────────────────────────────────────────────────

class TestTaskDAG:
    def test_add_and_get_task(self):
        from sharanbot.agent.team.dag import TaskDAG, TaskNode
        dag = TaskDAG()
        dag.add_task(TaskNode(id="t1", description="test", agent_profile="general"))
        assert dag.get_task("t1") is not None
        assert dag.get_task("t1").description == "test"

    def test_get_ready_tasks(self):
        from sharanbot.agent.team.dag import TaskDAG, TaskNode
        dag = TaskDAG()
        dag.add_task(TaskNode(id="t1", description="first", agent_profile="general"))
        dag.add_task(TaskNode(id="t2", description="second", agent_profile="general", depends_on=["t1"]))
        ready = dag.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "t1"

    def test_mark_completed_unlocks_dependents(self):
        from sharanbot.agent.team.dag import TaskDAG, TaskNode
        dag = TaskDAG()
        dag.add_task(TaskNode(id="t1", description="first", agent_profile="general"))
        dag.add_task(TaskNode(id="t2", description="second", agent_profile="general", depends_on=["t1"]))
        dag.mark_running("t1")
        dag.mark_completed("t1", "done")
        ready = dag.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "t2"

    def test_parallel_tasks(self):
        from sharanbot.agent.team.dag import TaskDAG, TaskNode
        dag = TaskDAG()
        dag.add_task(TaskNode(id="a", description="task a", agent_profile="general"))
        dag.add_task(TaskNode(id="b", description="task b", agent_profile="general"))
        dag.add_task(TaskNode(id="c", description="task c", agent_profile="general", depends_on=["a", "b"]))
        ready = dag.get_ready_tasks()
        assert len(ready) == 2
        assert {t.id for t in ready} == {"a", "b"}

    def test_is_complete(self):
        from sharanbot.agent.team.dag import TaskDAG, TaskNode
        dag = TaskDAG()
        dag.add_task(TaskNode(id="t1", description="first", agent_profile="general"))
        assert not dag.is_complete()
        dag.mark_completed("t1", "done")
        assert dag.is_complete()

    def test_retry_logic(self):
        from sharanbot.agent.team.dag import TaskDAG, TaskNode
        dag = TaskDAG()
        dag.add_task(TaskNode(id="t1", description="test", agent_profile="general", max_retries=2))
        dag.mark_running("t1")
        dag.mark_failed("t1", "error")
        assert dag.tasks["t1"].status == "retrying"
        dag.reset_for_retry("t1")
        assert dag.tasks["t1"].status == "pending"

    def test_from_plan(self):
        from sharanbot.agent.team.dag import TaskDAG
        plan = [
            {"id": "research", "description": "Research", "profile": "researcher", "depends_on": []},
            {"id": "implement", "description": "Implement", "profile": "coder", "depends_on": ["research"]},
        ]
        dag = TaskDAG.from_plan(plan)
        assert len(dag.tasks) == 2
        assert dag.tasks["implement"].depends_on == ["research"]

    def test_topological_order(self):
        from sharanbot.agent.team.dag import TaskDAG, TaskNode
        dag = TaskDAG()
        dag.add_task(TaskNode(id="a", description="A", agent_profile="general"))
        dag.add_task(TaskNode(id="b", description="B", agent_profile="general", depends_on=["a"]))
        dag.add_task(TaskNode(id="c", description="C", agent_profile="general", depends_on=["b"]))
        order = dag.topological_order()
        assert order.index("a") < order.index("b") < order.index("c")

    def test_progress(self):
        from sharanbot.agent.team.dag import TaskDAG, TaskNode
        dag = TaskDAG()
        dag.add_task(TaskNode(id="t1", description="first", agent_profile="general"))
        dag.add_task(TaskNode(id="t2", description="second", agent_profile="general"))
        dag.mark_completed("t1", "done")
        progress = dag.get_progress()
        assert progress["total"] == 2
        assert progress["completed"] == 1
        assert progress["pending"] == 1


# ── TeamTracer ───────────────────────────────────────────────────────

class TestTeamTracer:
    def test_trace_events(self):
        from sharanbot.agent.team.trace import TeamTracer
        tracer = TeamTracer()
        tracer.start()
        tracer.trace_start("agent_1", "task_1", "Do something")
        tracer.trace_complete("agent_1", "task_1", "Done")
        assert len(tracer.events) == 2
        assert tracer.events[0].event_type == "task_start"
        assert tracer.events[1].event_type == "task_complete"

    def test_compute_metrics(self):
        from sharanbot.agent.team.trace import TeamTracer
        tracer = TeamTracer()
        tracer.start()
        tracer.trace_start("agent_1", "task_1", "Do something")
        tracer.trace_complete("agent_1", "task_1", "Done")
        metrics = tracer.compute_metrics()
        assert metrics.completed_tasks == 1
        assert metrics.failed_tasks == 0

    def test_summary(self):
        from sharanbot.agent.team.trace import TeamTracer
        tracer = TeamTracer()
        tracer.start()
        tracer.trace_start("a", "t1", "test")
        tracer.trace_complete("a", "t1", "done")
        summary = tracer.get_summary()
        assert "1/1 completed" in summary

    def test_timeline(self):
        from sharanbot.agent.team.trace import TeamTracer
        tracer = TeamTracer()
        tracer.trace_start("a", "t1", "test")
        timeline = tracer.get_timeline()
        assert len(timeline) == 1
        assert timeline[0]["type"] == "task_start"


# ── QualityGate ──────────────────────────────────────────────────────

class TestQualityGate:
    def test_parse_result_pass(self):
        from sharanbot.agent.team.quality import QualityGate
        text = '{"passed": true, "score": 0.95, "feedback": "Good", "evidence": ["test"]}'
        result = QualityGate._parse_result(text)
        assert result.passed is True
        assert result.score == 0.95

    def test_parse_result_fail(self):
        from sharanbot.agent.team.quality import QualityGate
        text = '{"passed": false, "score": 0.3, "feedback": "Needs work", "evidence": ["incomplete"]}'
        result = QualityGate._parse_result(text)
        assert result.passed is False
        assert result.score == 0.3

    def test_parse_result_invalid(self):
        from sharanbot.agent.team.quality import QualityGate
        result = QualityGate._parse_result("not json")
        assert result.passed is False


# ── TaskPlanner ──────────────────────────────────────────────────────

class TestTaskPlanner:
    def test_parse_plan_valid(self):
        from sharanbot.agent.team.planner import TaskPlanner
        text = '[{"id": "t1", "description": "test", "profile": "general", "depends_on": []}]'
        plan = TaskPlanner._parse_plan(text)
        assert len(plan) == 1
        assert plan[0]["id"] == "t1"

    def test_parse_plan_code_block(self):
        from sharanbot.agent.team.planner import TaskPlanner
        text = '```json\n[{"id": "t1", "description": "test", "profile": "general", "depends_on": []}]\n```'
        plan = TaskPlanner._parse_plan(text)
        assert len(plan) == 1

    def test_parse_plan_fallback(self):
        from sharanbot.agent.team.planner import TaskPlanner
        plan = TaskPlanner._parse_plan("completely invalid")
        assert len(plan) == 1
        assert plan[0]["id"] == "task_1"


# ── Autopilot ────────────────────────────────────────────────────────

class TestAutopilot:
    def test_quick_classify_greeting(self):
        from sharanbot.agent.team.autopilot import Autopilot
        ap = Autopilot(provider=MagicMock(), model="test")
        result = ap._quick_classify("hello")
        assert result is not None
        assert result.strategy == "direct"

    def test_quick_classify_simple_question(self):
        from sharanbot.agent.team.autopilot import Autopilot
        ap = Autopilot(provider=MagicMock(), model="test")
        result = ap._quick_classify("What time is it?")
        assert result is not None
        assert result.strategy == "direct"

    def test_quick_classify_complex_task(self):
        from sharanbot.agent.team.autopilot import Autopilot
        ap = Autopilot(provider=MagicMock(), model="test")
        result = ap._quick_classify("Build a full stack web application with React frontend and Node.js backend with authentication and database")
        assert result is not None
        assert result.strategy == "team"

    def test_quick_classify_returns_none_for_moderate(self):
        from sharanbot.agent.team.autopilot import Autopilot
        ap = Autopilot(provider=MagicMock(), model="test")
        result = ap._quick_classify("Write a Python function to parse CSV files and extract email addresses")
        assert result is None  # needs LLM analysis

    def test_parse_decision(self):
        from sharanbot.agent.team.autopilot import Autopilot
        text = '{"strategy": "team", "complexity": "high", "suggested_profile": "coder", "suggested_pipeline": "build", "reasoning": "complex"}'
        result = Autopilot._parse_decision(text)
        assert result.strategy == "team"
        assert result.needs_team is True

    def test_parse_decision_invalid(self):
        from sharanbot.agent.team.autopilot import Autopilot
        result = Autopilot._parse_decision("not json")
        assert result.strategy == "direct"


# ── PipelineRegistry ─────────────────────────────────────────────────

class TestPipelineRegistry:
    def test_list_templates(self):
        from sharanbot.agent.team.pipelines import PipelineRegistry
        reg = PipelineRegistry()
        templates = reg.list_templates()
        assert "build" in templates
        assert "deep_research" in templates
        assert "dev_qa" in templates
        assert "full_stack" in templates

    def test_build_pipeline(self):
        from sharanbot.agent.team.pipelines import PipelineRegistry
        reg = PipelineRegistry()
        dag = reg.get("build", "test task")
        assert dag is not None
        assert len(dag.tasks) == 4
        assert "research" in dag.tasks
        assert "implement" in dag.tasks

    def test_deep_research_pipeline(self):
        from sharanbot.agent.team.pipelines import PipelineRegistry
        reg = PipelineRegistry()
        dag = reg.get("deep_research", "test topic")
        assert dag is not None
        # 3 parallel searches + 1 synthesis
        assert len(dag.tasks) == 4
        # First 3 should have no deps (parallel)
        ready = dag.get_ready_tasks()
        assert len(ready) == 3

    def test_full_stack_pipeline(self):
        from sharanbot.agent.team.pipelines import PipelineRegistry
        reg = PipelineRegistry()
        dag = reg.get("full_stack", "app")
        assert dag is not None
        assert len(dag.tasks) == 6  # design, backend, frontend, integrate, test, visual_test
        # design has no deps
        ready = dag.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "design"

    def test_unknown_pipeline(self):
        from sharanbot.agent.team.pipelines import PipelineRegistry
        reg = PipelineRegistry()
        assert reg.get("nonexistent") is None


# ── TeamTools ────────────────────────────────────────────────────────

class TestTeamTools:
    def test_team_create_tool_properties(self):
        from sharanbot.agent.team.tools import TeamCreateTool
        tool = TeamCreateTool(orchestrator=MagicMock())
        assert tool.name == "team_create"
        assert "task" in tool.parameters["properties"]
        assert "task" in tool.parameters["required"]

    def test_team_status_tool_properties(self):
        from sharanbot.agent.team.tools import TeamStatusTool
        tool = TeamStatusTool(orchestrator=MagicMock())
        assert tool.name == "team_status"

    def test_team_steer_tool_properties(self):
        from sharanbot.agent.team.tools import TeamSteerTool
        tool = TeamSteerTool(orchestrator=MagicMock())
        assert tool.name == "team_steer"
        assert "team_id" in tool.parameters["required"]
        assert "instruction" in tool.parameters["required"]

    def test_team_create_set_context(self):
        from sharanbot.agent.team.tools import TeamCreateTool
        tool = TeamCreateTool(orchestrator=MagicMock())
        tool.set_context("telegram", "chat123")
        assert tool._origin_channel == "telegram"
        assert tool._origin_chat_id == "chat123"

    @pytest.mark.asyncio
    async def test_team_status_no_teams(self):
        from sharanbot.agent.team.tools import TeamStatusTool
        mock_orch = MagicMock()
        mock_orch.list_teams.return_value = []
        tool = TeamStatusTool(orchestrator=mock_orch)
        result = await tool.execute()
        assert "No teams" in result


# ── Integration: loop.py ─────────────────────────────────────────────

class TestLoopIntegration:
    def test_team_tools_registered(self, tmp_path):
        from sharanbot.agent.loop import AgentLoop
        from sharanbot.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"

        loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path)
        assert loop.tools.get("team_create") is not None
        assert loop.tools.get("team_status") is not None
        assert loop.tools.get("team_steer") is not None

    def test_team_orchestrator_created(self, tmp_path):
        from sharanbot.agent.loop import AgentLoop
        from sharanbot.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"

        loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path)
        assert loop.team_orchestrator is not None
        assert loop.autopilot is not None

    def test_set_tool_context_includes_team(self, tmp_path):
        from sharanbot.agent.loop import AgentLoop
        from sharanbot.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"

        loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path)
        loop._set_tool_context("telegram", "chat_42")
        team_tool = loop.tools.get("team_create")
        assert team_tool._origin_channel == "telegram"
        assert team_tool._origin_chat_id == "chat_42"
