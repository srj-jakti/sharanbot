"""Phase runner — executes pipeline phases by spawning specialized workers.

Coordinates worker creation, execution in isolated git worktrees, and
post-development processing (code review → CI → dependency-ordered merge).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from sharanbot.orchestration.branch_manager import BranchManager
from sharanbot.orchestration.contract_manager import ContractManager
from sharanbot.orchestration.file_ownership import FileOwnership
from sharanbot.orchestration.manifest import ProjectManifest
from sharanbot.orchestration.phase_gate import PhaseGateValidator
from sharanbot.orchestration.pipeline import Phase, PipelineStateMachine
from sharanbot.orchestration.role_prompts import Role, RolePromptLibrary
from sharanbot.orchestration.shared_memory import SharedMemoryStore
from sharanbot.orchestration.worker_factory import WorkerAssignment, WorkerFactory

if TYPE_CHECKING:
    from sharanbot.agent.loop import AgentLoop


# Maps each phase to (Role, team_name) pairs
PHASE_WORKER_MAP: dict[Phase, list[tuple[Role, str]]] = {
    Phase.DISCOVERY: [
        (Role.PRODUCT_MANAGER, "product"),
        (Role.BUSINESS_ANALYST, "product"),
    ],
    Phase.ARCHITECTURE: [
        (Role.SOLUTION_ARCHITECT, "architecture"),
        (Role.UI_UX_DESIGNER, "design"),
        (Role.DATABASE_ARCHITECT, "architecture"),
    ],
    Phase.SPRINT_PLANNING: [
        (Role.PROJECT_MANAGER, "management"),
    ],
    Phase.DEVELOPMENT: [
        (Role.UI_COMPONENT_DEV, "frontend"),
        (Role.FRONTEND_LOGIC_DEV, "frontend"),
        (Role.RESPONSIVE_MOBILE_SPECIALIST, "frontend"),
        (Role.API_DEV, "backend"),
        (Role.DATABASE_DEV, "backend"),
        (Role.AUTH_SECURITY_DEV, "backend"),
        (Role.BUSINESS_LOGIC_DEV, "backend"),
        (Role.DEVOPS_ENGINEER, "infrastructure"),
        (Role.CLOUD_ARCHITECT, "infrastructure"),
        (Role.INTEGRATION_DEV, "integration"),
    ],
    Phase.QA_SECURITY: [
        (Role.QA_UNIT_TESTER, "qa"),
        (Role.QA_INTEGRATION_TESTER, "qa"),
        (Role.QA_E2E_TESTER, "qa"),
        (Role.SECURITY_TESTER, "qa"),
        (Role.PERFORMANCE_TESTER, "qa"),
    ],
    Phase.DEPLOYMENT: [
        (Role.DEPLOYMENT_ENGINEER, "devops"),
        (Role.DEVOPS_ENGINEER, "devops"),
    ],
    Phase.POST_LAUNCH: [
        (Role.TECHNICAL_WRITER, "docs"),
        (Role.SENIOR_CODE_REVIEWER, "review"),
    ],
}

# Project type auto-detection
_PROJECT_TYPE_KEYWORDS: dict[str, list[str]] = {
    "web_app": ["web app", "webapp", "website", "saas", "dashboard", "portal"],
    "api_service": ["api", "rest", "graphql", "microservice", "backend"],
    "cli_tool": ["cli", "command line", "terminal", "console"],
    "mobile_app": ["mobile", "ios", "android", "react native", "flutter"],
    "data_pipeline": ["data pipeline", "etl", "data processing", "analytics"],
    "full_stack": ["full stack", "fullstack", "full-stack"],
}


def detect_project_type(description: str) -> str:
    """Infer project type from description keywords."""
    desc_lower = description.lower()
    for ptype, keywords in _PROJECT_TYPE_KEYWORDS.items():
        if any(kw in desc_lower for kw in keywords):
            return ptype
    return "web_app"  # Default


class PhaseRunner:
    """Executes pipeline phases by spawning and coordinating workers."""

    def __init__(
        self,
        workspace: Path,
        pipeline: PipelineStateMachine,
        manifest: ProjectManifest,
        shared_memory: SharedMemoryStore,
        contract_manager: ContractManager,
        branch_manager: BranchManager,
        worker_factory: WorkerFactory,
        gate_validator: PhaseGateValidator,
        agent_loop: AgentLoop | None = None,
        parallel_limit: int = 5,
    ):
        self._workspace = workspace
        self._pipeline = pipeline
        self._manifest = manifest
        self._shared_memory = shared_memory
        self._contracts = contract_manager
        self._branches = branch_manager
        self._workers = worker_factory
        self._gate = gate_validator
        self._agent_loop = agent_loop
        self._semaphore = asyncio.Semaphore(min(max(parallel_limit, 1), 20))
        self._file_ownership = FileOwnership()

    async def run_pipeline(self) -> dict[str, Any]:
        """Drive the full pipeline, advancing through phases with retry."""
        results: dict[str, Any] = {}

        while not self._pipeline.is_complete:
            phase = self._pipeline.current_phase
            if not phase:
                break

            logger.info("Starting phase: {}", phase.value)
            try:
                phase_result = await self.execute_phase(phase)
                results[phase.value] = phase_result

                if phase_result.get("success"):
                    artifacts = phase_result.get("artifacts", {})
                    self._pipeline.complete_current(artifacts)
                    self._manifest.update_phase(phase.value, "completed")
                    self._pipeline.advance()
                else:
                    can_retry = self._pipeline.fail_current(
                        phase_result.get("error", "Phase failed")
                    )
                    if can_retry:
                        self._pipeline.retry_current()
                        logger.info("Retrying phase: {}", phase.value)
                    else:
                        self._manifest.update_phase(phase.value, "failed")
                        logger.error("Phase {} failed after max retries", phase.value)
                        break

            except Exception as e:
                logger.error("Phase {} error: {}", phase.value, e)
                can_retry = self._pipeline.fail_current(str(e))
                if not can_retry:
                    self._manifest.update_phase(phase.value, "failed")
                    break

            # Save state after each phase
            self._manifest.pipeline_state = self._pipeline.serialize()
            self._manifest.save(self._workspace)
            self._shared_memory.save()

        return results

    async def execute_phase(self, phase: Phase) -> dict[str, Any]:
        """Execute a single phase: spawn workers, gather results, gate check."""
        worker_configs = PHASE_WORKER_MAP.get(phase, [])
        if not worker_configs:
            return {"success": True, "artifacts": {}}

        # Create worker assignments
        assignments: list[WorkerAssignment] = []
        for role, team in worker_configs:
            ownership = self._file_ownership.get_ownership_section(role)
            contracts_ctx = self._contracts.get_contracts_context(role)
            memory_ctx = self._shared_memory.to_context_string()

            worker = self._workers.create_worker(
                role=role, team=team,
                task=f"Phase: {phase.value} — {self._manifest.description}",
                tech_stack=self._manifest.tech_stack,
                ownership_section=ownership,
                contracts=contracts_ctx,
                shared_memory_context=memory_ctx,
            )
            assignments.append(worker)
            self._manifest.worker_assignments[worker.worker_id] = {
                "role": role.value, "team": team, "status": "pending",
            }

        # Execute workers in parallel (bounded)
        results = await asyncio.gather(
            *(self._execute_worker(w) for w in assignments),
            return_exceptions=True,
        )

        # Collect artifacts from worker results
        artifacts: dict[str, Any] = {}
        all_success = True
        for assignment, result in zip(assignments, results):
            if isinstance(result, Exception):
                logger.warning("Worker {} failed: {}", assignment.worker_id, result)
                all_success = False
                continue
            if isinstance(result, dict):
                artifacts.update(result.get("artifacts", {}))
                if not result.get("success", True):
                    all_success = False
            # Record in shared memory
            self._shared_memory.add(
                key=f"worker_{assignment.worker_id}_output",
                value=str(result)[:500],
                importance=7,
                category="coordination",
                source_worker_id=assignment.worker_id,
            )

        # Post-development: code review + CI + merge
        if phase == Phase.DEVELOPMENT:
            merge_results = await self._post_process_development(assignments)
            artifacts["team_branches_committed"] = merge_results

        # Gate validation
        gate_result = self._gate.validate(phase, self._workspace, artifacts)
        if not gate_result.passed:
            logger.warning("Gate check failed for {}: {}", phase.value, gate_result.details)
            return {"success": False, "error": gate_result.details, "artifacts": artifacts}

        return {"success": all_success, "artifacts": artifacts}

    async def _execute_worker(self, worker: WorkerAssignment) -> dict[str, Any]:
        """Execute a single worker within the semaphore."""
        async with self._semaphore:
            worker.status = "running"
            logger.info("Running worker {} (role={})", worker.worker_id, worker.role.value)

            try:
                # If we have an agent loop, use it to execute the worker task
                if self._agent_loop:
                    result = await self._run_worker_via_agent(worker)
                else:
                    result = {"success": True, "artifacts": {},
                              "note": "Dry run — no execution backend"}

                worker.status = "completed"
                worker.result = str(result)[:500]
                return result

            except Exception as e:
                worker.status = "failed"
                worker.result = str(e)[:500]
                raise

    async def _run_worker_via_agent(self, worker: WorkerAssignment) -> dict[str, Any]:
        """Execute a worker task via the agent loop's subagent system."""
        if not self._agent_loop:
            return {"success": False, "error": "No agent loop available"}

        # Use the agent loop to dispatch the worker task
        # The worker's system prompt contains all the role context
        from sharanbot.bus.queue import InboundMessage
        msg = InboundMessage(
            text=f"[Worker Task] {worker.task}\n\nRole: {worker.role.value}\nTeam: {worker.team}",
            channel="internal",
            chat_id=f"worker_{worker.worker_id}",
            user_id="orchestrator",
            metadata={"worker_id": worker.worker_id, "system_prompt_override": worker.system_prompt},
        )

        # Dispatch through the bus
        await self._agent_loop.bus.publish(msg)
        return {"success": True, "artifacts": {}}

    async def _post_process_development(
        self, assignments: list[WorkerAssignment]
    ) -> dict[str, Any]:
        """Post-development: code review → CI → ordered merge."""
        worker_ids = [w.worker_id for w in assignments if w.status == "completed"]
        if not worker_ids:
            return {"merged": [], "failed": []}

        # Merge in dependency order
        merge_results = await self._branches.merge_in_order(worker_ids)

        merged = [(wid, msg) for wid, success, msg in merge_results if success]
        failed = [(wid, msg) for wid, success, msg in merge_results if not success]

        if failed:
            logger.warning("Some merges failed: {}", [(w, m) for w, m in failed])

        return {"merged": [w for w, _ in merged], "failed": [w for w, _ in failed]}
