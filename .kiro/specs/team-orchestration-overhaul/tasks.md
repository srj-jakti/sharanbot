# Implementation Plan: Team Orchestration Overhaul

## Overview

This plan implements the full team orchestration overhaul addressing the split-brain failure mode discovered across 4 rounds of E2E testing. The implementation follows a bottom-up approach: data model changes first, then core logic, then wiring, then cleanup. Each task builds on the previous, and property-based tests validate correctness properties from the design document using Hypothesis.

## Tasks

- [x] 1. Update TaskNode data model and add comments field
  - [x] 1.1 Add `comments` and `_last_read_index` fields to TaskNode in `sharanbot/agent/team/dag.py`
    - Add `comments: list[dict[str, Any]] = field(default_factory=list)` to TaskNode dataclass
    - Add `_last_read_index: int = 0` to TaskNode dataclass
    - _Requirements: 4.1_

  - [ ]* 1.2 Write property test for heartbeat unread comments (Property 3)
    - **Property 3: Heartbeat returns unread comments**
    - Given a TaskNode with N comments and `_last_read_index` K, reading comments[K:] returns exactly N-K items and advances `_last_read_index` to N
    - **Validates: Requirements 2.3, 4.3, 7.3**

- [x] 2. Implement atomic task checkout in TeamOrchestrator
  - [x] 2.1 Add `_task_checkouts` dict and `checkout_task`/`release_task` methods to `sharanbot/agent/team/orchestrator.py`
    - Add `self._task_checkouts: dict[str, str] = {}` to `__init__`
    - Implement `checkout_task(task_id, agent_id) -> bool` with mutual exclusion logic
    - Implement `release_task(task_id) -> None`
    - Call `checkout_task` in `_execute_task` before spawning subagent
    - Call `release_task` in `_execute_task` finally block
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [ ]* 2.2 Write property test for task checkout round-trip (Property 5)
    - **Property 5: Task checkout round-trip**
    - For any task_id and agent_id, checkout returns True and stores mapping; release removes it
    - **Validates: Requirements 3.2, 3.4**

  - [ ]* 2.3 Write property test for task checkout mutual exclusion (Property 6)
    - **Property 6: Task checkout mutual exclusion**
    - For any task_id already checked out by agent_A, checkout by agent_B returns False and leaves mapping unchanged
    - **Validates: Requirements 3.3**

- [x] 3. Implement heartbeat check-in model
  - [x] 3.1 Add `_heartbeat_check` method to TeamOrchestrator in `sharanbot/agent/team/orchestrator.py`
    - Implement `_heartbeat_check(session, task, agent_id) -> tuple[str, list[dict]]`
    - Check session.status for "cancelled"/"completed" → return "exit"
    - Check `_task_checkouts` for reassignment → return "exit"
    - Collect unread comments from `task.comments[task._last_read_index:]` and advance index
    - Return "continue" with new comment messages
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x] 3.2 Integrate heartbeat into `_run_task_as_subagent` loop in `sharanbot/agent/team/orchestrator.py`
    - Replace `session.bus.get_pending(agent_id)` with `_heartbeat_check` call at start of each iteration
    - On "exit" action, break the loop and return cancellation message
    - On "continue", inject new comment messages into LLM message history
    - _Requirements: 2.1, 2.3, 7.3_

  - [ ]* 3.3 Write property test for heartbeat exit on cancelled/completed session (Property 2)
    - **Property 2: Heartbeat exits on cancelled or completed session**
    - For any session with status "cancelled" or "completed", heartbeat returns "exit"
    - **Validates: Requirements 2.2, 5.3**

  - [ ]* 3.4 Write property test for heartbeat exit on task reassignment (Property 4)
    - **Property 4: Heartbeat exits on task reassignment**
    - For any task checked out by agent_A, heartbeat called with agent_B returns "exit"
    - **Validates: Requirements 2.4**

