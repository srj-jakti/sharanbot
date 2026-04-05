"""Tests for SharanAGI Watchdog / Patrol Mode."""

import json
from pathlib import Path

import pytest

from sharanbot.agi.watchdog import PatrolFinding, WatchdogPatrol


@pytest.fixture
def workspace(tmp_path):
    return tmp_path


def test_patrol_finding_to_dict():
    f = PatrolFinding(
        category="health", severity="critical",
        title="Test", details="Details", action_hint="Fix it",
    )
    d = f.to_dict()
    assert d["category"] == "health"
    assert d["severity"] == "critical"
    assert "timestamp" in d


def test_watchdog_detects_incomplete_teams(workspace):
    state_dir = workspace / ".team_state"
    state_dir.mkdir()
    (state_dir / "abc123.json").write_text(json.dumps({
        "team_id": "abc123",
        "task": "Build a web app",
        "status": "running",
        "dag": {
            "t1": {"status": "completed"},
            "t2": {"status": "pending"},
        },
    }))

    wd = WatchdogPatrol(workspace=workspace, enabled=False)
    findings = wd._check_incomplete_teams()
    assert len(findings) == 1
    assert findings[0].category == "incomplete_work"
    assert findings[0].severity == "warning"
    assert "abc123" in findings[0].title


def test_watchdog_detects_missing_node_modules(workspace):
    (workspace / "package.json").write_text('{"name": "test"}')
    # node_modules does NOT exist

    wd = WatchdogPatrol(workspace=workspace, enabled=False)
    findings = wd._check_project_health()
    assert any("node_modules" in f.title for f in findings)


def test_watchdog_no_findings_when_clean(workspace):
    wd = WatchdogPatrol(workspace=workspace, enabled=False)
    findings = wd._check_incomplete_teams()
    assert findings == []
    findings = wd._check_project_health()
    # No package.json, no logs — should be clean
    assert not any(f.severity == "critical" for f in findings)


def test_watchdog_detects_stale_services(workspace):
    (workspace / "services.json").write_text(json.dumps({
        "services": [
            {"name": "my-app", "auto_restart": True, "status": "stopped", "command": "node app.js"},
        ],
    }))

    wd = WatchdogPatrol(workspace=workspace, enabled=False)
    findings = wd._check_stale_services()
    assert len(findings) == 1
    assert findings[0].severity == "critical"
    assert "my-app" in findings[0].title


def test_watchdog_status(workspace):
    wd = WatchdogPatrol(workspace=workspace, enabled=True)
    status = wd.get_status()
    assert status["enabled"] is True
    assert status["running"] is False
