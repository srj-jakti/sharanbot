"""Tandem Browser tool — visual web interaction via Tandem Browser's local API.

Drop-in replacement for the Playwright-based BrowserTool.  When tandem-browser is
running (API on 127.0.0.1:8765), this tool delegates all browser actions to it,
giving the agent a persistent, visible, security-hardened Electron browser.

Falls back gracefully: if tandem-browser is not reachable the tool reports an
error rather than crashing.
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
from sharanbot.tandem.manager import TANDEM_API_BASE, TANDEM_API_PORT


class TandemBrowserTool(Tool):
    """Control Tandem Browser for visual web interaction.

    Provides the same action interface as BrowserTool (navigate, screenshot,
    click, type, get_text, evaluate, wait, scroll, close) but routes everything
    through tandem-browser's REST API on ``127.0.0.1:8765``.
    """

    def __init__(self, screenshots_dir: Path | None = None):
        self._screenshots_dir = screenshots_dir or Path.cwd() / ".sharanbot_screenshots"
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "SharanBot-TandemBrowser/1.0",
        })
        self._update_auth()

    # ------------------------------------------------------------------
    # Tool interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return (
            "Control Tandem Browser for visual web interaction. "
            "Actions: navigate (go to URL), screenshot (capture page), "
            "click (click element by selector), type (fill input field), "
            "get_text (extract visible text), evaluate (run JS), "
            "wait (wait for selector), scroll (scroll page), close (close tab). "
            "Use this to test web applications, verify UI, fill forms, "
            "and visually inspect rendered pages.  Backed by Tandem Browser "
            "(persistent Electron browser with security controls)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "Browser action to perform: navigate, screenshot, click, "
                        "type, get_text, evaluate, wait, scroll, close"
                    ),
                    "enum": [
                        "navigate", "screenshot", "click", "type",
                        "get_text", "evaluate", "wait", "scroll", "close",
                    ],
                },
                "url": {
                    "type": "string",
                    "description": "URL to navigate to (for 'navigate' action)",
                },
                "selector": {
                    "type": "string",
                    "description": "CSS selector for element interaction (click, type, wait)",
                },
                "text": {
                    "type": "string",
                    "description": "Text to type into an input field (for 'type' action)",
                },
                "script": {
                    "type": "string",
                    "description": "JavaScript code to evaluate in the browser (for 'evaluate' action)",
                },
                "direction": {
                    "type": "string",
                    "description": "Scroll direction: up or down (for 'scroll' action)",
                    "enum": ["up", "down"],
                },
                "full_page": {
                    "type": "boolean",
                    "description": "Whether to capture full page screenshot (default: false)",
                },
            },
            "required": ["action"],
        }

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _update_auth(self) -> None:
        """Load tandem API token from disk or env."""
        token = None
        token_path = Path.home() / ".tandem" / "api-token"
        try:
            if token_path.exists():
                t = token_path.read_text().strip()
                if len(t) >= 16:
                    token = t
        except Exception:
            pass
        if not token:
            token = os.environ.get("TANDEM_API_TOKEN")
        if token:
            self._session.headers["Authorization"] = f"Bearer {token}"

    # ------------------------------------------------------------------
    # API helpers
    # ------------------------------------------------------------------

    def _api(
        self,
        method: str,
        endpoint: str,
        json: dict | None = None,
        params: dict | None = None,
        timeout: int = 30,
    ) -> Any:
        """Make a request to the tandem-browser REST API."""
        url = f"{TANDEM_API_BASE}{endpoint}"
        try:
            resp = self._session.request(method, url, json=json, params=params, timeout=timeout)
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "")
            if "application/json" in ct:
                return resp.json()
            return resp.text
        except requests.exceptions.ConnectionError:
            raise RuntimeError(
                f"Cannot connect to Tandem Browser at {TANDEM_API_BASE}. "
                "Make sure tandem-browser is running."
            )
        except requests.exceptions.HTTPError as e:
            raise RuntimeError(f"Tandem API error: {e.response.status_code} — {e.response.text[:300]}")
        except requests.exceptions.Timeout:
            raise RuntimeError(f"Tandem Browser request timed out ({timeout}s)")

    def _is_reachable(self) -> bool:
        try:
            r = requests.get(f"{TANDEM_API_BASE}/health", timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Execute dispatcher
    # ------------------------------------------------------------------

    async def execute(self, action: str, **kwargs: Any) -> Any:
        """Execute a browser action via Tandem Browser API."""
        action = action.lower().strip()

        if not self._is_reachable():
            return (
                "Tandem Browser is not running.  Start it with the gateway "
                "or run `npm start` in the tandem-browser directory."
            )

        dispatch = {
            "navigate": self._navigate,
            "screenshot": self._screenshot,
            "click": self._click,
            "type": self._type,
            "get_text": self._get_text,
            "evaluate": self._evaluate,
            "wait": self._wait,
            "scroll": self._scroll,
            "close": self._close,
        }

        handler = dispatch.get(action)
        if not handler:
            return f"Unknown action '{action}'.  Use: {', '.join(dispatch)}"

        try:
            return handler(**kwargs)
        except RuntimeError as e:
            return str(e)
        except Exception as e:
            return f"Browser error ({action}): {e}"

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _navigate(self, url: str = "", **_: Any) -> str:
        if not url:
            return "Error: 'url' is required for navigate action."
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        result = self._api("POST", "/navigate", json={"url": url})
        title = ""
        if isinstance(result, dict):
            title = result.get("title", "")
        return f"Navigated to {url}\nTitle: {title}"

    def _screenshot(self, full_page: bool = False, **_: Any) -> Any:
        self._screenshots_dir.mkdir(parents=True, exist_ok=True)
        params = {"fullPage": str(full_page).lower()}
        b64_img = self._api("GET", "/screenshot", params=params)

        # Save to disk
        filename = f"screenshot_{int(time.time())}.png"
        path = self._screenshots_dir / filename
        if isinstance(b64_img, str):
            # Strip data-URI prefix if present
            if "," in b64_img:
                b64_img = b64_img.split(",", 1)[1]
            raw = base64.b64decode(b64_img)
            path.write_bytes(raw)
            return [
                {"type": "text", "text": f"Screenshot captured: {filename}"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_img}"}},
            ]
        return f"Screenshot saved: {filename}"

    def _click(self, selector: str = "", **_: Any) -> str:
        if not selector:
            return "Error: 'selector' is required for click action."
        self._api("POST", "/click", json={"selector": selector})
        return f"Clicked element: {selector}"

    def _type(self, selector: str = "", text: str = "", **_: Any) -> str:
        if not selector:
            return "Error: 'selector' is required for type action."
        if not text:
            return "Error: 'text' is required for type action."
        self._api("POST", "/type", json={"selector": selector, "text": text})
        return f"Typed '{text}' into {selector}"

    def _get_text(self, selector: str = "", **_: Any) -> str:
        if selector:
            code = f"""
                (function() {{
                    var el = document.querySelector('{selector}');
                    return el ? el.innerText : '';
                }})()
            """
            result = self._api("POST", "/execute-js", json={"code": code})
        else:
            # Get full page content
            try:
                result = self._api("GET", "/page-content", params={"settle": 500})
                if isinstance(result, dict):
                    result = result.get("text", "")
            except Exception:
                result = self._api("POST", "/execute-js", json={"code": "document.body.innerText"})
        text = str(result) if result else ""
        if len(text) > 15000:
            text = text[:15000] + "\n\n... [truncated, use a more specific selector]"
        return text

    def _evaluate(self, script: str = "", **_: Any) -> str:
        if not script:
            return "Error: 'script' is required for evaluate action."
        result = self._api("POST", "/execute-js", json={"code": script})
        return str(result) if result is not None else "undefined"

    def _wait(self, selector: str = "", **_: Any) -> str:
        if not selector:
            return "Error: 'selector' is required for wait action."
        self._api("POST", "/wait-for-element", json={"selector": selector, "timeout": 15000})
        return f"Element found: {selector}"

    def _scroll(self, direction: str = "down", **_: Any) -> str:
        amount = 500 if direction == "down" else -500
        self._api("POST", "/execute-js", json={"code": f"window.scrollBy(0, {amount})"})
        pos = self._api("POST", "/execute-js", json={"code": "window.scrollY"})
        return f"Scrolled {direction}. Current position: {pos}px"

    def _close(self, **_: Any) -> str:
        """Close the active tab (not the whole browser)."""
        try:
            tabs = self._api("GET", "/tabs")
            if isinstance(tabs, list) and tabs:
                active = next((t for t in tabs if t.get("active")), tabs[0])
                tab_id = active.get("id")
                if tab_id:
                    self._api("DELETE", f"/tabs/{tab_id}")
                    return f"Closed tab {tab_id}"
        except Exception:
            pass
        return "No tab to close."
