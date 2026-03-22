# SharanBot Self-Evolution Architecture Plan

**Codename: Vibe Agentic Engineering v2 — Unlimited Possibilities**
**Date: 2026-03-22**

---

## Vision

SharanBot becomes a **self-evolving AI system** that can:
1. Create new capabilities (tools, workers, agents, skills, channels) at runtime
2. Hot-load them **instantly without gateway restart**
3. Use its own GSD worker to build new components → then load and use them immediately

This creates an **infinite capability loop**: SharanBot → builds new capability → loads it → uses it → builds more.

---

## Current State (What Blocks Us)

| Component | Hot-Reload? | Blocker |
|-----------|-------------|---------|
| Python Tools | NO | Registered once in `_register_default_tools()`, no workspace loader |
| Skills | PARTIAL | Filesystem scanned per-message, but needs new session to refresh |
| MCP Tools | NO | `_mcp_connected` flag blocks reconnection |
| Workers | NO | `_GSD_LOADER` is hardcoded module constant, only GSD type |
| Subagents | NO | Generic only, no custom profiles/prompts/tool-restrictions |
| Channels | NO | `ChannelManager._init_channels()` runs once, no add/remove API |
| Config | NO | Loaded once at startup, immutable |
| Providers | NO | Created once, stored in AgentLoop |

**Critical Discovery**: `self.tools.get_definitions()` is called FRESH per LLM iteration inside the agent loop. This means **any tool added to the registry becomes instantly available to the agent on the very next message**. This is the foundation for everything.

---

## Architecture: 7 Phases

```
Phase 1: Hot-Reload Core        → Foundation infrastructure
Phase 2: Dynamic Tools           → Load Python tools from workspace at runtime
Phase 3: Dynamic Workers         → Register new worker engine types
Phase 4: Dynamic Subagents       → Custom agent profiles with specialized prompts/tools
Phase 5: Dynamic MCP & Config    → Live MCP reconnection + config reload
Phase 6: Dynamic Channels        → Add/remove chat channels without restart
Phase 7: Self-Evolution Loop     → Meta-tools that let SharanBot extend itself
```

---

## Phase 1: Hot-Reload Core Infrastructure

**Goal**: Build the foundational reload mechanism that all other phases depend on.

### 1.1 Config Watcher & Reloader

**File**: `sharanbot/config/watcher.py` (NEW)

```
ConfigWatcher:
  - Watches ~/.sharanbot/config.json for changes (file mtime polling, 5s interval)
  - On change: re-parse config, emit "config_changed" event on MessageBus
  - Components subscribe to relevant config sections
  - Does NOT restart process — individual components handle their own reload
```

**Changes to `sharanbot/config/loader.py`**:
- Add `reload_config()` function that re-reads and validates config.json
- Return a diff of what changed (for targeted reload)

### 1.2 Plugin Directory Convention

**Standard directory layout** in `~/.sharanbot/workspace/`:

```
~/.sharanbot/workspace/
├── plugins/
│   ├── tools/           ← Python tool modules (Phase 2)
│   ├── workers/         ← Worker type manifests (Phase 3)
│   ├── agents/          ← Subagent profiles (Phase 4)
│   └── channels/        ← Channel modules (Phase 6)
├── skills/              ← Already exists, skills loaded per-message
└── ...
```

### 1.3 Hot-Reload Bus Events

**Add to `sharanbot/bus/events.py`**:

```python
@dataclass
class ReloadEvent:
    component: str       # "tools", "workers", "agents", "mcp", "config", "channels"
    action: str          # "add", "remove", "reload_all"
    target: str | None   # specific item name, or None for all
    payload: dict        # component-specific data
```

### 1.4 Reload Tool (Meta-Tool)

**File**: `sharanbot/agent/tools/reload.py` (NEW)

A tool the agent can call to trigger reloads:

```python
class ReloadTool(Tool):
    name = "reload"
    description = "Hot-reload SharanBot components without restart."
    parameters = {
        "component": "tools|workers|agents|mcp|config|skills|channels",
        "target": "specific item name (optional)",
    }
```

**Files to modify**:
- `sharanbot/agent/loop.py` — register ReloadTool, add reload handlers
- `sharanbot/bus/events.py` — add ReloadEvent dataclass

---

## Phase 2: Dynamic Tool Loading

