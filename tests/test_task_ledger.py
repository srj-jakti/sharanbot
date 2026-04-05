"""Tests for the Task Ledger and Service Registry."""

import time
import pytest
from pathlib import Path

from sharanbot.agi.task_ledger import (
    TaskLedger,
    TaskEntry,
    TaskArtifact,
    TaskStatus,
    TaskType,
    ServiceEntry,
)


@pytest.fixture
def ledger(tmp_path):
    return TaskLedger(tmp_path)


# ── Task CRUD ─────────────────────────────────────────────────────────

class TestTaskCRUD:

    def test_create_task(self, ledger):
        task = ledger.create_task("Build React app", task_type=TaskType.BUILD)
        assert task.id.startswith("task_")
        assert task.title == "Build React app"
        assert task.status == TaskStatus.PENDING

    def test_start_task(self, ledger):
        task = ledger.create_task("Test task")
        assert ledger.start_task(task.id)
        t = ledger.get_task(task.id)
        assert t.status == TaskStatus.RUNNING
        assert t.started_at > 0

    def test_complete_task(self, ledger):
        task = ledger.create_task("Test task")
        ledger.start_task(task.id)
        assert ledger.complete_task(task.id, outcome="All tests passed")
        t = ledger.get_task(task.id)
        assert t.status == TaskStatus.COMPLETED
        assert t.completed_at > 0
        assert t.outcome == "All tests passed"

    def test_fail_task(self, ledger):
        task = ledger.create_task("Test task")
        ledger.start_task(task.id)
        assert ledger.fail_task(task.id, error="API timeout")
        t = ledger.get_task(task.id)
        assert t.status == TaskStatus.FAILED
        assert t.error == "API timeout"

    def test_cancel_task(self, ledger):
        task = ledger.create_task("Test task")
        assert ledger.cancel_task(task.id)
        assert ledger.get_task(task.id).status == TaskStatus.CANCELLED

    def test_nonexistent_task(self, ledger):
        assert not ledger.start_task("fake_id")
        assert not ledger.complete_task("fake_id")
        assert not ledger.fail_task("fake_id")

    def test_duration(self, ledger):
        task = ledger.create_task("Test")
        ledger.start_task(task.id)
        t = ledger.get_task(task.id)
        assert t.duration_s >= 0  # running, so duration is time since start
        ledger.complete_task(task.id)
        t = ledger.get_task(task.id)
        assert t.duration_s >= 0

    def test_is_active(self, ledger):
        task = ledger.create_task("Test")
        assert task.is_active
        ledger.start_task(task.id)
        assert ledger.get_task(task.id).is_active
        ledger.complete_task(task.id)
        assert not ledger.get_task(task.id).is_active

    def test_is_terminal(self, ledger):
        task = ledger.create_task("Test")
        assert not task.is_terminal
        ledger.complete_task(task.id)
        assert ledger.get_task(task.id).is_terminal


# ── Proof of Work ─────────────────────────────────────────────────────

class TestProofOfWork:

    def test_add_artifact(self, ledger):
        task = ledger.create_task("Build app")
        artifact = TaskArtifact("file", "/app/src/index.tsx", "Main entry point")
        assert ledger.add_artifact(task.id, artifact)
        t = ledger.get_task(task.id)
        assert len(t.artifacts) == 1
        assert t.artifacts[0]["value"] == "/app/src/index.tsx"

    def test_record_tool_use(self, ledger):
        task = ledger.create_task("Build app")
        ledger.record_tool_use(task.id, "scaffold_project")
        ledger.record_tool_use(task.id, "git")
        ledger.record_tool_use(task.id, "scaffold_project")  # duplicate
        t = ledger.get_task(task.id)
        assert t.tools_used == ["scaffold_project", "git"]

    def test_record_files(self, ledger):
        task = ledger.create_task("Build app")
        ledger.record_file_created(task.id, "src/index.tsx")
        ledger.record_file_modified(task.id, "package.json")
        t = ledger.get_task(task.id)
        assert "src/index.tsx" in t.files_created
        assert "package.json" in t.files_modified

    def test_record_command(self, ledger):
        task = ledger.create_task("Deploy")
        ledger.record_command(task.id, "npm run build")
        ledger.record_command(task.id, "vercel deploy")
        t = ledger.get_task(task.id)
        assert len(t.commands_run) == 2

    def test_update_tokens(self, ledger):
        task = ledger.create_task("Research")
        ledger.update_tokens(task.id, 5000, llm_calls=3)
        ledger.update_tokens(task.id, 3000, llm_calls=2)
        t = ledger.get_task(task.id)
        assert t.tokens_used == 8000
        assert t.llm_calls == 5


# ── Queries ───────────────────────────────────────────────────────────

class TestTaskQueries:

    def test_get_active_tasks(self, ledger):
        t1 = ledger.create_task("Active 1")
        t2 = ledger.create_task("Active 2")
        t3 = ledger.create_task("Done")
        ledger.complete_task(t3.id)
        active = ledger.get_active_tasks()
        assert len(active) == 2

    def test_get_recent_tasks(self, ledger):
        for i in range(5):
            ledger.create_task(f"Task {i}")
        recent = ledger.get_recent_tasks(3)
        assert len(recent) == 3

    def test_get_tasks_by_status(self, ledger):
        t1 = ledger.create_task("A")
        t2 = ledger.create_task("B")
        ledger.complete_task(t1.id)
        assert len(ledger.get_tasks_by_status(TaskStatus.COMPLETED)) == 1
        assert len(ledger.get_tasks_by_status(TaskStatus.PENDING)) == 1

    def test_get_tasks_by_type(self, ledger):
        ledger.create_task("App", task_type=TaskType.BUILD)
        ledger.create_task("Bot", task_type=TaskType.SERVICE)
        assert len(ledger.get_tasks_by_type(TaskType.BUILD)) == 1

    def test_search_tasks(self, ledger):
        ledger.create_task("Build React app")
        ledger.create_task("Deploy to Vercel")
        results = ledger.search_tasks("react")
        assert len(results) == 1
        assert results[0].title == "Build React app"


