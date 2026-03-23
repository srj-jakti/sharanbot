"""Tech lead orchestrator — top-level coordinator for multi-agent development.

Wires together the pipeline, worker factory, phase gate, shared memory,
contracts, manifest, and branch manager. Operates in Delegate Mode —
coordinates but NEVER writes project source files directly.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from sharanbot.orchestration.branch_manager import BranchManager
from sharanbot.orchestration.contract_manager import ContractManager
from sharanbot.orchestration.manifest import ProjectManifest
from sharanbot.orchestration.phase_gate import PhaseGateValidator
from sharanbot.orchestration.phase_runner import PhaseRunner, detect_project_type
from sharanbot.orchestration.pipeline import PHASE_ORDER, Phase, PipelineStateMachine
from sharanbot.orchestration.role_prompts import Role, RolePromptLibrary
from sharanbot.orchestration.shared_memory import SharedMemoryStore
from sharanbot.orchestration.worker_factory import WorkerFactory

if TYPE_CHECKING:
    from sharanbot.agent.loop import AgentLoop


class TechLeadOrchestrator:
    """Top-level coordinator for multi-agent software development.

    Coordinates workers but NEVER writes project source files directly.
    All development work is delegated to specialized worker roles.
    """

    def __init__(self, workspace: Path, agent_loop: AgentLoop | None = None,
                 parallel_limit: int = 5):
        self._workspace = workspace
        self._agent_loop = agent_loop
        self._parallel_limit = min(max(parallel_limit, 1), 20)

        self._pipeline: PipelineStateMachine | None = None
        self._manifest: ProjectManifest | None = None
        self._shared_memory: SharedMemoryStore | None = None
        self._contracts: ContractManager | None = None
        self._branches: BranchManager | None = None
        self._worker_factory: WorkerFactory | None = None
        self._gate: PhaseGateValidator | None = None

    def start_project(self, description: str, project_id: str = "",
                      skip_phases: list[Phase] | None = None,
                      tech_stack: str = "",
                      config_overrides: dict[str, Any] | None = None) -> ProjectManifest:
        """Initialize a new development project."""
        if not project_id:
            project_id = f"proj-{uuid.uuid4().hex[:8]}"

        project_type = detect_project_type(description)
        complexity = self._assess_complexity(description)

        # Initialize pipeline
        self._pipeline = PipelineStateMachine(skip_phases=skip_phases)

        # Initialize manifest
        self._manifest = ProjectManifest(
            project_id=project_id,
            description=description,
            project_type=project_type,
            tech_stack=tech_stack,
            complexity=complexity,
            pipeline_state=self._pipeline.serialize(),
        )

        # Initialize subsystems
        meta_dir = self._workspace / "Sharan_workplace" / ".projects_meta" / project_id
        meta_dir.mkdir(parents=True, exist_ok=True)

        self._shared_memory = SharedMemoryStore(
            path=meta_dir / "shared_memory.json",
        )
        self._contracts = ContractManager(workspace=self._workspace)
        self._branches = BranchManager(self._workspace)
        self._worker_factory = WorkerFactory(self._workspace)
        self._gate = PhaseGateValidator()

        # Store initial context in shared memory
        self._shared_memory.add(
            "project_description", description, importance=10,
            category="architecture",
        )
        self._shared_memory.add(
            "project_type", project_type, importance=9,
            category="technology",
        )
        if tech_stack:
            self._shared_memory.add(
                "tech_stack", tech_stack, importance=9,
                category="technology",
            )

        # Apply config overrides
        if config_overrides:
            if "parallel_limit" in config_overrides:
                self._parallel_limit = min(max(config_overrides["parallel_limit"], 1), 20)

        # Save manifest
        self._manifest.save(self._workspace)
        self._shared_memory.save()

        logger.info("Started project {} ({}, {})", project_id, project_type, complexity)
        return self._manifest

    async def run_pipeline(self) -> dict[str, Any]:
        """Run the full development pipeline."""
        if not self._pipeline or not self._manifest:
            raise RuntimeError("Call start_project() first")

        runner = PhaseRunner(
            workspace=self._workspace,
            pipeline=self._pipeline,
            manifest=self._manifest,
            shared_memory=self._shared_memory,
            contract_manager=self._contracts,
            branch_manager=self._branches,
            worker_factory=self._worker_factory,
            gate_validator=self._gate,
            agent_loop=self._agent_loop,
            parallel_limit=self._parallel_limit,
        )

        results = await runner.run_pipeline()

        # Final save
        self._manifest.pipeline_state = self._pipeline.serialize()
        self._manifest.save(self._workspace)
        self._shared_memory.save()
        self._contracts.save()

        return results

    def run_pipeline_sync(self) -> dict[str, Any]:
        """Synchronous wrapper for run_pipeline."""
        import asyncio
        return asyncio.run(self.run_pipeline())

    async def handle_worker_failure(self, worker_id: str, role: str,
                                    team: str, task: str, failure_reason: str) -> bool:
        """Handle worker failure with retry logic (max 2 retries)."""
        if not self._manifest:
            return False

        assignment = self._manifest.worker_assignments.get(worker_id, {})
        retries = assignment.get("retries", 0)

        if retries >= 2:
            logger.warning("Worker {} exceeded max retries", worker_id)
            self._manifest.worker_assignments[worker_id]["status"] = "failed"
            return False

        # Spawn replacement worker
        logger.info("Retrying worker {} (attempt {})", worker_id, retries + 1)
        self._manifest.worker_assignments[worker_id]["retries"] = retries + 1
        self._manifest.worker_assignments[worker_id]["status"] = "retrying"
        self._manifest.save(self._workspace)
        return True

    def resume_project(self, project_id: str) -> ProjectManifest | None:
        """Resume a project from saved state."""
        manifest = ProjectManifest.load(self._workspace, project_id)
        if not manifest:
            logger.warning("No manifest found for project {}", project_id)
            return None

        self._manifest = manifest
        self._pipeline = PipelineStateMachine.deserialize(manifest.pipeline_state)

        meta_dir = self._workspace / "Sharan_workplace" / ".projects_meta" / project_id
        self._shared_memory = SharedMemoryStore(path=meta_dir / "shared_memory.json")
        contracts_path = self._workspace / ".sharanbot" / "contracts.json"
        self._contracts = ContractManager.load(contracts_path) if contracts_path.exists() else ContractManager()
        self._branches = BranchManager(self._workspace)
        self._worker_factory = WorkerFactory(self._workspace)
        self._gate = PhaseGateValidator()

        logger.info("Resumed project {}", project_id)
        return manifest

    def reenter_pipeline(self, project_id: str, phase: Phase,
                         description: str = "") -> bool:
        """Re-enter pipeline at any phase for bug fixes/new features."""
        if not self._pipeline:
            manifest = self.resume_project(project_id)
            if not manifest:
                return False

        self._pipeline.reset_from_phase(phase)
        if description and self._manifest:
            self._manifest.description = description

        logger.info("Re-entering pipeline at phase {} for {}", phase.value, project_id)
        return True

    def get_project_status(self, project_id: str = "") -> str:
        """Get formatted project status report."""
        if project_id and not self._manifest:
            self.resume_project(project_id)

        if not self._manifest:
            return "No active project"

        return self._manifest.get_status_report()

    def _assess_complexity(self, description: str) -> str:
        """Assess project complexity from description."""
        desc_lower = description.lower()
        complex_signals = ["microservice", "distributed", "real-time", "machine learning",
                           "ai", "blockchain", "payment", "multi-tenant", "enterprise"]
        simple_signals = ["simple", "basic", "todo", "hello world", "landing page", "static"]

        if any(s in desc_lower for s in complex_signals):
            return "complex"
        if any(s in desc_lower for s in simple_signals):
            return "simple"
        return "medium"

    @property
    def has_execution_backend(self) -> bool:
        """Check if we have everything needed for actual execution."""
        return self._agent_loop is not None