**Goal**: Python tools placed in `workspace/plugins/tools/` are auto-loaded and immediately usable.

### 2.1 Dynamic Tool Scanner

**File**: `sharanbot/agent/tools/dynamic.py` (NEW)

```python
class DynamicToolLoader:
    """Scans workspace/plugins/tools/ and loads Tool subclasses."""

    def __init__(self, tools_dir: Path, registry: ToolRegistry):
        self.tools_dir = tools_dir
        self.registry = registry
        self._loaded: dict[str, str] = {}  # module_name -> tool_name

    def scan_and_load(self) -> list[str]:
        """Scan directory, import modules, register Tool subclasses."""
        # For each .py file in tools_dir:
        #   1. importlib.import_module() with unique module name
        #   2. Find all Tool subclasses
        #   3. Instantiate and register in ToolRegistry
        #   4. Track what was loaded for unload/reload

    def unload(self, tool_name: str):
        """Unregister a dynamically loaded tool."""

    def reload_all(self):
        """Unregister all dynamic tools, re-scan, re-load."""
```

### 2.2 Integration Points

**Changes to `sharanbot/agent/loop.py`**:
- After `_register_default_tools()`, call `self._load_dynamic_tools()`
- Add `_load_dynamic_tools()` method that creates `DynamicToolLoader`
- On ReloadEvent(component="tools"): call `loader.reload_all()`

### 2.3 How Agent Creates New Tools

The agent (or GSD worker) writes a Python file to `~/.sharanbot/workspace/plugins/tools/`:

```python
# ~/.sharanbot/workspace/plugins/tools/my_tool.py
from sharanbot.agent.tools.base import Tool

class MyCustomTool(Tool):
    name = "my_custom_tool"
    description = "Does something specific"
    parameters = {...}

    async def execute(self, **kwargs):
        ...
```

Then calls `reload(component="tools")` → tool is immediately available.

### 2.4 Security

- Dynamic tools run in the same Python process (same trust level as exec)
- Tool files are executed only from `workspace/plugins/tools/` (restricted path)
- Optional: sandbox via `RestrictedPython` or subprocess isolation (future)

---

## Phase 3: Dynamic Worker Types

**Goal**: New worker engine types can be defined and used without code changes.

### 3.1 Worker Type Registry

**File**: `sharanbot/agent/worker_registry.py` (NEW)

```python
@dataclass
class WorkerType:
    name: str              # e.g., "gsd", "custom-rust-agent"
    display_name: str      # e.g., "GSD v2", "Rust Code Agent"
    loader_path: str       # path to the binary/script entry point
    mode_args: list[str]   # e.g., ["--mode", "rpc"]
    protocol: str          # "json-rpc" | "stdio-lines" | "http"
    env_builder: str       # Python dotted path to env builder function
    model_config: str      # "gsd-models-json" | "env-var" | "cli-flag"
    runtime: str           # "node" | "python" | "binary"

class WorkerTypeRegistry:
    def __init__(self, plugins_dir: Path):
        self.types: dict[str, WorkerType] = {}
        self._plugins_dir = plugins_dir
        self._load_builtin_gsd()

    def _load_builtin_gsd(self):
        """Register the built-in GSD worker type."""
        self.register(WorkerType(
            name="gsd",
            display_name="GSD v2.41.0",
            loader_path=str(_GSD_LOADER),
            mode_args=["--mode", "rpc"],
            protocol="json-rpc",
            env_builder="sharanbot.agent.worker.build_gsd_env",
            model_config="gsd-models-json",
            runtime="node",
        ))

    def register(self, worker_type: WorkerType): ...
    def unregister(self, name: str): ...
    def get(self, name: str) -> WorkerType | None: ...
    def list_types(self) -> list[WorkerType]: ...
    def scan_plugins(self):
        """Scan workspace/plugins/workers/ for worker type manifests (JSON files)."""
```

### 3.2 Worker Manifest Format

**File**: `~/.sharanbot/workspace/plugins/workers/my-worker.json`

```json
{
    "name": "python-coder",
    "display_name": "Python Coding Agent",
    "loader_path": "/path/to/my_agent/main.py",
    "mode_args": ["--rpc"],
    "protocol": "stdio-lines",
    "runtime": "python",
    "model_config": "env-var",
    "description": "Specialized Python coding agent"
}
```

