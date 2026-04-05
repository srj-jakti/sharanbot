"""Tests for SharanAGI Stuck Detection + Smart Escalation."""

import pytest

from sharanbot.agi.stuck import ConfidenceScorer, StuckDetector


@pytest.fixture
def detector():
    return StuckDetector()


def test_not_stuck_initially(detector):
    diag = detector.diagnose()
    assert diag.is_stuck is False


def test_detect_repeated_tool_calls(detector):
    # Same tool with same args 4 times
    for _ in range(4):
        detector.record("read_file", {"path": "foo.py"}, "content of foo", success=True)

    diag = detector.diagnose()
    assert diag.is_stuck is True
    assert "read_file" in diag.reason
    assert diag.confidence > 0.5


def test_detect_error_streak(detector):
    # 5 consecutive errors
    for i in range(5):
        detector.record(f"tool_{i % 2}", {"x": i}, "Error: failed", success=False)

    diag = detector.diagnose()
    assert diag.is_stuck is True
    assert "consecutive errors" in diag.reason


def test_detect_oscillation(detector):
    # A→B→A→B→A→B pattern with different args to avoid repeat detection
    for i in range(3):
        detector.record("read_file", {"path": f"a_{i}"}, "ok", success=True)
        detector.record("write_file", {"path": f"b_{i}"}, "ok", success=True)

    diag = detector.diagnose()
    assert diag.is_stuck is True
    assert "Oscillating" in diag.reason


def test_no_stuck_with_varied_calls(detector):
    detector.record("read_file", {"path": "a.py"}, "ok", success=True)
    detector.record("write_file", {"path": "b.py"}, "ok", success=True)
    detector.record("exec", {"command": "npm test"}, "ok", success=True)
    detector.record("web_search", {"query": "react"}, "ok", success=True)

    diag = detector.diagnose()
    assert diag.is_stuck is False


def test_reset(detector):
    for _ in range(5):
        detector.record("tool", {"a": 1}, "Error", success=False)
    assert detector.diagnose().is_stuck is True

    detector.reset()
    assert detector.diagnose().is_stuck is False


def test_build_stuck_context(detector):
    detector.record("read_file", {"path": "x"}, "content", success=True)
    detector.record("exec", {"cmd": "test"}, "Error: failed", success=False)
    ctx = detector.build_stuck_context()
    assert "read_file" in ctx
    assert "exec" in ctx


def test_stats(detector):
    detector.record("a", {}, "ok", success=True)
    detector.record("b", {}, "err", success=False)
    stats = detector.get_stats()
    assert stats["total_calls"] == 2
    assert stats["total_errors"] == 1


# ── Confidence Scorer ──

def test_confidence_scorer_basic():
    scorer = ConfidenceScorer()
    score = scorer.score_response("I successfully completed the task", tool_success_rate=0.9)
    assert score > 0.7


def test_confidence_scorer_hedging():
    scorer = ConfidenceScorer()
    score = scorer.score_response("I'm not sure but I think this might work, maybe", tool_success_rate=0.5)
    assert score < 0.7


def test_confidence_scorer_should_escalate():
    scorer = ConfidenceScorer()
    # Multiple low-confidence responses with errors and heavy hedging
    scorer.score_response("I'm not sure, I think this might work, maybe, possibly, let me try", tool_success_rate=0.1)
    scorer.score_response("Error: failed again, hopefully this could try to work, not certain", tool_success_rate=0.1)
    scorer.score_response("I'm not sure, I think maybe this might possibly work, let me try again", tool_success_rate=0.1)
    assert scorer.should_escalate() is True


def test_confidence_scorer_should_not_escalate():
    scorer = ConfidenceScorer()
    scorer.score_response("Successfully completed", tool_success_rate=0.9)
    scorer.score_response("Done, all tests pass", tool_success_rate=1.0)
    assert scorer.should_escalate() is False


def test_confidence_scorer_reset():
    scorer = ConfidenceScorer()
    scorer.score_response("test", tool_success_rate=0.5)
    scorer.reset()
    assert scorer.get_average() == 0.7  # default
