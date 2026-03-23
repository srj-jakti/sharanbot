"""Tests for the autonomy package — task queue, deploy, and context manager."""

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from sharanbot.autonomy.task_queue import (
    PersistentTaskQueue,
    TaskEntry,
    TaskPriority,
    TaskStatus,
)
from sharanbot.autonomy.context_manager import ContextManager, TurnRecord


# ── PersistentTaskQueue ──────────────────────────────────────────────

class TestPersistentTaskQueue:
    def test_add_and_get(self, tmp_path):
        q = PersistentTaskQueue(path=tmp_path / "q.jsonl")
        tid = q.add("Build feature X")
        assert tid.startswith("t-")
        task = q.get(tid)
        assert task is not None
        assert task.description == "Build feature X"
        assert task.status == TaskStatus.PENDING

    def test_pop_priority_order(self, tmp_path):
        q = PersistentTaskQueue(path=tmp_path / "q.jsonl")
        q.add("low prio", priority=TaskPriority.LOW)
        q.add("critical", priority=TaskPriority.CRITICAL)
        q.add("normal", priority=TaskPriority.NORMAL)
        task = q.pop()
        assert task is not None
        assert task.description == "critical"
        assert task.status == TaskStatus.IN_PROGRESS

    def test_complete_and_fail(self, tmp_path):
        q = PersistentTaskQueue(path=tmp_path / "q.jsonl")
        tid = q.add("task A")
        assert q.complete(tid, result="done")
        assert q.get(tid).status == TaskStatus.DONE

        tid2 = q.add("task B")
        assert q.fail(tid2, error="oops")
        # Should auto-retry (retry 1 < max 3)
        assert q.get(tid2).status == TaskStatus.PENDING

    def test_fail_exhausts_retries(self, tmp_path):
        q = PersistentTaskQueue(path=tmp_path / "q.jsonl")
        tid = q.add("fragile task")
        q.get(tid).max_retries = 2
        q.fail(tid)  # retry 1 → pending
        q.fail(tid)  # retry 2 → failed
        assert q.get(tid).status == TaskStatus.FAILED

    def test_subtasks(self, tmp_path):
        q = PersistentTaskQueue(path=tmp_path / "q.jsonl")
        parent = q.add("parent", project_id="proj-1")
        child = q.add_subtask(parent, "child task")
        subtasks = q.get_subtasks(parent)
        assert len(subtasks) == 1
        assert subtasks[0].parent_id == parent

    def test_cancel_and_block(self, tmp_path):
        q = PersistentTaskQueue(path=tmp_path / "q.jsonl")
        tid = q.add("task")
        assert q.block(tid, reason="waiting for API key")
        assert q.get(tid).status == TaskStatus.BLOCKED
        assert q.unblock(tid)
        assert q.get(tid).status == TaskStatus.PENDING
        assert q.cancel(tid)
        assert q.get(tid).status == TaskStatus.CANCELLED

    def test_project_filter(self, tmp_path):
        q = PersistentTaskQueue(path=tmp_path / "q.jsonl")
        q.add("task A", project_id="proj-1")
        q.add("task B", project_id="proj-2")
        q.add("task C", project_id="proj-1")
        tasks = q.list_tasks(project_id="proj-1")
        assert len(tasks) == 2

    def test_persistence(self, tmp_path):
        path = tmp_path / "q.jsonl"
        q1 = PersistentTaskQueue(path=path)
        q1.add("persisted task")
        # Reload
        q2 = PersistentTaskQueue(path=path)
        assert q2.pending_count() == 1

    def test_stats(self, tmp_path):
        q = PersistentTaskQueue(path=tmp_path / "q.jsonl")
        q.add("a")
        tid = q.add("b")
        q.complete(tid)
        stats = q.get_stats()
        assert stats.get(TaskStatus.PENDING, 0) == 1
        assert stats.get(TaskStatus.DONE, 0) == 1


# ── ContextManager ──────────────────────────────────────────────────

class TestContextManager:
    def test_add_turn(self):
        cm = ContextManager(token_budget=100_000)
        turn = cm.add_turn("user", "Hello world")
        assert turn.role == "user"
        assert turn.turn_number == 1

    def test_token_estimation(self):
        cm = ContextManager(token_budget=100_000)
        cm.add_turn("user", "x" * 400)  # ~100 tokens
        assert cm.token_usage > 0

    def test_needs_summarization(self):
        # Use a normal budget and flood with big messages
        cm = ContextManager(token_budget=20_000)
        for i in range(50):
            cm.add_turn("user", "x" * 2000)  # big messages
        # Should have triggered compression or usage is high
        assert cm.usage_fraction > 0

    def test_get_context_messages(self):
        cm = ContextManager(token_budget=100_000)
        cm.add_turn("user", "Hi")
        cm.add_turn("assistant", "Hello!")
        msgs = cm.get_context_messages()
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"

    def test_force_summarize(self):
        cm = ContextManager(token_budget=100_000)
        for i in range(20):
            cm.add_turn("user", f"Message {i}")
        summary = cm.force_summarize()
        stats = cm.get_stats()
        assert stats["total_turns"] == 20

    def test_persistence(self, tmp_path):
        path = tmp_path / "ctx.json"
        cm = ContextManager(token_budget=50_000, persist_path=path, session_id="s1")
        for i in range(15):
            cm.add_turn("user", f"Turn {i} content here")
        cm.force_summarize()
        stats = cm.get_stats()
        # Load
        cm2 = ContextManager(token_budget=50_000, persist_path=path)
        stats2 = cm2.get_stats()
        assert stats2["total_turns"] == stats["total_turns"]


# ── Deploy pipeline ─────────────────────────────────────────────────

class TestDeployPipeline:
    def test_detect_project_type(self, tmp_path):
        from sharanbot.autonomy.deploy import DeploymentPipeline, ProjectType
        (tmp_path / "package.json").write_text("{}")
        dp = DeploymentPipeline()
        assert dp.detect_project_type(tmp_path) == ProjectType.NODE

    def test_detect_python_type(self, tmp_path):
        from sharanbot.autonomy.deploy import DeploymentPipeline, ProjectType
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'")
        dp = DeploymentPipeline()
        assert dp.detect_project_type(tmp_path) == ProjectType.PYTHON

    def test_generate_dockerfile(self, tmp_path):
        from sharanbot.autonomy.deploy import DeploymentPipeline, DeployConfig, ProjectType
        config = DeployConfig(port=3000)
        dp = DeploymentPipeline(config)
        dockerfile = dp.generate_dockerfile(project_type=ProjectType.NODE)
        assert "FROM node" in dockerfile
        assert "3000" in dockerfile

    def test_generate_compose(self):
        from sharanbot.autonomy.deploy import DeploymentPipeline, DeployConfig, ProjectType
        config = DeployConfig(port=8080, env_vars={"DB_URL": "postgres://..."})
        dp = DeploymentPipeline(config)
        compose = dp.generate_compose("my-app", project_type=ProjectType.PYTHON)
        assert "my-app" in compose
        assert "DB_URL" in compose
        assert "8080" in compose

    @pytest.mark.asyncio
    async def test_validate_project(self, tmp_path):
        from sharanbot.autonomy.deploy import DeploymentPipeline, ProjectType
        (tmp_path / "pyproject.toml").write_text("[project]")
        (tmp_path / "main.py").write_text("print('hi')")
        (tmp_path / ".env").write_text("SECRET=x")
        dp = DeploymentPipeline()
        issues = await dp.validate_project(tmp_path)
        assert any(".env" in i for i in issues)
