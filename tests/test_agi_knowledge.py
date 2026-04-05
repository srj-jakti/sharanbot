"""Tests for SharanAGI Knowledge Accumulation."""

import pytest

from sharanbot.agi.knowledge import KnowledgeBase, KnowledgeEntry, SkillAssessment


@pytest.fixture
def kb(tmp_path):
    return KnowledgeBase(tmp_path)


def test_learn_and_recall(kb):
    kb.learn("React hooks", "useState and useEffect are the most common hooks", tags=["react", "frontend"])
    results = kb.recall("react hooks")
    assert len(results) >= 1
    assert results[0].topic == "React hooks"


def test_learn_updates_existing(kb):
    kb.learn("Python asyncio", "Use async/await for concurrent IO")
    kb.learn("Python asyncio", "asyncio.gather runs tasks concurrently")
    entry = kb.get_entry("Python asyncio")
    assert "gather" in entry.content
    assert entry.confidence > 0.8  # should have increased


def test_recall_no_results(kb):
    results = kb.recall("quantum computing")
    assert results == []


def test_recall_by_tags(kb):
    kb.learn("Docker basics", "Dockerfile defines the image", tags=["docker", "devops"])
    results = kb.recall("docker")
    assert len(results) >= 1


def test_skill_assessment(kb):
    skill = kb.assess_skill("python")
    assert skill.level == "novice"
    assert skill.confidence == 0.3

    # Record successes
    for _ in range(5):
        kb.record_task_outcome("python", success=True)

    skill = kb.assess_skill("python")
    assert skill.tasks_completed == 5
    assert skill.level in ("intermediate", "proficient")


def test_skill_failure_reduces_confidence(kb):
    kb.assess_skill("rust")
    for _ in range(3):
        kb.record_task_outcome("rust", success=False)

    skill = kb.assess_skill("rust")
    assert skill.confidence < 0.3


def test_should_study_before_task(kb):
    # No skills assessed yet — should suggest studying
    domains = kb.should_study_before_task("Deploy the app to kubernetes using helm charts")
    assert "kubernetes" in domains


def test_should_not_study_when_proficient(kb):
    kb.assess_skill("docker")
    for _ in range(10):
        kb.record_task_outcome("docker", success=True)

    domains = kb.should_study_before_task("Build a docker container")
    assert "docker" not in domains


def test_knowledge_persistence(tmp_path):
    kb1 = KnowledgeBase(tmp_path)
    kb1.learn("Test topic", "Test content", tags=["test"])
    kb1.record_task_outcome("testing", success=True)

    kb2 = KnowledgeBase(tmp_path)
    assert kb2.get_entry("Test topic") is not None
    assert kb2.assess_skill("testing").tasks_completed == 1


def test_forget(kb):
    kb.learn("Temporary", "Will be forgotten")
    assert kb.forget("Temporary") is True
    assert kb.get_entry("Temporary") is None


def test_build_knowledge_context(kb):
    kb.learn("Express.js", "Use app.get() for GET routes", tags=["express", "backend"])
    ctx = kb.build_knowledge_context("express backend api")
    assert "Express" in ctx


def test_stats(kb):
    kb.learn("A", "content a")
    kb.learn("B", "content b")
    kb.record_task_outcome("python", success=True)
    stats = kb.get_stats()
    assert stats["total_entries"] == 2
    assert stats["total_skills"] == 1
