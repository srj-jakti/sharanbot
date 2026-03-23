# SharanBot — Next Version Release Analysis & Plan

> **Generated:** 2026-03-23  
> **Current Version:** 0.1.4.post5  
> **Proposed Next Version:** 0.2.0 (Stable Release)  
> **Scope:** Full project audit, stabilization plan, and execution roadmap

---

## 1. Project Overview

### What Is SharanBot?

SharanBot is a **lightweight personal AI assistant framework** built in Python 3.11+ that supports multi-agent orchestration, self-healing recovery, cross-platform chat integration, and autonomous software development workflows. It bundles a full Electron-based browser (Tandem Browser) for agentic web interaction.

### Architecture Summary

```
┌──────────────────────────────────────────────────────────────────┐
│                        CLI / Gateway                             │
│  (typer CLI, HTTP gateway @ :18790, Dashboard @ :18791)          │
├──────────┬───────────┬─────────────┬────────────────────────────┤
│ Channels │ Agent Core│ Orchestration│ Tandem Browser             │
│ (14 chat │ (loop,    │ (7-phase    │ (Electron app,             │
│ platforms│  memory,  │  pipeline,  │  42+ modules,              │
│  via     │  tools,   │  DAG teams, │  MCP server,               │
│  message │  context, │  quality    │  REST API,                 │
│  bus)    │  recovery)│  gates)     │  OpenClaw bridge)          │
├──────────┴───────────┴─────────────┴────────────────────────────┤
│ Support Services                                                 │
│ (Cron, Heartbeat, Session, Dashboard, Vault, Bus, Workers)       │
├──────────────────────────────────────────────────────────────────┤
│ LLM Providers (LiteLLM, Azure OpenAI, Codex, Custom, 22+ models)│
└──────────────────────────────────────────────────────────────────┘
```

### Key Stats

| Metric | Value |
|--------|-------|
| Python source files | ~155 |
| Total Python LOC | ~15,000+ |
| TypeScript files (Tandem) | 200+ |
| Test files | 57 (Python) |
| Test functions | 674 (all passing) |
| Chat channels | 14 |
| Agent tools | 28 |
| LLM providers | 22+ |
| Orchestration roles | 43 |
| Dependencies (Python) | 34 core + 4 extras groups |

---

## 2. Feature Inventory

### Core Features

| # | Feature | Module(s) | Quality | Status |
|---|---------|-----------|---------|--------|
| 1 | **Agent Loop** — Core request/response engine with tool calling, multi-turn, streaming | `agent/loop.py` (~680 LOC) | Medium | Complete but monolithic |
| 2 | **Memory System** — Persistent JSONL memory with consolidation | `agent/memory.py` | Medium | Complete, no tests |
| 3 | **Vector Memory** — Semantic search with embedding + hash fallback | `agent/vector_memory.py` (~180 LOC) | High | Complete |
| 4 | **Subconscious** — Cross-session background memory blocks | `agent/subconscious.py` (~300 LOC) | Medium | Framework solid, integration incomplete |
| 5 | **Context Management** — System prompt building, image handling | `agent/context.py`, `context_tracker.py`, `context_summarizer.py` | Medium | Functional, some overlap |
| 6 | **Learning System** — Error-keyed learning + adaptive project learning | `agent/learning.py`, `agent/adaptive_learning.py` | Medium | Duplicated concepts |
| 7 | **Recovery Engine** — 11 error categories, circuit breakers, auto-recovery | `agent/recovery.py` (~250 LOC) | High | Production-ready |
| 8 | **Parallel Engine** — Priority queue with chat reservation slots | `agent/parallel_engine.py` (~180 LOC) | High | Excellent design |
| 9 | **Agent Profiles** — general/coder/researcher + custom YAML profiles | `agent/profiles.py` (~120 LOC) | High | Complete |
| 10 | **Permissions** — 4-level permission classification (SAFE→HARD_BLOCKED) | `agent/permissions.py` | High | Complete, well-tested |

### Multi-Agent & Orchestration

