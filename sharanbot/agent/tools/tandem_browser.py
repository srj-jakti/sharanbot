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
            "Actions: navigate, screenshot, click, type, get_text, evaluate, "
            "wait, scroll, close, snapshot (accessibility tree with @eN refs), "
            "find (semantic element search), open_tab, close_tab, list_tabs, focus_tab, "
            "devtools_console, devtools_network, devtools_dom, extract_content, "
            "search_history, search_bookmarks, network_apis. "
            "Backed by Tandem Browser (persistent Electron with 8-layer security, "
            "DevTools bridge, semantic locators, and network inspection)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "Browser action: navigate, screenshot, click, type, "
                        "get_text, evaluate, wait, scroll, close, "
                        "snapshot, find, find_click, find_fill, "
                        "open_tab, close_tab, list_tabs, focus_tab, "
                        "devtools_console, devtools_network, devtools_dom, devtools_performance, "
                        "extract_content, search_history, search_bookmarks, network_apis, status"
                    ),
                    "enum": [
                        "navigate", "screenshot", "click", "type",
                        "get_text", "evaluate", "wait", "scroll", "close",
                        "snapshot", "find", "find_click", "find_fill",
                        "open_tab", "close_tab", "list_tabs", "focus_tab",
                        "devtools_console", "devtools_network", "devtools_dom",
                        "devtools_performance",
                        "extract_content", "search_history", "search_bookmarks",
                        "network_apis", "status",
                    ],
                },
                "url": {
                    "type": "string",
                    "description": "URL to navigate to (navigate, open_tab)",
                },
                "selector": {
                    "type": "string",
                    "description": "CSS selector or @eN ref for interaction (click, type, wait, find_click, find_fill, devtools_dom)",
                },
                "text": {
                    "type": "string",
                    "description": "Text to type or search query (type, find, find_click, find_fill, search_history, search_bookmarks)",
                },
                "script": {
                    "type": "string",
                    "description": "JavaScript code to evaluate (evaluate action)",
                },
                "direction": {
                    "type": "string",
                    "description": "Scroll direction: up or down",
                    "enum": ["up", "down"],
                },
                "full_page": {
                    "type": "boolean",
                    "description": "Capture full page screenshot (default: false)",
                },
                "tab_id": {
                    "type": "string",
                    "description": "Tab ID (close_tab, focus_tab)",
                },
                "by": {
                    "type": "string",
                    "description": "Locator strategy for find actions: text, role, label, placeholder",
                    "enum": ["text", "role", "label", "placeholder"],
                },
                "level": {
                    "type": "string",
                    "description": "Console log level filter (devtools_console): all, error, warning, info",
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
            # Snapshot / Semantic locators
            "snapshot": self._snapshot,
            "find": self._find,
            "find_click": self._find_click,
            "find_fill": self._find_fill,
            # Tab management
            "open_tab": self._open_tab,
            "close_tab": self._close_tab_by_id,
            "list_tabs": self._list_tabs,
            "focus_tab": self._focus_tab,
            # DevTools bridge
            "devtools_console": self._devtools_console,
            "devtools_network": self._devtools_network,
            "devtools_dom": self._devtools_dom,
            "devtools_performance": self._devtools_performance,
            # Content & data
            "extract_content": self._extract_content,
            "search_history": self._search_history,
            "search_bookmarks": self._search_bookmarks,
            "network_apis": self._network_apis,
            "status": self._status,
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
            tabs = self._api("GET", "/tabs/list")
            tab_list = tabs.get("tabs", tabs) if isinstance(tabs, dict) else tabs
            if isinstance(tab_list, list) and tab_list:
                active = next((t for t in tab_list if t.get("active")), tab_list[0])
                tab_id = active.get("id")
                if tab_id:
                    self._api("POST", "/tabs/close", json={"tabId": tab_id})
                    return f"Closed tab {tab_id}"
        except Exception:
            pass
        return "No tab to close."

    # ------------------------------------------------------------------
    # Snapshot / Semantic Locators (@eN refs)
    # ------------------------------------------------------------------

    def _snapshot(self, **_: Any) -> str:
        """Get accessibility tree snapshot with @eN element references."""
        result = self._api("GET", "/snapshot", params={"compact": "true"})
        if isinstance(result, dict):
            text = result.get("snapshot", result.get("text", str(result)))
        else:
            text = str(result)
        if len(text) > 20000:
            text = text[:20000] + "\n\n... [truncated]"
        return text

    def _find(self, text: str = "", by: str = "text", **_: Any) -> str:
        """Find elements using semantic locator queries."""
        if not text:
            return "Error: 'text' query is required for find action."
        result = self._api("POST", "/find", json={"query": text, "by": by})
        if isinstance(result, dict):
            elements = result.get("elements", [result])
        elif isinstance(result, list):
            elements = result
        else:
            return str(result)
        lines = [f"Found {len(elements)} element(s):"]
        for el in elements[:20]:
            ref = el.get("ref", "?")
            tag = el.get("tag", el.get("role", "?"))
            txt = el.get("text", el.get("name", ""))[:80]
            lines.append(f"  {ref} <{tag}> {txt}")
        return "\n".join(lines)

    def _find_click(self, text: str = "", by: str = "text", **_: Any) -> str:
        """Find element by semantic query and click it."""
        if not text:
            return "Error: 'text' query is required."
        result = self._api("POST", "/find/click", json={"query": text, "by": by})
        return f"Clicked element matching '{text}' (by {by})"

    def _find_fill(self, selector: str = "", text: str = "", by: str = "text", **_: Any) -> str:
        """Find an input element and fill it with text."""
        if not selector or not text:
            return "Error: 'selector' (query) and 'text' are required."
        result = self._api("POST", "/find/fill", json={"query": selector, "value": text, "by": by})
        return f"Filled '{selector}' with '{text}'"

    # ------------------------------------------------------------------
    # Tab Management
    # ------------------------------------------------------------------

    def _open_tab(self, url: str = "", **_: Any) -> str:
        """Open a new browser tab."""
        payload: dict[str, Any] = {"focus": True}
        if url:
            payload["url"] = url
        result = self._api("POST", "/tabs/open", json=payload)
        tab_id = result.get("id", "?") if isinstance(result, dict) else "?"
        return f"Opened new tab {tab_id}" + (f" → {url}" if url else "")

    def _close_tab_by_id(self, tab_id: str = "", **_: Any) -> str:
        """Close a tab by ID."""
        if not tab_id:
            return "Error: 'tab_id' is required."
        self._api("POST", "/tabs/close", json={"tabId": tab_id})
        return f"Closed tab {tab_id}"

    def _list_tabs(self, **_: Any) -> str:
        """List all open browser tabs."""
        result = self._api("GET", "/tabs/list")
        tab_list = result.get("tabs", result) if isinstance(result, dict) else result
        if not tab_list:
            return "No tabs open."
        lines = ["**Open Tabs:**"]
        for t in (tab_list if isinstance(tab_list, list) else [tab_list]):
            tid = t.get("id", "?")
            title = t.get("title", "")[:50]
            url = t.get("url", "")[:60]
            active = " ← active" if t.get("active") else ""
            lines.append(f"  [{tid}] {title} ({url}){active}")
        return "\n".join(lines)

    def _focus_tab(self, tab_id: str = "", **_: Any) -> str:
        """Switch to a specific tab."""
        if not tab_id:
            return "Error: 'tab_id' is required."
        self._api("POST", "/tabs/focus", json={"tabId": tab_id})
        return f"Focused tab {tab_id}"

    # ------------------------------------------------------------------
    # DevTools Bridge
    # ------------------------------------------------------------------

    def _devtools_console(self, level: str = "all", **_: Any) -> str:
        """Get browser console output."""
        endpoint = "/devtools/console/errors" if level == "error" else "/devtools/console"
        params = {} if level in ("all", "error") else {"level": level}
        result = self._api("GET", endpoint, params=params)
        entries = result if isinstance(result, list) else result.get("entries", result.get("logs", []))
        if not entries:
            return "No console output."
        lines = ["**Console Output:**"]
        for e in (entries[-30:] if isinstance(entries, list) else [entries]):
            if isinstance(e, dict):
                lvl = e.get("level", "log")
                msg = e.get("text", e.get("message", str(e)))[:200]
                lines.append(f"  [{lvl}] {msg}")
            else:
                lines.append(f"  {str(e)[:200]}")
        return "\n".join(lines)

    def _devtools_network(self, **_: Any) -> str:
        """Get recent network requests."""
        result = self._api("GET", "/devtools/network")
        entries = result if isinstance(result, list) else result.get("entries", result.get("requests", []))
        if not entries:
            return "No network requests logged."
        lines = ["**Network Requests:**"]
        for e in (entries[-20:] if isinstance(entries, list) else [entries]):
            if isinstance(e, dict):
                method = e.get("method", "?")
                url = e.get("url", "")[:80]
                status = e.get("status", e.get("statusCode", "?"))
                lines.append(f"  {method} {url} → {status}")
            else:
                lines.append(f"  {str(e)[:100]}")
        return "\n".join(lines)

    def _devtools_dom(self, selector: str = "", **_: Any) -> str:
        """Query DOM elements by CSS selector or XPath."""
        if not selector:
            return "Error: 'selector' is required for devtools_dom."
        is_xpath = selector.startswith("/") or selector.startswith("(")
        endpoint = "/devtools/dom/xpath" if is_xpath else "/devtools/dom/query"
        result = self._api("POST", endpoint, json={"selector" if not is_xpath else "expression": selector})
        if isinstance(result, dict):
            nodes = result.get("nodes", result.get("elements", [result]))
        elif isinstance(result, list):
            nodes = result
        else:
            return str(result)
        lines = [f"Found {len(nodes)} node(s):"]
        for n in nodes[:15]:
            if isinstance(n, dict):
                tag = n.get("nodeName", n.get("tag", "?"))
                text = n.get("textContent", n.get("text", ""))[:100]
                lines.append(f"  <{tag}> {text}")
            else:
                lines.append(f"  {str(n)[:100]}")
        return "\n".join(lines)

    def _devtools_performance(self, **_: Any) -> str:
        """Get page performance metrics."""
        result = self._api("GET", "/devtools/performance")
        if isinstance(result, dict):
            lines = ["**Performance Metrics:**"]
            metrics = result.get("metrics", result)
            for k, v in (metrics.items() if isinstance(metrics, dict) else []):
                lines.append(f"  {k}: {v}")
            return "\n".join(lines)
        return str(result)

    # ------------------------------------------------------------------
    # Content & Data
    # ------------------------------------------------------------------

    def _extract_content(self, **_: Any) -> str:
        """Extract current page content as clean markdown."""
        result = self._api("POST", "/content/extract")
        if isinstance(result, dict):
            md = result.get("markdown", result.get("content", result.get("text", "")))
        else:
            md = str(result)
        if len(md) > 30000:
            md = md[:30000] + "\n\n... [truncated]"
        return md

    def _search_history(self, text: str = "", **_: Any) -> str:
        """Search browsing history."""
        if not text:
            return "Error: 'text' search query is required."
        result = self._api("GET", "/history/search", params={"query": text})
        entries = result if isinstance(result, list) else result.get("entries", result.get("results", []))
        if not entries:
            return f"No history matches for '{text}'."
        lines = [f"**History search: '{text}'**"]
        for e in (entries[:15] if isinstance(entries, list) else [entries]):
            if isinstance(e, dict):
                title = e.get("title", "")[:50]
                url = e.get("url", "")[:60]
                lines.append(f"  {title} — {url}")
            else:
                lines.append(f"  {str(e)[:100]}")
        return "\n".join(lines)

    def _search_bookmarks(self, text: str = "", **_: Any) -> str:
        """Search bookmarks."""
        if not text:
            # List all bookmarks
            result = self._api("GET", "/bookmarks")
        else:
            result = self._api("GET", "/bookmarks", params={"search": text})
        entries = result if isinstance(result, list) else result.get("bookmarks", [])
        if not entries:
            return "No bookmarks found."
        lines = ["**Bookmarks:**"]
        for b in (entries[:20] if isinstance(entries, list) else [entries]):
            if isinstance(b, dict):
                title = b.get("title", "")[:50]
                url = b.get("url", "")[:60]
                lines.append(f"  {title} — {url}")
            else:
                lines.append(f"  {str(b)[:100]}")
        return "\n".join(lines)

    def _network_apis(self, **_: Any) -> str:
        """Get detected API endpoints from network traffic."""
        result = self._api("GET", "/network/apis")
        entries = result if isinstance(result, list) else result.get("apis", result.get("endpoints", []))
        if not entries:
            return "No API endpoints detected."
        lines = ["**Detected APIs:**"]
        for e in (entries[:30] if isinstance(entries, list) else [entries]):
            if isinstance(e, dict):
                method = e.get("method", "?")
                url = e.get("url", e.get("endpoint", ""))[:80]
                lines.append(f"  {method} {url}")
            else:
                lines.append(f"  {str(e)[:100]}")
        return "\n".join(lines)

    def _status(self, **_: Any) -> str:
        """Get tandem-browser status and health info."""
        result = self._api("GET", "/status")
        if isinstance(result, dict):
            lines = ["**Tandem Browser Status:**"]
            for k, v in result.items():
                if isinstance(v, dict):
                    lines.append(f"  {k}:")
                    for sk, sv in v.items():
                        lines.append(f"    {sk}: {sv}")
                else:
                    lines.append(f"  {k}: {v}")
            return "\n".join(lines)
        return str(result)
