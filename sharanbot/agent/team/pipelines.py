"""Pipeline templates — pre-built task DAG patterns."""

from __future__ import annotations

from sharanbot.agent.team.dag import TaskDAG, TaskNode


class PipelineRegistry:
    """Registry of reusable pipeline templates for common workflows."""

    def __init__(self) -> None:
        self._templates: dict[str, callable] = {}
        self._register_builtins()

    def _register_builtins(self) -> None:
        self._templates["build"] = self._build_pipeline
        self._templates["deep_research"] = self._deep_research_pipeline
        self._templates["dev_qa"] = self._dev_qa_pipeline
        self._templates["full_stack"] = self._full_stack_pipeline
        self._templates["visual_qa"] = self._visual_qa_pipeline

    def get(self, name: str, task: str = "") -> TaskDAG | None:
        """Get a pipeline template by name, parameterized with the task."""
        builder = self._templates.get(name)
        return builder(task) if builder else None

    def list_templates(self) -> list[str]:
        return list(self._templates.keys())

    def register(self, name: str, builder) -> None:
        self._templates[name] = builder

    # ------------------------------------------------------------------
    # Built-in pipelines
    # ------------------------------------------------------------------

    @staticmethod
    def _build_pipeline(task: str) -> TaskDAG:
        """Research → Implement → Test → Review pipeline."""
        dag = TaskDAG()
        dag.add_task(TaskNode(
            id="research",
            description=f"Research requirements and existing patterns for: {task}",
            agent_profile="researcher",
            quality_gate=False,
        ))
        dag.add_task(TaskNode(
            id="implement",
            description=f"Implement the solution for: {task}",
            agent_profile="coder",
            depends_on=["research"],
            quality_gate=True,
        ))
        dag.add_task(TaskNode(
            id="test",
            description=f"Write and run tests for: {task}",
            agent_profile="coder",
            depends_on=["implement"],
            quality_gate=True,
        ))
        dag.add_task(TaskNode(
            id="review",
            description=f"Review code quality, security, and completeness for: {task}",
            agent_profile="reviewer",
            depends_on=["test"],
            quality_gate=False,
        ))
        return dag

    @staticmethod
    def _deep_research_pipeline(task: str) -> TaskDAG:
        """Parallel web research → Synthesis → Report pipeline."""
        dag = TaskDAG()
        dag.add_task(TaskNode(
            id="search_web",
            description=f"Search the web for information about: {task}",
            agent_profile="researcher",
        ))
        dag.add_task(TaskNode(
            id="search_docs",
            description=f"Search documentation and technical references for: {task}",
            agent_profile="researcher",
        ))
        dag.add_task(TaskNode(
            id="search_code",
            description=f"Search code examples and implementations for: {task}",
            agent_profile="researcher",
        ))
        dag.add_task(TaskNode(
            id="synthesize",
            description=f"Synthesize all research findings into a comprehensive analysis: {task}",
            agent_profile="general",
            depends_on=["search_web", "search_docs", "search_code"],
            quality_gate=True,
        ))
        return dag

    @staticmethod
    def _dev_qa_pipeline(task: str) -> TaskDAG:
        """Implement → QA Review → Fix → Final Review pipeline."""
        dag = TaskDAG()
        dag.add_task(TaskNode(
            id="implement",
            description=f"Implement: {task}",
            agent_profile="coder",
            quality_gate=True,
            max_retries=2,
        ))
        dag.add_task(TaskNode(
            id="qa_review",
            description=f"Review implementation quality, test coverage, security: {task}",
            agent_profile="reviewer",
            depends_on=["implement"],
            quality_gate=True,
        ))
        dag.add_task(TaskNode(
            id="final_fix",
            description=f"Address any issues found in QA review: {task}",
            agent_profile="coder",
            depends_on=["qa_review"],
        ))
        return dag

    @staticmethod
    def _full_stack_pipeline(task: str) -> TaskDAG:
        """Full-stack: Design → Backend + Frontend (parallel) → Integration → Test."""
        dag = TaskDAG()
        dag.add_task(TaskNode(
            id="design",
            description=f"Design the architecture and plan for: {task}",
            agent_profile="architect",
            quality_gate=True,
        ))
        dag.add_task(TaskNode(
            id="backend",
            description=f"Implement backend/API for: {task}",
            agent_profile="coder",
            depends_on=["design"],
        ))
        dag.add_task(TaskNode(
            id="frontend",
            description=f"Implement frontend/UI for: {task}",
            agent_profile="coder",
            depends_on=["design"],
        ))
        dag.add_task(TaskNode(
            id="integrate",
            description=f"Integrate backend and frontend: {task}",
            agent_profile="coder",
            depends_on=["backend", "frontend"],
            quality_gate=True,
        ))
        dag.add_task(TaskNode(
            id="test",
            description=f"End-to-end testing: {task}",
            agent_profile="coder",
            depends_on=["integrate"],
        ))
        dag.add_task(TaskNode(
            id="visual_test",
            description=(
                f"Visual E2E testing using the browser tool: Start the application, "
                f"navigate to it with the browser, take screenshots, click through "
                f"the main user flows, verify the UI renders correctly and the "
                f"frontend-backend integration works. Task: {task}"
            ),
            agent_profile="e2e_tester",
            depends_on=["test"],
            quality_gate=True,
        ))
        return dag

    @staticmethod
    def _visual_qa_pipeline(task: str) -> TaskDAG:
        """Implement → Deploy to sandbox → Browser-based visual QA → Fix loop."""
        dag = TaskDAG()
        dag.add_task(TaskNode(
            id="implement",
            description=f"Implement: {task}",
            agent_profile="coder",
            quality_gate=True,
            max_retries=2,
        ))
        dag.add_task(TaskNode(
            id="deploy_sandbox",
            description=(
                f"Deploy the implementation to a Docker sandbox: "
                f"Create a sandbox, copy the code in, install dependencies, "
                f"and start the server. Return the URL. Task: {task}"
            ),
            agent_profile="devops",
            depends_on=["implement"],
        ))
        dag.add_task(TaskNode(
            id="visual_test",
            description=(
                f"Use the browser tool to navigate to the running application, "
                f"take screenshots, test all user flows (forms, buttons, navigation), "
                f"verify the UI renders correctly and data flows between frontend and "
                f"backend. Report any visual or functional issues. Task: {task}"
            ),
            agent_profile="e2e_tester",
            depends_on=["deploy_sandbox"],
            quality_gate=True,
            max_retries=2,
        ))
        dag.add_task(TaskNode(
            id="fix_issues",
            description=f"Fix any issues found during visual QA testing: {task}",
            agent_profile="coder",
            depends_on=["visual_test"],
        ))
        return dag
