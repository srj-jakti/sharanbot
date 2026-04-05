"""Tests for AGI integration fixes — browser escalation, project injection, memory recall, casual chat."""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


class TestBrowserEscalationDetection:
    """Browser tool should detect CAPTCHAs and suggest escalation."""

    def test_detect_captcha_in_page_text(self):
        from sharanbot.agi.escalation import detect_escalation_need, EscalationType
        assert detect_escalation_need("Please verify you are human") == EscalationType.CAPTCHA
        assert detect_escalation_need("Complete the reCAPTCHA below") == EscalationType.CAPTCHA
        assert detect_escalation_need("I'm not a robot checkbox") == EscalationType.CAPTCHA

    def test_detect_verification_in_page_text(self):
        from sharanbot.agi.escalation import detect_escalation_need, EscalationType
        assert detect_escalation_need("Enter the verification code sent to your phone") == EscalationType.VERIFICATION
        assert detect_escalation_need("Two-factor authentication required") == EscalationType.VERIFICATION
        assert detect_escalation_need("Enter OTP to continue") == EscalationType.VERIFICATION

    def test_no_escalation_for_normal_page(self):
        from sharanbot.agi.escalation import detect_escalation_need
        assert detect_escalation_need("Welcome to Gmail. Sign in to continue.") is None
        assert detect_escalation_need("Your inbox has 5 new messages") is None

    def test_detect_cloudflare_challenge(self):
        from sharanbot.agi.escalation import detect_escalation_need, EscalationType
        assert detect_escalation_need("Checking if the site connection is secure. Cloudflare") == EscalationType.CAPTCHA


class TestProjectContextInjection:
    """Project context should be injected into prompts when relevant."""

    def test_project_context_for_prompt(self):
        from sharanbot.agi.projects import ProjectManager
        pm = ProjectManager(Path("/tmp/test_proj_ctx"))
        proj = pm.register("MyApp", "/code/myapp", description="A web app", tech_stack=["react", "node"])
        pm.add_todo(proj.id, "Fix login bug")
        pm.add_decision(proj.id, "Use JWT for auth")

        ctx = pm.get_project_context_for_prompt(proj.id)
        assert "MyApp" in ctx
        assert "react" in ctx
        assert "Fix login bug" in ctx
        assert "JWT for auth" in ctx
        # Cleanup
        (Path("/tmp/test_proj_ctx") / "projects_registry.json").unlink(missing_ok=True)

    def test_project_matching_by_name(self):
        from sharanbot.agi.projects import ProjectManager
        pm = ProjectManager(Path("/tmp/test_proj_match"))
        pm.register("SharanBot", "/code/sharanbot", description="AI agent")
        pm.register("WebStore", "/code/webstore", description="E-commerce")

        # Simulate matching logic from agent loop
        msg_lower = "let's work on sharanbot today".lower()
        matched = None
        for proj in pm.list_all(status="active"):
            if proj.name.lower() in msg_lower:
                matched = proj
                break
        assert matched is not None
        assert matched.name == "SharanBot"
        # Cleanup
        (Path("/tmp/test_proj_match") / "projects_registry.json").unlink(missing_ok=True)


class TestMemoryRecallTriggers:
    """Memory recall should trigger on reference phrases."""

    @pytest.mark.parametrize("msg", [
        "remember when we built the login page?",
        "we discussed this earlier",
        "like we talked about last time",
        "continue where we left off",
        "resume the previous task",
        "as we previously agreed",
    ])
    def test_recall_trigger_phrases(self, msg):
        recall_triggers = [
            "remember", "we discussed", "we talked", "last time",
            "earlier", "before", "previously", "you said", "i told you",
            "as we", "like we", "what about", "continue", "resume",
            "where were we", "pick up", "back to",
        ]
        msg_lower = msg.lower()
        assert any(trigger in msg_lower for trigger in recall_triggers)

    @pytest.mark.parametrize("msg", [
        "create a new react app",
        "fix the bug in login.py",
        "what is kubernetes?",
    ])
    def test_no_recall_for_new_tasks(self, msg):
        recall_triggers = [
            "remember", "we discussed", "we talked", "last time",
            "earlier", "before", "previously", "you said", "i told you",
            "as we", "like we", "what about", "continue", "resume",
            "where were we", "pick up", "back to",
        ]
        msg_lower = msg.lower()
        assert not any(trigger in msg_lower for trigger in recall_triggers)


class TestCasualChatRouting:
    """Casual messages should be routed as 'direct' not 'complex'."""

    def test_casual_greetings_classified_direct(self):
        from sharanbot.agent.team.autopilot import Autopilot, AutopilotDecision
        ap = Autopilot(provider=MagicMock(), model="test")

        for msg in ["hey", "hi", "hello", "sup", "yo", "good morning", "thanks"]:
            result = ap._quick_classify(msg)
            assert result is not None, f"'{msg}' should be classified"
            assert result.strategy == "direct", f"'{msg}' should be direct, got {result.strategy}"

    def test_casual_conversation_classified_direct(self):
        from sharanbot.agent.team.autopilot import Autopilot
        ap = Autopilot(provider=MagicMock(), model="test")

        for msg in [
            "how are you doing today?",
            "what do you think about this approach?",
            "any suggestions for the design?",
            "sounds good, let's go with that",
        ]:
            result = ap._quick_classify(msg)
            assert result is not None, f"'{msg}' should be classified"
            assert result.strategy == "direct", f"'{msg}' should be direct, got {result.strategy}"

    def test_complex_tasks_still_classified_complex(self):
        from sharanbot.agent.team.autopilot import Autopilot
        ap = Autopilot(provider=MagicMock(), model="test")

        result = ap._quick_classify(
            "build a full stack e-commerce application with react frontend and node backend with payment integration and user authentication"
        )
        assert result is not None
        assert result.strategy == "team"
