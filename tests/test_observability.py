"""Tests for the observability system."""

import time
import pytest
from pathlib import Path

from sharanbot.agi.observability import (
    Observer,
    MetricName,
    HealthStatus,
    MetricCounter,
)


# ── MetricCounter ──

def test_metric_counter_record():
    c = MetricCounter()
    c.record(success=True)
    c.record(success=True)
    c.record(success=False, reason="timeout")
    assert c.total == 3
    assert c.success == 2
    assert c.failure == 1
    assert c.last_failure_reason == "timeout"

def test_metric_counter_to_dict():
    c = MetricCounter()
    c.record(success=True)
    d = c.to_dict()
    assert d["total"] == 1
    assert d["success"] == 1
    assert d["failure"] == 0
    assert d["success_rate"] == "100%"

def test_metric_counter_empty():
    c = MetricCounter()
    d = c.to_dict()
    assert d["success_rate"] == "N/A"


# ── Observer ──

def test_observer_record_metric():
    obs = Observer()
    obs.record(MetricName.TOOL_CALL, success=True, component="tool", operation="exec")
    obs.record(MetricName.TOOL_CALL, success=True)
    obs.record(MetricName.TOOL_ERROR, success=False, reason="timeout")
    summary = obs.get_summary()
    assert summary["metrics"]["tool_call"]["total"] == 2
    assert summary["metrics"]["tool_error"]["total"] == 1

def test_observer_health():
    obs = Observer()
    obs.set_health("llm_provider", HealthStatus.OK)
    obs.set_health("browser", HealthStatus.WARN, reason="Playwright not installed")
    summary = obs.get_summary()
    assert summary["overall_health"] == "warn"
    assert summary["health"]["llm_provider"]["status"] == "ok"
    assert summary["health"]["browser"]["status"] == "warn"

def test_observer_health_error_overrides():
    obs = Observer()
    obs.set_health("a", HealthStatus.OK)
    obs.set_health("b", HealthStatus.WARN)
    obs.set_health("c", HealthStatus.ERROR, reason="crash")
    summary = obs.get_summary()
    assert summary["overall_health"] == "error"

def test_observer_traces():
    obs = Observer()
    obs.record("test_metric", component="test", operation="op1")
    obs.record("test_metric", component="test", operation="op2")
    summary = obs.get_summary()
    assert len(summary["recent_traces"]) == 2
    assert summary["recent_traces"][0]["op"] == "op1"

def test_observer_session_tracking():
    obs = Observer()
    obs.record("tool_call", session_key="cli:direct", operation="exec")
    obs.record("tool_call", session_key="cli:direct", operation="read_file")
    obs.record("tool_call", session_key="whatsapp:123", operation="web_search")
    history = obs.get_session_history("cli:direct")
    assert len(history) == 2
    assert history[0] == "tool_call:ok"
    assert obs.get_summary()["active_sessions"] == 2

def test_observer_format_status():
    obs = Observer()
    obs.record(MetricName.TASK_COMPLETED, success=True)
    obs.set_health("llm", HealthStatus.OK)
    status = obs.format_status()
    assert "📊 Observability" in status
    assert "task_completed" in status
    assert "🟢" in status

def test_observer_persist(tmp_path):
    obs = Observer(workspace=tmp_path)
    obs._PERSIST_INTERVAL = 0  # Force immediate persist
    obs.record("test", success=True)
    obs._persist()
    snapshot_path = tmp_path / ".observability" / "snapshot.json"
    assert snapshot_path.exists()
    import json
    data = json.loads(snapshot_path.read_text())
    assert "metrics" in data
    assert "test" in data["metrics"]

def test_observer_trace_limit():
    obs = Observer()
    for i in range(300):
        obs.record("metric", operation=f"op_{i}")
    assert len(obs._traces) <= obs._TRACE_LIMIT

def test_observer_uptime():
    obs = Observer()
    summary = obs.get_summary()
    assert summary["uptime_s"] >= 0
