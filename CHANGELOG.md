# Changelog

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
