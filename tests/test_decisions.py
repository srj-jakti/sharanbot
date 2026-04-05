"""Tests for the Decision Store and Watchdog decision filtering."""

import time
import pytest
from pathlib import Path

from sharanbot.agi.decisions import (
    Decision,
    DecisionStore,
    DecisionStatus,
    DecisionType,
)
from sharanbot.agi.watchdog import PatrolFinding, WatchdogPatrol


@pytest.fixture
def store(tmp_path):
    return DecisionStore(tmp_path)


# ── Decision Store CRUD ───────────────────────────────────────────────

class TestDecisionStore:

    def test_record_and_query(self, store):
        store.record("bitcoin_scanner", DecisionType.STOP_PERMANENT, "User said stop permanently")
        assert store.is_stopped("bitcoin_scanner")
        assert store.is_permanently_stopped("bitcoin_scanner")

    def test_temporary_stop(self, store):
        expires = time.time() + 3600  # 1 hour
        store.record("monitor", DecisionType.STOP_TEMPORARY, "Pause for 1h", expires_at=expires)
        assert store.is_stopped("monitor")
        assert not store.is_permanently_stopped("monitor")

    def test_expired_decision(self, store):
        expires = time.time() - 1  # already expired
        store.record("old_task", DecisionType.STOP_TEMPORARY, "Was paused", expires_at=expires)
        assert not store.is_stopped("old_task")

    def test_resume_revokes_stop(self, store):
        store.record("scanner", DecisionType.STOP_PERMANENT, "Stop it")
        assert store.is_stopped("scanner")
        store.record("scanner", DecisionType.RESUME, "Start it again")
        assert not store.is_stopped("scanner")

    def test_stop_revokes_resume(self, store):
        store.record("scanner", DecisionType.RESUME, "Start it")
        store.record("scanner", DecisionType.STOP_PERMANENT, "Actually stop it")
        assert store.is_stopped("scanner")

    def test_suppress_alert(self, store):
        store.record("bitcoin_scanner", DecisionType.SUPPRESS_ALERT, "Don't alert about this")
        assert store.is_suppressed("bitcoin_scanner")

    def test_permanent_stop_also_suppresses(self, store):
        store.record("bitcoin_scanner", DecisionType.STOP_PERMANENT, "Stop permanently")
        assert store.is_suppressed("bitcoin_scanner")

    def test_revoke(self, store):
        store.record("scanner", DecisionType.STOP_PERMANENT, "Stop")
        assert store.is_stopped("scanner")
        count = store.revoke("scanner")
        assert count == 1
        assert not store.is_stopped("scanner")

    def test_fuzzy_subject_match(self, store):
        store.record("bitcoin_scanner", DecisionType.STOP_PERMANENT, "Stop")
        # Partial match
        assert store.is_stopped("bitcoin")
        assert store.is_stopped("scanner")
        # Underscore/space/dash normalization
        assert store.is_stopped("bitcoin-scanner")
        assert store.is_stopped("bitcoin scanner")

    def test_get_active_decisions(self, store):
        store.record("a", DecisionType.STOP_PERMANENT, "Stop A")
        store.record("b", DecisionType.PAUSE, "Pause B")
        store.record("c", DecisionType.STOP_TEMPORARY, "Temp stop C", expires_at=time.time() - 1)
        active = store.get_active_decisions()
        subjects = [d.subject for d in active]
        assert "a" in subjects
        assert "b" in subjects
        assert "c" not in subjects  # expired

    def test_persistence(self, tmp_path):
        store1 = DecisionStore(tmp_path)
        store1.record("bitcoin_scanner", DecisionType.STOP_PERMANENT, "Stop permanently")
        # Reload from disk
        store2 = DecisionStore(tmp_path)
        assert store2.is_permanently_stopped("bitcoin_scanner")

    def test_build_context_for_prompt(self, store):
        store.record("bitcoin_scanner", DecisionType.STOP_PERMANENT, "Stop permanently")
        store.record("monitor", DecisionType.PAUSE, "Pause monitoring")
        ctx = store.build_context_for_prompt()
        assert "bitcoin_scanner" in ctx
        assert "monitor" in ctx
        assert "MUST be respected" in ctx

    def test_format_status(self, store):
        store.record("scanner", DecisionType.STOP_PERMANENT, "Stop")
        status = store.format_status()
        assert "scanner" in status
        assert "stop_permanent" in status

    def test_get_stats(self, store):
        store.record("a", DecisionType.STOP_PERMANENT, "Stop A")
        store.record("b", DecisionType.SUPPRESS_ALERT, "Suppress B")
        stats = store.get_stats()
        assert stats["active"] == 2
        assert stats["permanent_stops"] == 1
        assert stats["suppressed_alerts"] == 1

    def test_decision_is_active_property(self):
        d = Decision(id="test", subject="x", decision_type="stop_permanent", description="test")
        assert d.is_active
        d.status = DecisionStatus.REVOKED
        assert not d.is_active

    def test_decision_is_permanent_property(self):
        d = Decision(id="test", subject="x", decision_type=DecisionType.STOP_PERMANENT, description="test")
        assert d.is_permanent
        d2 = Decision(id="test2", subject="x", decision_type=DecisionType.STOP_TEMPORARY, description="test")
        assert not d2.is_permanent


# ── Watchdog Decision Filtering ───────────────────────────────────────

