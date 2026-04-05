"""Tests for the Self-Evolution Engine — SharanAGI's real self-improvement system."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sharanbot.agi.evolution import (
    Diagnosis,
    EvolutionPlan,
    EvolutionResult,
    EvolutionRisk,
    EvolutionTrigger,
    GrowthTracker,
    ImprovementPlanner,
    PlannedChange,
    SelfDiagnosisEngine,
    SelfEvolutionEngine,
)


# ── GrowthTracker ────────────────────────────────────────────────────

class TestGrowthTracker:
    def test_record_and_stats(self, tmp_path):
        tracker = GrowthTracker(tmp_path)
        assert tracker.total_evolutions == 0

        result = EvolutionResult(
            plan_id="evo-1", success=True, changes_applied=2,
            tests_passed=True, new_capabilities=["new_tool", "better_recovery"],
        )
        tracker.record(result)

        stats = tracker.get_stats()
        assert stats["total_attempts"] == 1
        assert stats["successes"] == 1
        assert stats["capabilities_added"] == 2
        assert "new_tool" in stats["capabilities"]

    def test_persistence(self, tmp_path):
        tracker1 = GrowthTracker(tmp_path)
        tracker1.record(EvolutionResult(
            plan_id="evo-1", success=True, changes_applied=1,
            new_capabilities=["cap1"],
        ))

        # Reload from disk
        tracker2 = GrowthTracker(tmp_path)
        assert tracker2.total_evolutions == 1
        assert "cap1" in tracker2.capabilities

    def test_failed_evolution_tracked(self, tmp_path):
        tracker = GrowthTracker(tmp_path)
        tracker.record(EvolutionResult(
            plan_id="evo-fail", success=False, error="regressions",
        ))
        stats = tracker.get_stats()
        assert stats["failures"] == 1
        assert stats["successes"] == 0

    def test_get_recent(self, tmp_path):
        tracker = GrowthTracker(tmp_path)
        for i in range(5):
            tracker.record(EvolutionResult(plan_id=f"evo-{i}", success=i % 2 == 0))
        recent = tracker.get_recent(3)
        assert len(recent) == 3
        assert recent[-1]["plan_id"] == "evo-4"


# ── SelfDiagnosisEngine ─────────────────────────────────────────────

class TestSelfDiagnosisEngine:
    def _make_learning(self, tmp_path):
        from sharanbot.agent.learning import LearningStore
        store = LearningStore(tmp_path)
        return store

    def test_no_issues(self, tmp_path):
        learning = self._make_learning(tmp_path)
        engine = SelfDiagnosisEngine(learning=learning)
        diagnoses = engine.run_diagnosis()
        assert diagnoses == []

    def test_repeated_error_diagnosed(self, tmp_path):
        learning = self._make_learning(tmp_path)
        # Simulate an error that occurred 6 times without fix
        from sharanbot.agent.learning import LearningEntry
        learning._entries["rate_limit:kimi"] = LearningEntry(
            category="rate_limit",
            error_signature="rate_limit:kimi",
            description="Rate limited on kimi provider",
            fix_applied="waited 10s",
            success=False,
            occurrences=6,
        )

        engine = SelfDiagnosisEngine(learning=learning)
        diagnoses = engine.run_diagnosis()
        assert len(diagnoses) >= 1
        assert diagnoses[0].trigger == EvolutionTrigger.REPEATED_ERROR
        assert "rate_limit:kimi" in diagnoses[0].description

    def test_stuck_pattern_diagnosed(self, tmp_path):
        learning = self._make_learning(tmp_path)
        from sharanbot.agi.stuck import StuckDetector
        stuck = StuckDetector()
        # Simulate being stuck 4 times
        stuck._stuck_count = 4
        stuck._total_calls = 20
        stuck._total_errors = 8

        engine = SelfDiagnosisEngine(learning=learning, stuck_detector=stuck)
        diagnoses = engine.run_diagnosis()
        stuck_diags = [d for d in diagnoses if d.trigger == EvolutionTrigger.STUCK_PATTERN]
        assert len(stuck_diags) >= 1

    def test_performance_degradation(self, tmp_path):
        learning = self._make_learning(tmp_path)
        from sharanbot.agi.observability import Observer
        observer = Observer()
        # Simulate low success rate
        for _ in range(15):
            observer.record("tool_call", success=False, component="test")
        for _ in range(5):
            observer.record("tool_call", success=True, component="test")

        engine = SelfDiagnosisEngine(learning=learning, observer=observer)
        diagnoses = engine.run_diagnosis()
        perf_diags = [d for d in diagnoses if d.trigger == EvolutionTrigger.PERFORMANCE_DEGRADATION]
        assert len(perf_diags) >= 1

    def test_circuit_breaker_diagnosed(self, tmp_path):
        learning = self._make_learning(tmp_path)
        from sharanbot.agent.recovery import RecoveryEngine
        recovery = RecoveryEngine(learning=learning, agent=None)
        # Open a circuit
        recovery._circuits["test_component"] = MagicMock(
            is_open=True, failures=5, cooldown=60.0,
        )

        engine = SelfDiagnosisEngine(learning=learning, recovery=recovery)
        diagnoses = engine.run_diagnosis()
        assert any(d.trigger == EvolutionTrigger.REPEATED_ERROR for d in diagnoses)

    def test_severity_ordering(self, tmp_path):
        learning = self._make_learning(tmp_path)
        from sharanbot.agent.learning import LearningEntry
        learning._entries["err1"] = LearningEntry(
            category="tool_error", error_signature="err1",
            description="Minor error", fix_applied="retry",
            success=False, occurrences=5,
        )
        learning._entries["err2"] = LearningEntry(
            category="api_error", error_signature="err2",
            description="Major error", fix_applied="retry",
            success=False, occurrences=15,
        )

        engine = SelfDiagnosisEngine(learning=learning)
        diagnoses = engine.run_diagnosis()
        assert len(diagnoses) >= 2
        # Higher severity first
        assert diagnoses[0].severity >= diagnoses[1].severity


# ── ImprovementPlanner ───────────────────────────────────────────────

class TestImprovementPlanner:
    @pytest.mark.asyncio
    async def test_plan_generation(self, tmp_path):
        provider = MagicMock()
        resp = MagicMock()
        resp.content = json.dumps({
            "description": "Add retry logic to provider",
            "rationale": "Repeated rate limit errors",
            "estimated_impact": "Reduce rate limit failures by 50%",
            "risk": "low",
            "changes": [
                {
                    "path": "sharanbot/providers/retry_helper.py",
                    "action": "create",
                    "description": "Retry helper with exponential backoff",
                    "content": "def retry(): pass",
                }
            ],
        })
        provider.chat_with_retry = AsyncMock(return_value=resp)

        planner = ImprovementPlanner(provider, "test-model", tmp_path)
        diagnosis = Diagnosis(
            id="diag-1",
            trigger=EvolutionTrigger.REPEATED_ERROR,
            description="Rate limit errors recurring",
            evidence=["5 occurrences", "fix failed"],
            severity=0.7,
            suggested_area="sharanbot/providers/",
        )

        plan = await planner.plan(diagnosis)
        assert plan is not None
        assert plan.diagnosis_id == "diag-1"
        assert len(plan.changes) == 1
        assert plan.risk == EvolutionRisk.LOW

    @pytest.mark.asyncio
    async def test_plan_with_code_fences(self, tmp_path):
        provider = MagicMock()
        resp = MagicMock()
        resp.content = '```json\n{"description":"fix","rationale":"test","risk":"medium","changes":[{"path":"test.py","action":"create","content":"x=1"}]}\n```'
        provider.chat_with_retry = AsyncMock(return_value=resp)

        planner = ImprovementPlanner(provider, "test-model", tmp_path)
        diagnosis = Diagnosis(
            id="diag-2", trigger=EvolutionTrigger.MANUAL,
            description="test", evidence=[], severity=0.5,
            suggested_area="sharanbot/",
        )
        plan = await planner.plan(diagnosis)
        assert plan is not None
        assert plan.risk == EvolutionRisk.MEDIUM

    @pytest.mark.asyncio
    async def test_plan_llm_failure(self, tmp_path):
        provider = MagicMock()
        provider.chat_with_retry = AsyncMock(side_effect=Exception("LLM down"))

        planner = ImprovementPlanner(provider, "test-model", tmp_path)
        diagnosis = Diagnosis(
            id="diag-3", trigger=EvolutionTrigger.MANUAL,
            description="test", evidence=[], severity=0.5,
            suggested_area="sharanbot/",
        )
        plan = await planner.plan(diagnosis)
        assert plan is None

    @pytest.mark.asyncio
    async def test_plan_invalid_json(self, tmp_path):
        provider = MagicMock()
        resp = MagicMock()
        resp.content = "not valid json at all"
        provider.chat_with_retry = AsyncMock(return_value=resp)

        planner = ImprovementPlanner(provider, "test-model", tmp_path)
        diagnosis = Diagnosis(
            id="diag-4", trigger=EvolutionTrigger.MANUAL,
            description="test", evidence=[], severity=0.5,
            suggested_area="sharanbot/",
        )
        plan = await planner.plan(diagnosis)
        assert plan is None


# ── SelfEvolutionEngine ─────────────────────────────────────────────

class TestSelfEvolutionEngine:
    def _make_engine(self, tmp_path):
        from sharanbot.agent.learning import LearningStore
        learning = LearningStore(tmp_path)
        provider = MagicMock()
        provider.chat_with_retry = AsyncMock(return_value=MagicMock(content="{}"))
        engine = SelfEvolutionEngine(
            provider=provider,
            model="test-model",
            workspace=tmp_path,
            learning=learning,
        )
        return engine, learning

    def test_rate_limiting(self, tmp_path):
        engine, _ = self._make_engine(tmp_path)
        assert engine._check_rate_limit() is True
        engine._last_cycle_time = time.time()
        assert engine._check_rate_limit() is False

    def test_daily_limit(self, tmp_path):
        engine, _ = self._make_engine(tmp_path)
        engine._daily_cycles = engine.MAX_DAILY_CYCLES
        assert engine._check_rate_limit() is False

    def test_daily_reset(self, tmp_path):
        engine, _ = self._make_engine(tmp_path)
        engine._daily_cycles = engine.MAX_DAILY_CYCLES
        engine._daily_reset_time = time.time() - 90000  # > 24h ago
        assert engine._check_rate_limit() is True

    @pytest.mark.asyncio
    async def test_cycle_no_issues(self, tmp_path):
        engine, _ = self._make_engine(tmp_path)
        result = await engine.run_cycle()
        assert result is None  # No issues to fix

    @pytest.mark.asyncio
    async def test_cycle_with_diagnosis(self, tmp_path):
        engine, learning = self._make_engine(tmp_path)

        # Create a diagnosis manually
        diagnosis = Diagnosis(
            id="diag-test",
            trigger=EvolutionTrigger.MANUAL,
            description="Test improvement",
            evidence=["test evidence"],
            severity=0.5,
            suggested_area="sharanbot/",
        )

        # Mock the planner to return a simple plan
        plan_response = MagicMock()
        plan_response.content = json.dumps({
            "description": "Add test utility",
            "rationale": "Testing",
            "risk": "low",
            "changes": [{
                "path": "sharanbot/utils/test_util.py",
                "action": "create",
                "description": "Test utility",
                "content": "def test_util():\n    return 42\n",
            }],
        })
        engine._planner._provider.chat_with_retry = AsyncMock(return_value=plan_response)

        # Mock test validation
        with patch("sharanbot.workers.self_build.validator.TestValidator") as MockValidator, \
             patch("sharanbot.workers.self_build.rollback.GitRollback") as MockRollback:
            mock_validator = MockValidator.return_value
            mock_report = MagicMock()
            mock_report.verdict = "PASS"
            mock_report.regressions_detected = False
            mock_validator.capture_baseline = AsyncMock(return_value=mock_report)
            mock_validator.run_tests = AsyncMock(return_value=mock_report)

            mock_rollback = MockRollback.return_value
            mock_rollback.create_checkpoint = AsyncMock(return_value=MagicMock())
            mock_rollback.rollback = AsyncMock(return_value=True)
            mock_rollback._git = AsyncMock(return_value="")

            result = await engine.run_cycle(
                trigger=EvolutionTrigger.MANUAL,
                specific_diagnosis=diagnosis,
            )

        assert result is not None
        assert result.success is True
        assert result.changes_applied == 1

    def test_safety_check_blocks_protected(self, tmp_path):
        engine, _ = self._make_engine(tmp_path)
        plan = EvolutionPlan(
            plan_id="evo-bad",
            diagnosis_id="diag-1",
            description="Modify vault",
            changes=[PlannedChange(
                path="sharanbot/vault/core.py",
                action="modify",
                description="Modify vault",
                content="hacked",
            )],
        )
        blocked = engine._safety_check(plan)
        assert "BLOCKED" in blocked

    def test_safety_check_allows_normal(self, tmp_path):
        engine, _ = self._make_engine(tmp_path)
        plan = EvolutionPlan(
            plan_id="evo-ok",
            diagnosis_id="diag-1",
            description="Add utility",
            changes=[PlannedChange(
                path="sharanbot/utils/new_helper.py",
                action="create",
                description="New helper",
                content="def helper(): pass",
            )],
        )
        blocked = engine._safety_check(plan)
        assert blocked == ""

    @pytest.mark.asyncio
    async def test_high_risk_requires_approval(self, tmp_path):
        engine, _ = self._make_engine(tmp_path)

        diagnosis = Diagnosis(
            id="diag-risky",
            trigger=EvolutionTrigger.MANUAL,
            description="Risky change",
            evidence=[],
            severity=0.8,
            suggested_area="sharanbot/agent/loop.py",
        )

        # Mock planner to return high-risk plan
        plan_response = MagicMock()
        plan_response.content = json.dumps({
            "description": "Modify core loop",
            "rationale": "Testing",
            "risk": "high",
            "changes": [{
                "path": "sharanbot/utils/safe.py",
                "action": "create",
                "description": "Safe file",
                "content": "x = 1",
            }],
        })
        engine._planner._provider.chat_with_retry = AsyncMock(return_value=plan_response)

        result = await engine.run_cycle(
            trigger=EvolutionTrigger.MANUAL,
            specific_diagnosis=diagnosis,
        )

        assert result is not None
        assert not result.success
        assert "approval" in result.error.lower()
        assert engine._pending_approval is not None

    def test_reject_pending(self, tmp_path):
        engine, _ = self._make_engine(tmp_path)
        assert engine.reject_pending() is False
        engine._pending_approval = MagicMock()
        assert engine.reject_pending() is True
        assert engine._pending_approval is None

    def test_get_status(self, tmp_path):
        engine, _ = self._make_engine(tmp_path)
        status = engine.get_status()
        assert "growth" in status
        assert "rate_limit" in status
        assert status["pending_approval"] is False

    @pytest.mark.asyncio
    async def test_trigger_from_event(self, tmp_path):
        engine, _ = self._make_engine(tmp_path)
        # No issues to diagnose, so should return None (rate limit ok, but no plan)
        result = await engine.trigger_from_event(
            EvolutionTrigger.TASK_FAILURE,
            description="Task failed with timeout",
        )
        # Will be None because planner returns empty plan from mock
        # (the mock returns "{}" which has no changes)
        assert result is None or not result.success


# ── Diagnosis dataclass ──────────────────────────────────────────────

class TestDiagnosis:
    def test_to_dict(self):
        d = Diagnosis(
            id="diag-1",
            trigger=EvolutionTrigger.REPEATED_ERROR,
            description="Test",
            evidence=["ev1"],
            severity=0.5,
            suggested_area="sharanbot/",
        )
        data = d.to_dict()
        assert data["id"] == "diag-1"
        assert data["trigger"] == "repeated_error"


class TestEvolutionResult:
    def test_to_dict(self):
        r = EvolutionResult(
            plan_id="evo-1", success=True, changes_applied=3,
            tests_passed=True, new_capabilities=["cap1"],
        )
        data = r.to_dict()
        assert data["success"] is True
        assert data["changes_applied"] == 3


# ── Integration: spawn_self_build uses correct classes ───────────────

class TestSpawnSelfBuildFixed:
    @pytest.mark.asyncio
    async def test_dry_run(self, tmp_path):
        """Verify spawn_self_build no longer imports non-existent classes."""
        from sharanbot.agent.tools.spawn_self_build import SpawnSelfBuildTool
        tool = SpawnSelfBuildTool()
        # Create minimal sharanbot dir for analyzer
        (tmp_path / "sharanbot").mkdir()
        (tmp_path / "sharanbot" / "test.py").write_text("x = 1\n")

        with patch("sharanbot.agent.tools.spawn_self_build.Path") as MockPath:
            MockPath.return_value = tmp_path
            # The tool should not crash with ImportError anymore
            result = await tool.execute(
                improvement="test improvement",
                dry_run=True,
            )
            # Should get a result (not an ImportError)
            assert isinstance(result, str)
