"""
Entry point for running sharanbot as a module: python -m sharanbot
"""

import sys

# Ensure UTF-8 output on Windows to avoid emoji/Unicode encoding errors
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from sharanbot.cli.commands import app

if __name__ == "__main__":
    app()
