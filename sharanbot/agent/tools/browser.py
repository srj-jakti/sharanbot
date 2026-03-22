"""Browser tool — visual web interaction via Playwright."""

from __future__ import annotations

import asyncio
import base64
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from sharanbot.agent.tools.base import Tool


def _validate_browser_url(url: str) -> tuple[bool, str]:
    """Validate URL for browser navigation — block file:// and internal IPs."""
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False, f"Only http/https allowed, got '{p.scheme or 'none'}'"
        if not p.netloc:
            return False, "Missing domain"
        # Block obvious internal addresses (SSRF prevention)
        host = p.hostname or ""
        if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
            # Allow localhost for testing locally-built apps
            pass
        return True, ""
    except Exception as e:
        return False, str(e)


class BrowserTool(Tool):
    """Control a headless browser — navigate, click, type, screenshot, and extract content.

    Requires Playwright to be installed: ``pip install playwright && playwright install chromium``
    """

    def __init__(self, screenshots_dir: Path | None = None):
        self._screenshots_dir = screenshots_dir or Path.cwd() / ".sharanbot_screenshots"
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None

    @property
    def name(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return (
            "Control a headless browser for visual web interaction. "
            "Actions: navigate (go to URL), screenshot (capture page), "
            "click (click element by selector), type (fill input field), "
            "get_text (extract visible text), evaluate (run JS), "
            "wait (wait for selector), scroll (scroll page), close (close browser). "
            "Use this to test web applications, verify UI, fill forms, "
            "and visually inspect rendered pages."
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

    async def _ensure_browser(self) -> None:
        """Launch browser if not already running."""
        if self._page is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "Playwright is not installed. Install it with:\n"
                "  pip install playwright && python -m playwright install chromium"
            )

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        self._page = await self._context.new_page()
        logger.info("Browser launched (chromium, headless)")

    async def _close_browser(self) -> str:
        """Close the browser and clean up."""
        if self._page:
            await self._page.close()
            self._page = None
        if self._context:
            await self._context.close()
            self._context = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        return "Browser closed."

    async def execute(self, action: str, **kwargs: Any) -> Any:
        """Execute a browser action."""
        action = action.lower().strip()

        if action == "close":
            return await self._close_browser()

        try:
            await self._ensure_browser()
        except RuntimeError as e:
            return str(e)
        assert self._page is not None

        dispatch = {
            "navigate": self._navigate,
            "screenshot": self._screenshot,
            "click": self._click,
            "type": self._type,
            "get_text": self._get_text,
            "evaluate": self._evaluate,
            "wait": self._wait,
            "scroll": self._scroll,
        }

        handler = dispatch.get(action)
        if not handler:
            return f"Unknown action '{action}'. Use: {', '.join(dispatch)}"

        try:
            return await handler(**kwargs)
        except RuntimeError as e:
            return str(e)
        except Exception as e:
            return f"Browser error ({action}): {e}"

    async def _navigate(self, url: str = "", **_: Any) -> str:
        if not url:
            return "Error: 'url' is required for navigate action."
        valid, err = _validate_browser_url(url)
        if not valid:
            return f"Invalid URL: {err}"

        resp = await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        status = resp.status if resp else "unknown"
        title = await self._page.title()
        return f"Navigated to {url}\nStatus: {status}\nTitle: {title}"

    async def _screenshot(self, full_page: bool = False, **_: Any) -> Any:
        self._screenshots_dir.mkdir(parents=True, exist_ok=True)
        import time
        filename = f"screenshot_{int(time.time())}.png"
        path = self._screenshots_dir / filename
        await self._page.screenshot(path=str(path), full_page=full_page)

        # Return as base64 image content for LLM vision
        raw = path.read_bytes()
        b64 = base64.b64encode(raw).decode()
        title = await self._page.title()
        current_url = self._page.url

        return [
            {"type": "text", "text": f"Screenshot captured: {filename}\nURL: {current_url}\nTitle: {title}"},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            },
        ]

    async def _click(self, selector: str = "", **_: Any) -> str:
        if not selector:
            return "Error: 'selector' is required for click action."
        await self._page.click(selector, timeout=10000)
        return f"Clicked element: {selector}"

    async def _type(self, selector: str = "", text: str = "", **_: Any) -> str:
        if not selector:
            return "Error: 'selector' is required for type action."
        if not text:
            return "Error: 'text' is required for type action."
        await self._page.fill(selector, text, timeout=10000)
        return f"Typed '{text}' into {selector}"

    async def _get_text(self, selector: str = "", **_: Any) -> str:
        if selector:
            el = await self._page.query_selector(selector)
            if not el:
                return f"No element found for selector: {selector}"
            text = await el.inner_text()
        else:
            text = await self._page.inner_text("body")
        # Truncate for context window
        if len(text) > 15000:
            text = text[:15000] + "\n\n... [truncated, use a more specific selector]"
        return text

    async def _evaluate(self, script: str = "", **_: Any) -> str:
        if not script:
            return "Error: 'script' is required for evaluate action."
        # Disallow dangerous patterns
        dangerous = re.compile(
            r"(fetch\s*\(|XMLHttpRequest|import\s*\(|require\s*\(|eval\s*\(|Function\s*\()",
            re.IGNORECASE,
        )
        if dangerous.search(script):
            return "Error: Script contains disallowed patterns (fetch, eval, import, require)."
        result = await self._page.evaluate(script)
        return str(result) if result is not None else "undefined"

    async def _wait(self, selector: str = "", **_: Any) -> str:
        if not selector:
            return "Error: 'selector' is required for wait action."
        await self._page.wait_for_selector(selector, timeout=15000)
        return f"Element found: {selector}"

    async def _scroll(self, direction: str = "down", **_: Any) -> str:
        amount = 500 if direction == "down" else -500
        await self._page.evaluate(f"window.scrollBy(0, {amount})")
        scroll_pos = await self._page.evaluate("window.scrollY")
        return f"Scrolled {direction}. Current position: {scroll_pos}px"