| # | Feature | Module(s) | Quality | Status |
|---|---------|-----------|---------|--------|
| 11 | **Team Orchestrator** — DAG-based multi-agent task execution | `agent/team/orchestrator.py`, `dag.py`, `planner.py` | High | Complete, no cycle detection |
| 12 | **7-Phase Pipeline** — SDLC lifecycle (Discovery→Post-Launch) | `orchestration/pipeline.py`, `phase_runner.py`, `phase_gate.py` | High | Complete, gates are soft |
| 13 | **43 Role Prompts** — Specialized agent roles with DoD checklists | `orchestration/role_prompts.py` (~400 LOC) | High | Comprehensive |
| 14 | **Team Autopilot** — Autonomous team execution with oversight | `agent/team/autopilot.py` | Medium | Functional |
| 15 | **Quality Gates** — Artifact presence, BRD validation | `orchestration/phase_gate.py` | Low | Checks existence only, not content |

### Communication Channels

| # | Channel | File | Quality | Status |
|---|---------|------|---------|--------|
| 16 | **Telegram** | `channels/telegram.py` (~800 LOC) | High | Production-ready |
| 17 | **Discord** | `channels/discord.py` (~600 LOC) | High | Production-ready |
| 18 | **Slack** | `channels/slack.py` (~500 LOC) | High | Production-ready |
| 19 | **Matrix** | `channels/matrix.py` (~500 LOC) | High | Production-ready (E2E encryption) |
| 20 | **DingTalk** | `channels/dingtalk.py` | Medium | Functional, minimal |
| 21 | **Feishu** | `channels/feishu.py` | Medium | Functional, minimal |
| 22 | **QQ** | `channels/qq.py` | Medium | Functional, minimal |
| 23 | **WeChat Work** | `channels/wecom.py` | Low | Minimal placeholder |
| 24 | **WhatsApp** | `channels/whatsapp.py` + `bridge/` | Medium | Bridge functional, pre-release Baileys |
| 25 | **Email** | `channels/email.py` | Low | Minimal |
| 26 | **MoChat** | `channels/mochat.py` | Low | Minimal |

### Tools (28 Total)

| # | Tool Category | Files | Quality | Status |
|---|---------------|-------|---------|--------|
| 27 | **Shell Execution** | `tools/shell.py` (~180 LOC) | High | 9 deny patterns, SSRF prevention, timeout |
| 28 | **File Operations** | `tools/filesystem.py` | High | Path traversal + symlink protection |
| 29 | **Web Search/Fetch** | `tools/web.py` (~400 LOC) | High | SSRF protection, untrusted content flagging |
| 30 | **Browser (Basic)** | `tools/browser.py` (~320 LOC) | Medium | Playwright headless, 8 actions |
| 31 | **Tandem Browser** | `tools/tandem_browser.py` (~600 LOC) | High | 25 actions, semantic locators, DevTools |
| 32 | **Computer Tool** | `tools/computer.py` | Medium | Screenshots, clicks |
| 33 | **Process Mgmt** | `tools/process.py` | Medium | Start/stop/status/port checking |
| 34 | **MCP Integration** | `tools/mcp.py` (~300 LOC) | Medium | Schema normalization, incomplete lifecycle |
| 35 | **Create Tools** (5) | `tools/create_*.py` | Medium | Tool/Plugin/Worker/Skill/Software creation |
| 36 | **Spawn Tools** (3) | `tools/spawn*.py` | Medium | SubAgent/DevTeam/SelfBuild spawning |
| 37 | **Self-Evolution** | `tools/evolve.py` (~150 LOC) | Low | No pre-validation, no rollback |
| 38 | **Dynamic Loader** | `tools/dynamic.py` (~150 LOC) | High | 12 reserved names, async-safe reload |
| 39 | **Cron Tool** | `tools/cron.py` | High | Schedule management |
| 40 | **Worker Tool** | `tools/worker.py` | Medium | Lifecycle management |
| 41 | **Message Tool** | `tools/message.py` | Medium | Cross-channel messaging |

### Infrastructure Services