- [x] 4. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement Mode A/B enforcement in Autopilot
  - [x] 5.1 Add MODE_B_ALLOWLIST and allowlist-based tool filtering in `sharanbot/agent/team/autopilot.py`
    - Define `MODE_B_ALLOWLIST = frozenset({"team_create", "team_status", "team_steer", "read_file", "list_dir", "web_search", "web_fetch", "wait"})`
    - Replace the existing `_blocked_tools` blocklist in `execute()` with allowlist filtering: `tool_defs = [t for t in self._tools.get_definitions() if t.get("function", {}).get("name") in MODE_B_ALLOWLIST]`
    - Keep Mode A path unchanged (full tool access)
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [x] 5.2 Add cancel_team on Autopilot exit in `sharanbot/agent/team/autopilot.py`
    - At the end of `execute()`, if `decision.strategy == "team"` and `self._team_orchestrator`, iterate over teams and call `cancel_team` for any running sessions
    - _Requirements: 5.1_

  - [x] 5.3 Lower token consolidation threshold from 80% to 60% in `sharanbot/agent/team/autopilot.py`
    - Change `threshold = int(self._context_window_tokens * 0.80)` to `threshold = int(self._context_window_tokens * 0.60)`
    - _Requirements: 11.1, 11.2_

  - [ ]* 5.4 Write property test for mode-based tool filtering (Property 1)
    - **Property 1: Mode-based tool filtering**
    - For any set of tool definitions and strategy "team", filtered set contains only MODE_B_ALLOWLIST names; for non-"team" strategy, filtered set equals original
    - **Validates: Requirements 1.1, 1.2, 1.3**

  - [ ]* 5.5 Write property test for token consolidation threshold (Property 12)
    - **Property 12: Token consolidation threshold at 60%**
    - For any positive integer context_window_tokens, threshold equals `int(context_window_tokens * 0.60)`
    - **Validates: Requirements 11.1, 11.2**

- [x] 6. Implement task-based steering and cancel_team enhancement
  - [x] 6.1 Rewrite `steer_team` to use TaskNode comments in `sharanbot/agent/team/orchestrator.py`
    - Replace `bus.broadcast` with appending comment dicts to TaskNode.comments for tasks with status "running" or "pending"
    - Comment format: `{"from": "orchestrator", "content": instruction, "timestamp": time.time()}`
    - Return count of targeted tasks
    - _Requirements: 4.2, 7.1, 7.2_

  - [x] 6.2 Enhance `cancel_team` with asyncio task tracking in `sharanbot/agent/team/orchestrator.py`
    - Add `_task_handles: list[asyncio.Task]` to TeamSession dataclass
    - Track asyncio.current_task() in `_execute_task_throttled`
    - In `cancel_team`: set status to "cancelled", clear bus, cancel all task handles, release all checkouts
    - _Requirements: 5.2, 5.3, 5.4_

  - [ ]* 6.3 Write property test for steer appends to active tasks (Property 7)
    - **Property 7: Steer appends comments to active tasks**
    - For any DAG with mixed statuses, steer appends to "running"/"pending" tasks only, not "completed"/"failed"/"skipped"
    - **Validates: Requirements 4.2, 7.2**

  - [ ]* 6.4 Write property test for cancel team sets status (Property 8)
    - **Property 8: Cancel team sets status to cancelled**
    - For any running TeamSession, cancel_team sets status to "cancelled" and clears the bus
    - **Validates: Requirements 5.2**

- [x] 7. Fix project name derivation and port assignment
  - [x] 7.1 Rewrite `_derive_project_name` in `sharanbot/agent/team/manifest.py` (or `orchestrator.py`)
    - Add emoji/Unicode symbol stripping via regex
    - Normalize to lowercase before pattern matching
    - Add fallback: extract first 3 significant words (excluding stop words)
    - Ensure output is filesystem-safe: `[a-z0-9-]+`, max 40 chars, no leading/trailing hyphens
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

  - [x] 7.2 Fix port range and add availability check in `sharanbot/agent/team/manifest.py`
    - Change `_DEFAULT_PORT_RANGE` from `(3000, 9000)` to `(8000, 9000)`
    - Update `assign_port` to verify existing cached port is still free via `_is_port_free`
    - Add `_is_port_free(port) -> bool` static method using socket bind test
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

  - [ ]* 7.3 Write property test for project name filesystem safety (Property 9)
    - **Property 9: Project name is filesystem-safe**
    - For any input string (emoji, Unicode, mixed case), output matches `[a-z0-9-]+`, length <= 40, non-empty, no leading/trailing hyphens
    - **Validates: Requirements 8.1, 8.2, 8.4**

  - [ ]* 7.4 Write property test for project name fallback (Property 10)
    - **Property 10: Project name fallback extracts significant words**
    - For any task without build verbs but with 3+ significant words, result is NOT `project-{team_id}`
    - **Validates: Requirements 8.3, 8.5**

  - [ ]* 7.5 Write property test for assigned ports bindable (Property 11)
    - **Property 11: Assigned ports are bindable**
    - For any service name, assigned port is >= 8000 and bindable on 127.0.0.1 (with mocked socket)
    - **Validates: Requirements 9.1, 9.2**

