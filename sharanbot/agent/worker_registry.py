"""Worker type registry — supports dynamic worker engine types.

Built-in GSD is always registered.  Additional worker types can be
defined via JSON manifests placed in ``workspace/plugins/workers/``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

# Default GSD loader path (relative to project root).
_GSD_DIR = Path(__file__).resolve().parent.parent / "sharanbot_worker" / "gsd"
_GSD_LOADER = _GSD_DIR / "dist" / "loader.js"


@dataclass
class WorkerType:
    """Describes a worker engine that can be spawned by WorkerManager."""

    name: str                            # e.g. "gsd", "python-coder"
    display_name: str = ""               # Human-readable label
    loader_path: str = ""                # Path to entry point (binary/script)
    mode_args: list[str] = field(default_factory=list)  # CLI args for RPC mode
    protocol: str = "json-rpc"           # "json-rpc" | "stdio-lines"
    runtime: str = "node"                # "node" | "python" | "binary"
    model_config_style: str = "gsd-models-json"  # how to pass LLM config
    description: str = ""

    @property
    def label(self) -> str:
        return self.display_name or self.name


class WorkerTypeRegistry:
    """Registry of available worker engine types."""

    def __init__(self, plugins_dir: Path | None = None):
        self.types: dict[str, WorkerType] = {}
        self._plugins_dir = plugins_dir
        self._register_builtin_gsd()

    # ------------------------------------------------------------------
    # Builtins
    # ------------------------------------------------------------------

    def _register_builtin_gsd(self) -> None:
        self.register(WorkerType(
            name="gsd",
            display_name="GSD v2",
            loader_path=str(_GSD_LOADER),
            mode_args=["--mode", "rpc"],
            protocol="json-rpc",
            runtime="node",
            model_config_style="gsd-models-json",
            description="Built-in GSD agentic coding engine.",
        ))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(self, wt: WorkerType) -> None:
        self.types[wt.name] = wt
        logger.debug("WorkerType registered: {}", wt.name)

    def unregister(self, name: str) -> None:
        self.types.pop(name, None)

    def get(self, name: str) -> WorkerType | None:
        return self.types.get(name)

    def list_types(self) -> list[WorkerType]:
        return list(self.types.values())

    # ------------------------------------------------------------------
    # Plugin scanning
    # ------------------------------------------------------------------

    def scan_plugins(self) -> list[str]:
        """Scan ``plugins/workers/`` for JSON manifests and register them."""
        if not self._plugins_dir or not self._plugins_dir.is_dir():
            return []

        registered: list[str] = []
        for jf in sorted(self._plugins_dir.glob("*.json")):
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
                name = data.get("name")
                if not name:
                    continue
                # Don't overwrite builtin gsd
                if name == "gsd":
                    continue
                wt = WorkerType(
                    name=name,
                    display_name=data.get("display_name", name),
                    loader_path=data.get("loader_path", ""),
                    mode_args=data.get("mode_args", []),
                    protocol=data.get("protocol", "json-rpc"),
                    runtime=data.get("runtime", "node"),
                    model_config_style=data.get("model_config", "env-var"),
                    description=data.get("description", ""),
                )
                self.register(wt)
                registered.append(name)
            except Exception as exc:
                logger.warning("Bad worker manifest {}: {}", jf.name, exc)

        if registered:
            logger.info("Worker types loaded from plugins: {}", ", ".join(registered))
        return registered

    def reload_plugins(self) -> list[str]:
        """Remove all non-builtin types and re-scan."""
        for name in list(self.types):
            if name != "gsd":
                self.unregister(name)
        return self.scan_plugins()