| # | Feature | Module(s) | Quality | Status |
|---|---------|-----------|---------|--------|
| 42 | **Message Bus** | `bus/queue.py`, `bus/events.py` | Medium | Simple async queues, functional |
| 43 | **Session Manager** | `session/manager.py` (~250 LOC) | High | JSONL persistence, tool-call boundary |
| 44 | **Cron Service** | `cron/service.py` (~250 LOC) | High | 3 schedule types, persistent state |
| 45 | **Heartbeat** | `heartbeat/service.py` (~180 LOC) | High | 2-phase LLM-driven decision |
| 46 | **Dashboard** | `dashboard/server.py`, `index.html` | Medium | 8 tabs, dark UI, **no auth** |
| 47 | **Vault** | `vault/__init__.py` (~150 LOC) | High | PBKDF2 + Fernet AES, no insecure fallback |
| 48 | **Config System** | `config/schema.py`, `loader.py`, `watcher.py` | High | Pydantic models, hot-reload, 22+ providers |
| 49 | **Service Manager** | `services/manager.py` | Medium | Lifecycle manager |
| 50 | **Autonomy Suite** | `autonomy/deploy.py`, `backup.py`, `iac.py`, `task_queue.py` | Medium | Deploy pipeline, IaC, backups |

### Tandem Browser (Electron App)

| # | Feature | Directory | Quality | Status |
|---|---------|-----------|---------|--------|
| 51 | **MCP Server** | `tandem-browser/src/mcp/` | High | Proper stdio JSONRPC |
| 52 | **HTTP API** | `tandem-browser/src/api/` | High | RBAC, rate limiting, 15+ routes |
| 53 | **Agent Integration** | `tandem-browser/src/agents/` | High | Tab locking, task distribution |
| 54 | **OpenClaw Bridge** | `tandem/openclaw_bridge.py` | High | WebSocket protocol bridge |
| 55 | **Wingman** | `tandem/wingman.py`, `manager.py` | Medium | ~80% complete |

### Self-Build & Evolution

| # | Feature | Module(s) | Quality | Status |
|---|---------|-----------|---------|--------|
| 56 | **Self-Build Worker** | `workers/self_build/` (6 files) | Medium | Safety gates, rollback, AST validation |
| 57 | **Evolve Tool** | `tools/evolve.py` | Low | No pre-validation or rollback |
| 58 | **Worker Registry** | `agent/worker_registry.py` | Medium | Registration + discovery |
| 59 | **Worker Health** | `workers/health.py`, `workers/retry.py` | High | Monitoring + exponential backoff |

---

## 3. Issues & Duplications

### Critical Issues

| ID | Issue | Severity | Location | Impact |
|----|-------|----------|----------|--------|
| I-01 | **Core agent loop (loop.py) has zero test coverage** | CRITICAL | `agent/loop.py` | Cannot guarantee core behavior across changes |
| I-02 | **Memory system (memory.py) has zero test coverage** | CRITICAL | `agent/memory.py` | Memory consolidation bugs undetectable |
| I-03 | **Dashboard API has no authentication** | HIGH | `dashboard/server.py` | Any local user can control agent |
| I-04 | **DAG has no cycle detection** | HIGH | `agent/team/dag.py` | Circular dependencies cause silent failure |
| I-05 | **evolve.py hot-loads code with zero validation** | HIGH | `agent/tools/evolve.py` | Crashes or security holes from generated code |
| I-06 | **MCP lifecycle management incomplete** | MEDIUM | `agent/tools/mcp.py` | Connection drops not recovered cleanly |
| I-07 | **Config reload partially broken** | MEDIUM | `agent/loop.py:378-385` | Provider/channel/tool configs not reloaded |
| I-08 | **Token estimation uses 4-char heuristic** | MEDIUM | `memory.py`, `context_tracker.py` | Premature/late consolidation |
| I-09 | **Quality gates check existence, not content** | LOW | `orchestration/phase_gate.py` | Empty artifacts pass validation |
| I-10 | **deploy.py uses synchronous urllib in async** | MEDIUM | `autonomy/deploy.py:290` | Blocks event loop during health checks |

### Duplications

| ID | Modules | Overlap | Recommendation |
|----|---------|---------|----------------|
| D-01 | `learning.py` vs `adaptive_learning.py` | Both track learning/errors per session with different storage formats | **Merge** into unified `LearningStore` with categories |
| D-02 | `context_tracker.py` vs `context_summarizer.py` | Tracker only tracks token ratios (~200 LOC); could be a method in context.py | **Absorb** tracker into `context.py` |
| D-03 | `browser.py` vs `tandem_browser.py` | Both expose `name="browser"` — drop-in replacement | **Document** the intentional design, add mutual exclusion |
| D-04 | `sharanbot/tools/tandem_browser.py` vs `sharanbot/agent/tools/tandem_browser.py` | Two files with same name in different paths | **Investigate** — likely one imports the other, consolidate |
| D-05 | `agent/worker.py` vs `workers/` directory | Worker process mgmt vs worker health/retry | **No action** — clean separation confirmed |

