"""Computer tool — combined mouse, keyboard, and screenshot interaction.

Provides coordinate-based visual interaction with Tandem Browser.  The agent
takes screenshots to see the screen, then uses mouse clicks, keyboard input,
and scrolling to interact — similar to Anthropic's computer-use tool.

Available to SharanBot, all workers, and Wingman.
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any

import requests
from loguru import logger

from sharanbot.agent.tools.base import Tool
from sharanbot.tandem.manager import TANDEM_API_BASE


class ComputerTool(Tool):
    """Mouse + keyboard + screenshot interaction with Tandem Browser."""

    def __init__(self, screenshots_dir: Path | None = None):
        self._screenshots_dir = screenshots_dir or Path.cwd() / ".sharanbot_screenshots"
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "SharanBot-Computer/1.0",
        })
        self._update_auth()

    def _update_auth(self) -> None:
        token_path = Path.home() / ".tandem" / "api-token"
        if token_path.exists():
            token = token_path.read_text().strip()
            self._session.headers["Authorization"] = f"Bearer {token}"

    @property
    def name(self) -> str:
        return "computer"

    @property
    def description(self) -> str:
        return (
            "Interact with the browser using mouse, keyboard, and screenshots. "
            "Take a screenshot to see the screen, then click at coordinates, "
            "type text, press keys, scroll, or drag. "
            "Actions: screenshot, mouse_click, mouse_double_click, mouse_move, "
            "mouse_drag, key_press, key_combo, type_text, scroll."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "Action to perform: screenshot, mouse_click, "
                        "mouse_double_click, mouse_move, mouse_drag, "
                        "key_press, key_combo, type_text, scroll"
                    ),
                    "enum": [
                        "screenshot", "mouse_click", "mouse_double_click",
                        "mouse_move", "mouse_drag", "key_press", "key_combo",
                        "type_text", "scroll",
                    ],
                },
                "x": {
                    "type": "integer",
                    "description": "X coordinate for mouse actions",
                },
                "y": {
                    "type": "integer",
                    "description": "Y coordinate for mouse actions",
                },
                "start_x": {
                    "type": "integer",
                    "description": "Start X for mouse_drag",
                },
                "start_y": {
                    "type": "integer",
                    "description": "Start Y for mouse_drag",
                },
                "end_x": {
                    "type": "integer",
                    "description": "End X for mouse_drag",
                },
                "end_y": {
                    "type": "integer",
                    "description": "End Y for mouse_drag",
                },
                "button": {
                    "type": "string",
                    "description": "Mouse button: left, right, middle",
                    "enum": ["left", "right", "middle"],
                },
                "key": {
                    "type": "string",
                    "description": "Key name for key_press (e.g. Enter, Tab, Escape, Backspace, a, b)",
                },
                "keys": {
                    "type": "string",
                    "description": "Key combination for key_combo (e.g. 'ctrl+c', 'ctrl+shift+t', 'alt+F4')",
                },
                "text": {
                    "type": "string",
                    "description": "Text to type for type_text action",
                },
                "direction": {
                    "type": "string",
                    "description": "Scroll direction: up or down",
                    "enum": ["up", "down"],
                },
                "amount": {
                    "type": "integer",
                    "description": "Scroll amount in pixels (default 500)",
                },
            },
            "required": ["action"],
        }

    async def execute(self, **kwargs: Any) -> Any:
        action = kwargs.get("action", "")
        self._update_auth()

        try:
            if action == "screenshot":
                return await self._screenshot()
            elif action == "mouse_click":
                return await self._mouse_click(kwargs)
            elif action == "mouse_double_click":
                return await self._mouse_double_click(kwargs)
            elif action == "mouse_move":
                return await self._mouse_move(kwargs)
            elif action == "mouse_drag":
                return await self._mouse_drag(kwargs)
            elif action == "key_press":
                return await self._key_press(kwargs)
            elif action == "key_combo":
                return await self._key_combo(kwargs)
            elif action == "type_text":
                return await self._type_text(kwargs)
            elif action == "scroll":
                return await self._scroll(kwargs)
            else:
                return f"Unknown action: {action}"
        except requests.ConnectionError:
            return "Error: Tandem Browser not reachable. Is it running?"
        except Exception as e:
            return f"Error: {e}"

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def _screenshot(self) -> list[dict[str, Any]]:
        """Take a screenshot and return as image content block."""
        r = self._session.get(f"{TANDEM_API_BASE}/screenshot", timeout=10)
        if r.status_code != 200:
            return [{"type": "text", "text": f"Screenshot failed: {r.status_code}"}]

        png_data = r.content
        b64 = base64.b64encode(png_data).decode("ascii")

        # Save to disk for reference
        self._screenshots_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        filepath = self._screenshots_dir / f"screen_{ts}.png"
        filepath.write_bytes(png_data)

        return [
            {"type": "text", "text": f"Screenshot taken ({len(png_data)} bytes). Saved to {filepath.name}"},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            },
        ]

    async def _mouse_click(self, params: dict) -> str:
        x = params.get("x")
        y = params.get("y")
        if x is None or y is None:
            return "Error: x and y coordinates required for mouse_click"
        button = params.get("button", "left")
        r = self._session.post(
            f"{TANDEM_API_BASE}/mouse/click",
            json={"x": x, "y": y, "button": button, "clickCount": 1},
            timeout=10,
        )
        if r.status_code == 200:
            return f"Clicked at ({x}, {y}) with {button} button"
        return f"Click failed: {r.text}"

    async def _mouse_double_click(self, params: dict) -> str:
        x = params.get("x")
        y = params.get("y")
        if x is None or y is None:
            return "Error: x and y coordinates required for mouse_double_click"
        button = params.get("button", "left")
        r = self._session.post(
            f"{TANDEM_API_BASE}/mouse/click",
            json={"x": x, "y": y, "button": button, "clickCount": 2},
            timeout=10,
        )
        if r.status_code == 200:
            return f"Double-clicked at ({x}, {y})"
        return f"Double-click failed: {r.text}"

    async def _mouse_move(self, params: dict) -> str:
        x = params.get("x")
        y = params.get("y")
        if x is None or y is None:
            return "Error: x and y coordinates required for mouse_move"
        r = self._session.post(
            f"{TANDEM_API_BASE}/mouse/move",
            json={"x": x, "y": y},
            timeout=10,
        )
        if r.status_code == 200:
            return f"Mouse moved to ({x}, {y})"
        return f"Mouse move failed: {r.text}"

    async def _mouse_drag(self, params: dict) -> str:
        sx = params.get("start_x")
        sy = params.get("start_y")
        ex = params.get("end_x")
        ey = params.get("end_y")
        if None in (sx, sy, ex, ey):
            return "Error: start_x, start_y, end_x, end_y required for mouse_drag"
        r = self._session.post(
            f"{TANDEM_API_BASE}/mouse/drag",
            json={"startX": sx, "startY": sy, "endX": ex, "endY": ey},
            timeout=10,
        )
        if r.status_code == 200:
            return f"Dragged from ({sx}, {sy}) to ({ex}, {ey})"
        return f"Drag failed: {r.text}"

    async def _key_press(self, params: dict) -> str:
        key = params.get("key")
        if not key:
            return "Error: key required for key_press"
        r = self._session.post(
            f"{TANDEM_API_BASE}/keyboard/press",
            json={"key": key},
            timeout=10,
        )
        if r.status_code == 200:
            return f"Pressed key: {key}"
        return f"Key press failed: {r.text}"

    async def _key_combo(self, params: dict) -> str:
        keys_str = params.get("keys", "")
        if not keys_str:
            return "Error: keys required for key_combo (e.g. 'ctrl+c')"

        parts = [k.strip().lower() for k in keys_str.split("+")]
        if len(parts) < 2:
            return "Error: key_combo needs modifier+key (e.g. 'ctrl+c')"

        # Last part is the key, rest are modifiers
        key = parts[-1]
        modifier_map = {
            "ctrl": "control",
            "control": "control",
            "shift": "shift",
            "alt": "alt",
            "meta": "meta",
            "cmd": "meta",
            "win": "meta",
        }
        modifiers = []
        for p in parts[:-1]:
            mod = modifier_map.get(p)
            if mod:
                modifiers.append(mod)
            else:
                return f"Error: unknown modifier '{p}'"

        r = self._session.post(
            f"{TANDEM_API_BASE}/keyboard/press",
            json={"key": key, "modifiers": modifiers},
            timeout=10,
        )
        if r.status_code == 200:
            return f"Pressed key combo: {keys_str}"
        return f"Key combo failed: {r.text}"

    async def _type_text(self, params: dict) -> str:
        text = params.get("text", "")
        if not text:
            return "Error: text required for type_text"
        r = self._session.post(
            f"{TANDEM_API_BASE}/keyboard/type",
            json={"text": text},
            timeout=30,
        )
        if r.status_code == 200:
            return f"Typed {len(text)} characters"
        return f"Type failed: {r.text}"

    async def _scroll(self, params: dict) -> str:
        direction = params.get("direction", "down")
        amount = params.get("amount", 500)
        r = self._session.post(
            f"{TANDEM_API_BASE}/scroll",
            json={"direction": direction, "amount": amount},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            scroll_info = data.get("scroll", {})
            return (
                f"Scrolled {direction} by {amount}px. "
                f"Position: {scroll_info.get('scrollTop', '?')}/"
                f"{scroll_info.get('scrollHeight', '?')}px"
            )
        return f"Scroll failed: {r.text}"
