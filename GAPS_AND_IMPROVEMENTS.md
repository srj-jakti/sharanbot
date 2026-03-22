# SharanBot — Gaps & Improvements Found During Live Testing

**Date:** 2025-01-XX  
**Testing:** 7 live Telegram tests covering math, NLU, file ops, shell, web search, code gen, team orchestration  
**Result:** All core functions working. Critical DAG deadlock fixed. Remaining items below.

---

## FIXED (This Session)

### 1. DAG Deadlock on Failed Dependencies (CRITICAL — FIXED)
- **File:** `sharanbot/agent/team/dag.py`
- **Problem:** `get_ready_tasks()` only checked `status == "completed"` for dependencies. When any dep failed, all downstream tasks stayed `pending` forever → deadlock.
- **Fix:** Check for `completed`, `failed`, or `skipped` statuses.
- **Commit:** `1bed051`

### 2. Orchestrator Infinite Loop on Deadlock (CRITICAL — FIXED)
- **File:** `sharanbot/agent/team/orchestrator.py`
- **Problem:** If no tasks were ready and none were running, the orchestrator looped forever with `await asyncio.sleep(1)`.
- **Fix:** Added `deadlock_wait_rounds` counter (max 3). After 3 idle rounds, all remaining pending tasks are marked `skipped` with "dependency deadlock" error.
- **Commit:** `1bed051`

### 3. Context Window Consolidation Too Late (FIXED)
- **File:** `sharanbot/agent/memory.py`
- **Problem:** Consolidation only triggered at 100% of context window — by then it's too late.
- **Fix:** Trigger at 75%, consolidate down to 50%.
- **Commit:** `cf051bb`

---

## REMAINING GAPS

### 4. Team Subagents Missing MCP Tools (MEDIUM)
- **File:** `sharanbot/agent/team/orchestrator.py` → `_run_task_as_subagent()`
- **Problem:** Team subagents only get filesystem, exec, web-search, and web-fetch tools. MCP server tools (filesystem MCP, memory MCP), browser (Playwright), and sandbox (Docker) tools are NOT registered.
- **Impact:** Team agents can't use MCP-based memory, can't browse websites, can't use Docker sandboxes.
- **Fix:** Import and register `BrowserTool`, `SandboxTool`, and connect MCP servers in `_run_task_as_subagent()`.

### 5. No Team-Level Timeout (MEDIUM)
- **Problem:** If a subagent hangs (e.g., waiting for LLM response), the orchestrator waits indefinitely.
- **Fix:** Add `asyncio.wait_for()` wrapper around `_run_task_as_subagent()` calls with a configurable per-task timeout (e.g., 5 minutes default).

### 6. MCP Cleanup RuntimeError (LOW — COSMETIC)
- **Error:** `RuntimeError: Attempted to exit cancel scope in a different task than it was entered in`
- **Cause:** MCP SDK cancel scope cleanup issue when async generators are closed during shutdown.
- **Impact:** Logged error only, no functional impact.
- **Fix:** Wrap MCP cleanup in try/except or update MCP SDK when fix is available.

### 7. Unicode Emoji Encoding on Windows (LOW — COSMETIC)
- **Problem:** `print()` fails with `UnicodeEncodeError: 'charmap' codec can't encode character` for emoji characters (✅, 🇫🇷, etc.) on Windows console.
- **Impact:** Only affects console output/logging. Actual Telegram responses are correct.
- **Fix:** Set `PYTHONIOENCODING=utf-8` or use `sys.stdout.reconfigure(encoding='utf-8')` at startup.

### 8. Task Planner May Generate Invalid Dependency Chains (LOW)
- **Problem:** The LLM-based task planner can generate subtasks with dependency chains that reference non-existent task IDs or create circular dependencies.
- **Impact:** DAG validation catches this, but the team then fails to start.
- **Fix:** Add retry logic in planner — if DAG validation fails, re-prompt LLM with the error.

### 9. No Partial Team Results on Failure (LOW)
- **Problem:** When a team partially completes (some tasks succeed, some fail/skip), the final summary doesn't clearly separate successful outputs from failures.
- **Fix:** Enhance `_collect_results()` to categorize outputs by status and present a structured summary.

---

## POTENTIAL IMPROVEMENTS

### A. Streaming Responses for Telegram
- Currently waits for full LLM response before sending. For long answers, user sees nothing until complete.
- Could send partial responses or "typing..." indicators.

### B. Team Re-Planning on Failure
- When critical tasks fail, the team could re-plan with remaining objectives instead of just skipping downstream tasks.

### C. Agent Profile Auto-Selection Improvement
- The planner picks profiles based on task description matching. Could be improved with embedding-based similarity or a dedicated classifier.

### D. Persistent Team State
- Team sessions are in-memory only. If the bot restarts mid-team, all progress is lost.
- Could persist team state to disk for resume capability.

### E. Rate Limiting / Throttling
- No protection against Kimi API rate limits when running teams (multiple parallel LLM calls).
- Should add exponential backoff and concurrent request limits.