### Dead Code

| ID | Location | Description |
|----|----------|-------------|
| DC-01 | `services/manager.py` | `clear_completed()` method defined but never called |
| DC-02 | `channels/base.py:32` | `transcription_api_key` field never populated by subclasses |
| DC-03 | `security/sanitizer.py` | `validate_config_update()` exists but is never called |

---

## 4. Merge Recommendations

### Merge 1: Unified Learning System

**Current:** `learning.py` (JSONL, per-error) + `adaptive_learning.py` (JSON, per-project)

**Proposed:** Single `agent/learning.py` with:
- `LearningStore` class with categories (error, project, preference, pattern)
- Unified JSON storage format
- Auto-cleanup (TTL, max entries)
- **Saves:** ~300 LOC, eliminates conceptual confusion

**Files Affected:** `agent/learning.py`, `agent/adaptive_learning.py`, `agent/loop.py` (imports)

### Merge 2: Context Management Consolidation

**Current:** 3 files (context.py, context_tracker.py, context_summarizer.py) = ~750 LOC

**Proposed:** 
- Move token tracking from `context_tracker.py` (5 methods, ~200 LOC) into `context.py` as `ContextBuilder.token_tracker` 
- Keep `context_summarizer.py` separate (distinct LLM-driven summarization logic)
- **Saves:** ~100 LOC, 1 fewer file

**Files Affected:** `agent/context.py`, `agent/context_tracker.py` (removed), `agent/loop.py` (imports)

### Merge 3: Browser Tool Disambiguation

**Current:** Two tools both claiming `name="browser"` — confusing for users

**Proposed:**
- `browser.py` → `name="browser"` (lightweight Playwright)
- `tandem_browser.py` → `name="tandem_browser"` (full Tandem integration)
- Config flag: `tools.browser_backend: "playwright" | "tandem"` (default: tandem if available)
- Auto-fallback: If Tandem not running, fall back to Playwright

**Files Affected:** `agent/tools/browser.py`, `agent/tools/tandem_browser.py`, `config/schema.py`

---

## 5. Cleanup Plan

### 5.1 Code Cleanup

| Priority | Task | Files | Complexity |
|----------|------|-------|------------|
| P0 | Remove `clear_completed()` dead code from ServiceManager | `services/manager.py` | Low |
| P0 | Remove unused `transcription_api_key` from BaseChannel | `channels/base.py` | Low |
| P0 | Wire `validate_config_update()` into config reload path | `security/sanitizer.py`, `agent/loop.py` | Low |
| P1 | Merge `learning.py` + `adaptive_learning.py` | `agent/learning.py`, `agent/adaptive_learning.py` | Medium |
| P1 | Absorb `context_tracker.py` into `context.py` | `agent/context.py`, `agent/context_tracker.py` | Medium |
| P1 | Resolve duplicate tandem_browser.py files | `sharanbot/tools/`, `sharanbot/agent/tools/` | Low |
| P2 | Standardize naming: all tools use `Tool` suffix | `agent/tools/*.py` | Low |

### 5.2 Architecture Improvements (see Section 6)

### 5.3 Reliability Fixes

| Priority | Task | Files | Complexity |
|----------|------|-------|------------|
| P0 | Add cycle detection to DAG topological sort | `agent/team/dag.py` | Low |
| P0 | Replace `except Exception: pass` in MCP reconnect with logging | `agent/loop.py:516-532` | Low |
| P1 | Replace synchronous urllib with httpx in deploy health check | `autonomy/deploy.py:290` | Low |
| P1 | Add task exception monitoring for `asyncio.create_task()` calls | `agent/worker.py:176` | Low |
| P1 | Add timeout to dashboard service calls | `dashboard/server.py` | Low |
| P2 | Implement proper token counting via provider tokenizer API | `agent/memory.py`, `agent/context_tracker.py` | Medium |
| P2 | Add atomic file writes for state persistence (write→rename) | `agent/learning.py`, `services/manager.py` | Medium |

### 5.4 Performance Optimization

| Priority | Task | Impact | Complexity |
|----------|------|--------|------------|
| P1 | Add connection pooling for MCP servers | Reduce reconnection overhead | Medium |
| P1 | Implement learning store cleanup (TTL/max entries) | Prevent unbounded file growth | Low |
| P2 | Cache compiled regex patterns in sanitizer | Minor CPU savings | Low |
| P2 | Add reconnection jitter to WhatsApp bridge (currently fixed 5s) | Prevent thundering herd | Low |

