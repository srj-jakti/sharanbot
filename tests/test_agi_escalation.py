"""Tests for SharanAGI Human-in-the-Loop Escalation."""

import asyncio

import pytest

from sharanbot.agi.escalation import (
    EscalationManager,
    EscalationType,
    detect_escalation_need,
)


def test_detect_captcha():
    assert detect_escalation_need("Please solve the reCAPTCHA") == EscalationType.CAPTCHA
    assert detect_escalation_need("Verify you are human") == EscalationType.CAPTCHA


def test_detect_verification():
    assert detect_escalation_need("Enter the verification code") == EscalationType.VERIFICATION
    assert detect_escalation_need("Two-factor authentication required") == EscalationType.VERIFICATION


def test_detect_nothing():
    assert detect_escalation_need("Welcome to the dashboard") is None
    assert detect_escalation_need("Hello world") is None


def test_escalation_manager_no_pending():
    mgr = EscalationManager()
    assert not mgr.has_pending()
    assert mgr.get_pending() == []


def test_escalation_receive_response_no_pending():
    mgr = EscalationManager()
    assert mgr.receive_response("hello") is False


@pytest.mark.asyncio
async def test_escalation_ask_and_respond():
    sent_messages = []

    async def mock_send(message, screenshot=None):
        sent_messages.append(message)

    mgr = EscalationManager(on_send=mock_send, default_timeout_s=5)

    # Start ask_human in background, respond quickly
    async def respond_after_delay():
        await asyncio.sleep(0.1)
        mgr.receive_response("the answer is 42")

    task = asyncio.create_task(respond_after_delay())
    result = await mgr.ask_human(
        type=EscalationType.JUDGMENT,
        question="What is the answer?",
    )
    await task

    assert result == "the answer is 42"
    assert len(sent_messages) == 1
    assert "What is the answer?" in sent_messages[0]


@pytest.mark.asyncio
async def test_escalation_timeout():
    async def mock_send(message, screenshot=None):
        pass

    mgr = EscalationManager(on_send=mock_send, default_timeout_s=0.1)
    result = await mgr.ask_human(
        type=EscalationType.CAPTCHA,
        question="Solve this",
        timeout_s=0.1,
    )
    assert result is None
    assert mgr.get_stats()["timed_out"] == 1


def test_escalation_stats():
    mgr = EscalationManager()
    stats = mgr.get_stats()
    assert stats["total_requests"] == 0
    assert stats["resolved"] == 0
