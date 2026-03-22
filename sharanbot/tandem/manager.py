"""Tandem Browser lifecycle manager — start, stop, health-check."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import requests
from loguru import logger

# Default API port for tandem-browser
TANDEM_API_PORT = 8766
TANDEM_API_BASE = f"http://127.0.0.1:{TANDEM_API_PORT}"


def _find_tandem_dir() -> Path | None:
    """Locate the tandem-browser directory relative to the project root."""
    # Check common locations
    candidates = [
        Path(__file__).resolve().parent.parent.parent / "tandem-browser",
        Path.cwd() / "tandem-browser",
    ]
    for d in candidates:
        if (d / "package.json").exists():
            return d
    return None


def _find_node() -> str:
    """Find the Node.js executable."""
    node = shutil.which("node")
    if node:
        return node
    # Windows fallback
    for p in (r"C:\Program Files\nodejs\node.exe", r"C:\Program Files (x86)\nodejs\node.exe"):
        if os.path.isfile(p):
            return p
    return "node"


class TandemManager:
    """Manages the tandem-browser Electron process lifecycle."""

    def __init__(
        self,
        tandem_dir: Path | None = None,
        dashboard_url: str | None = None,
    ):
        self._tandem_dir = tandem_dir or _find_tandem_dir()
        self._dashboard_url = dashboard_url
        self._process: Optional[subprocess.Popen] = None
        self._started = False

    @property
    def tandem_dir(self) -> Path | None:
        return self._tandem_dir

    @property
    def api_base(self) -> str:
        return TANDEM_API_BASE

    @property
    def is_running(self) -> bool:
        """Check if tandem-browser API is reachable."""
        try:
            r = requests.get(f"{TANDEM_API_BASE}/status", timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    def _electron_exe(self) -> str:
        """Return path to the Electron executable inside tandem-browser."""
        if not self._tandem_dir:
            raise RuntimeError("tandem-browser directory not found")
        if sys.platform == "win32":
            return str(self._tandem_dir / "node_modules" / "electron" / "dist" / "electron.exe")
        elif sys.platform == "darwin":
            return str(
                self._tandem_dir / "node_modules" / "electron" / "dist"
                / "Electron.app" / "Contents" / "MacOS" / "Electron"
            )
        else:
            return str(self._tandem_dir / "node_modules" / "electron" / "dist" / "electron")

    async def start(self) -> bool:
        """Start tandem-browser as a subprocess.

        Returns True if started successfully (or already running).
        """
        if self.is_running:
            logger.info("Tandem Browser already running on port {}", TANDEM_API_PORT)
            self._started = True
            # Open dashboard in existing instance if requested
            if self._dashboard_url:
                await self._open_dashboard()
            return True

        if not self._tandem_dir or not (self._tandem_dir / "package.json").exists():
            logger.warning("tandem-browser directory not found — skipping launch")
            return False

        # Verify compiled output exists
        main_js = self._tandem_dir / "dist" / "main.js"
        if not main_js.exists():
            logger.warning("tandem-browser not compiled (dist/main.js missing) — run 'npm run compile' in tandem-browser/")
            return False

        electron_path = self._electron_exe()
        if not os.path.isfile(electron_path):
            logger.warning("Electron executable not found at {} — run 'npm install' in tandem-browser/", electron_path)
            return False

        # Build clean environment (strip Electron-as-Node vars from parent VSCode)
        env = os.environ.copy()
        env.pop("ELECTRON_RUN_AS_NODE", None)
        env.pop("ATOM_SHELL_INTERNAL_RUN_AS_NODE", None)

        logger.info("Starting Tandem Browser from {}", self._tandem_dir)
        try:
            self._process = subprocess.Popen(
                [electron_path, "."],
                cwd=str(self._tandem_dir),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            logger.error("Failed to start Tandem Browser: {}", e)
            return False

        # Wait for the API to become available (up to 30s)
        for _ in range(60):
            await asyncio.sleep(0.5)
            if self.is_running:
                logger.info("Tandem Browser started — API available on port {}", TANDEM_API_PORT)
                self._started = True
                # Configure Wingman autonomy for SharanBot usage
                await self._configure_wingman()
                # Open dashboard in tandem
                if self._dashboard_url:
                    await self._open_dashboard()
                return True

        logger.warning("Tandem Browser started but API not responding after 30s")
        return False

    async def _open_dashboard(self) -> None:
        """Navigate tandem-browser to the SharanBot dashboard URL."""
        if not self._dashboard_url:
            return
        try:
            r = requests.post(
                f"{TANDEM_API_BASE}/navigate",
                json={"url": self._dashboard_url},
                timeout=5,
            )
            if r.status_code == 200:
                logger.info("Tandem Browser opened dashboard: {}", self._dashboard_url)
            else:
                logger.warning("Failed to open dashboard in Tandem: {} {}", r.status_code, r.text[:200])
        except Exception as e:
            logger.warning("Could not navigate Tandem to dashboard: {}", e)

    async def _configure_wingman(self) -> None:
        """Configure Wingman autonomy for SharanBot — auto-approve safe actions."""
        try:
            requests.patch(
                f"{TANDEM_API_BASE}/autonomy",
                json={
                    "autoApproveRead": True,
                    "autoApproveNavigate": True,
                },
                timeout=5,
            )
            logger.info("Wingman autonomy configured (auto-approve read + navigate)")
        except Exception as e:
            logger.debug("Could not configure Wingman autonomy: {}", e)

    async def stop(self) -> None:
        """Stop the tandem-browser process."""
        if self._process:
            logger.info("Stopping Tandem Browser...")
            try:
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
            except Exception as e:
                logger.warning("Error stopping Tandem Browser: {}", e)
            self._process = None
        self._started = False

    def get_api_token(self) -> str | None:
        """Read the tandem API token from disk."""
        token_path = Path.home() / ".tandem" / "api-token"
        try:
            if token_path.exists():
                token = token_path.read_text().strip()
                if len(token) >= 16:
                    return token
        except Exception:
            pass
        return os.environ.get("TANDEM_API_TOKEN")