---

## 6. Architecture Improvements

### 6.1 Extract `ToolManager` from `loop.py`

**Problem:** `loop.py` (~680 LOC) handles tool registration, MCP connection, config reload, recovery, AND message processing.

**Solution:** Extract 3 classes:
```
agent/loop.py (400 LOC) — Core message loop only
agent/tool_manager.py (150 LOC) — Tool registration, reload, MCP lifecycle
agent/config_manager.py (80 LOC) — Config reload with validation
```

**Complexity:** Medium  
**Impact:** High (maintainability, testability)

### 6.2 Add Dashboard Authentication

**Problem:** Dashboard HTTP endpoints have no auth — any local process can query agent state, trigger actions.

**Solution:** 
- Add bearer token auth (reuse gateway token if configured)
- Add `dashboard.require_auth: bool` config option (default: true)
- Protect all non-healthcheck endpoints

**Complexity:** Low  
**Impact:** High (security)

### 6.3 Harden Quality Gates

**Problem:** Phase gates check artifact *existence* but not *content quality*.

**Solution:**
- Add minimum content length checks
- Validate required sections in BRD/Architecture docs  
- Make gate results block pipeline progression (configurable `strict_gates: bool`)

**Complexity:** Medium  
**Impact:** Medium (orchestration reliability)

### 6.4 Add Import Dependency Order

**Problem:** `loop.py` imports from many modules; risk of circular imports as project grows.

**Solution:**
- Document import order in `CONTRIBUTING.md`
- Consider a lightweight dependency injection pattern for tool registration
- Add `ruff` rule to detect circular imports in CI

**Complexity:** Low  
**Impact:** Medium (maintainability)

### 6.5 Evolve Tool Safety

**Problem:** `evolve.py` hot-loads generated code with zero pre-validation.

**Solution:**
- Run `ast.parse()` on generated code before loading
- Execute in subprocess sandbox first
- Add rollback mechanism (backup pre-state)
- Log all evolution events to audit trail

**Complexity:** Medium  
**Impact:** High (stability, security)

---

## 7. Testing Plan

### 7.1 Current Coverage

| Area | Test Files | Functions | Coverage |
|------|-----------|-----------|----------|
| Autonomy | 1 | 20 | Good |
| Orchestration | 2 | 78 | Good |
| Security | 3 | 19 | Excellent |
| Tools (filesystem, exec, web) | 3 | 36 | Good |
| Channels | 8 | ~80 | Good |
| Phase 2 Modules | 2 | 120 | Excellent |
| Self-Build | 1 | 20 | Good |
| **TOTAL** | **57 files** | **674** | **Good overall** |

### 7.2 Critical Testing Gaps

| Missing Test | Priority | Complexity | What to Test |
|-------------|----------|------------|-------------|
| `test_agent_loop.py` | **P0** | High | Message processing, tool calling, consolidation triggers, error recovery, streaming, multi-turn conversations |
| `test_memory.py` | **P0** | Medium | Consolidation logic, JSONL read/write, threshold triggers, concurrent access |
| `test_evolve.py` | **P1** | Medium | Code generation, hot-load safety, rollback, template validation |
| `test_mcp_integration.py` | **P1** | Medium | Schema normalization, connection lifecycle, reconnection |
| `test_dashboard_auth.py` | **P1** | Low | Auth enforcement, token validation, endpoint protection |
| `test_dag_cycles.py` | **P1** | Low | Cycle detection, error reporting, complex dependency graphs |
| `test_config_reload.py` | **P2** | Medium | Provider reloading, channel reconfig, tool config updates |
| `test_bridge_integration.py` | **P2** | Medium | Python↔TypeScript serialization, WebSocket protocol |
| `test_subconscious.py` | **P2** | Medium | Memory block lifecycle, transcript processing, injection |

### 7.3 Recommended Test Types

**Unit Tests (immediate):**
- Agent loop message processing with mocked provider
- Memory consolidation thresholds and JSONL format
- DAG cycle detection
- Evolve tool AST validation

