"""Dynamic tool loader — scans a workspace directory for Python Tool subclasses.

Tools placed in ``<workspace>/plugins/tools/*.py`` are automatically
discovered, imported, instantiated and registered in the live ToolRegistry.
A reload unregisters old dynamic tools then re-scans.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from sharanbot.agent.tools.base import Tool
from sharanbot.agent.tools.registry import ToolRegistry

# Reserved names that cannot be overridden by dynamic tools.
_BUILTIN_NAMES = frozenset({
    "read_file", "write_file", "edit_file", "list_dir",
    "exec", "web_search", "web_fetch", "message", "spawn",
    "cron", "worker_start", "worker_status", "worker_steer",
    "worker_stop", "reload", "evolve",
})


class DynamicToolLoader:
    """Load/unload Python Tool subclasses from a plugins directory."""

    def __init__(self, tools_dir: Path, registry: ToolRegistry):
        self.tools_dir = tools_dir
        self.registry = registry
        # module_name → list of registered tool names
        self._loaded: dict[str, list[str]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan_and_load(self) -> list[str]:
        """Scan *tools_dir* for ``*.py`` files, import them and register
        every ``Tool`` subclass found.  Returns names of newly registered tools.
        """
        if not self.tools_dir.is_dir():
            return []

        registered: list[str] = []
        for py_file in sorted(self.tools_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            names = self._load_module(py_file)
            registered.extend(names)

        if registered:
            logger.info("Dynamic tools loaded: {}", ", ".join(registered))
        return registered

    def unload_all(self) -> list[str]:
        """Unregister every dynamically loaded tool and remove its module."""
        removed: list[str] = []
        for mod_name, tool_names in list(self._loaded.items()):
            for tn in tool_names:
                self.registry.unregister(tn)
                removed.append(tn)
            sys.modules.pop(mod_name, None)
        self._loaded.clear()
        if removed:
            logger.info("Dynamic tools unloaded: {}", ", ".join(removed))
        return removed

    def reload_all(self) -> list[str]:
        """Unload everything then re-scan."""
        self.unload_all()
        return self.scan_and_load()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_module(self, py_file: Path) -> list[str]:
        """Import a single file and register its Tool subclasses."""
        mod_name = f"sharanbot_dynamic_tools.{py_file.stem}"
        try:
            spec = importlib.util.spec_from_file_location(mod_name, py_file)
            if spec is None or spec.loader is None:
                return []
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)
        except Exception as exc:
            logger.error("Failed to import dynamic tool {}: {}", py_file.name, exc)
            sys.modules.pop(mod_name, None)
            return []

        tool_names: list[str] = []
        for attr_name in dir(mod):
            obj = getattr(mod, attr_name)
            if (
                isinstance(obj, type)
                and issubclass(obj, Tool)
                and obj is not Tool
                and not getattr(obj, "__abstractmethods__", None)
            ):
                try:
                    instance = obj()
                except Exception as exc:
                    logger.warning("Cannot instantiate {}.{}: {}", py_file.name, attr_name, exc)
                    continue

                if instance.name in _BUILTIN_NAMES:
                    logger.warning(
                        "Dynamic tool '{}' in {} conflicts with built-in — skipped",
                        instance.name, py_file.name,
                    )
                    continue

                self.registry.register(instance)
                tool_names.append(instance.name)

        if tool_names:
            self._loaded[mod_name] = tool_names
        else:
            sys.modules.pop(mod_name, None)

        return tool_names
