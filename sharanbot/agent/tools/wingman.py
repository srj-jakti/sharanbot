"""Wingman tools — let SharanBot assign tasks to tandem-browser's AI agent.

Provides tools for the agent to:
- Create tasks for Wingman to execute in the browser
- Check task status and approve/reject steps
- Chat with Wingman
- Control autonomy settings
- Emergency stop
"""

from __future__ import annotations

from typing import Any

from sharanbot.agent.tools.base import Tool
from sharanbot.tandem.wingman import WingmanManager


# Singleton — shared across all tool instances
_wingman: WingmanManager | None = None


def _get_wingman() -> WingmanManager:
    global _wingman
    if _wingman is None:
        _wingman = WingmanManager()
    return _wingman


class WingmanTaskTool(Tool):
    """Create and manage tasks for Wingman (tandem-browser's AI agent)."""

    @property
    def name(self) -> str:
        return "wingman_task"

    @property
    def description(self) -> str:
        return (
            "Create a task for Wingman to execute in the browser. "
            "Wingman is an AI agent inside Tandem Browser that can perform "
            "multi-step web tasks with human-in-the-loop approval for risky actions. "
            "Use this to delegate browser automation, web research, form filling, "
            "and complex multi-page workflows."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Action: create, status, approve, reject, stop, activity",
                    "enum": ["create", "status", "approve", "reject", "stop", "activity"],
                },
                "description": {
                    "type": "string",
                    "description": "Task description (for 'create' action)",
                },
                "task_id": {
                    "type": "string",
                    "description": "Task ID (for status, approve, reject actions)",
                },
                "reason": {
                    "type": "string",
                    "description": "Rejection reason (for 'reject' action)",
                },
            },
            "required": ["action"],
        }

    async def execute(self, action: str, **kwargs: Any) -> Any:
        wm = _get_wingman()
        action = action.lower().strip()
        try:
            if action == "create":
                desc = kwargs.get("description", "")
                if not desc:
                    return "Error: 'description' is required to create a task."
                return await wm.create_task(desc)
            elif action == "status":
                return await wm.get_task_status(kwargs.get("task_id"))
            elif action == "approve":
                tid = kwargs.get("task_id", "")
                if not tid:
                    return "Error: 'task_id' is required."
                return await wm.approve_step(tid)
            elif action == "reject":
                tid = kwargs.get("task_id", "")
                if not tid:
                    return "Error: 'task_id' is required."
                return await wm.reject_step(tid, kwargs.get("reason", ""))
            elif action == "stop":
                return await wm.emergency_stop()
            elif action == "activity":
                return await wm.get_activity_log()
            else:
                return f"Unknown action '{action}'. Use: create, status, approve, reject, stop, activity"
        except Exception as e:
            return f"Wingman error: {e}"


class WingmanChatTool(Tool):
    """Chat with Wingman — send messages and get responses."""

    @property
    def name(self) -> str:
        return "wingman_chat"

    @property
    def description(self) -> str:
        return (
            "Communicate with Wingman via chat. Send messages, get chat history, "
            "or control autonomy settings. Wingman is the AI agent inside "
            "Tandem Browser that handles browser automation tasks."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Action: send, history, autonomy, set_autonomy",
                    "enum": ["send", "history", "autonomy", "set_autonomy"],
                },
                "message": {
                    "type": "string",
                    "description": "Message to send (for 'send' action)",
                },
                "settings": {
                    "type": "object",
                    "description": "Autonomy settings to update (for 'set_autonomy')",
                },
            },
            "required": ["action"],
        }

    async def execute(self, action: str, **kwargs: Any) -> Any:
        wm = _get_wingman()
        action = action.lower().strip()
        try:
            if action == "send":
                msg = kwargs.get("message", "")
                if not msg:
                    return "Error: 'message' is required."
                return await wm.send_chat(msg)
            elif action == "history":
                return await wm.get_chat_history()
            elif action == "autonomy":
                return await wm.get_autonomy()
            elif action == "set_autonomy":
                settings = kwargs.get("settings", {})
                if not settings:
                    return "Error: 'settings' dict is required."
                return await wm.set_autonomy(**settings)
            else:
                return f"Unknown action '{action}'. Use: send, history, autonomy, set_autonomy"
        except Exception as e:
            return f"Wingman error: {e}"