**Integration Tests (next sprint):**
- End-to-end agent loop: user message → tool call → response
- Channel → Bus → Agent → Bus → Channel flow
- Session persistence across restarts
- Config hot-reload propagation

**End-to-End Tests (v0.2.0 release gate):**
- Full team orchestration: plan → spawn → execute → verify
- Tandem browser: navigate → screenshot → extract → respond
- Multi-channel: receive on Telegram → process → respond

---

## 8. Gap Analysis (Human vs System)

### Capabilities Comparison

| Capability | Human Engineer | SharanBot Current | Gap | Priority |
|-----------|---------------|-------------------|-----|----------|
| **Self-Monitoring** | Checks own work, reviews output | ✅ Heartbeat service, watchdog, health checks | Small | Low |
| **Auto-Debugging** | Uses debugger, reads logs, hypothesizes | ⚠️ Recovery engine handles known error categories; no root cause analysis | Medium | Medium |
| **Progress Tracking** | Updates Jira, communicates status | ✅ Project tracker, team trace, but no external integration | Small | Low |
| **Decision-Making** | Weighs tradeoffs, considers context | ⚠️ Model router picks providers; no strategic decision framework | Medium | Medium |
| **Context Awareness** | Remembers past conversations, projects | ✅ Subconscious, vector memory, session history | Small | Low |
| **Code Review** | Reviews before merge, catches issues | ⚠️ Quality gates check existence, not content quality | Large | High |
| **Testing Discipline** | Writes tests for new code | ❌ Self-build creates code but doesn't generate tests | Large | High |
| **Learning from Failures** | Post-mortems, process improvement | ⚠️ Learning store records errors but no cross-project patterns | Medium | Medium |
| **Proactive Communication** | Asks clarifying questions, reports blockers | ⚠️ Agent asks questions but doesn't proactively report blockers | Medium | Low |
| **Security Awareness** | Reviews for vulnerabilities, follows OWASP | ✅ Sanitizer, SSRF protection, permission system | Small | Low |
| **Performance Optimization** | Profiles code, optimizes hot paths | ❌ No performance monitoring or optimization capability | Large | Low |
| **Documentation** | Maintains docs alongside code | ⚠️ Creates files but doesn't maintain documentation | Medium | Low |

### Key Gaps to Close for v0.2.0

1. **Auto-Test Generation:** When self-build creates code, it should also generate test files. Add a test generation step to the self-build pipeline.

2. **Content-Aware Quality Gates:** Replace existence-only checks with LLM-powered content quality evaluation.

3. **Root Cause Analysis:** Extend recovery engine with a "diagnose" phase that uses LLM to analyze error context before applying fixes.

4. **Cross-Project Learning:** Enable the learning store to share patterns across projects (currently per-workspace).

---

## 9. Next Version Plan (v0.2.0)

### Version Goals

1. **Stability First** — Fix all P0/P1 issues, add critical test coverage
2. **Security Hardening** — Dashboard auth, evolve tool sandboxing
3. **Developer Experience** — Cleaner codebase, reduced duplication, better docs
4. **Selective New Features** — Only high-impact, low-effort additions

### Task Categories

#### Critical Fixes (Must-Have for v0.2.0)

| ID | Task | Effort | Impact |
|----|------|--------|--------|
| CF-01 | Add DAG cycle detection | Low | Prevents silent orchestration failures |
| CF-02 | Fix MCP error swallowing (add logging) | Low | Makes debugging possible |
| CF-03 | Wire `validate_config_update()` into reload path | Low | Closes security gap |
| CF-04 | Replace synchronous urllib in deploy health check | Low | Prevents event loop blocking |
| CF-05 | Add dashboard bearer token auth | Low | Closes medium security gap |
| CF-06 | Add exception monitoring on fire-and-forget tasks | Low | Surfaces silent worker crashes |

#### Improvements (Should-Have)

| ID | Task | Effort | Impact |
|----|------|--------|--------|
| IM-01 | Write `test_agent_loop.py` (10+ critical tests) | High | Core reliability assurance |
| IM-02 | Write `test_memory.py` (8+ tests) | Medium | Memory system confidence |
| IM-03 | Merge learning.py + adaptive_learning.py | Medium | Code clarity, -300 LOC |
| IM-04 | Extract ToolManager from loop.py | Medium | Maintainability |
| IM-05 | Add evolve.py AST pre-validation + rollback | Medium | Prevents runtime crashes from generated code |
| IM-06 | Remove dead code (3 identified locations) | Low | Code hygiene |
| IM-07 | Absorb context_tracker into context.py | Medium | Reduce file count |
| IM-08 | Implement learning store auto-cleanup (TTL) | Low | Prevents unbounded growth |

