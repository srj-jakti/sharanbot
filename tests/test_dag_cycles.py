"""Tests for DAG cycle detection — verifies Kahn's algorithm in topological_order
and the DFS _check_cycles guard on add_task / from_plan."""

import pytest

from sharanbot.agent.team.dag import TaskDAG, TaskNode


class TestDAGCycleDetection:
    """Cycle detection via topological_order (Kahn's algorithm)."""

    def test_simple_cycle_raises(self):
        """A→B→A is a direct cycle."""
        dag = TaskDAG()
        dag.tasks["a"] = TaskNode(id="a", description="A", agent_profile="g", depends_on=["b"])
        dag.tasks["b"] = TaskNode(id="b", description="B", agent_profile="g", depends_on=["a"])
        dag._execution_order = None
        with pytest.raises(ValueError, match="[Cc]ircular"):
            dag.topological_order()

    def test_self_loop_raises(self):
        """A task depending on itself is a trivial cycle."""
        dag = TaskDAG()
        dag.tasks["x"] = TaskNode(id="x", description="X", agent_profile="g", depends_on=["x"])
        dag._execution_order = None
        with pytest.raises(ValueError, match="[Cc]ircular"):
            dag.topological_order()

    def test_three_node_cycle_raises(self):
        """A→B→C→A triangle."""
        dag = TaskDAG()
        dag.tasks["a"] = TaskNode(id="a", description="A", agent_profile="g", depends_on=["c"])
        dag.tasks["b"] = TaskNode(id="b", description="B", agent_profile="g", depends_on=["a"])
        dag.tasks["c"] = TaskNode(id="c", description="C", agent_profile="g", depends_on=["b"])
        dag._execution_order = None
        with pytest.raises(ValueError, match="[Cc]ircular"):
            dag.topological_order()

    def test_cycle_in_subgraph(self):
        """Independent root + a cycle deeper in the graph."""
        dag = TaskDAG()
        dag.tasks["root"] = TaskNode(id="root", description="R", agent_profile="g")
        dag.tasks["a"] = TaskNode(id="a", description="A", agent_profile="g", depends_on=["root", "b"])
        dag.tasks["b"] = TaskNode(id="b", description="B", agent_profile="g", depends_on=["a"])
        dag._execution_order = None
        with pytest.raises(ValueError, match="[Cc]ircular"):
            dag.topological_order()

    def test_linear_dag_no_cycle(self):
        """A→B→C is valid."""
        dag = TaskDAG()
        dag.add_task(TaskNode(id="a", description="A", agent_profile="g"))
        dag.add_task(TaskNode(id="b", description="B", agent_profile="g", depends_on=["a"]))
        dag.add_task(TaskNode(id="c", description="C", agent_profile="g", depends_on=["b"]))
        order = dag.topological_order()
        assert order == ["a", "b", "c"]

    def test_diamond_dag_no_cycle(self):
        """Diamond shape: A→{B,C}→D is valid."""
        dag = TaskDAG()
        dag.add_task(TaskNode(id="a", description="A", agent_profile="g"))
        dag.add_task(TaskNode(id="b", description="B", agent_profile="g", depends_on=["a"]))
        dag.add_task(TaskNode(id="c", description="C", agent_profile="g", depends_on=["a"]))
        dag.add_task(TaskNode(id="d", description="D", agent_profile="g", depends_on=["b", "c"]))
        order = dag.topological_order()
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")

    def test_single_node_no_cycle(self):
        dag = TaskDAG()
        dag.add_task(TaskNode(id="only", description="Only", agent_profile="g"))
        assert dag.topological_order() == ["only"]

    def test_empty_dag_no_cycle(self):
        dag = TaskDAG()
        assert dag.topological_order() == []


class TestAddTaskCycleGuard:
    """DFS _check_cycles guard in add_task."""

    def test_add_task_creating_cycle_rolls_back(self):
        """Adding a task that creates a→b→c→a cycle raises and rolls back."""
        dag = TaskDAG()
        dag.add_task(TaskNode(id="a", description="A", agent_profile="g", depends_on=["c"]))
        dag.add_task(TaskNode(id="b", description="B", agent_profile="g", depends_on=["a"]))
        with pytest.raises(ValueError, match="[Cc]ircular"):
            dag.add_task(TaskNode(id="c", description="C", agent_profile="g", depends_on=["b"]))
        # c was rolled back
        assert "c" not in dag.tasks

    def test_add_task_no_cycle(self):
        dag = TaskDAG()
        dag.add_task(TaskNode(id="a", description="A", agent_profile="g"))
        dag.add_task(TaskNode(id="b", description="B", agent_profile="g", depends_on=["a"]))
        assert len(dag.tasks) == 2


class TestFromPlanCycleGuard:
    """from_plan class method validates cycles."""

    def test_from_plan_with_cycle_raises(self):
        plan = [
            {"id": "a", "description": "A", "profile": "g", "depends_on": ["b"]},
            {"id": "b", "description": "B", "profile": "g", "depends_on": ["a"]},
        ]
        with pytest.raises(ValueError, match="[Cc]ircular"):
            TaskDAG.from_plan(plan)

    def test_from_plan_valid(self):
        plan = [
            {"id": "a", "description": "A", "profile": "g", "depends_on": []},
            {"id": "b", "description": "B", "profile": "g", "depends_on": ["a"]},
        ]
        dag = TaskDAG.from_plan(plan)
        assert dag.topological_order() == ["a", "b"]


class TestToSummaryWithCycles:
    """to_summary relies on topological_order — ensure it propagates errors."""

    def test_summary_raises_on_cycle(self):
        dag = TaskDAG()
        dag.tasks["x"] = TaskNode(id="x", description="X", agent_profile="g", depends_on=["y"])
        dag.tasks["y"] = TaskNode(id="y", description="Y", agent_profile="g", depends_on=["x"])
        dag._execution_order = None
        with pytest.raises(ValueError, match="[Cc]ircular"):
            dag.to_summary()
