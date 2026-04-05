# Changelog

## 0.9.0 — 2026-03-24 — Unified Team Architecture (E2E Round 3 Fixes)

### Architectural Changes
- **Project Manifest** — new `ProjectManifest` class (`sharanbot/agent/team/manifest.py`) creates a `PROJECT_MANIFEST.json` in every project root. Single source of truth for architecture decisions, port assignments, agent ownership, and file tracking.
- **Unified Workspace** — all team subagents and GSD workers now write to the same `project_root` directory (derived from task description). No more split-brain across CWD, orchestrator workspace, and MCP workspace.
- **Live TeamBus** — subagents now read pending steer messages from TeamBus before each LLM call. `team_steer` actually reaches running subagents instead of broadcasting into void.
- **GSD Workers as Team Members** — `TeamOrchestrator` can now dispatch coding-heavy tasks to GSD workers via `_run_task_as_gsd_worker()`, with automatic fallback to subagent if GSD is unavailable.
- **Auto-executing Teams** — `create_team()` now auto-launches `run_team()` in background. No more confusion about whether teams auto-execute.
- **Autopilot Can't Compete** — when team strategy is active, direct build tools (`create_software`, `scaffold_project`, `write_file`) are filtered from autopilot's tool definitions. Autopilot is the manager, not the builder.
- **Always-On WorkerManager** — `WorkerManager` is always created in gateway (not gated on `config.worker.enable`). GSD workers are available as team members by default.
- **Watchdog Cooldown** — Watchdog checks Task Ledger before firing. 30-minute cooldown after any task completion prevents infinite watchdog→autopilot loops.

### Files Changed
- `sharanbot/agent/team/manifest.py` — NEW: ProjectManifest with port assignment, agent claims, decisions
- `sharanbot/agent/team/orchestrator.py` — project_root, manifest, GSD worker dispatch, live TeamBus, auto-run
- `sharanbot/agent/team/autopilot.py` — team strategy tool filtering, updated prompt
- `sharanbot/agent/team/tools.py` — updated TeamCreateTool for auto-run
- `sharanbot/agi/watchdog.py` — task_ledger cooldown check
- `sharanbot/cli/commands.py` — always-on WorkerManager, task_ledger wired to watchdog
- `sharanbot/agent/loop.py` — worker_manager passed to TeamOrchestrator
- `tests/test_round3_fixes.py` — 18 new tests

### Test Results
- 1414 tests passing (was 1396)

## 0.8.1 — 2026-03-24 — E2E Round 2 Fixes (Team Execution + Reaction Scoping)

### Problems Fixed (from E2E observation)

1. **Autopilot team_status polling loop (CRITICAL)** — After team_create, the autopilot called team_status 13+ times without ever calling team_steer. Fixed by: rewriting the team strategy prompt to explicitly instruct team_steer usage, adding a polling detector that injects a corrective hint after 2 consecutive team_status calls.

2. **Reaction engine hijacking main loop (CRITICAL)** — quality.gate_failed events injected system messages that caused the main loop to investigate old projects (tradearena). Fixed by: changing quality.gate_failed default action from SEND_TO_AGENT to NOTIFY, and suppressing reaction engine system messages during active autopilot execution.

3. **Task Ledger auto-wiring** — Task ledger was never called during execution. Fixed by: auto-creating task entries when autopilot starts, auto-completing on success, auto-failing on error, auto-cancelling on /stop.

4. **Stuck detection for polling loops** — Added Check 1b: same tool name repeated 60%+ of window = polling loop detected, even with different args.

5. **Progress feedback** — Added periodic progress updates every 8 iterations during autopilot execution so the user knows work is happening.

6. **Circuit reset log spam** — Changed circuit reset logging from INFO to DEBUG level. Eliminates 30+ noisy log lines per task.

7. **MCP timeout circuit breaker** — MCP tools now track consecutive timeouts and auto-disable after 2 timeouts. Prevents wasting 90+ seconds on repeated timeout retries.

