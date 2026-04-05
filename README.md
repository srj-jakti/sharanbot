<div align="center">
  <img src="sharanbot_logo.svg" alt="SharanAGI" width="300">
  <h1>SharanAGI — The Digital Human</h1>
  <p><b>Autonomous. Self-Healing. Self-Learning. Unstoppable.</b></p>
  <p>
    <img src="https://img.shields.io/badge/python-≥3.11-blue" alt="Python">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
    <img src="https://img.shields.io/badge/version-0.6.0-orange" alt="Version">
    <img src="https://img.shields.io/badge/agents-32-red" alt="Agents">
    <img src="https://img.shields.io/badge/tools-24-purple" alt="Tools">
    <img src="https://img.shields.io/badge/channels-11-teal" alt="Channels">
    <img src="https://img.shields.io/badge/AGI_modules-8-gold" alt="AGI">
    <a href="https://github.com/airajakathi/Sharanbot"><img src="https://img.shields.io/github/stars/airajakathi/Sharanbot?style=social" alt="Stars"></a>
  </p>
</div>

---

**SharanAGI** (formerly SharanBot) is an autonomous AI agent framework that doesn't just respond — it thinks, patrols, learns, asks for help, and manages multiple projects like a real human developer. It commands 32 specialist agents, self-heals from any error, accumulates knowledge over time, and knows when to escalate to you.

Built for builders who want an AI that acts like a **digital human**: proactive, self-directed, and relentless.

## What Makes SharanAGI Different

| | Traditional Bots | SharanBot v0.2 | SharanAGI v0.3 |
|---|---|---|---|
| **Initiative** | Waits for input | Waits for input | Watchdog patrols, finds work autonomously |
| **Error Handling** | Crashes and waits | Self-heals, auto-recovers | Self-heals + stuck detection + smart escalation |
| **Task Complexity** | Single-shot responses | DAG-based multi-agent | Multi-project orchestration with priority queue |
| **Learning** | None | Error pattern memory | Full knowledge base + skill self-assessment |
| **Human Interaction** | None | Text in/out | Human-in-the-loop escalation (CAPTCHA, 2FA, judgment) |
| **Continuity** | Forgets everything | Cross-session memory | Persistent project context + auto-resume |
| **Self-Awareness** | None | None | Confidence scoring + knows when to ask for help |

## SharanAGI — The Digital Human Layer

SharanAGI v0.3 adds 5 subsystems that close the gap between an AI agent and a real human developer:

### 1. Watchdog / Patrol Mode (Initiative)
A human doesn't wait to be told — they wake up, look around, and decide what needs doing. The Watchdog does the same:

- **Periodic health patrols** — checks project health, tests, servers, error logs
- **Incomplete work detection** — scans `.team_state/` for unfinished teams and resumes them
- **Stale service monitoring** — detects auto-restart services that are down
- **Learning store analysis** — finds recurring unresolved errors
- **Auto-action** — acts on critical findings autonomously, notifies on warnings
- Runs as a background asyncio task alongside the gateway

### 2. Human-in-the-Loop Escalation (Real-World Handling)
A human knows when to ask a colleague for help. SharanAGI does the same:

- **CAPTCHA escalation** — detects CAPTCHAs via page text analysis, sends screenshot to user, waits for solution
- **2FA/Verification** — detects phone/email verification prompts, asks user to complete
- **Judgment calls** — when confidence is low, asks user to confirm before proceeding
- **Stuck escalation** — when going in circles, asks user for guidance
- **Permission requests** — asks before destructive or irreversible actions
- Works across all channels — sends to user's active Telegram/Discord/WhatsApp/etc.
- Async ask/wait/respond pattern with configurable timeout (default 5 min)

### 3. Persistent Project Context (Multi-Project Management)
A human juggles multiple projects, remembers context for each, and prioritizes by deadline:

- **Project registry** — persistent catalog with metadata, tech stack, tags
- **Per-project memory** — accumulated context, key decisions, TODOs per project
- **Priority queue** — deadline-aware prioritization (urgent deadlines → pending TODOs → least recently worked)
- **Cross-project knowledge** — search patterns and solutions across all projects
- **Status tracking** — active, paused, completed, archived