- [x] 8. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Create wait tool and fix SessionManager bug
  - [x] 9.1 Create `sharanbot/agent/tools/wait.py` with WaitTool class
    - Implement Tool subclass with name="wait", accepts `seconds` parameter
    - Clamp duration to `min(max(0, seconds), 300)`
    - Execute `asyncio.sleep(duration)` and return confirmation message
    - _Requirements: 12.1, 12.2, 12.3_

  - [x] 9.2 Register WaitTool in the tool registry
    - Import and register WaitTool in the appropriate tool registration code so it's available in both Mode A and Mode B
    - _Requirements: 12.4_

  - [x] 9.3 Fix `_sessions` → `_cache` attribute reference in `sharanbot/agent/loop.py`
    - Change `self.sessions._sessions` to `self.sessions._cache` at the relevant line
    - _Requirements: 10.1, 10.2_

  - [ ]* 9.4 Write property test for wait tool duration capping (Property 13)
    - **Property 13: Wait tool duration capping**
    - For any requested duration (negative, zero, large), actual duration equals `min(max(0, requested), 300)`
    - **Validates: Requirements 12.3**

- [x] 10. Improve manifest tracking and structured completion parsing
  - [x] 10.1 Enhance `_update_manifest_from_tool` in `sharanbot/agent/team/orchestrator.py`
    - Track file writes from write_file, edit_file, mcp_filesystem_write_file, mcp_filesystem_edit_file
    - Track architecture decisions from exec commands matching known patterns (npm init, npx create-, pip install, cargo init, go mod init, dotnet new, django-admin startproject, rails new, vite create)
    - Track config file writes as architecture decisions (vite.config, tsconfig, Dockerfile, docker-compose, .eslintrc, webpack.config, next.config, tailwind.config)
    - Wrap all updates in try/except with debug logging
    - _Requirements: 13.1, 13.2, 13.4_

  - [x] 10.2 Add `_parse_completion_report` method and completion format prompt to `sharanbot/agent/team/orchestrator.py`
    - Add COMPLETION_FORMAT string to subagent system prompt in `_run_task_as_subagent`
    - Implement `_parse_completion_report(result, manifest, agent_id)` to extract files and decisions from structured completion
    - Call `_parse_completion_report` after subagent completes successfully
    - _Requirements: 15.1, 15.2_

  - [x] 10.3 Add consolidated summary generation from TaskNode completion comments
    - After all DAG tasks complete in `run_team`, generate summary from all task results
    - Include references to each completed task's result in the final summary
    - _Requirements: 15.3_

  - [ ]* 10.4 Write property test for manifest tracking (Property 14)
    - **Property 14: Manifest tracks file writes and architecture decisions**
    - For any write tool call with non-empty path, file is added to claims; for exec with known pattern, decision is recorded; for config file write, decision is recorded
    - **Validates: Requirements 13.1, 13.2, 13.4**

  - [ ]* 10.5 Write property test for completion report parsing (Property 15)
    - **Property 15: Completion report parsing extracts files and decisions**
    - For any response with "## Task Completion" section containing file paths and decisions, parser extracts and records them in manifest
    - **Validates: Requirements 4.5, 15.2**

  - [ ]* 10.6 Write property test for consolidated summary (Property 16)
    - **Property 16: Consolidated summary includes all task completions**
    - For any completed DAG where all tasks have non-empty results, summary references each task's result
    - **Validates: Requirements 15.3**

- [x] 11. Remove GSD workers and clean up commands.py
  - [x] 11.1 Remove `_run_task_as_gsd_worker` method from `sharanbot/agent/team/orchestrator.py`
    - Delete the entire `_run_task_as_gsd_worker` method
    - Remove `self._worker_manager` from `__init__` and the `worker_manager` constructor parameter
    - Ensure `_execute_task` routes all tasks through `_run_task_as_subagent` only
    - _Requirements: 6.1, 6.3, 6.4_

  - [x] 11.2 Remove WorkerManager creation from `sharanbot/cli/commands.py`
    - Remove `from sharanbot.agent.worker import WorkerManager` import
    - Remove `WorkerManager(...)` instantiation in `gateway()`
    - Remove `worker_manager=worker_mgr` from `AgentLoop` constructor call
    - _Requirements: 6.2_

- [x] 12. Add workspace resolution before work starts
  - [x] 12.1 Add workspace resolution logic to Autopilot.execute() in `sharanbot/agent/team/autopilot.py`
    - For Mode A: derive project name, create workspace folder, update tool working directories before first tool call
    - For Mode B: workspace is set by team_create → project_root, no action needed
    - _Requirements: 14.1, 14.2, 14.3, 14.4_

  - [x] 12.2 Add `manifest.claim_agent` call in `_execute_task` in `sharanbot/agent/team/orchestrator.py`
    - After assigning agent_id, call `session.manifest.claim_agent(agent_id, task.agent_profile)` to register the agent in the manifest
    - _Requirements: 13.3_

- [x] 13. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document using Hypothesis
- Unit tests validate specific examples and edge cases
- All property tests go in `tests/test_team_orchestration_overhaul.py` and run via `pytest tests/test_team_orchestration_overhaul.py`
- The implementation language is Python throughout, matching the existing codebase
