<div align="center">
  <img src="sharanbot_logo.svg" alt="SharanBot" width="300">
  <h1>SharanBot — The Demon Boss of AI Agents</h1>
  <p><b>Autonomous. Self-Healing. Unstoppable.</b></p>
  <p>
    <img src="https://img.shields.io/badge/python-≥3.11-blue" alt="Python">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
    <img src="https://img.shields.io/badge/agents-26-red" alt="Agents">
    <img src="https://img.shields.io/badge/channels-11-orange" alt="Channels">
    <img src="https://img.shields.io/badge/tests-647%20passing-brightgreen" alt="Tests">
    <a href="https://github.com/airajakathi/Sharanbot"><img src="https://img.shields.io/github/stars/airajakathi/Sharanbot?style=social" alt="Stars"></a>
  </p>
</div>

---

**SharanBot** is an autonomous AI agent framework that commands a team of 26 specialist agents, self-heals from any error, learns from its mistakes, and gets the job done — no hand-holding required.

Built for builders who want an AI that acts like a **demon boss**: fierce, relentless, and in total control.

## What Makes SharanBot Different

| | Traditional Bots | SharanBot |
|---|---|---|
| **Error Handling** | Crashes and waits | Self-heals, auto-recovers, learns |
| **Task Complexity** | Single-shot responses | DAG-based multi-agent orchestration |
| **Adaptability** | Static behavior | Self-learning + adaptive cooldowns |
| **Reliability** | Hope it works | Circuit breakers + 5-level retry |
| **Evolution** | Redeploy to update | Hot-reload everything at runtime |

## Core Powers

### 🔥 Multi-Agent Team Orchestration
Decomposes complex tasks into a DAG of subtasks, assigns specialist agents (architect, frontend, backend, devops, tester, security, data-scientist, and 19 more), and orchestrates parallel execution with quality gates.

### 🛡️ Self-Healing & Auto-Recovery
Never crashes permanently. The **RecoveryEngine** handles 8 error categories with circuit breakers:
- **Rate limits** → Adaptive exponential backoff (10-120s)
- **API errors** → 5-level retry (1, 2, 4, 8, 16s)
- **Tool failures** → Auto-retry with MCP reconnect
- **Stuck workers** → Health check + auto-restart
- **Gateway errors** → Circuit trip + graceful recovery
- **Timeouts** → Adaptive task timeout adjustment

### 🧠 Self-Learning
The **LearningStore** remembers every error and every fix. Patterns are persisted to disk. SharanBot never makes the same mistake twice:
- Error signatures tracked with occurrence counts
- Successful fixes replayed automatically on matching errors
- Adaptive cooldowns via exponential moving average
- Cross-session persistence (survives restarts)

### ⚡ Autopilot Routing
Automatically classifies task complexity and routes to the optimal strategy:
- **Simple** → Direct LLM response
- **Moderate** → Background subagent
- **Complex** → Full team orchestration with DAG pipeline

### 🌐 Browser & Sandbox
- **Playwright** browser control: navigate, click, type, screenshot
- **Docker** sandbox: execute code in isolated containers

### 🔄 Self-Evolution
Hot-reload at runtime without restart:
- Tools, plugins, agent profiles
- MCP servers, worker types
- Channel configurations

## Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/airajakathi/Sharanbot.git
cd SharanBot
pip install -e .
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
  }
}
```

### 3. Run

```bash
# CLI mode
sharanbot agent

# Gateway mode (connects to Telegram, Discord, etc.)
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

## Architecture

```
SharanBot
├── Agent Loop ─────── LLM ↔ Tool execution cycle
│   ├── RecoveryEngine ─ Circuit breakers + auto-recovery (8 categories)
│   ├── LearningStore ── Persistent error memory + adaptive cooldowns
│   └── Context ──────── System prompt + memory consolidation
├── Team Orchestrator ─ DAG-based multi-agent execution
│   ├── 26 Agent Profiles (architect, frontend, backend, tester...)
│   ├── Task Planner ──── LLM-powered decomposition
│   ├── Quality Gates ─── Output validation
│   └── Auto-Recovery ─── Task retry on failure
├── Tools ─────────── Filesystem, Shell, Web, Browser, Sandbox, MCP
├── Workers ───────── GSD-2 background processes with health checks
├── Subagents ─────── Background task execution (3 nesting levels)
├── Providers ─────── 20+ LLM providers with 5-level retry
├── Channels ──────── 11 chat platforms
└── Self-Evolution ── Hot-reload everything at runtime
```

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

## Docker

```bash
docker build -t sharanbot .
docker run -v ~/.sharanbot:/root/.sharanbot --rm sharanbot onboard
docker run -v ~/.sharanbot:/root/.sharanbot -p 18790:18790 sharanbot gateway
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
│   ├── profiles.py        # 26 agent profiles
│   ├── team/              # Multi-agent orchestration
│   │   ├── orchestrator.py
│   │   ├── dag.py         # Task dependency graph
│   │   ├── planner.py     # LLM-powered decomposition
│   │   ├── quality.py     # Quality gates
│   │   └── autopilot.py   # Complexity routing
│   └── tools/             # Built-in tools
│       ├── filesystem.py  # Read/write/edit/list
│       ├── shell.py       # Command execution
│       ├── web.py         # Search & fetch
│       ├── browser.py     # Playwright control
│       ├── sandbox.py     # Docker sandbox
│       └── mcp.py         # MCP integration
├── providers/             # 20+ LLM providers
├── channels/              # 11 chat platforms
├── config/                # Config + hot-reload
└── cli/                   # CLI commands
```

## Contributing

PRs welcome. The codebase is intentionally small and readable.

See [CONTRIBUTING.md](./CONTRIBUTING.md) for guidelines and [SECURITY.md](./SECURITY.md) for security policy.

## License

MIT — see [LICENSE](./LICENSE).

---

<div align="center">
  <p><b>SharanBot doesn't ask for permission. It gets the job done.</b></p>
  <p>
    <a href="https://github.com/airajakathi/Sharanbot">
      <img src="https://img.shields.io/github/stars/airajakathi/Sharanbot?style=for-the-badge&color=ff2200" alt="Stars">
    </a>
  </p>
</div>