### 4. Knowledge Accumulation (Proactive Learning)
A human reads docs, builds expertise, and knows what they're good at:

- **Knowledge base** — persistent technical knowledge that grows over time (not just error patterns)
- **Study agent** — LLM-powered knowledge extraction from documentation and text
- **Skill self-assessment** — tracks task outcomes per domain, auto-levels (novice → expert)
- **Pre-task study check** — "I don't know Kubernetes well enough, let me study first"
- **Prompt injection** — relevant knowledge automatically injected into agent prompts
- **Tag-based search** — find knowledge by topic, tags, or content

### 5. Stuck Detection + Smart Escalation (Self-Awareness)
A human knows when they're going in circles. SharanAGI does the same:

- **4 detection methods**: exact repetition, error streak, oscillation (A→B→A→B), high error rate
- **Confidence scoring** — hedge phrase detection, tool success rate weighting
- **Automatic warnings** — injected into agent loop every 5 iterations when stuck
- **Escalation trigger** — when confidence drops below threshold, asks user for help
- **Context building** — builds detailed "what was tried" context for escalation messages

## Core Powers

### Multi-Agent Team Orchestration
Decomposes complex tasks into a DAG of subtasks, assigns specialist agents (architect, frontend, backend, devops, tester, security, data-engineer, and 25 more), and orchestrates parallel execution with quality gates.

### Self-Healing & Auto-Recovery
Never crashes permanently. The **RecoveryEngine** handles 8 error categories with circuit breakers:
- **Rate limits** → Adaptive exponential backoff (10-120s)
- **API errors** → 5-level retry (1, 2, 4, 8, 16s)
- **Tool failures** → Auto-retry with MCP reconnect
- **Stuck workers** → Health check + auto-restart
- **Gateway errors** → Circuit trip + graceful recovery
- **Timeouts** → Adaptive task timeout adjustment

### Self-Learning
The **LearningStore** remembers every error and every fix. Patterns are persisted to disk. SharanBot never makes the same mistake twice:
- Error signatures tracked with occurrence counts
- Successful fixes replayed automatically on matching errors
- Adaptive cooldowns via exponential moving average
- Cross-session persistence (survives restarts)

### Proactive Autopilot
Goes beyond reactive routing — the autopilot **anticipates** what you need:
- **Superhuman memory**: Cross-session user profiling (tracks your tasks, preferred strategies, peak hours, complexity trends)
- **Pattern caching**: Instant re-routing for similar tasks without LLM call
- **Memory-driven decisions**: Injects accumulated intelligence into routing analysis
- **Proactive suggestions**: Recommends next actions based on your workflow patterns
- **Learning-store-aware**: Avoids strategies that previously failed

### Vibe-Coding Engine (v0.6)
Modern project building toolkit — scaffold, preview, test, and deploy apps from natural language:
- **TemplateEngine** (`scaffold_project`) — 12 modern templates: Next.js, Vite+React, Flutter, Expo, Django, Docker full-stack. Real CLI scaffolders with rich fallbacks.
- **GitTool** (`git`) — Structured git: init, status, diff, add, commit, branch, checkout, log, push, pull, stash, reset, tag.
- **LivePreview** (`live_preview`) — Auto-detect framework → install deps → start server → browser → screenshot. One tool call.
- **PackageManager** (`package_manager`) — Unified deps across 10 managers (npm/yarn/pnpm/pip/uv/poetry/flutter/cargo/go).
- **TestRunner** (`test_runner`) — Parsed test results for pytest/jest/vitest/flutter/cargo/go.
- **Deploy** (`deploy`) — One-command deploy to Vercel, Netlify, Docker. Auto-detects target.

