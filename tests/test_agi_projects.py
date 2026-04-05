"""Tests for SharanAGI Persistent Project Context."""

import pytest

from sharanbot.agi.projects import Project, ProjectManager


@pytest.fixture
def pm(tmp_path):
    return ProjectManager(tmp_path)


def test_register_project(pm):
    proj = pm.register("My App", "/path/to/app", description="A web app", priority=2)
    assert proj.name == "My App"
    assert proj.priority == 2
    assert proj.status == "active"


def test_list_projects(pm):
    pm.register("Alpha", "/a", priority=3)
    pm.register("Beta", "/b", priority=1)
    projects = pm.list_all()
    assert len(projects) == 2
    # Should be sorted by priority
    assert projects[0].name == "Beta"


def test_project_context(pm):
    pm.register("Test", "/test", description="Test project")
    pm.update_context("test", "This is a React app with Express backend")
    pm.add_decision("test", "Use PostgreSQL for the database")

    ctx = pm.get_project_context_for_prompt("test")
    assert "React" in ctx
    assert "PostgreSQL" in ctx


def test_project_todos(pm):
    pm.register("Test", "/test")
    pm.add_todo("test", "Fix the login bug")
    pm.add_todo("test", "Add unit tests")

    proj = pm.get("test")
    assert len(proj.todos) == 2

    pm.complete_todo("test", 0)
    proj = pm.get("test")
    assert len(proj.todos) == 1
    assert proj.todos[0] == "Add unit tests"


def test_get_next_work(pm):
    pm.register("Urgent", "/urgent", priority=1)
    pm.add_todo("urgent", "Fix critical bug")
    pm.register("Low", "/low", priority=8)

    next_proj = pm.get_next_work()
    assert next_proj.name == "Urgent"


def test_get_next_work_empty(pm):
    assert pm.get_next_work() is None


def test_project_persistence(tmp_path):
    pm1 = ProjectManager(tmp_path)
    pm1.register("Persistent", "/p", description="Survives restart")
    pm1.add_todo("persistent", "Do something")

    pm2 = ProjectManager(tmp_path)
    proj = pm2.get("persistent")
    assert proj is not None
    assert proj.description == "Survives restart"
    assert len(proj.todos) == 1


def test_set_status(pm):
    pm.register("Done", "/done")
    pm.set_status("done", "completed")
    proj = pm.get("done")
    assert proj.status == "completed"


def test_cross_project_search(pm):
    pm.register("Frontend", "/fe")
    pm.update_context("frontend", "Uses React with TypeScript and Tailwind CSS")
    pm.register("Backend", "/be")
    pm.update_context("backend", "Uses Express with PostgreSQL")

    results = pm.find_cross_project_patterns("react")
    assert len(results) >= 1
    assert results[0]["project"] == "Frontend"


def test_stats(pm):
    pm.register("A", "/a")
    pm.register("B", "/b")
    pm.add_todo("a", "something")
    stats = pm.get_stats()
    assert stats["total"] == 2
    assert stats["active"] == 2
    assert stats["with_todos"] == 1