### 3.3 Changes to WorkerManager

**Modify `sharanbot/agent/worker.py`**:

- Remove `_GSD_LOADER` module constant
- Accept `WorkerTypeRegistry` in `__init__`
- `spawn()` takes optional `worker_type="gsd"` parameter
- Lookup worker type from registry → use its loader_path, mode_args, runtime
- Different env builders for different worker types

### 3.4 The Self-Referential Power

SharanBot can use its GSD worker to:
1. **Build a new worker** (write Python/Node/Rust code)
2. **Create a manifest** for it in `plugins/workers/`
3. **Call `reload(component="workers")`** → new worker type available
4. **Spawn the new worker** via `worker_start(worker_type="python-coder")`

---

## Phase 4: Dynamic Subagent Profiles

**Goal**: Define specialized subagent types with custom prompts, tool restrictions, and models.

### 4.1 Agent Profile System

**File**: `sharanbot/agent/profiles.py` (NEW)

```python
@dataclass
class AgentProfile:
    name: str                    # "code-reviewer", "security-auditor"
    display_name: str
    system_prompt: str           # Custom system prompt
    allowed_tools: list[str]     # ["read_file", "web_search"] or ["*"] for all
    denied_tools: list[str]      # Tools explicitly blocked
    model: str | None            # Override model (or None for default)
    max_iterations: int          # Override iteration limit
    temperature: float           # Override temperature
    description: str             # Shown when listing agents

class AgentProfileRegistry:
    def __init__(self, plugins_dir: Path):
        self.profiles: dict[str, AgentProfile] = {}
        self._plugins_dir = plugins_dir
        self._load_builtin_profiles()

    def _load_builtin_profiles(self):
        """Register default profiles: 'general', 'coder', 'researcher'."""

    def scan_plugins(self):
        """Scan workspace/plugins/agents/ for .yaml profile files."""

    def register(self, profile: AgentProfile): ...
    def get(self, name: str) -> AgentProfile | None: ...
```

### 4.2 Agent Profile Format

**File**: `~/.sharanbot/workspace/plugins/agents/security-auditor.yaml`

```yaml
name: security-auditor
display_name: Security Auditor
system_prompt: |
  You are a security-focused code auditor. Your job is to:
  - Find vulnerabilities (OWASP Top 10)
  - Check for credential leaks
  - Review dependency versions
  - Suggest fixes
  Never modify code directly. Only report findings.
allowed_tools:
  - read_file
  - list_dir
  - web_search
  - web_fetch
  - exec  # for running security scanners
denied_tools:
  - write_file
  - edit_file  # read-only audit
max_iterations: 25
temperature: 0.2
description: "Audits code for security vulnerabilities without modifying files."
```

### 4.3 Changes to SubagentManager

**Modify `sharanbot/agent/subagent.py`**:

- Accept `AgentProfileRegistry` in `__init__`
- `spawn()` takes optional `profile="security-auditor"` parameter
- Profile controls: system prompt, which tools get registered, iteration limit, model

### 4.4 Changes to SpawnTool

**Modify `sharanbot/agent/tools/spawn.py`**:

- Add `profile` parameter: `"profile": {"type": "string", "description": "Agent profile to use"}`
- Agent can choose the right profile for each task

---

## Phase 5: Dynamic MCP & Config Reload

**Goal**: MCP servers can be reconnected, config changes take effect live.

### 5.1 MCP Reconnection

**Changes to `sharanbot/agent/loop.py`**:

```python
async def _reconnect_mcp(self) -> None:
    """Disconnect existing MCP sessions, re-read config, reconnect."""
    # 1. Close existing MCP stack
    if self._mcp_stack:
        await self._mcp_stack.aclose()
    
    # 2. Unregister all mcp_* tools
    for name in list(self.tools._tools.keys()):
        if name.startswith("mcp_"):
            self.tools.unregister(name)
    
    # 3. Reset flags
    self._mcp_connected = False
    self._mcp_connecting = False
    
    # 4. Re-read MCP config from config.json
    config = reload_config()
    self._mcp_servers = config.tools.mcp_servers
    
    # 5. Reconnect
    await self._connect_mcp()
```

### 5.2 Config Reload Handler

**Changes to `sharanbot/agent/loop.py`**:

```python
async def reload_config(self) -> str:
    """Reload config.json and apply changes to live components."""
    from sharanbot.config.loader import load_config, get_config_path
    
    new_config = load_config(get_config_path())
    
    changes = []
    
    # Update model
    if new_config.agents.defaults.model != self.model:
        self.model = new_config.agents.defaults.model
        changes.append(f"Model → {self.model}")
    
    # Update max iterations
    new_max = new_config.agents.defaults.max_tool_iterations
    if new_max != self.max_iterations:
        self.max_iterations = new_max
        changes.append(f"Max iterations → {new_max}")
    
    # Update context window
    new_ctx = new_config.agents.defaults.context_window_tokens
    if new_ctx != self.context_window_tokens:
        self.context_window_tokens = new_ctx
        changes.append(f"Context window → {new_ctx}")
    
    # Update MCP servers (triggers reconnect)
    if new_config.tools.mcp_servers != self._mcp_servers:
        await self._reconnect_mcp()
        changes.append("MCP servers reconnected")
    
    # Update worker manager
    if self.worker_manager:
        # Update API key, model, etc. in worker manager
        ...
    
    return "Config reloaded: " + ", ".join(changes) if changes else "No changes detected."
```

---

## Phase 6: Dynamic Channels

**Goal**: Add/remove chat channels without restarting the gateway.

### 6.1 Channel Manager Extensions

**Changes to `sharanbot/channels/manager.py`**:

```python
async def add_channel(self, name: str, config_section) -> str:
    """Dynamically add and start a new channel."""
    if name in self.channels:
        return f"Channel '{name}' already running."
    
    from sharanbot.channels.registry import load_channel_class
    cls = load_channel_class(name)
    channel = cls(config_section, self.bus)
    self.channels[name] = channel
    await channel.start()
    return f"Channel '{name}' started."

async def remove_channel(self, name: str) -> str:
    """Stop and remove a channel."""
    if name not in self.channels:
        return f"Channel '{name}' not found."
    await self.channels[name].stop()
    del self.channels[name]
    return f"Channel '{name}' stopped and removed."

async def reload_channel(self, name: str) -> str:
    """Restart a channel with fresh config."""
    await self.remove_channel(name)
    # Re-read config, re-add
    ...
```

### 6.2 External Channel Plugins

Channels can also be loaded from `workspace/plugins/channels/` as Python modules that define a `BaseChannel` subclass, using the same `DynamicToolLoader` pattern from Phase 2.

---

## Phase 7: Self-Evolution Loop (The Infinite Capability Machine)

**Goal**: SharanBot uses its own capabilities to build NEW capabilities, then loads and uses them immediately.

### 7.1 The Self-Evolution Meta-Tool

**File**: `sharanbot/agent/tools/evolve.py` (NEW)

```python
class EvolveTool(Tool):
    """Meta-tool that orchestrates self-evolution."""
    
    name = "evolve"
    description = (
        "Create a new SharanBot capability and hot-load it. "
        "Types: tool, worker, agent, skill. "
        "Uses the GSD worker to build the component, then reloads."
    )
    parameters = {
        "type": "object",
        "properties": {
            "capability_type": {
                "type": "string",
                "enum": ["tool", "worker", "agent", "skill"],
            },
            "name": {"type": "string"},
            "description": {"type": "string"},
            "specification": {"type": "string"},
        },
        "required": ["capability_type", "name", "specification"],
    }
```

**What it does**:

1. **Receives request**: "Create a tool that can analyze Python AST"
2. **Spawns GSD worker** with task: "Build a Python tool that..."
3. **Worker writes code** to `workspace/plugins/tools/ast_analyzer.py`
4. **Calls `reload(component="tools")`** → tool is live
5. **Returns**: "New tool `ast_analyzer` is ready to use."

### 7.2 The Infinite Loop

```
User: "I need you to be able to analyze Docker containers"

SharanBot thinks: "I don't have a Docker tool. Let me create one."

1. evolve(type="tool", name="docker_inspect", spec="...")
   → GSD worker writes docker_inspect.py to plugins/tools/
   → reload(component="tools")
   → "docker_inspect" tool now available

2. SharanBot uses docker_inspect tool to answer user's question

User: "Now I need a specialized agent for DevOps"

1. evolve(type="agent", name="devops-agent", spec="...")
   → Writes devops-agent.yaml to plugins/agents/
   → reload(component="agents")

2. spawn(task="...", profile="devops-agent")
   → Specialized DevOps subagent running with Docker tools
```