### Self-Evolution Engine (v0.5)
The **real** self-improving AI — not marketing, not dogfooding, actual autonomous self-modification:
- **Self-Diagnosis** — Analyzes error patterns, stuck patterns, performance metrics, and circuit breaker states to identify what needs improving
- **LLM-Powered Planning** — Turns diagnoses into concrete code changes (not just bug reports)
- **Safe Self-Modification** — Git checkpoint → modify → test → rollback-if-failed → commit-if-passed
- **Growth Tracking** — Persistent log of all evolution attempts, new capabilities added, success rates
- **Automatic Triggers** — Background task failures automatically trigger evolution cycles
- **Safety Boundaries** — Cannot modify security, vault, auth, or safety code
- **Rate Limited** — Max 1 cycle per 10 minutes, max 20 per day
- **Human Approval** — High-risk changes to core code require approval before execution
- Routes to **direct** (simple) → **subagent** (moderate) → **full team** (complex)

### Encrypted Vault
Secure credential storage for API keys, tokens, and passwords:
- **PBKDF2** key derivation (480K iterations + SHA-256)
- **Fernet** symmetric encryption (AES-128-CBC + HMAC)
- Falls back to XOR obfuscation if `cryptography` isn't installed
- Master-password locked — credentials encrypted at rest
- Slash-path keys: `provider/openrouter/apiKey` → nested config overlay
- Persistent `vault.json` with encrypted entries

### Tandem Browser (Native)
SharanBot ships with **Tandem Browser** — a native Electron-based browser built for human-AI collaboration:

- **Human + AI shared browsing**: You browse normally, the AI agent watches and assists via the Wingman panel
- **24 MCP tools**: Navigate, click, type, screenshot, read pages, manage tabs, search bookmarks & history, create AI tasks with approval gates
- **Computer tool**: Coordinate-based visual automation (mouse, keyboard, screenshot) — available to all agents
- **OpenClaw Bridge**: WebSocket server on port `18789` linking SharanBot to the Wingman chat panel
- **Tandem API**: REST + SSE API on port `8766` with 250+ endpoints for full browser control
- **8-layer security**: Network shield, credential leak scanning, AST-level JS analysis, human-in-the-loop approval
- **Native dashboard panel**: Real-time SharanBot dashboard embedded directly in the Wingman panel (WebSocket-powered, no iframe)
- **Liquid Glass Lite**: Dark-themed UI with glass effects and smooth animations

### Real-Time Dashboard
Live web dashboard showing everything SharanBot is doing:
- **7 tabs**: Overview, Services, Teams, Health, Integrations, Projects, Logs
- **WebSocket**: Auto-reconnecting real-time updates every 3 seconds
- **Service monitoring**: Running apps, monitors, agents with status badges
- **Team tracking**: Active teams with task progress (completed/total)
- **Health view**: Circuit breaker visualization (closed/open)
- **Learning summary**: Patterns learned, successful fixes by category
- **Integration overview**: MCP servers, tools, skills, agent profiles, pipelines, plugins
- **Project browser**: Workspace project discovery with file viewer and one-click run
- **Activity log**: Live timestamp-tagged events
- Available at `http://127.0.0.1:18795` (configurable) and natively inside the Tandem Browser Wingman panel

### Autonomous Service Manager
Run and monitor long-lived services, deployed apps, and autonomous agents:
- **Lifecycle management**: Register → Start → Running → Stop/Restart/Remove
- **Auto-restart**: Dead processes detected and restarted (configurable max restarts)
- **Health checks**: Periodic process monitoring with status tracking
- **Log buffering**: 500-line circular buffer per service, accessible via API
- **Persistence**: Service configs saved to `services.json`, restored on restart
- Service kinds: `app` | `agent` | `monitor` | `cron` | `custom`

### Self-Evolution
Hot-reload at runtime without restart:
- Tools, plugins, agent profiles
- MCP servers, worker types
- Channel configurations

## Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/airajakathi/Sharanbot.git
cd Sharanbot
pip install -e .

# Optional: for encrypted vault
pip install cryptography
```

### 2. Configure

```bash
sharanbot onboard
```

Edit `~/.sharanbot/config.json`:

```json
{
  "providers": {
    "custom": {
      "apiKey": "your-api-key",
      "apiBase": "https://api.your-provider.com/v1",
      "api": "openai-completions"
    }
  },
  "agents": {
    "defaults": {
      "model": "your-model-name",
      "provider": "custom"
    }
  },
  "dashboard": {
    "enable": true,
    "host": "127.0.0.1",
    "port": 18795
  }
}
```

### 3. Run

```bash
# CLI mode — interactive chat
sharanbot agent