# ── Service Registry ──────────────────────────────────────────────────

class TestServiceRegistry:

    def test_register_service(self, ledger):
        svc = ledger.register_service("bitcoin_scanner", "python scanner.py")
        assert svc.id.startswith("svc_")
        assert svc.name == "bitcoin_scanner"
        assert svc.status == "stopped"

    def test_start_service(self, ledger):
        svc = ledger.register_service("scanner", "python scanner.py")
        assert ledger.start_service(svc.id, process_id="bg_1")
        s = ledger.get_service(svc.id)
        assert s.is_running
        assert s.process_id == "bg_1"
        assert s.restart_count == 1

    def test_stop_service(self, ledger):
        svc = ledger.register_service("scanner", "python scanner.py")
        ledger.start_service(svc.id)
        assert ledger.stop_service(svc.id, reason="User requested")
        s = ledger.get_service(svc.id)
        assert s.status == "stopped"
        assert s.stop_reason == "User requested"

    def test_stop_permanently(self, ledger):
        svc = ledger.register_service("scanner", "python scanner.py")
        ledger.start_service(svc.id)
        assert ledger.stop_service(svc.id, permanent=True, reason="No longer needed")
        s = ledger.get_service(svc.id)
        assert s.is_permanently_stopped
        # Cannot restart
        assert not ledger.start_service(svc.id)

    def test_find_by_name(self, ledger):
        ledger.register_service("bitcoin_scanner", "python scanner.py")
        svc = ledger.get_service("bitcoin_scanner")
        assert svc is not None
        assert svc.name == "bitcoin_scanner"

    def test_find_partial_name(self, ledger):
        ledger.register_service("bitcoin_scanner", "python scanner.py")
        svc = ledger._find_service("bitcoin")
        assert svc is not None

    def test_get_running_services(self, ledger):
        s1 = ledger.register_service("svc1", "cmd1")
        s2 = ledger.register_service("svc2", "cmd2")
        ledger.start_service(s1.id)
        running = ledger.get_running_services()
        assert len(running) == 1

    def test_get_services_to_restart(self, ledger):
        s1 = ledger.register_service("svc1", "cmd1", auto_restart=True)
        s2 = ledger.register_service("svc2", "cmd2", auto_restart=False)
        s3 = ledger.register_service("svc3", "cmd3", auto_restart=True)
        ledger.start_service(s1.id)
        ledger.stop_service(s1.id)  # stopped, auto_restart=True → should restart
        # s2 is stopped but auto_restart=False → should NOT restart
        ledger.start_service(s3.id)
        ledger.stop_service(s3.id, permanent=True)  # permanently stopped → should NOT restart
        to_restart = ledger.get_services_to_restart()
        assert len(to_restart) == 1
        assert to_restart[0].name == "svc1"

    def test_uptime_tracking(self, ledger):
        svc = ledger.register_service("scanner", "python scanner.py")
        ledger.start_service(svc.id)
        s = ledger.get_service(svc.id)
        assert s.last_started > 0
        ledger.stop_service(svc.id)
        s = ledger.get_service(svc.id)
        assert s.total_uptime_s >= 0

    def test_update_existing_service(self, ledger):
        ledger.register_service("scanner", "python scanner.py")
        svc = ledger.register_service("scanner", "python scanner_v2.py", description="Updated")
        assert svc.command == "python scanner_v2.py"
        assert svc.description == "Updated"
        assert len(ledger.get_all_services()) == 1


# ── Persistence ───────────────────────────────────────────────────────

class TestPersistence:

    def test_tasks_persist(self, tmp_path):
        l1 = TaskLedger(tmp_path)
        task = l1.create_task("Persistent task", task_type=TaskType.BUILD)
        l1.start_task(task.id)
        l1.complete_task(task.id, outcome="Done")
        # Reload
        l2 = TaskLedger(tmp_path)
        t = l2.get_task(task.id)
        assert t is not None
        assert t.status == TaskStatus.COMPLETED
        assert t.outcome == "Done"

    def test_services_persist(self, tmp_path):
        l1 = TaskLedger(tmp_path)
        svc = l1.register_service("scanner", "python scanner.py")
        l1.start_service(svc.id)
        l1.stop_service(svc.id, permanent=True, reason="User said stop")
        # Reload
        l2 = TaskLedger(tmp_path)
        s = l2.get_service(svc.id)
        assert s is not None
        assert s.is_permanently_stopped
        assert s.stop_reason == "User said stop"


# ── Formatting ────────────────────────────────────────────────────────

class TestFormatting:

    def test_format_task_status(self, ledger):
        t1 = ledger.create_task("Active task")
        ledger.start_task(t1.id)
        t2 = ledger.create_task("Done task")
        ledger.complete_task(t2.id, outcome="All good")
        output = ledger.format_task_status()
        assert "Active" in output
        assert "Recent" in output

    def test_format_service_status(self, ledger):
        svc = ledger.register_service("scanner", "python scanner.py")
        ledger.start_service(svc.id)
        output = ledger.format_service_status()
        assert "scanner" in output
        assert "🟢" in output

    def test_get_stats(self, ledger):
        ledger.create_task("A")
        t2 = ledger.create_task("B")
        ledger.complete_task(t2.id)
        ledger.register_service("svc", "cmd")
        stats = ledger.get_stats()
        assert stats["total_tasks"] == 2
        assert stats["completed"] == 1
        assert stats["total_services"] == 1