#### New Features (Nice-to-Have)

| ID | Task | Effort | Impact |
|----|------|--------|--------|
| NF-01 | Content-aware quality gates (LLM-powered) | Medium | Better orchestration output quality |
| NF-02 | Auto-test generation in self-build pipeline | Medium | Matches human engineer practice |
| NF-03 | Root cause analysis in recovery engine | Medium | Smarter error handling |
| NF-04 | Browser backend auto-selection config | Low | Better UX for browser tools |
| NF-05 | Proper token counting via provider tokenizer | Medium | Accurate consolidation |

---

## 10. Execution Roadmap (Step-by-Step)

### Phase 1: Stabilization (Priority: CRITICAL)

Focus: Fix bugs, close security gaps, add critical tests.

| Step | Task | Files Affected | Dependencies | Complexity |
|------|------|---------------|-------------|------------|
| 1.1 | **Add DAG cycle detection** — Implement Kahn's algorithm in `topological_sort()`, raise `ValueError` on cycles | `agent/team/dag.py` | None | Low |
| 1.2 | **Fix MCP error handling** — Replace `except Exception: pass` with `logger.warning()` + structured error info | `agent/loop.py` (lines 516-532) | None | Low |
| 1.3 | **Wire config validation** — Call `sanitizer.validate_config_update()` in `apply_config_reload()` | `agent/loop.py` (line 378), `security/sanitizer.py` | None | Low |
| 1.4 | **Add dashboard auth** — Bearer token middleware, skip for `/health` and `/status` | `dashboard/server.py`, `config/schema.py` | None | Low |
| 1.5 | **Fix async health check** — Replace `urllib.request` with `httpx.AsyncClient` in deploy | `autonomy/deploy.py` (line 290) | None | Low |
| 1.6 | **Add task exception handlers** — Wrap `asyncio.create_task()` with exception callback | `agent/worker.py` (line 176), any other create_task calls | None | Low |
| 1.7 | **Remove dead code** — Delete `clear_completed()`, unused `transcription_api_key`, wire `validate_config_update` | `services/manager.py`, `channels/base.py`, `security/sanitizer.py` | 1.3 | Low |
| 1.8 | **Write test_agent_loop.py** — 10+ tests: message processing, tool calling, error recovery, consolidation | New: `tests/test_agent_loop.py` | 1.2 | High |
| 1.9 | **Write test_memory.py** — 8+ tests: JSONL format, consolidation, concurrent access, thresholds | New: `tests/test_memory.py` | None | Medium |
| 1.10 | **Write test_dag_cycles.py** — Test cycle detection, complex graphs, diamond dependencies | `tests/test_team_orchestration.py` (extend) | 1.1 | Low |

### Phase 2: Optimization (Priority: HIGH)

Focus: Reduce complexity, consolidate modules, improve reliability.

| Step | Task | Files Affected | Dependencies | Complexity |
|------|------|---------------|-------------|------------|
| 2.1 | **Merge learning modules** — Combine `learning.py` + `adaptive_learning.py` into unified store with categories | `agent/learning.py`, `agent/adaptive_learning.py` (delete), `agent/loop.py` | Phase 1 complete | Medium |
| 2.2 | **Extract ToolManager** — Move tool registration + MCP lifecycle from loop.py into `agent/tool_manager.py` | `agent/loop.py`, new `agent/tool_manager.py` | Phase 1 complete | Medium |
| 2.3 | **Absorb context_tracker** — Move 5 tracking methods into `ContextBuilder` class | `agent/context.py`, `agent/context_tracker.py` (delete) | None | Medium |
| 2.4 | **Harden evolve tool** — Add `ast.parse()` pre-check, subprocess sandbox, rollback on failure | `agent/tools/evolve.py` | None | Medium |
| 2.5 | **Add learning store TTL** — Auto-expire entries older than 90 days, cap at 10K entries | `agent/learning.py` | 2.1 | Low |
| 2.6 | **Atomic state writes** — Use write→fsync→rename pattern for JSON/JSONL persistence | `agent/learning.py`, `services/manager.py`, `cron/service.py` | None | Medium |
| 2.7 | **Improve token estimation** — Use `tiktoken` (already in deps) for accurate token counting | `agent/memory.py`, `agent/context.py` | None | Medium |
| 2.8 | **Write test_evolve.py** — Test code generation, AST validation, hot-load, rollback | New: `tests/test_evolve.py` | 2.4 | Medium |
| 2.9 | **Write test_mcp.py** — Schema normalization, lifecycle, reconnection scenarios | New: `tests/test_mcp_lifecycle.py` | None | Medium |

