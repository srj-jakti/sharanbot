"""Tests for task decomposition with lineage and sibling context."""

import pytest

from sharanbot.agent.team.dag import TaskDAG, TaskNode


# ── Lineage on TaskNode ──

def test_task_node_lineage_default():
    node = TaskNode(id="t1", description="Test", agent_profile="general")
    assert node.lineage == []

def test_task_node_lineage_set():
    node = TaskNode(
        id="t1", description="Implement API",
        agent_profile="coder",
        lineage=["Build e-commerce app", "Backend services"],
    )
    assert len(node.lineage) == 2
    assert node.lineage[0] == "Build e-commerce app"


# ── DAG.get_siblings ──

def test_dag_get_siblings():
    dag = TaskDAG()
    dag.add_task(TaskNode(id="research", description="Research APIs", agent_profile="researcher"))
    dag.add_task(TaskNode(id="design", description="Design schema", agent_profile="architect"))
    dag.add_task(TaskNode(
        id="implement", description="Implement backend",
        agent_profile="coder", depends_on=["research", "design"],
    ))
    dag.add_task(TaskNode(
        id="frontend", description="Build frontend",
        agent_profile="coder", depends_on=["research", "design"],
    ))
    dag.add_task(TaskNode(
        id="test", description="Write tests",
        agent_profile="tester", depends_on=["implement", "frontend"],
    ))

    # implement and frontend are siblings (same deps)
    siblings = dag.get_siblings("implement")
    assert "Build frontend" in siblings
    assert "Implement backend" not in siblings

    # research and design are siblings (both have no deps)
    siblings = dag.get_siblings("research")
    assert "Design schema" in siblings

    # test has no siblings (unique deps)
    siblings = dag.get_siblings("test")
    assert siblings == []

def test_dag_get_siblings_unknown_task():
    dag = TaskDAG()
    assert dag.get_siblings("nonexistent") == []


# ── DAG.from_plan with lineage ──

def test_dag_from_plan_with_lineage():
    plan = [
        {"id": "t1", "description": "Task 1", "profile": "general", "depends_on": []},
        {"id": "t2", "description": "Task 2", "profile": "coder", "depends_on": ["t1"]},
    ]
    lineage = ["Root project", "Phase 1"]
    dag = TaskDAG.from_plan(plan, lineage=lineage)
    assert dag.tasks["t1"].lineage == ["Root project", "Phase 1"]
    assert dag.tasks["t2"].lineage == ["Root project", "Phase 1"]

def test_dag_from_plan_without_lineage():
    plan = [
        {"id": "t1", "description": "Task 1", "profile": "general", "depends_on": []},
    ]
    dag = TaskDAG.from_plan(plan)
    assert dag.tasks["t1"].lineage == []


# ── Enhanced stuck detector ──

def test_stuck_detector_idle_threshold():
    from sharanbot.agi.stuck import StuckDetector
    import time

    detector = StuckDetector(idle_threshold_s=0.1)
    detector.record("exec", {"cmd": "ls"}, "output", success=True)
    detector.record("exec", {"cmd": "pwd"}, "/home", success=True)
    detector.record("exec", {"cmd": "cat"}, "content", success=True)

    # Not stuck yet
    diag = detector.diagnose()
    assert not diag.is_stuck

    # Simulate idle by backdating last_activity
    detector._last_activity = time.time() - 1.0
    diag = detector.diagnose()
    assert diag.is_stuck
    assert "No activity" in diag.reason

def test_stuck_detector_progress_stall():
    from sharanbot.agi.stuck import StuckDetector

    detector = StuckDetector()
    # Record the same result many times (progress stall)
    for i in range(15):
        detector.record(f"tool_{i % 3}", {"arg": i}, "same result every time", success=True)

    diag = detector.diagnose()
    assert diag.is_stuck
    assert "No new results" in diag.reason

def test_stuck_detector_configurable_thresholds():
    from sharanbot.agi.stuck import StuckDetector

    # Custom thresholds
    detector = StuckDetector(
        repeat_threshold=5,
        error_streak_threshold=6,
        idle_threshold_s=60,
    )
    assert detector._repeat_threshold == 5
    assert detector._error_streak_threshold == 6
    assert detector._idle_threshold_s == 60

def test_stuck_detector_reset_clears_new_fields():
    from sharanbot.agi.stuck import StuckDetector

    detector = StuckDetector()
    detector.record("exec", {"cmd": "ls"}, "output", success=True)
    detector.reset()
    assert detector._progress_stall_count == 0
    assert len(detector._unique_results) == 0
    stats = detector.get_stats()
    assert stats["progress_stall_count"] == 0
    assert "idle_s" in stats