# Gateway mode — connects to Telegram, Discord, etc.
# Also starts: dashboard, Tandem Browser, OpenClaw bridge
sharanbot gateway
```

## Supported Providers

| Provider | Type |
|----------|------|
| `custom` | Any OpenAI-compatible endpoint |
| `openrouter` | Access to all models |
| `anthropic` | Claude direct |
| `openai` | GPT direct |
| `deepseek` | DeepSeek direct |
| `gemini` | Gemini direct |
| `azure_openai` | Azure OpenAI |
| `groq` | LLM + Voice transcription |
| `moonshot` | Moonshot/Kimi |
| `ollama` | Local LLM |
| `vllm` | Local OpenAI-compatible |
| And 10+ more... | |

## Chat Channels

Connect SharanBot to 11 chat platforms:

| Channel | Connection |
|---------|-----------|
| **Telegram** | Bot token from @BotFather |
| **Discord** | Bot token + Message Content intent |
| **WhatsApp** | QR code scan |
| **Slack** | Socket Mode (no public IP needed) |
| **Feishu** | WebSocket (no public IP needed) |
| **DingTalk** | Stream Mode |
| **Email** | IMAP/SMTP |
| **QQ** | WebSocket |
| **Matrix** | E2EE supported |
| **Wecom** | WebSocket |
| **Mochat** | Socket.IO |

Example Telegram setup:
```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "allowFrom": ["YOUR_USER_ID"]
    }
  }
}
```

## Agent Profiles

SharanBot includes **32 specialist agent profiles** (3 built-in + 29 plugin-based):

| Profile | Role |
|---------|------|
| `general` | Default all-purpose agent |
| `coder` | Coding-focused (extended iterations) |
| `researcher` | Read-only research with web access |
| `architect` | System design & architecture |
| `backend_dev` | Backend development |
| `frontend_dev` | Frontend development |
| `devops` | CI/CD & infrastructure |
| `security` | Security analysis & hardening |
| `tester` | Test writing & QA |
| `debugger` | Bug investigation |
| `code_reviewer` | Code review |
| `data_engineer` | Data pipeline design |
| `mcp_builder` | MCP server development |
| `orchestrator` | Multi-agent orchestration |
| `planner` | Task planning & decomposition |
| And 17 more... | (see `~/.sharanbot/workspace/plugins/agents/`) |

## Built-in Tools

| Tool | Description |
|------|-------------|
| `filesystem` | Read, write, edit, list, search files |
| `shell` | Execute shell commands |
| `web` | DuckDuckGo search + page fetch |
| `browser` | Playwright browser control |
| `tandem_browser` | Tandem Browser integration (24 MCP tools) |
| `computer` | Mouse, keyboard, screenshot automation |
| `wingman` | Wingman panel chat interaction |
| `sandbox` | Docker sandbox execution |
| `mcp` | Model Context Protocol tool bridge |
| `cron` | Scheduled task management |
| `spawn` | Subagent spawning |
| `worker` | Background worker task distribution |
| `message` | Cross-channel message delivery |
| `evolve` | Agent self-improvement |
| `reload` | Hot-reload tools & config |
| `dynamic` | Dynamic tool loading from workspace |
| `import_agent` | Import & compose agent code |
| `agi` | SharanAGI human-like capabilities (projects, knowledge, escalation, stuck detection, self-evolution) |

## Architecture

```
SharanAGI
├── Gateway ──────────── Port 18790 (configurable)
│   ├── Agent Loop ───── LLM ↔ Tool execution cycle
│   │   ├── RecoveryEngine ─ Circuit breakers + auto-recovery
│   │   ├── LearningStore ── Persistent error memory
│   │   ├── Autopilot ────── Proactive routing + superhuman memory
│   │   ├── Context ──────── System prompt + memory consolidation
│   │   └── AGI Layer ────── Digital human capabilities
│   │       ├── StuckDetector ── Circular attempt + error streak detection
│   │       ├── ConfidenceScorer ── Hedge phrase + success rate scoring
│   │       ├── KnowledgeBase ──── Persistent technical knowledge
│   │       ├── ProjectManager ─── Multi-project context + priorities
│   │       ├── EscalationMgr ──── Human-in-the-loop ask/wait/respond
│   │       ├── ReactionEngine ─── Event-driven auto-handling + retry
│   │       ├── Observer ────────── Structured metrics + traces + health
│   │       └── EvolutionEngine ── Self-diagnosis → plan → modify → test → grow
│   ├── Team Orchestrator ─ DAG-based multi-agent execution
│   │   ├── 32 Agent Profiles
│   │   ├── Task Planner ──── LLM-powered decomposition
│   │   ├── Quality Gates ─── Output validation
│   │   └── Auto-Recovery ─── Task retry on failure
│   ├── Vault ─────────── Encrypted credential store (PBKDF2 + Fernet)
│   ├── Service Manager ── Autonomous service lifecycle
│   ├── Workers ───────── GSD-2 background processes
│   ├── Subagents ─────── Background task execution (3 nesting levels)
│   └── Self-Evolution ── Hot-reload everything at runtime
├── Watchdog Patrol ──── Background health checks + auto-action
├── Dashboard ────────── Port 18795 — Real-time web UI (WebSocket)
├── Tandem Browser ───── Native Electron browser
│   ├── Wingman Panel ─── AI chat + activity + screenshots + dashboard
│   ├── OpenClaw Bridge ─ Port 18789 — WebSocket link to SharanAGI
│   ├── Browser API ───── Port 8766 — REST + SSE (250+ endpoints)
│   ├── Computer Tool ─── Coordinate-based mouse/keyboard/screenshot
│   └── MCP Server ────── 24 browser automation tools
├── Tools ─────────────── 18 built-in tools + MCP extensions
├── Providers ─────────── 20+ LLM providers with 5-level retry
└── Channels ──────────── 11 chat platforms
```

## Ports Reference

| Port | Service | Protocol |
|------|---------|----------|
| `18790` | SharanBot Gateway | HTTP |
| `18795` | Dashboard | HTTP + WebSocket |
| `18789` | OpenClaw Bridge | HTTP + WebSocket |
| `8766` | Tandem Browser API | HTTP + SSE |

## Dashboard

The real-time dashboard runs alongside the gateway at `http://127.0.0.1:18795` and is also embedded natively in the Tandem Browser Wingman panel:

```
┌──────────────────────────────────────────────────┐
│  SHARANBOT                         [Connected]   │
│  ┌──────┬──────┬──────┬──────┐                   │
│  │  3   │  2   │  5   │  47  │                   │
│  │ Svcs │Teams │ Cron │Learn │                   │
│  └──────┴──────┴──────┴──────┘                   │
│                                                   │
│  Services   │ Status  │ PID  │ Uptime            │
│  ──────────────────────────────                   │
│  my-app     │ running │ 1234 │ 2h 15m            │
│  monitor    │ running │ 5678 │ 1h 30m            │
│  worker-1   │ stopped │ —    │ —                  │
│                                                   │
│  Circuits: [*] LLM  [*] MCP  [*] Tools           │
└──────────────────────────────────────────────────┘
```

Dashboard API endpoints:
- `GET /api/status` — Full system snapshot
- `GET /api/services` — Service manager status
- `GET /api/teams` — Active team orchestrations
- `GET /api/workers` — Worker manager status
- `GET /api/cron` — Scheduled cron jobs
- `GET /api/health` — Recovery engine + circuit breakers
- `GET /api/vault/keys` — Vault credential keys (no values)
- `GET /api/projects` — Workspace projects
- `GET /api/integrations` — MCP servers, tools, skills, profiles, pipelines, plugins
- `WS /ws` — Real-time WebSocket updates (3s interval)

## MCP (Model Context Protocol)