### Phase 3: Feature Upgrades (Priority: MEDIUM)

Focus: Close human-vs-AI gaps, improve orchestration quality.

| Step | Task | Files Affected | Dependencies | Complexity |
|------|------|---------------|-------------|------------|
| 3.1 | **Content-aware quality gates** — LLM-powered artifact quality check at phase gates | `orchestration/phase_gate.py` | Phase 2 complete | Medium |
| 3.2 | **Auto-test generation** — Self-build pipeline generates test files alongside code | `workers/self_build/agent_loop.py`, `workers/self_build/analyzer.py` | None | Medium |
| 3.3 | **Root cause analysis** — Recovery engine "diagnose" phase using LLM context | `agent/recovery.py` | None | Medium |
| 3.4 | **Browser backend config** — Auto-select Playwright vs Tandem with fallback | `agent/tools/browser.py`, `agent/tools/tandem_browser.py`, `config/schema.py` | None | Low |
| 3.5 | **Reconnection jitter** — Add randomized jitter to WhatsApp bridge retry | `bridge/src/whatsapp.ts` | None | Low |
| 3.6 | **MCP connection pooling** — Persistent MCP connections with health checks | `agent/tools/mcp.py`, `agent/loop.py` | 2.2 | Medium |
| 3.7 | **Cross-project learning** — Share learning patterns across workspaces via global store | `agent/learning.py` | 2.1, 2.5 | High |
| 3.8 | **Complete config reload** — Full provider + channel + tool reloading on config change | `agent/loop.py` (or new `agent/config_manager.py`) | 2.2 | High |
| 3.9 | **Integration test suite** — End-to-end tests: Channel→Bus→Agent→Tool→Response | New: `tests/test_integration_e2e.py` | Phase 2 complete | High |
| 3.10 | **Version bump to 0.2.0** — Update `pyproject.toml`, `__init__.py`, CHANGELOG entry | `pyproject.toml`, `sharanbot/__init__.py`, new `CHANGELOG.md` | All phases | Low |

---

## Summary

### Project Health Score

| Area | Score | Notes |
|------|-------|-------|
| **Architecture** | 8/10 | Clean separation, extensible, minor monolith in loop.py |
| **Code Quality** | 7/10 | Good patterns with some dead code and duplication |
| **Security** | 8/10 | Strong foundations, dashboard auth gap |
| **Testing** | 7/10 | 674 passing tests, but core loop/memory untested |
| **Documentation** | 8/10 | README, SECURITY.md, audit report all comprehensive |
| **Stability** | 7/10 | Self-healing recovery works; evolve.py risky, MCP fragile |
| **Scalability** | 8/10 | Parallel engine, DAG orchestration, message bus |
| **Overall** | **7.6/10** | Solid framework with clear path to 9/10 |

### Total Effort Estimate

| Phase | Tasks | Complexity Profile |
|-------|-------|--------------------|
| Phase 1: Stabilization | 10 tasks | 7 Low, 1 Medium, 2 High |
| Phase 2: Optimization | 9 tasks | 2 Low, 7 Medium |
| Phase 3: Feature Upgrades | 10 tasks | 2 Low, 5 Medium, 3 High |

### What Makes v0.2.0 "Release-Ready"

1. ✅ All P0 bugs fixed (DAG cycles, MCP errors, async blocking, dead code)
2. ✅ Dashboard authenticated
3. ✅ Core loop and memory tested
4. ✅ Learning modules consolidated
5. ✅ ToolManager extracted for maintainability
6. ✅ Evolve tool sandboxed with rollback
7. ✅ Token counting accurate
8. ✅ State persistence atomic

This project has strong bones. The path from 0.1.4.post5 to 0.2.0 is about **hardening**, not rebuilding. Execute Phase 1 first, validate, then proceed through Phase 2 and Phase 3 incrementally.