### 7.3 Self-Evolution Safety

- **Approval mode**: Optional config `evolution.require_approval: true`
  - Before loading new code, show it to user for approval
- **Sandbox**: New tools run in same process — trust boundary is same as `exec`
- **Rollback**: Keep previous versions in `plugins/.versions/`
- **Audit log**: All evolutions logged to `workspace/evolution.log`

---

## Implementation Order & Dependencies

```
Phase 1 ──┬── Phase 2 (Dynamic Tools)
           ├── Phase 3 (Dynamic Workers)
           ├── Phase 4 (Dynamic Subagents)
           ├── Phase 5 (MCP & Config Reload)
           └── Phase 6 (Dynamic Channels)
                    │
                    └── Phase 7 (Self-Evolution Loop)
                         (depends on all above)
```

**Phase 1** is the foundation. Phases 2-6 are independent of each other and can be done in parallel or any order. Phase 7 ties everything together.

---

## Files to Create (NEW)

| File | Phase | Purpose |
|------|-------|---------|
| `sharanbot/config/watcher.py` | 1 | Config file change watcher |
| `sharanbot/agent/tools/reload.py` | 1 | Reload meta-tool |
| `sharanbot/agent/tools/dynamic.py` | 2 | Dynamic tool loader from workspace |
| `sharanbot/agent/worker_registry.py` | 3 | Worker type registry |
| `sharanbot/agent/profiles.py` | 4 | Agent profile registry |
| `sharanbot/agent/tools/evolve.py` | 7 | Self-evolution orchestrator tool |

## Files to Modify

| File | Phases | Changes |
|------|--------|---------|
| `sharanbot/agent/loop.py` | 1,2,4,5 | Reload handlers, dynamic tool loading, profile support |
| `sharanbot/agent/worker.py` | 3 | Use WorkerTypeRegistry instead of hardcoded _GSD_LOADER |
| `sharanbot/agent/tools/worker.py` | 3 | Add `worker_type` parameter to WorkerStartTool |
| `sharanbot/agent/subagent.py` | 4 | Accept AgentProfileRegistry, use profiles |
| `sharanbot/agent/tools/spawn.py` | 4 | Add `profile` parameter |
| `sharanbot/channels/manager.py` | 6 | Add add_channel/remove_channel/reload_channel |
| `sharanbot/config/loader.py` | 1,5 | Add reload_config() function |
| `sharanbot/config/schema.py` | 1,3,4 | Add EvolutionConfig, WorkerTypeConfig |
| `sharanbot/cli/commands.py` | 1 | Start ConfigWatcher, pass registries |
| `sharanbot/bus/events.py` | 1 | Add ReloadEvent |

---

## Estimated Scope per Phase

| Phase | New Files | Modified Files | Complexity |
|-------|-----------|----------------|------------|
| 1: Core | 2 | 4 | Medium |
| 2: Tools | 1 | 1 | Medium |
| 3: Workers | 1 | 3 | High |
| 4: Subagents | 1 | 3 | Medium |
| 5: MCP/Config | 0 | 2 | Medium |
| 6: Channels | 0 | 1 | Medium |
| 7: Evolution | 1 | 2 | High |
| **Total** | **6** | **~12** | — |

---

## Post-Implementation: What Becomes Possible

Once all 7 phases are complete:

| Scenario | How It Works |
|----------|-------------|
| "Create a tool for Docker" | evolve → GSD builds it → reload → use it |
| "Add a Slack channel" | evolve → writes channel module → reload → Slack live |
| "Build me a Rust worker" | GSD builds Rust binary → manifest created → spawn it |
| "Make a security audit agent" | writes profile YAML → reload → spawn with profile |
| "Add a new MCP server" | edit config.json → config watcher → MCP reconnect |
| "Create a skill for Kubernetes" | write SKILL.md → reload → skill available |
| "Build an app and deploy it" | worker builds app → exec deploys → done |
| "Build a tool, test it, fix bugs, ship" | evolve → test → evolve(fix) → ship |

**SharanBot becomes a system that can build anything, including improvements to itself.**
