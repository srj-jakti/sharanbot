"""Tests for the Computer tool and OpenClaw bridge."""

from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from sharanbot.agent.tools.computer import ComputerTool
from sharanbot.tandem.openclaw_bridge import OpenClawBridge, OPENCLAW_PORT


# ── Computer Tool Tests ──────────────────────────────────────────────

class TestComputerToolMeta:
    """Test tool metadata and schema."""

    def test_name(self):
        tool = ComputerTool()
        assert tool.name == "computer"

    def test_description_mentions_mouse_keyboard_screenshot(self):
        tool = ComputerTool()
        desc = tool.description.lower()
        assert "mouse" in desc
        assert "keyboard" in desc
        assert "screenshot" in desc

    def test_parameters_has_action(self):
        tool = ComputerTool()
        props = tool.parameters["properties"]
        assert "action" in props
        assert "enum" in props["action"]
        actions = props["action"]["enum"]
        assert "screenshot" in actions
        assert "mouse_click" in actions
        assert "key_press" in actions
        assert "type_text" in actions
        assert "scroll" in actions

    def test_parameters_has_coordinates(self):
        tool = ComputerTool()
        props = tool.parameters["properties"]
        assert "x" in props
        assert "y" in props
        assert props["x"]["type"] == "integer"

    def test_parameters_has_key_fields(self):
        tool = ComputerTool()
        props = tool.parameters["properties"]
        assert "key" in props
        assert "keys" in props
        assert "text" in props

    def test_parameters_has_scroll_fields(self):
        tool = ComputerTool()
        props = tool.parameters["properties"]
        assert "direction" in props
        assert "amount" in props


class TestComputerToolExecute:
    """Test execute method with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        tool = ComputerTool()
        result = await tool.execute(action="fly")
        assert "Unknown action" in result

    @pytest.mark.asyncio
    async def test_mouse_click_missing_coords(self):
        tool = ComputerTool()
        result = await tool.execute(action="mouse_click")
        assert "x and y" in result

    @pytest.mark.asyncio
    async def test_key_press_missing_key(self):
        tool = ComputerTool()
        result = await tool.execute(action="key_press")
        assert "key required" in result

    @pytest.mark.asyncio
    async def test_type_text_missing_text(self):
        tool = ComputerTool()
        result = await tool.execute(action="type_text")
        assert "text required" in result

    @pytest.mark.asyncio
    async def test_key_combo_missing_keys(self):
        tool = ComputerTool()
        result = await tool.execute(action="key_combo")
        assert "keys required" in result

    @pytest.mark.asyncio
    async def test_key_combo_needs_modifier(self):
        tool = ComputerTool()
        result = await tool.execute(action="key_combo", keys="c")
        assert "modifier+key" in result

    @pytest.mark.asyncio
    async def test_mouse_drag_missing_coords(self):
        tool = ComputerTool()
        result = await tool.execute(action="mouse_drag")
        assert "start_x" in result

    @pytest.mark.asyncio
    async def test_mouse_move_missing_coords(self):
        tool = ComputerTool()
        result = await tool.execute(action="mouse_move")
        assert "x and y" in result

    @pytest.mark.asyncio
    async def test_connection_error_handled(self):
        tool = ComputerTool()
        import requests
        with patch.object(tool._session, "post", side_effect=requests.ConnectionError):
            result = await tool.execute(action="mouse_click", x=100, y=200)
            assert "not reachable" in result

    @pytest.mark.asyncio
    async def test_screenshot_returns_image_block(self):
        tool = ComputerTool()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        with patch.object(tool._session, "get", return_value=mock_resp):
            result = await tool.execute(action="screenshot")
            assert isinstance(result, list)
            assert any(b.get("type") == "image_url" for b in result)

    @pytest.mark.asyncio
    async def test_mouse_click_success(self):
        tool = ComputerTool()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True}
        with patch.object(tool._session, "post", return_value=mock_resp):
            result = await tool.execute(action="mouse_click", x=100, y=200)
            assert "Clicked at (100, 200)" in result

    @pytest.mark.asyncio
    async def test_key_combo_ctrl_c(self):
        tool = ComputerTool()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch.object(tool._session, "post", return_value=mock_resp):
            result = await tool.execute(action="key_combo", keys="ctrl+c")
            assert "ctrl+c" in result

    @pytest.mark.asyncio
    async def test_scroll_success(self):
        tool = ComputerTool()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True, "scroll": {"scrollTop": 500, "scrollHeight": 2000}}
        with patch.object(tool._session, "post", return_value=mock_resp):
            result = await tool.execute(action="scroll", direction="down", amount=300)
            assert "Scrolled down" in result


# ── OpenClaw Bridge Tests ────────────────────────────────────────────

class TestOpenClawBridge:
    """Test OpenClaw bridge initialization and protocol."""

    def test_default_port(self):
        bridge = OpenClawBridge()
        assert bridge.port == OPENCLAW_PORT

    def test_custom_port(self):
        bridge = OpenClawBridge(port=19999)
        assert bridge.port == 19999

    def test_custom_callback(self):
        async def my_cb(s, m):
            return "hello"
        bridge = OpenClawBridge(on_chat=my_cb)
        assert bridge._on_chat is my_cb

    @pytest.mark.asyncio
    async def test_handle_message_connect(self):
        bridge = OpenClawBridge()
        ws = AsyncMock()
        ws.closed = False
        await bridge._handle_message(ws, json.dumps({
            "type": "req",
            "id": "test-id",
            "method": "connect",
            "params": {"token": "abc"},
        }))
        ws.send_json.assert_called_once()
        call_data = ws.send_json.call_args[0][0]
        assert call_data["type"] == "res"
        assert call_data["id"] == "test-id"
        assert call_data["ok"] is True

    @pytest.mark.asyncio
    async def test_handle_message_history(self):
        bridge = OpenClawBridge()
        ws = AsyncMock()
        ws.closed = False
        await bridge._handle_message(ws, json.dumps({
            "type": "req",
            "id": "hist-id",
            "method": "chat.history",
            "params": {"sessionKey": "test", "limit": 5},
        }))
        ws.send_json.assert_called_once()
        call_data = ws.send_json.call_args[0][0]
        assert call_data["type"] == "res"
        assert "messages" in call_data["payload"]

    @pytest.mark.asyncio
    async def test_handle_message_unknown_method(self):
        bridge = OpenClawBridge()
        ws = AsyncMock()
        ws.closed = False
        await bridge._handle_message(ws, json.dumps({
            "type": "req",
            "id": "unk-id",
            "method": "nonexistent",
            "params": {},
        }))
        ws.send_json.assert_called_once()
        call_data = ws.send_json.call_args[0][0]
        assert call_data["ok"] is False

    @pytest.mark.asyncio
    async def test_handle_invalid_json(self):
        bridge = OpenClawBridge()
        ws = AsyncMock()
        # Should not raise on invalid JSON
        await bridge._handle_message(ws, "not json {{{}}")
        ws.send_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_non_req_type(self):
        bridge = OpenClawBridge()
        ws = AsyncMock()
        await bridge._handle_message(ws, json.dumps({"type": "event"}))
        ws.send_json.assert_not_called()
