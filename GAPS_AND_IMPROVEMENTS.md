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

### 4. Team Subagents Missing MCP Tools (MEDIUM — FIXED)
- **File:** `sharanbot/agent/team/orchestrator.py` → `_run_task_as_subagent()`
- **Problem:** Team subagents only get filesystem, exec, web-search, and web-fetch tools. MCP server tools (filesystem MCP, memory MCP), browser (Playwright), and sandbox (Docker) tools are NOT registered.
- **Impact:** Team agents can't use MCP-based memory, can't browse websites, can't use Docker sandboxes.
- **Fix:** Added BrowserTool and SandboxTool registration (with graceful skip if deps missing).
- **Commit:** `4070858`

### 5. No Team-Level Timeout (MEDIUM — FIXED)
- **Problem:** If a subagent hangs (e.g., waiting for LLM response), the orchestrator waits indefinitely.
- **Fix:** Added `asyncio.wait_for()` wrapper with configurable `task_timeout` (default 5 minutes). Timed-out tasks are marked as failed.
- **Commit:** `4070858`

### 6. MCP Cleanup RuntimeError (LOW — FIXED)
- **Error:** `RuntimeError: Attempted to exit cancel scope in a different task than it was entered in`
- **Cause:** MCP SDK cancel scope cleanup issue when async generators are closed during shutdown.
- **Fix:** `_reconnect_mcp()` now catches `RuntimeError` and `BaseExceptionGroup` (matching `close_mcp()` behavior).
- **Commit:** `4070858`

### 7. Unicode Emoji Encoding on Windows (LOW — FIXED)
- **Problem:** `print()` fails with `UnicodeEncodeError: 'charmap' codec can't encode character` for emoji characters.
- **Fix:** Added `sys.stdout.reconfigure(encoding='utf-8', errors='replace')` at startup in `__main__.py`.
- **Commit:** `4070858`

### 8. Task Planner May Generate Invalid Dependency Chains (LOW — FIXED)
- **Problem:** The LLM-based task planner can generate subtasks with invalid dep refs or circular dependencies.
- **Fix:** `from_plan()` now strips invalid dep IDs and detects cycles. `decompose()` retries up to 3x on validation failure, falls back to single-task plan.
- **Commit:** `4070858`

### 9. No Partial Team Results on Failure (LOW — FIXED)
- **Problem:** When a team partially completes, the final summary doesn't separate successes from failures.
- **Fix:** Results now categorized into Completed/Failed/Skipped sections with status icons.
- **Commit:** `4070858`

---

## LIVE CHALLENGE TEST RESULTS (Round 2)

### Challenge: 3D Car Racing Game (React Native/Expo)
**Date:** 2026-03-22  
**Test:** Build complete 3D racing game with research → code → bundling → QR code

#### Round 1 (Before Fixes):
- **Time:** 746.5s (12.4 min) — hit max iteration limit
- **Iterations used:** 100/100 (hit limit, task incomplete)
- **team_status polls:** ~70 out of 100 iterations (70% wasted)
- **Result:** Files created but task marked incomplete

#### Round 2 (After Fixes):
- **Time:** 336.9s (5.6 min) — completed successfully
- **Iterations used:** ~11 tool calls total
- **team_status polls:** 0 (agent built directly)
- **Result:** ✅ Complete project with 15 files, summary, instructions

#### Gaps Found & Fixed:

### 10. Team Status Polling Loop Wastes Iterations (CRITICAL — FIXED)
- **File:** `sharanbot/agent/team/tools.py` → `TeamStatusTool`
- **Problem:** Agent called `team_status` ~70 times in a tight loop (~2s intervals), wasting 70% of its iteration budget on busy-polling.
- **Fix:** Added adaptive backoff sleep (15s→30s) when team is still running. Response includes anti-polling hint. Tool description warns against tight-loop polling.
- **Commit:** `95a4a75`

### 11. Max Tool Iterations Too Low for Complex Tasks (CRITICAL — FIXED)
- **Files:** `sharanbot/config/schema.py`, user's `config.json`
- **Problem:** Default 40 iterations (user had 100) insufficient for complex multi-phase tasks with team orchestration + file creation.
- **Fix:** Increased default from 40 to 200. Updated user config to 200.
- **Commit:** `95a4a75`

### 12. Team Task Timeout Too Short (MEDIUM — FIXED)
- **File:** `sharanbot/agent/team/orchestrator.py`
- **Problem:** Default 300s (5min) timeout caused complex LLM-driven subtasks (design/research) to fail.
- **Fix:** Increased default from 300s to 600s (10min).
- **Commit:** `95a4a75`

### 13. TeamCreateTool Missing Anti-Polling Guidance (LOW — FIXED)
- **File:** `sharanbot/agent/team/tools.py` → `TeamCreateTool.execute()`
- **Problem:** team_create response didn't warn the agent to avoid excessive status polling.
- **Fix:** Added guidance message: "Do NOT poll team_status in a loop. Work on other aspects instead."
- **Commit:** `95a4a75`

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
