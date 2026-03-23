# SharanBot vs Airabot — Full Comparison Report

## Executive Summary

Both projects are AI agent frameworks with chat interfaces, tool execution, and autonomous capabilities. They share the **same DNA** (identical module names like `bus/`, `channels/`, `config/`, `session/`, `skills/`, `templates/`, `agent/tools/`), suggesting they forked from a common ancestor.

**Airabot's edge**: A sophisticated **multi-agent orchestration pipeline** that breaks down full-stack development into 7 phases with 43 specialized roles, isolated git worktrees, contract-enforced interfaces, and CI-gated merges. This is why it can "create full-stack applications with 100% accuracy."

**SharanBot's edge**: Stronger **operational infrastructure** — 13 channel adapters (vs Airabot's similar set), Subconscious memory system (8 persistent blocks), Wingman/Tandem browser integration, circuit breaker pattern in error recovery, dashboard, and more polished session management.

---

## Architecture Comparison

| Dimension | SharanBot | Airabot |
|-----------|-----------|---------|
| **Language** | Python | Python (same) |
| **Agent Loop** | `AgentLoop` with mixins | `AgentLoop` with 4 mixins (Handlers, Dispatch, MCP, Autonomy) |
| **Memory** | MEMORY.md + HISTORY.md + Subconscious (8 blocks) | MEMORY.md + HISTORY.md + Vector Memory (embeddings) |
| **Learning** | `LearningStore` (JSONL, EMA cooldown) | `KnowledgeStore` + `AdaptiveLearningEngine` + `VectorMemoryStore` |
| **Error Recovery** | `RecoveryEngine` (10 categories, circuit breakers) | `ErrorRecoveryEngine` (10 categories, knowledge store integration) |
| **Team System** | `TeamOrchestrator` + DAG + 6 pipeline templates | `TechLeadOrchestrator` + 7-phase pipeline + 43 roles |
| **Tools** | 21 tool files, ~35 tools | 23 tool files, ~30 tools + creation tools |
| **Worker System** | GSD Node.js workers via JSON-RPC | GSD2 workers + Self-build workers + Browser workers |
| **Channels** | 13 adapters | Similar set |
| **Self-Evolution** | `EvolveTool` + `DynamicToolLoader` + `ReloadTool` | Self-build system (analyzer, validator, safety, rollback) |
| **Deployment** | Manual | `DeploymentPipeline` (auto-Docker, cloud deploy, health checks, rollback) |
| **Git Integration** | None (raw exec) | `BranchManager` (worktrees, CI-gated merge, dependency-ordered) |
| **Context Management** | `ContextBuilder` + `MemoryConsolidator` | `ContextWindowTracker` (token-based) + `ContextSummarizer` (8-section) |
| **Tests** | 730 passing | Similar test suite |

---

## Detailed Module-by-Module Comparison

### 1. AGENT LOOP

| Feature | SharanBot | Airabot |
|---------|-----------|---------|
| Core loop | Message → context → LLM → tools → response | Same pattern |
| Mixin composition | ❌ Monolithic loop | ✅ 4 mixins (Handlers, Dispatch, MCP, Autonomy) |
| Proactive task pickup | ❌ Only responds to messages | ✅ Pulls from TaskQueue during idle |
| Rate limiting | ✅ Present | ✅ Present |
| Approval waiters | ✅ Present | ✅ Present |

**What to steal**: Proactive task pickup from TaskQueue during idle periods.

---

### 2. MEMORY SYSTEMS

| Feature | SharanBot | Airabot |
|---------|-----------|---------|
| Long-term memory | ✅ MEMORY.md | ✅ MEMORY.md |
| History | ✅ HISTORY.md (grep-searchable) | ✅ HISTORY.md |
| Consolidation | ✅ LLM-driven with forced tool_choice | ✅ Same + raw archive fallback after 3 failures |
| Vector memory | ❌ MISSING | ✅ Embeddings + cosine similarity + zvec/numpy |
| Memory extraction | ❌ LLM-only | ✅ Regex-based fast extraction (bilingual) |
| Subconscious | ✅ 8 persistent memory blocks | ❌ Not present |
| Confidence scoring | ❌ | ✅ Per-pattern confidence with decay/growth |

**What to steal**:
1. **Vector Memory Store** — semantic search over memories using embeddings (with SHA-256 hash fallback for zero-cost operation)
2. **Regex-based memory extraction** — fast extraction of facts from conversations without LLM calls (max 2 implicit memories per turn)
3. **Graceful degradation** — raw archive after 3 consolidation failures instead of losing data

