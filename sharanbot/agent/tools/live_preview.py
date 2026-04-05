"""LivePreviewManager — automated build → preview → iterate loop.

Chains process_start → wait_for_port → browser.navigate → browser.screenshot
to give instant visual feedback after scaffolding or code changes.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

from loguru import logger

from sharanbot.agent.tools.base import Tool


# ── Framework detection and dev server configs ────────────────────────

_FRAMEWORK_CONFIGS: dict[str, dict[str, Any]] = {
    "nextjs": {
        "detect_files": ["next.config.js", "next.config.mjs", "next.config.ts"],
        "dev_cmd": "npm run dev",
        "port": 3000,
        "ready_pattern": "ready",
        "wait_timeout": 60,
    },
    "vite": {
        "detect_files": ["vite.config.ts", "vite.config.js", "vite.config.mjs"],
        "dev_cmd": "npm run dev",
        "port": 5173,
        "ready_pattern": "VITE v",
        "wait_timeout": 45,
    },
    "expo": {
        "detect_files": ["app.json", "expo.json"],
        "detect_deps": ["expo"],
        "dev_cmd": "npx expo start --web",
        "port": 8081,
        "ready_pattern": "Logs for your project",
        "wait_timeout": 60,
    },
    "django": {
        "detect_files": ["manage.py"],
        "detect_content": {"manage.py": "DJANGO_SETTINGS_MODULE"},
        "dev_cmd": "python manage.py runserver",
        "port": 8000,
        "ready_pattern": "Starting development server",
        "wait_timeout": 30,
    },
    "flask": {
        "detect_deps": ["flask"],
        "dev_cmd": "flask run",
        "port": 5000,
        "ready_pattern": "Running on",
        "wait_timeout": 20,
    },
    "fastapi": {
        "detect_deps": ["fastapi"],
        "dev_cmd": "uvicorn main:app --reload",
        "port": 8000,
        "ready_pattern": "Uvicorn running",
        "wait_timeout": 20,
    },
    "express": {
        "detect_deps": ["express"],
        "dev_cmd": "npm start",
        "port": 3000,
        "ready_pattern": "listening",
        "wait_timeout": 20,
    },
    "flutter": {
        "detect_files": ["pubspec.yaml"],
        "dev_cmd": "flutter run -d chrome",
        "port": 8080,
        "ready_pattern": "is being served",
        "wait_timeout": 120,
    },
}


def detect_framework(project_path: Path) -> str | None:
    """Auto-detect the framework from project files."""
    for name, config in _FRAMEWORK_CONFIGS.items():
        # Check for marker files
        for f in config.get("detect_files", []):
            if (project_path / f).exists():
                # Extra content check if needed
                content_checks = config.get("detect_content", {})
                if content_checks:
                    for check_file, check_str in content_checks.items():
                        fp = project_path / check_file
                        if fp.exists() and check_str in fp.read_text(errors="ignore"):
                            return name
                    continue
                return name

        # Check package.json dependencies
        deps_to_check = config.get("detect_deps", [])
        if deps_to_check:
            pkg_json = project_path / "package.json"
            if pkg_json.exists():
                try:
                    import json
                    pkg = json.loads(pkg_json.read_text())
                    all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                    if any(d in all_deps for d in deps_to_check):
                        return name
                except Exception:
                    pass

    return None


class LivePreviewTool(Tool):
    """Start a dev server, wait for it, navigate browser, and take a screenshot."""

    def __init__(self, screenshots_dir: Path | None = None):
        self._screenshots_dir = screenshots_dir

    @property
    def name(self) -> str:
        return "live_preview"

    @property
    def description(self) -> str:
        return (
            "Start a dev server for a project, wait for it to be ready, "
            "open it in the browser, and take a screenshot. "
            "Auto-detects the framework (Next.js, Vite, Django, Flask, FastAPI, Expo, Flutter). "
            "Actions: start (launch preview), refresh (re-screenshot), stop (kill server). "
            "Use after scaffolding or code changes to see the result visually."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["start", "refresh", "stop", "status"],
                    "description": "Preview action: start, refresh, stop, status.",
                },
                "project_path": {
                    "type": "string",
                    "description": "Path to the project directory.",
                },
                "framework": {
                    "type": "string",
                    "description": "Override framework detection (nextjs, vite, django, etc.).",
                },
                "port": {
                    "type": "integer",
                    "description": "Override the default port.",
                },
                "dev_cmd": {
                    "type": "string",
                    "description": "Override the dev server command.",
                },
                "url_path": {
                    "type": "string",
                    "description": "URL path to navigate to after server starts (default: /).",
                },
                "install_first": {
                    "type": "boolean",
                    "description": "Run npm install / pip install before starting (default: true).",
                },
            },
            "required": ["action"],
        }

    async def execute(self, action: str, **kwargs: Any) -> Any:
        dispatch = {
            "start": self._start,
            "refresh": self._refresh,
            "stop": self._stop,
            "status": self._status,
        }
        handler = dispatch.get(action)
        if not handler:
            return f"Unknown action '{action}'. Use: start, refresh, stop, status."
        return await handler(**kwargs)

    async def _start(self, project_path: str = "", framework: str = "",
                     port: int = 0, dev_cmd: str = "", url_path: str = "/",
                     install_first: bool = True, **_: Any) -> Any:
        if not project_path:
            return "Error: project_path is required for start."

        root = Path(project_path)
        if not root.is_absolute():
            workspace = Path(os.environ.get("SHARANBOT_WORKSPACE", "."))
            root = workspace / root

        if not root.exists():
            return f"Error: Project directory not found: {root}"

        # Detect framework
        fw = framework or detect_framework(root)
        if not fw:
            return (
                f"Could not detect framework in {root}. "
                "Specify framework= manually (nextjs, vite, django, flask, fastapi, expo, flutter)."
            )

        config = _FRAMEWORK_CONFIGS.get(fw, {})
        actual_port = port or config.get("port", 3000)
        actual_cmd = dev_cmd or config.get("dev_cmd", "npm run dev")
        ready_pattern = config.get("ready_pattern", "ready")
        wait_timeout = config.get("wait_timeout", 45)

        results = [f"🔍 Detected framework: {fw}"]

        # Install dependencies first
        if install_first:
            install_result = await self._install_deps(root, fw)
            if install_result:
                results.append(install_result)

        # Start dev server via process_start
        from sharanbot.agent.tools.process import (
            ProcessStartTool, _PROCESS_REGISTRY,
        )

        starter = ProcessStartTool(working_dir=str(root))
        start_result = await starter.execute(
            command=actual_cmd,
            working_dir=str(root),
            wait_for=ready_pattern,
            wait_timeout=wait_timeout,
        )
        results.append(f"🚀 Server: {start_result[:300]}")

        # Check if server started successfully
        if "Error:" in start_result:
            return "\n".join(results) + "\n\n❌ Server failed to start."

        # Extract process ID
        pid = None
        for line in start_result.split("\n"):
            if "Process ID:" in line:
                pid = line.split("Process ID:")[-1].strip()
                break

        # Wait a moment for server to stabilize
        await asyncio.sleep(1)

        # Navigate browser and screenshot
        url = f"http://localhost:{actual_port}{url_path}"
        try:
            from sharanbot.agent.tools.browser import BrowserTool
            browser = BrowserTool(screenshots_dir=self._screenshots_dir)
            nav_result = await browser.execute(action="navigate", url=url)
            results.append(f"🌐 Browser: {nav_result[:200]}")

            # Take screenshot
            await asyncio.sleep(1)  # Let page render
            screenshot = await browser.execute(action="screenshot", full_page=False)
            results.append("📸 Screenshot captured")

            return [
                {"type": "text", "text": "\n".join(results) + f"\n\nProcess ID: {pid}\nURL: {url}"},
                *(screenshot if isinstance(screenshot, list) else []),
            ]
        except Exception as e:
            results.append(f"⚠️ Browser preview failed: {e}")
            results.append(f"Server is running at {url} (Process ID: {pid})")
            return "\n".join(results)

    async def _refresh(self, project_path: str = "", port: int = 0,
                       url_path: str = "/", **_: Any) -> Any:
        """Re-screenshot the running preview."""
        actual_port = port or 3000
        url = f"http://localhost:{actual_port}{url_path}"

        try:
            from sharanbot.agent.tools.browser import BrowserTool
            browser = BrowserTool(screenshots_dir=self._screenshots_dir)
            await browser.execute(action="navigate", url=url)
            await asyncio.sleep(1)
            screenshot = await browser.execute(action="screenshot", full_page=False)
            return [
                {"type": "text", "text": f"🔄 Refreshed preview at {url}"},
                *(screenshot if isinstance(screenshot, list) else []),
            ]
        except Exception as e:
            return f"Refresh failed: {e}"

    async def _stop(self, **_: Any) -> str:
        """Stop all preview servers."""
        from sharanbot.agent.tools.process import ProcessStopTool, _PROCESS_REGISTRY

        if not _PROCESS_REGISTRY:
            return "No running preview servers."

        stopper = ProcessStopTool()
        results = []
        for pid in list(_PROCESS_REGISTRY.keys()):
            r = await stopper.execute(process_id=pid)
            results.append(f"  {pid}: {r[:100]}")

        # Close browser
        try:
            from sharanbot.agent.tools.browser import BrowserTool
            browser = BrowserTool()
            await browser.execute(action="close")
        except Exception:
            pass

        return "Stopped preview servers:\n" + "\n".join(results)

    async def _status(self, **_: Any) -> str:
        """Check status of running preview servers."""
        from sharanbot.agent.tools.process import ProcessStatusTool
        status_tool = ProcessStatusTool()
        return await status_tool.execute()

    async def _install_deps(self, root: Path, framework: str) -> str:
        """Install dependencies if needed."""
        node_based = {"nextjs", "vite", "expo", "express"}
        python_based = {"django", "flask", "fastapi"}

        if framework in node_based:
            if not (root / "node_modules").exists():
                proc = await asyncio.create_subprocess_shell(
                    "npm install",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=str(root),
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
                output = stdout.decode("utf-8", errors="replace") if stdout else ""
                if proc.returncode == 0:
                    return "📦 npm install completed"
                return f"⚠️ npm install failed: {output[-200:]}"
        elif framework in python_based:
            req_file = root / "requirements.txt"
            if req_file.exists():
                proc = await asyncio.create_subprocess_shell(
                    f"pip install -r requirements.txt",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=str(root),
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
                if proc.returncode == 0:
                    return "📦 pip install completed"
        elif framework == "flutter":
            proc = await asyncio.create_subprocess_shell(
                "flutter pub get",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(root),
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
            if proc.returncode == 0:
                return "📦 flutter pub get completed"

        return ""