class TestWatchdogDecisionFiltering:

    def test_watchdog_filters_suppressed_findings(self, tmp_path):
        store = DecisionStore(tmp_path)
        store.record("bitcoin_scanner", DecisionType.STOP_PERMANENT, "Stop permanently")

        wd = WatchdogPatrol(workspace=tmp_path, enabled=False, decision_store=store)

        findings = [
            PatrolFinding(
                category="health", severity="warning",
                title="Errors in bitcoin_scanner.log",
                details="50 errors", action_hint="Fix it",
                subject="bitcoin_scanner",
            ),
            PatrolFinding(
                category="health", severity="warning",
                title="Missing node_modules",
                details="npm install needed", action_hint="Install",
                subject="node_modules",
            ),
        ]

        filtered = wd._filter_by_decisions(findings)
        assert len(filtered) == 1
        assert filtered[0].subject == "node_modules"

    def test_watchdog_no_filter_without_store(self, tmp_path):
        wd = WatchdogPatrol(workspace=tmp_path, enabled=False)
        findings = [
            PatrolFinding(
                category="health", severity="warning",
                title="Test", details="test", action_hint="test",
                subject="bitcoin_scanner",
            ),
        ]
        filtered = wd._filter_by_decisions(findings)
        assert len(filtered) == 1

    def test_watchdog_dedup(self, tmp_path):
        wd = WatchdogPatrol(workspace=tmp_path, enabled=False)
        # Simulate a recently reported finding
        wd._reported_subjects["bitcoin_scanner"] = time.time()

        findings = [
            PatrolFinding(
                category="health", severity="warning",
                title="Errors in bitcoin_scanner.log",
                details="50 errors", action_hint="Fix it",
                subject="bitcoin_scanner",
            ),
        ]
        deduped = wd._deduplicate(findings)
        assert len(deduped) == 0

    def test_watchdog_dedup_allows_after_window(self, tmp_path):
        wd = WatchdogPatrol(workspace=tmp_path, enabled=False)
        # Reported 31 minutes ago (outside 30-min window)
        wd._reported_subjects["bitcoin_scanner"] = time.time() - 31 * 60

        findings = [
            PatrolFinding(
                category="health", severity="warning",
                title="Errors in bitcoin_scanner.log",
                details="50 errors", action_hint="Fix it",
                subject="bitcoin_scanner",
            ),
        ]
        deduped = wd._deduplicate(findings)
        assert len(deduped) == 1

    def test_watchdog_status_shows_suppressed(self, tmp_path):
        store = DecisionStore(tmp_path)
        store.record("bitcoin_scanner", DecisionType.STOP_PERMANENT, "Stop")
        wd = WatchdogPatrol(workspace=tmp_path, enabled=True, decision_store=store)
        status = wd.get_status()
        assert "bitcoin_scanner" in status["suppressed_subjects"]

    def test_finding_has_subject_field(self):
        f = PatrolFinding(
            category="health", severity="warning",
            title="Test", details="test", action_hint="test",
            subject="my_service",
        )
        d = f.to_dict()
        assert d["subject"] == "my_service"

    def test_log_file_subject_extraction(self, tmp_path):
        """Watchdog should extract log file stem as subject."""
        log_file = tmp_path / "bitcoin_scanner.log"
        log_file.write_text("ERROR something broke\n" * 5)

        wd = WatchdogPatrol(workspace=tmp_path, enabled=False)
        findings = wd._check_project_health()
        scanner_findings = [f for f in findings if "bitcoin_scanner" in f.subject]
        assert len(scanner_findings) == 1
        assert scanner_findings[0].subject == "bitcoin_scanner"


# ── AGI Tool Decision Actions ─────────────────────────────────────────

class TestAGIToolDecisions:

    @pytest.mark.asyncio
    async def test_decision_record(self, tmp_path):
        from sharanbot.agent.tools.agi import AGITool
        store = DecisionStore(tmp_path)
        tool = AGITool(decision_store=store)
        result = await tool.execute(
            action="decision_record",
            subject="bitcoin_scanner",
            decision_type="stop_permanent",
            description="User said stop bitcoin scanner permanently",
        )
        assert "recorded" in result.lower()
        assert store.is_permanently_stopped("bitcoin_scanner")

    @pytest.mark.asyncio
    async def test_decision_list(self, tmp_path):
        from sharanbot.agent.tools.agi import AGITool
        store = DecisionStore(tmp_path)
        store.record("scanner", DecisionType.STOP_PERMANENT, "Stop")
        tool = AGITool(decision_store=store)
        result = await tool.execute(action="decision_list")
        assert "scanner" in result

    @pytest.mark.asyncio
    async def test_decision_revoke(self, tmp_path):
        from sharanbot.agent.tools.agi import AGITool
        store = DecisionStore(tmp_path)
        store.record("scanner", DecisionType.STOP_PERMANENT, "Stop")
        tool = AGITool(decision_store=store)
        result = await tool.execute(action="decision_revoke", subject="scanner")
        assert "Revoked" in result
        assert not store.is_stopped("scanner")

    @pytest.mark.asyncio
    async def test_decision_status(self, tmp_path):
        from sharanbot.agent.tools.agi import AGITool
        store = DecisionStore(tmp_path)
        store.record("scanner", DecisionType.STOP_PERMANENT, "Stop")
        tool = AGITool(decision_store=store)
        result = await tool.execute(action="decision_status")
        assert "scanner" in result

    @pytest.mark.asyncio
    async def test_decision_record_with_expiry(self, tmp_path):
        from sharanbot.agent.tools.agi import AGITool
        store = DecisionStore(tmp_path)
        tool = AGITool(decision_store=store)
        result = await tool.execute(
            action="decision_record",
            subject="monitor",
            decision_type="stop_temporary",
            description="Pause for 2 hours",
            expires_in_hours=2,
        )
        assert "expires" in result.lower()
        assert store.is_stopped("monitor")

    @pytest.mark.asyncio
    async def test_decision_not_available(self):
        from sharanbot.agent.tools.agi import AGITool
        tool = AGITool()
        result = await tool.execute(action="decision_record", subject="x", decision_type="stop_permanent")
        assert "not available" in result.lower()