8. **Quality gate reaction config** — Changed from SEND_TO_AGENT (which hijacks the main loop) to NOTIFY (which just informs the user). The team orchestrator handles quality gate retries internally.

### Stats
- 17 new tests (1396 total passing)
- 8 files modified

## 0.8.0 — 2026-03-24 — Task Ledger + Service Registry (Proof-of-Work Tracking)

### The Problem
SharanBot had no unified record of what it actually did. When the user asked "what did you build?" or "what services are running?", there was no structured audit trail. Autopilot memory tracked routing decisions but not outcomes or proof. Background processes were in-memory only — lost on restart. No way to track task lifecycle, duration, token cost, or artifacts produced.

### Task Ledger (`sharanbot/agi/task_ledger.py`)
- Full task lifecycle tracking: create → start → complete/fail/cancel
- Proof-of-work recording: artifacts, files created/modified, commands run, tools used
- Token usage and LLM call tracking per task
- 7 task types: `build`, `service`, `research`, `automation`, `chat`, `maintenance`, `other`
- 6 task statuses: `pending`, `running`, `completed`, `failed`, `cancelled`, `paused`
- Query by status, type, session, or free-text search
- Automatic pruning (keeps last 1000 tasks, 500 completed)
- Persisted to `task_ledger.json` — survives restarts

### Service Registry
- Tracks long-running services/bots with full lifecycle
- Service statuses: `running`, `stopped`, `stopped_permanently`, `crashed`, `paused`
- Uptime tracking, restart counts, auto-restart flags
- `get_services_to_restart()` — identifies services that should auto-restart after gateway restart
- Permanently stopped services cannot be restarted (respects user decisions)
- Fuzzy service lookup by name or partial match
- Persisted to `service_registry.json`

### AGI Tool — 10 New Actions
- `task_create`, `task_start`, `task_complete`, `task_fail` — Task lifecycle
- `task_list`, `task_status` — Task queries and stats
- `service_register`, `service_start`, `service_stop`, `service_list` — Service management

### /status Integration
- `/status` now shows task ledger summary (active/completed/failed counts)
- Shows running services count

### Stats
- 34 new tests (1379 total passing)
- 1 new module (`sharanbot/agi/task_ledger.py`)

## 0.7.0 — 2026-03-24 — Decision Store + Smart Memory (Watchdog respects user decisions)

### The Problem
When the user said "stop the bitcoin scanner permanently", SharanBot stored it in free-text memory (MEMORY.md) but the Watchdog had no way to query it. Every patrol cycle, it re-reported the same bitcoin_scanner.log errors — ignoring the user's explicit decision. The memory system lacked structured decision tracking that other subsystems could programmatically respect.

### Decision Store (`sharanbot/agi/decisions.py`)
- New structured persistence for user decisions and directives
- Machine-readable JSON store (not free-text like MEMORY.md)
- 7 decision types: `stop_permanent`, `stop_temporary`, `pause`, `resume`, `suppress_alert`, `configuration`, `preference`
- 3 statuses: `active`, `expired`, `revoked`
- Automatic expiry for temporary decisions (e.g. "pause for 2 hours")
- Conflict resolution: recording `resume` auto-revokes `stop_permanent` for same subject, and vice versa
- Fuzzy subject matching: "bitcoin_scanner", "bitcoin-scanner", "bitcoin scanner", "bitcoin" all match
- Queryable by all subsystems: `is_stopped()`, `is_permanently_stopped()`, `is_suppressed()`
- Context injection into agent prompts: active decisions shown as "[Active User Decisions — MUST be respected]"
- Persisted to `decisions.json` in workspace

### Watchdog Improvements
- Watchdog now checks Decision Store before reporting findings — suppressed/stopped subjects are silently skipped
- Finding deduplication: same subject not re-reported within 30-minute window
- All findings now carry a `subject` field for machine-matchable decision filtering
- Log file findings use the log file stem as subject (e.g. `bitcoin_scanner.log` → subject `bitcoin_scanner`)
- `get_status()` now shows list of suppressed subjects