---

### 3. LEARNING & ERROR RECOVERY

| Feature | SharanBot | Airabot |
|---------|-----------|---------|
| Error categorization | ✅ 10 categories | ✅ 10 categories (similar) |
| Circuit breakers | ✅ Per-component | ❌ Not present |
| Auto-fix execution | ❌ Suggests fixes | ✅ Auto-executes pip installs, import fixes |
| Knowledge store | ✅ JSONL with EMA cooldown | ✅ JSON with vector-indexed semantic search |
| Self-learning | 🟡 Error patterns only | ✅ Errors + corrections + successful tool chains + preferences |
| Adaptive learning | ❌ | ✅ Project profiles, mistake journal, user preferences |
| Package name mapping | ❌ | ✅ `cv2→opencv-python`, `PIL→Pillow`, etc. |
| WSL path detection | ❌ | ✅ Detects `/mnt/c/` paths on Windows |

**What to steal**:
1. **Auto-execute safe fixes** — pip installs and import corrections can be auto-applied
2. **Package name mapping** — known import→pip-package mappings for instant resolution
3. **Adaptive learning engine** — per-project profiles, structured mistake journal (200-entry cap), growth statistics
4. **Positive pattern learning** — track successful tool chains, not just errors

---

### 4. ORCHESTRATION PIPELINE (🔴 BIGGEST GAP)

| Feature | SharanBot | Airabot |
|---------|-----------|---------|
| Pipeline templates | ✅ 6 templates (build, research, full_stack, etc.) | ✅ 7-phase state machine |
| Phase gates | 🟡 `QualityGate` (LLM-scored) | ✅ `PhaseGateValidator` (artifact-based, deep checks) |
| Specialized roles | 🟡 `AgentProfileRegistry` (generic) | ✅ 43 roles with Definition-of-Done checklists |
| Task planning | ✅ LLM-powered DAG | ✅ LLM + tech lead coordination |
| Parallel execution | ✅ DAG parallelism | ✅ 10 parallel workers with semaphore |
| Git isolation | ❌ All in same directory | ✅ Per-worker git worktrees |
| Merge ordering | ❌ | ✅ Infra→Backend→Frontend→Integration |
| CI gating | ❌ | ✅ flake8→mypy→pytest→build before merge |
| Contract management | ❌ | ✅ Shared API specs, DB schemas, design tokens |
| Shared memory | ❌ | ✅ Importance-weighted cross-worker memory |
| Agency expertise injection | ❌ | ✅ 20+ domain-expert persona files injected into prompts |

**What to steal** (HIGH PRIORITY):
1. **Phase Gate Validator** — artifact-based validation (BRD must have requirements, sprint plans checked for circular deps, test results ≥80% pass rate)
2. **Contract Manager** — shared interface definitions between team agents (API spec, DB schema, component interfaces, design tokens) with amendment proposals
3. **Branch Manager** — git worktree isolation per worker, CI-gated merge, dependency-ordered merge (Infra→Backend→Frontend→Integration)
4. **Shared Memory Store** — importance-weighted (1-10) cross-worker memory with eviction (drops entries <importance 4 when >100 entries)
5. **43 specialized role prompts** — each with domain expertise description + Definition-of-Done checklist
6. **Post-development pipeline** — code review → CI → ordered merge

---

### 5. SELF-BUILD / SELF-EVOLUTION