SharanBot supports MCP for extended tool capabilities:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@anthropic/mcp-filesystem"]
    },
    "memory": {
      "command": "npx",
      "args": ["-y", "@anthropic/mcp-memory"]
    }
  }
}
```

Tandem Browser provides 24 MCP tools out of the box for browser automation — no additional configuration needed.

## Docker

```bash
docker build -t sharanbot .
docker run -v ~/.sharanbot:/root/.sharanbot --rm sharanbot onboard
docker run -v ~/.sharanbot:/root/.sharanbot -p 18790:18790 -p 18795:18795 sharanbot gateway
```

Docker Compose:
```bash
docker compose up -d sharanbot-gateway
```

## Project Structure

```
sharanbot/
├── agent/                 # Core agent logic
│   ├── loop.py            # Agent loop (LLM ↔ tools)
│   ├── recovery.py        # Auto-recovery engine + circuit breakers
│   ├── learning.py        # Self-learning store (JSONL persistence)
│   ├── context.py         # System prompt builder
│   ├── memory.py          # Persistent memory + consolidation
│   ├── subagent.py        # Background subagent execution
│   ├── worker.py          # Worker manager + health checks
│   ├── profiles.py        # Agent profile loader
│   ├── team/              # Multi-agent orchestration
│   │   ├── orchestrator.py
│   │   ├── dag.py         # Task dependency graph
│   │   ├── planner.py     # LLM-powered decomposition
│   │   ├── quality.py     # Quality gates
│   │   └── autopilot.py   # Proactive routing + superhuman memory
│   └── tools/             # 17 built-in tools
│       ├── filesystem.py  # Read/write/edit/list
│       ├── shell.py       # Command execution
│       ├── web.py         # Search & fetch
│       ├── browser.py     # Playwright control
│       ├── computer.py    # Mouse/keyboard/screenshot
│       ├── tandem_browser.py # Tandem Browser MCP integration
│       ├── wingman.py     # Wingman panel interaction
│       ├── sandbox.py     # Docker sandbox
│       └── mcp.py         # MCP protocol bridge
├── tandem/                # Tandem Browser integration
│   ├── manager.py         # Electron process lifecycle
│   └── openclaw_bridge.py # WebSocket bridge (port 18789)
├── vault/                 # Encrypted credential store
├── services/              # Autonomous service manager
├── agi/                   # SharanAGI digital human layer
│   ├── watchdog.py        # Proactive patrol mode
│   ├── escalation.py      # Human-in-the-loop escalation
│   ├── projects.py        # Multi-project context management
│   ├── knowledge.py       # Knowledge accumulation + skill assessment
│   ├── stuck.py           # Stuck detection + confidence scoring
│   ├── reactions.py       # Event-driven reaction engine
│   ├── observability.py   # Structured metrics + traces + health
│   └── evolution.py       # Self-evolution: diagnose → plan → modify → test → grow
├── dashboard/             # Real-time web UI + WebSocket
├── providers/             # 20+ LLM providers
├── channels/              # 11 chat platforms
├── config/                # Config + hot-reload
└── cli/                   # CLI commands
tandem-browser/            # Native Electron browser (inbuilt)
├── main/                  # Electron main process
├── shell/                 # Browser shell (HTML/CSS/JS)
│   ├── index.html         # Main shell with Wingman panel
│   ├── js/                # Tab management, sidebar, wingman, dashboard
│   └── css/               # Liquid Glass Lite design system
├── api/                   # REST + SSE API (port 8766)
├── mcp/                   # 24 MCP tools for browser automation
└── package.json           # Electron app config (v0.65.5)
```

## Contributing

PRs welcome. The codebase is intentionally small and readable.

See [CONTRIBUTING.md](./CONTRIBUTING.md) for guidelines and [SECURITY.md](./SECURITY.md) for security policy.

## License

MIT — see [LICENSE](./LICENSE).

---

<div align="center">
  <p><b>SharanAGI doesn't just respond. It thinks, patrols, learns, and gets the job done.</b></p>
  <p>
    <a href="https://github.com/airajakathi/Sharanbot">
      <img src="https://img.shields.io/github/stars/airajakathi/Sharanbot?style=for-the-badge&color=ff2200" alt="Stars">
    </a>
  </p>
</div>