### AGI Tool — 4 New Decision Actions
- `decision_record` — Record a user decision (with optional expiry via `expires_in_hours`)
- `decision_list` — List active decisions (optionally filtered by subject)
- `decision_revoke` — Revoke decisions for a subject
- `decision_status` — Show full decision store status

### Memory Integration
- Decision context injected into every agent prompt alongside knowledge, project, and subconscious hints
- Agent sees active decisions and MUST respect them (e.g. won't restart a permanently stopped service)
- When user says "stop X permanently", agent should call `agi(action='decision_record', subject='X', decision_type='stop_permanent')`

### Stats
- 29 new tests (1345 total passing)
- 1 new module (`sharanbot/agi/decisions.py`)
- Watchdog, AGI tool, agent loop all updated

## 0.6.3 — 2026-03-24 — Complete Vibe-Coding Toolkit (FormatTool, DesignToCodeTool, DatabaseTool)

### New Tools (3 tools, all registered in AgentLoop)

- **FormatTool** (`format_code`) — Auto-detect and run code formatters/linters. Detects prettier, eslint, ruff, black, isort, rustfmt, clippy, gofmt, go vet, dart format, dart analyze from project config files. Actions: format, lint, detect, check (dry-run). Supports file-level targeting and formatter override.
- **DesignToCodeTool** (`design_to_code`) — Convert screenshots/mockups/wireframes into code using LLM vision. Supports react, nextjs, vue, svelte, flutter, swiftui, react-native, html frameworks with tailwind/css/styled-components/css-modules styling. Actions: generate, describe.
- **DatabaseTool** (`database`) — Database schema management. Auto-detects ORM from project files (prisma, drizzle, django, alembic, typeorm, knex, sequelize, diesel, goose, golang-migrate). Actions: migrate, deploy, seed, reset, status, push, pull, generate, studio.

### Updated
- All 10 vibe-coding gaps from VIBECODING_IMPROVEMENT_REPORT.md now marked ✅ DONE
- Version bumped to 0.6.3

## 0.6.2 — 2026-03-24 — E2E Fixes: Team Routing, Fast-Path, Circuit Breaker

### E2E Test Fixes (from 3D Car Racing Game observation)

#### Team Routing Fix (CRITICAL)
- Autopilot analysis prompt now strongly biases build/create tasks toward `team` strategy
- Executor prompt REQUIRES `team_create` when strategy is `team` — no more single-agent for complex projects
- Strategy-specific instruction blocks in executor prompt (team, single_agent, direct)

#### Fast-Path Improvements
- Fast-path now uses only last 4 messages (not full 176K+ session history) — dramatically faster
- Added delivery logging for debugging undelivered responses

#### Path Confusion Fix
- Executor prompt now injects workspace path and instructs autopilot to use native file tools
- Recommends `scaffold_project` over `create_software` for new projects
- Explicitly tells autopilot NOT to use MCP filesystem tools (different path resolution)

#### Circuit Breaker Tuning (for slow providers like Kimi)
- API_ERROR threshold: 3 → 8 failures before circuit opens
- TIMEOUT threshold: 3 → 8 failures before circuit opens
- Retry delays: 1,2,4,8,16 → 2,4,8,16,30 seconds

#### Session Consolidation Before Complex Tasks
- When delegating to autopilot, if session exceeds 50% of context window, consolidate first
- Prevents autopilot from starting with a bloated 176K token context

### Updated
- E2E Observation Report updated with all findings and fix status
- Screenshot/image handling clarified as provider-dependent (not a bug)

## 0.6.1 — 2026-03-24 — Boss Fast-Path + Spec Kit Integration

### Telegram Responsiveness Fix
- Added "fast path" in `_process_message`: when autopilot is running in background, Boss skips intent resolution, autopilot analysis, and the full agent loop — makes a single lightweight LLM call with task context and responds immediately
- Boss stays conversational while background tasks run — no more silent Telegram when autopilot is active
- Fallback message if fast-path LLM call fails (tells user task is still running, offers /status and /stop)
- Slash commands (/status, /stop, /help, /evolve) still work normally during autopilot

### Spec Kit Integration (GitHub Spec-Driven Development)
- New `SpecifyInitTool` (`specify_init`) — wraps GitHub's `specify` CLI for Spec-Driven Development
- Auto-detects `specify` in PATH or falls back to `uvx --from git+https://github.com/github/spec-kit.git`
- Handles Windows Unicode issues with PYTHONIOENCODING/PYTHONUTF8 env vars
- Integrated into `TemplateEngineTool` — Spec Kit auto-initializes on every new project scaffold (CLI and fallback paths)
- Graceful skip when `specify` CLI is not available (no errors, just skips)
- Registered in AgentLoop's default tools

### Tests
- 17 new tests (1270 total passing)
- `test_boss_fastpath.py` — 7 tests for Boss fast-path during autopilot
- `test_speckit_tool.py` — 10 tests for Spec Kit tool and auto-init

## 0.6.0 — 2026-03-24 — Vibe-Coding Engine (modern project building toolkit)

### New Tools (6 tools, all registered in AgentLoop)

- **TemplateEngineTool** (`scaffold_project`) — Modern project scaffolding via real CLI tools (create-next-app, create-vite, flutter create, create-expo-app, django-admin). Falls back to rich built-in templates (Next.js + Tailwind, Vite + React + TS, Flutter, Expo/React Native, Django, Docker Compose full-stack). 12 template types with alias resolution.
- **GitTool** (`git`) — Structured git operations: init, status, diff, add, commit, branch, checkout, log, push, pull, stash, stash_pop, reset, tag. Replaces raw exec() for all git workflows.
- **LivePreviewTool** (`live_preview`) — Automated build → preview → iterate loop. Auto-detects framework (Next.js, Vite, Django, Flask, FastAPI, Expo, Flutter), starts dev server, installs deps, navigates browser, takes screenshot. Actions: start, refresh, stop, status.
- **PackageManagerTool** (`package_manager`) — Unified dependency management across 10 package managers: npm, yarn, pnpm, bun, pip, uv, poetry, flutter, cargo, go. Auto-detects from lock files. Actions: install, add, remove, list, outdated, audit, run, detect.
- **TestRunnerTool** (`test_runner`) — Structured test execution with parsed results. Auto-detects pytest/jest/vitest/flutter/cargo/go. Parses output into pass/fail counts, failure details, coverage info.
- **DeployTool** (`deploy`) — One-command deployment. Auto-detects target (Vercel, Netlify, Docker, Docker Compose). Actions: deploy, preview, status, detect, build.

### Enriched Agent Profiles

- **mobile_dev** — Now includes React Native/Expo and Flutter framework knowledge, CLI commands, state management patterns, navigation setup, and common mobile patterns.
- **frontend_dev** — Now includes Next.js and Vite workflow knowledge, Tailwind CSS patterns, and a complete scaffold → preview → test → deploy workflow.
- **fullstack_dev** — Now includes full-stack Docker Compose knowledge, API-first design patterns, and cross-stack workflow.
- **backend_dev** — Now includes FastAPI, Django, and Express framework knowledge with specific CLI commands and best practices.

### Analysis Report

- Created `VIBECODING_IMPROVEMENT_REPORT.md` — comprehensive analysis of SharanBot's vibe-coding capabilities with 10 identified gaps, severity ratings, comparison table vs Cursor/Bolt/v0, and 3-phase implementation plan.

### Stats
- 61 new tests (1253 total passing)
- 6 new tools registered in AgentLoop
- 4 enriched agent profiles with real framework knowledge

## 0.5.0 — 2026-03-24 — Self-Evolution Engine (REAL autonomous self-improvement)

### Self-Evolution Engine (`sharanbot/agi/evolution.py`)
- **Self-Diagnosis Engine** — Analyzes observability data, error patterns, stuck patterns, circuit breaker states, and performance metrics to identify what needs improving
- **Improvement Planner** — LLM-powered planning that turns diagnoses into concrete code changes (not just bug reports like agent-orchestrator)
- **Safe Self-Modification Pipeline** — Git checkpoint → modify → test → rollback-if-failed → commit-if-passed
- **Growth Tracker** — Persistent log of all evolution attempts, successes, failures, and new capabilities added over time
- **Feedback Loop** — Background task failures automatically trigger evolution cycles
- **Rate Limiting** — Max 1 cycle per 10 minutes, max 20 per day to prevent runaway self-modification
- **Safety Boundaries** — Cannot modify security, vault, auth, or safety code (enforced by SafetyBoundary)
- **High-Risk Approval** — Changes to core code require human approval before execution
- 5 evolution triggers: repeated_error, stuck_pattern, performance_degradation, task_failure, manual

### New CLI Commands
- `/evolve` — Run a self-evolution cycle (diagnose + plan + execute)
- `/evolve status` — Show evolution stats and growth history
- `/evolve approve` / `/evolve reject` — Approve or reject high-risk evolution plans

### AGI Tool Extensions
- `evolve_diagnose` — Run self-diagnosis and list issues
- `evolve_run` — Trigger an evolution cycle
- `evolve_status` — Show evolution engine status
- `evolve_approve` / `evolve_reject` — Manage pending high-risk plans

### Bug Fixes
- Fixed `spawn_self_build` tool importing non-existent classes (`SelfBuildAnalyzer`, `SelfBuildValidator`, `SafetyChecker`) — now uses correct `CodebaseAnalyzer`, `SafetyBoundary`
- Background task failures now trigger self-evolution for autonomous improvement

## 0.4.0 — 2026-03-24 — Reaction Engine & Observability (agent-orchestrator fusion)

### Event-Driven Reaction System (`sharanbot/agi/reactions.py`)
- Configurable reactions for 12 event types (task.failed, task.stuck, ci.failed, rate.limited, team.deadlock, quality.gate_failed, etc.)
- 5 reaction actions: send-to-agent, notify, retry, pause, escalate
- Automatic retry tracking with configurable max retries
- Time-based and count-based escalation to human
- Global pause on rate limits — prevents new task spawns during cooldown
- Priority-based notification routing (urgent/action/warning/info)
- Full stats tracking and event log

### Observability System (`sharanbot/agi/observability.py`)
- Structured metrics with success/failure counters for all operations
- Trace recording for significant operations (tool calls, LLM calls, task lifecycle)
- Health surface monitoring (ok/warn/error) for subsystems
- Per-session operation history tracking
- Periodic disk persistence for dashboard consumption
- Human-readable status formatting for `/status` command

### Task Decomposition with Lineage & Sibling Context
- TaskNode now carries `lineage` (ancestor task descriptions root→parent)
- DAG.get_siblings() returns descriptions of parallel tasks with same dependencies
- TaskPlanner.decompose() accepts lineage context for recursive decomposition
- Team orchestrator injects lineage hierarchy and sibling awareness into agent prompts
- Agents know their place in the task hierarchy and avoid duplicating sibling work

### Enhanced Stuck Detection
- Configurable time-based idle threshold (default 120s)
- Progress stall detection — detects when tool calls return same results repeatedly
- Configurable repeat_threshold, error_streak_threshold, idle_threshold_s
- New stats: idle_s, progress_stall_count

### Integration
- Reaction engine wired into agent loop: stuck detection → reaction → escalation
- Reaction engine wired into team orchestrator: deadlock → reaction, quality gate → reaction
- Background task failures trigger retry reactions before notifying user
- Task completion clears all reaction trackers for the session
- `/status` shows reaction engine stats and global pause status
- Observability records tool calls, errors, stuck detections, task lifecycle
- Global pause blocks complex task dispatch during rate limit cooldown

### Tests
- 40 new tests (1164 total, all passing)
- `test_reactions.py` — 18 tests for reaction engine
- `test_observability.py` — 12 tests for observability system
- `test_lineage_context.py` — 10 tests for lineage, siblings, enhanced stuck detection

## 0.3.0 — 2026-03-24 — SharanAGI: The Digital Human

### SharanAGI Core (5 new subsystems)
- **Watchdog Patrol** (`sharanbot/agi/watchdog.py`) — proactive background health checks, incomplete work detection, stale service monitoring, auto-action on critical findings
- **Human-in-the-Loop Escalation** (`sharanbot/agi/escalation.py`) — CAPTCHA/2FA/judgment/stuck escalation with async ask/wait/respond, timeout support, cross-channel delivery
- **Persistent Project Context** (`sharanbot/agi/projects.py`) — multi-project registry with per-project memory, decisions, TODOs, deadline-aware priority queue, cross-project pattern search
- **Knowledge Accumulation** (`sharanbot/agi/knowledge.py`) — persistent technical knowledge base, LLM-powered study agent, skill self-assessment (novice→expert), pre-task study checks, prompt injection
- **Stuck Detection + Confidence Scoring** (`sharanbot/agi/stuck.py`) — 4 detection methods (repetition, error streak, oscillation, high error rate), hedge phrase analysis, automatic escalation trigger

### Integration
- Agent loop wired with all AGI components: stuck detection per tool call, knowledge context injection, escalation interception, auto-learn from completed tasks
- Gateway startup creates Watchdog, wires escalation send callback, adds AGI status logging
- New `agi` tool with 14 actions: project CRUD, knowledge learn/recall/skills, ask_human, stuck_status, watchdog_status

### Fixes (from audit)
- Fixed vault XOR fallback when `cryptography` not installed
- Added team state persistence (`.team_state/` directory)
- Added rate limiting semaphore to helpers
- Bundled all 29 agent profile YAMLs in templates
- Fixed provider `__init__.py` re-export for test compatibility

### Stats
- 1080 tests passing (49 new AGI tests)
- 5 new modules, 1 new tool, 2 modified core files

## 0.2.0 — 2025-03-24

### Phase 1 — Stabilization
- Fixed DAG cycle detection in orchestrator
- Hardened dashboard auth with constant-time token comparison
- Added input sanitization for exec tool command injection
- Fixed MCP schema normalizer for nullable unions
- Fixed context summarizer to use truncated content
- Corrected agent loop iteration guard and empty-response handling
- Pinned critical dependency versions
- Added `test_agent_loop.py` (20 tests covering loop fundamentals)
- Added `test_memory.py` (memory consolidation round-trip tests)
- Added `test_dag_cycles.py` (cycle detection correctness tests)

### Phase 2 — Optimization
- Merged `AdaptiveLearningEngine` into `LearningStore` (single learning module)
- Extracted `ToolManager` from `loop.py` (tool registration, MCP lifecycle, hot-reload)
- Absorbed `context_tracker.py` into `context.py` (backward-compatible re-export shim)
- Hardened `evolve.py` with AST validation and backup/rollback on failure
- Added TTL (90 days) and cap (10 000 entries) to learning store
- Introduced atomic state writes (`write→fsync→rename`) for JSONL/JSON persistence
- Improved token estimation using tiktoken with char-length fallback
- Added `test_evolve.py` (12 tests) and `test_mcp_lifecycle.py` (12 tests)

### Phase 3 — Feature Upgrades
- **Content-aware quality gates** — LLM-powered artifact quality scoring in phase gates
- **Auto-test generation** — self-build pipeline generates pytest skeletons alongside code
- **Root cause analysis** — LLM-powered diagnostics in recovery engine before applying fixes
- **Browser backend config** — `browser_backend` setting: `auto` / `tandem` / `playwright` with fallback
- **Reconnection jitter** — exponential backoff with ±25% random jitter on WhatsApp bridge retry
- **MCP connection pooling** — persistent MCP sessions with periodic health checks and auto-reconnect
- **Cross-project learning** — export/import high-confidence patterns via global `~/.sharanbot/global_learning/`
- **Complete config reload** — full provider, channel, tool, and MCP server reloading on config change
- **Integration test suite** — 10 end-to-end tests (Channel→Bus→Agent→Tool→Response)

## 0.1.4.post5

Initial public pre-release.