| Feature | SharanBot | Airabot |
|---------|-----------|---------|
| Self-modification | ✅ `EvolveTool` (templates) | ✅ LLM agent loop with AST analysis |
| Safety boundaries | ✅ Builtin name protection | ✅ Glob-pattern file protection (can't modify own safety code) |
| Codebase analysis | ❌ | ✅ AST-based scanner (modules, classes, functions, imports, deps) |
| Regression detection | ❌ | ✅ Baseline comparison (newly-failing tests identified) |
| Rollback | ❌ | ✅ Git branch-based automatic rollback |
| Hot reload | ✅ `ReloadTool` | ❌ Not mentioned |
| Dynamic discovery | ✅ `DynamicToolLoader` | ❌ Not mentioned |

**What to steal**:
1. **Codebase analyzer** — AST-based scanner providing full module/class/function/import/dependency graph before any self-modification
2. **Regression detection** — capture baseline test results, then after modification, identify specifically which tests went from passing to failing
3. **Git-based rollback** — all self-modifications happen on a `self-build/<session_id>` branch; on test failure, automatic `git checkout original && git branch -D safety-branch`

---

### 6. AUTONOMOUS OPERATIONS

| Feature | SharanBot | Airabot |
|---------|-----------|---------|
| Heartbeat | ✅ Periodic wake-ups | ✅ Similar |
| Watchdog | 🟡 ServiceManager (auto-restart 3x) | ✅ Full watchdog (stuck detection, progressive recovery, checkpoint resume) |
| Parallel engine | 🟡 DAG-based team execution | ✅ Priority queue with chat fast-lane (2 reserved chat slots) |
| Task queue | ❌ | ✅ Persistent JSONL queue with priority, retry, project grouping |
| Checkpoint resume | ❌ | ✅ Saves/loads checkpoints, resumes after crashes |
| Model routing | ✅ Provider-based | ✅ Cost-aware routing (simple→cheap model, complex→powerful model) |
| Project tracking | ❌ | ✅ Auto-scans workspace for `package.json`/`pyproject.toml` |
| Deployment | ❌ | ✅ Auto-Docker, cloud deploy, health checks, rollback |

**What to steal**:
1. **Priority task queue** — persistent JSONL with priority ordering, automatic retry, project grouping, subtask tracking
2. **Chat fast-lane** — reserve 2 worker slots exclusively for interactive chat, preventing background work from starving user responses
3. **Checkpoint/resume system** — save autonomy state to disk, resume after crashes (1-hour stale cutoff)
4. **Project tracker** — auto-scan workspace for project indicator files, inject project awareness into system prompt
5. **Cost-aware model routing** — classify task complexity via regex, route simple queries to cheap models

---

### 7. DEPLOYMENT (🔴 COMPLETELY MISSING)

| Feature | SharanBot | Airabot |
|---------|-----------|---------|
| Auto-detect project type | ❌ | ✅ |
| Generate Dockerfile | ❌ | ✅ |
| Generate docker-compose | ❌ | ✅ |
| Local Docker deploy | ❌ | ✅ |
| Cloud deploy (AWS/GCP/Azure) | ❌ | ✅ (via GSD2 workers) |
| Health checks | ❌ | ✅ |
| Auto-rollback | ❌ | ✅ |
| CLI auto-install | ❌ | ✅ |

**What to steal**: The entire `DeploymentPipeline` module.

---

### 8. TOOLS COMPARISON

| SharanBot Has, Airabot Doesn't | Airabot Has, SharanBot Doesn't |
|-------------------------------|-------------------------------|
| `ComputerTool` (desktop automation) | `CreateSoftwareTool` (scaffold complete projects) |
| `WingmanTaskTool` / `WingmanChatTool` | `SpawnDevTeamTool` (trigger full orchestration) |
| `TandemBrowserTool` | `SpawnBrowserTool`, `SpawnCliAnythingTool` |
| `ProcessStartTool` (dev server management) | `SpawnOpenWorkerTool` |
| `ImportAgentTool` | `PaperclipTool` (external sync) |
| `SandboxTool` | `SpawnAgencyAgentTool` (domain expert agents) |
| | `SpawnSelfBuildTool` (trigger self-modification) |
| | `SpawnGSD2Tool` (spawn worker processes) |

**What to steal**:
1. **CreateSoftwareTool** — single tool call that scaffolds an entire project from a `{path: content}` dict
2. **SpawnDevTeamTool** — entry point for triggering the full 7-phase, 43-role pipeline from a simple chat command

---

## PRIORITY IMPLEMENTATION PLAN

### Tier 1 — Critical (Enable Full-Stack App Creation)

| # | Feature | Effort | Impact |
|---|---------|--------|--------|
| 1 | **Contract Manager** — shared API specs/DB schemas between team agents | Medium | 🔴 Critical for multi-agent coordination |
| 2 | **Branch Manager** — git worktree isolation + CI-gated merge + dependency ordering | Medium | 🔴 Critical for parallel worker safety |
| 3 | **Phase Gate Validator** — artifact-based quality checks between pipeline phases | Small | 🔴 Prevents broken builds from progressing |
| 4 | **CreateSoftwareTool** — scaffold complete projects from file dict | Small | 🔴 Missing basic capability |
| 5 | **Shared Memory Store** — importance-weighted cross-worker memory | Small | 🔴 Workers need to share decisions |

### Tier 2 — High Value (Reliability & Intelligence)

| # | Feature | Effort | Impact |
|---|---------|--------|--------|
| 6 | **Vector Memory** — embedding-based semantic search over memories | Medium | 🟡 Transforms memory retrieval |
| 7 | **Persistent Task Queue** — JSONL-backed priority queue with retry | Small | 🟡 Enables autonomous operation |
| 8 | **Regression Detection** — baseline comparison for self-build | Small | 🟡 Safety net for self-modification |
| 9 | **Git Rollback for Self-Build** — branch-based automatic rollback | Small | 🟡 Safety net for self-modification |
| 10 | **Auto-execute safe fixes** — pip install auto-fix on import errors | Small | 🟡 Reduces manual intervention |

### Tier 3 — Nice to Have (Polish & Optimization)

| # | Feature | Effort | Impact |
|---|---------|--------|--------|
| 11 | **Adaptive Learning Engine** — project profiles, mistake journal | Medium | 🟢 Cross-session intelligence |
| 12 | **Memory Extractor** — regex-based fast memory extraction | Small | 🟢 Reduces LLM calls |
| 13 | **Cost-Aware Model Router** — classify tasks, route to appropriate model | Small | 🟢 Cost optimization |
| 14 | **Project Tracker** — auto-scan workspace, inject awareness | Small | 🟢 Better context |
| 15 | **Checkpoint/Resume** — crash recovery for autonomous operations | Medium | 🟢 Reliability |
| 16 | **Deployment Pipeline** — auto-Docker + health checks | Large | 🟢 End-to-end delivery |
| 17 | **43 Role Prompts** — specialized prompts with Definition of Done | Medium | 🟢 Better role specialization |
| 18 | **Chat Fast-Lane** — reserve worker slots for interactive chat | Small | 🟢 UX improvement |

---

## What Makes Airabot Build Full-Stack Apps "With 100% Accuracy"

The secret is **NOT a single feature** — it's the **combination of 6 mechanisms**:

1. **7-Phase Lifecycle** (`pipeline.py`) — Forces structured progression: Discovery → Architecture → Sprint Planning → Development → QA → Deployment → Post-Launch. No phase can be skipped accidentally.

2. **Phase Gates** (`phase_gate.py`) — Each phase MUST produce concrete artifacts. BRD must contain requirements, sprint plans are checked for circular dependencies, test results must have ≥80% pass rate. No hand-waving.

3. **43 Specialized Roles** (`role_prompts.py`) — Each worker gets a domain-specific prompt with Definition of Done checklist. A "Database Dev" knows DB schema design; a "Frontend Logic Dev" knows state management patterns. The LLM doesn't have to be a generalist.

4. **Contract-Based Coordination** (`contract_manager.py`) — All workers reference shared API specs, DB schemas, and component interfaces. The frontend dev builds against the same API spec that the backend dev implements. No drift.

5. **Git Isolation + Ordered Merge** (`branch_manager.py`) — Each worker operates in its own git worktree. Merges happen in dependency order (Infra→Backend→Frontend→Integration) with CI gating. No conflicts from parallel work.

6. **Proof of Work + Verification** (`proof_of_work.py`) — Workers can't just claim success — the system verifies evidence (tool calls were made, files were changed, no recent errors). Trust-but-verify.

SharanBot has pieces of this (DAG pipelines, quality gates, agent profiles) but lacks the **integration glue**: contracts, branch isolation, ordered merge, artifact validation, and the comprehensive role library.

---

## Quick Wins (Can Implement Today)

1. **CreateSoftwareTool** (~50 lines) — Accept `{path: content}` dict, write files, report result
2. **Package name mapping** in recovery.py — Add `cv2→opencv-python` etc. mapping for auto-resolution
3. **Auto-execute pip install** on import errors — When error is `ModuleNotFoundError`, auto-run `pip install <package>`
4. **Phase gate artifact validation** in `quality.py` — Check that required files exist, not just LLM scoring
5. **Shared memory for teams** in `team/bus.py` — Add importance-weighted key-value store

---

*Report generated from file-by-file analysis of both codebases.*
*Airabot analyzed: 50+ files across agent/, orchestration/, workers/, autonomy/, context/, artifacts/*
*SharanBot analyzed: 40+ files across agent/, tools/, team/, services/, providers/*
