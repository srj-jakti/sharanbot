# Requirements Document

## Introduction

SharanBot's team orchestration system suffers from a fundamental architectural flaw discovered across 4 rounds of E2E testing: the Autopilot acts as both manager AND builder simultaneously. When team strategy is active, the Autopilot gets impatient after approximately 4 minutes, abandons the team, and builds directly — creating two competing codebases (split-brain). This overhaul introduces two mutually exclusive execution modes, heartbeat-based subagent lifecycle management, atomic task checkout, task-based communication, and fixes 12 specific bugs identified in E2E Round 4.

## Glossary

- **Autopilot**: SharanBot's autonomous executor (`sharanbot/agent/team/autopilot.py`) that receives delegated tasks from SharanBot (the Boss), plans execution strategy, and manages tools/subagents/teams during execution.
- **Team_Orchestrator**: The component (`sharanbot/agent/team/orchestrator.py`) that creates and drives multi-agent teams, decomposes tasks into a DAG, and manages subagent execution.
- **Task_DAG**: A directed acyclic graph (`sharanbot/agent/team/dag.py`) of TaskNode objects representing decomposed subtasks with dependency relationships.
- **TaskNode**: A single task within the Task_DAG, containing description, agent profile, dependencies, status, result, and metadata.
- **Subagent**: A specialist LLM-powered agent loop spawned by the Team_Orchestrator to execute a single TaskNode.
- **Mode_A**: Solo Autopilot mode — the Autopilot builds everything directly with full tool access, no team created.
- **Mode_B**: Team Manager mode — the Autopilot is a pure manager with zero build tools, all building done by team subagents.
- **Heartbeat_Check**: A check performed by a subagent at the start of each iteration to verify the parent team is still active, read new steering messages, and check for cancellation.
- **Task_Checkout**: An atomic lock mechanism mapping task_id to agent_id, ensuring only one agent can work on a given task at a time.
- **Project_Manifest**: A JSON file (`PROJECT_MANIFEST.json`) in the project root that serves as the single source of truth for project metadata, ports, agent assignments, architecture decisions, and file ownership.
- **TeamBus**: The current inter-agent messaging system (`sharanbot/agent/team/bus.py`) using asyncio queues — to be replaced by task-based comments.
- **GSD_Worker**: A Node.js-based worker process (`sharanbot/agent/worker.py`) that attempts to execute tasks via a loader.js file — currently non-functional.
- **Reaction_Engine**: The event-driven reaction system (`sharanbot/agi/reactions.py`) that triggers configurable responses to system events.
- **Session_Manager**: The component (`sharanbot/session/manager.py`) that manages conversation sessions, storing messages in JSONL format.
- **Allowed_Tools_Mode_B**: The set of tools available to the Autopilot in Mode_B: `team_create`, `team_status`, `team_steer`, `read_file`, `list_dir`, `web_search`, `wait`.
- **Split_Brain**: A failure mode where two independent codebases are created for the same task — one by the Autopilot and one by the team subagents.
- **Zombie_Subagent**: A subagent that continues running after the parent task or team has been completed or cancelled.

## Requirements

### Requirement 1: Two Exclusive Execution Modes

**User Story:** As a developer using SharanBot, I want the Autopilot to commit to exactly one execution mode per task, so that the Autopilot and team subagents never compete by building separate codebases.

#### Acceptance Criteria

1. WHEN the Autopilot decision has strategy equal to "team", THE Autopilot SHALL enter Mode_B and remove all build tools from the tool definitions passed to the LLM, retaining only the Allowed_Tools_Mode_B set.
2. WHEN the Autopilot decision has strategy not equal to "team", THE Autopilot SHALL enter Mode_A with full tool access and create no team.
3. WHILE the Autopilot is in Mode_B, THE Autopilot SHALL have zero access to the following tools at the tool definitions level: `write_file`, `edit_file`, `create_software`, `scaffold_project`, `exec`, `mcp_filesystem_write_file`, `mcp_filesystem_edit_file`, `mcp_filesystem_create_directory`, `deploy`, `git`, `package_manager`, `test_runner`, `format_code`, `database`, `design_to_code`, `template_engine`, `live_preview`, and all other MCP filesystem write tools.
4. THE Autopilot SHALL enforce tool filtering at the tool definitions level by constructing an allowlist of permitted tool names for Mode_B, rather than using a blocklist or prompt-level hints.
5. WHEN the Autopilot is in Mode_B and the team completes all Task_DAG tasks, THE Autopilot SHALL review the team output, start any required servers, and report results to the user.

### Requirement 2: Heartbeat Check-In Model for Subagents

**User Story:** As a system operator, I want subagents to check in with the parent team at each iteration, so that subagents exit immediately when the parent task is cancelled or completed, preventing Zombie_Subagents.

#### Acceptance Criteria

1. WHEN a subagent begins a new iteration in `_run_task_as_subagent`, THE Team_Orchestrator SHALL execute a Heartbeat_Check before making the LLM call.
2. WHEN the Heartbeat_Check detects that the parent TeamSession status is "cancelled" or "completed", THE subagent SHALL exit its execution loop immediately and return a cancellation message.
3. WHEN the Heartbeat_Check finds new comments on the subagent's TaskNode, THE subagent SHALL inject those comments into the LLM message history as steering instructions before the next LLM call.
4. WHEN the Heartbeat_Check finds that the subagent's task has been reassigned to a different agent via Task_Checkout, THE subagent SHALL exit its execution loop immediately.
5. THE Heartbeat_Check SHALL complete within 10 milliseconds under normal conditions, adding negligible overhead to each subagent iteration.

### Requirement 3: Atomic Task Checkout

**User Story:** As a system operator, I want only one agent to work on a given task at a time, so that Split_Brain scenarios where multiple agents build the same thing are prevented.

#### Acceptance Criteria

1. THE Team_Orchestrator SHALL maintain a Task_Checkout dictionary mapping task_id to agent_id for all active teams.
2. WHEN `_execute_task` is called for a TaskNode, THE Team_Orchestrator SHALL attempt to check out the task by writing the agent_id into the Task_Checkout dictionary for that task_id.
3. IF a checkout is attempted for a task_id that is already checked out by a different agent_id, THEN THE Team_Orchestrator SHALL skip execution of that task and log a warning.
4. WHEN a subagent completes or fails a task, THE Team_Orchestrator SHALL release the checkout by removing the task_id entry from the Task_Checkout dictionary.
5. WHILE the Autopilot is in Mode_B, THE Autopilot SHALL be unable to check out any build tasks, because the Autopilot lacks build tools.

### Requirement 4: Task-Based Communication

**User Story:** As a developer, I want team steering messages to reach subagents reliably through task comments, so that the Autopilot can effectively guide team execution without relying on the dead TeamBus.

#### Acceptance Criteria

1. THE TaskNode dataclass SHALL include a `comments` field of type `list[dict]` for storing steering messages and status updates.
2. WHEN `team_steer` is called with an instruction, THE Team_Orchestrator SHALL append the instruction as a comment to the relevant TaskNode(s) in the Task_DAG.
3. WHEN a subagent performs a Heartbeat_Check, THE subagent SHALL read all unread comments from its assigned TaskNode and incorporate them as LLM context.
4. WHEN a subagent completes a task, THE subagent SHALL append a structured completion comment to the TaskNode containing: files created or modified, architecture decisions made, and a summary of work done.
5. THE Team_Orchestrator SHALL record structured completion comments from subagents into the Project_Manifest for audit and coordination purposes.

### Requirement 5: Cancel Team on Autopilot Completion

**User Story:** As a system operator, I want all team subagents to stop when the Autopilot finishes its task, so that compute resources and API calls are not wasted on Zombie_Subagents.

#### Acceptance Criteria

1. WHEN the Autopilot completes execution and a team is active for the current task, THE Autopilot SHALL call `Team_Orchestrator.cancel_team(team_id)` to stop all running subagents.
2. WHEN `cancel_team` is called, THE Team_Orchestrator SHALL set the TeamSession status to "cancelled", clear the TeamBus, and cancel all running asyncio tasks associated with the team.
3. WHEN a subagent detects cancellation via Heartbeat_Check, THE subagent SHALL exit cleanly within one iteration, releasing any held resources.
4. THE `cancel_team` method SHALL complete within 5 seconds, even if subagents are mid-execution.

### Requirement 6: Remove GSD Workers

**User Story:** As a maintainer, I want the non-functional GSD worker system removed, so that the codebase is cleaner and all task execution follows a single proven path through subagents.

#### Acceptance Criteria

1. THE Team_Orchestrator SHALL remove the `_run_task_as_gsd_worker` method entirely.
2. THE gateway startup in `commands.py` SHALL remove the creation of WorkerManager and all references to GSD workers.
3. THE Team_Orchestrator constructor SHALL remove the `worker_manager` parameter.
4. THE `_execute_task` method SHALL route all tasks exclusively through `_run_task_as_subagent`.

### Requirement 7: Remove Dead TeamBus

**User Story:** As a maintainer, I want the ineffective TeamBus replaced with task-based comments, so that inter-agent communication uses a mechanism that subagents actually read.

#### Acceptance Criteria

1. THE Team_Orchestrator SHALL stop using `TeamBus.broadcast` and `TeamBus.send` for delivering steering messages to subagents.
2. THE `steer_team` method SHALL deliver instructions by appending comments to TaskNode objects instead of broadcasting via TeamBus.
3. THE subagent execution loop SHALL read steering instructions from TaskNode comments during Heartbeat_Check instead of calling `bus.get_pending`.
4. THE TeamBus module (`bus.py`) SHALL remain available for progress reporting between subagents and the orchestrator, but SHALL NOT be the primary communication channel for steering.

### Requirement 8: Fix Project Name Derivation

**User Story:** As a user, I want the project folder to have a meaningful name derived from my prompt, so that I can identify projects by name instead of generic UUIDs.

#### Acceptance Criteria

1. WHEN the task description contains emoji characters, THE `_derive_project_name` method SHALL strip all emoji and Unicode symbols before attempting name extraction.
2. WHEN the task description uses capitalized or all-caps words (e.g., "3D CAR RACING GAME"), THE `_derive_project_name` method SHALL normalize the text to lowercase for pattern matching.
3. WHEN the standard verb-based pattern ("build/create/develop/make/implement") does not match, THE `_derive_project_name` method SHALL fall back to extracting the first 3 significant words (excluding stop words, emojis, and punctuation) as the project name.
4. THE `_derive_project_name` method SHALL produce filesystem-safe names containing only lowercase alphanumeric characters and hyphens, with a maximum length of 40 characters.
5. FOR ALL valid task descriptions, THE `_derive_project_name` method SHALL return a non-empty string that is not the generic fallback pattern `project-{team_id}` when the description contains at least 3 significant words.

### Requirement 9: Port Pre-Assignment Checks Availability

**User Story:** As a developer, I want port assignments to verify that ports are actually free before assigning them, so that team subagents do not encounter port conflicts at runtime.

#### Acceptance Criteria

1. WHEN `assign_port` is called for a service, THE Project_Manifest SHALL perform a TCP socket bind test on the candidate port before assigning it.
2. IF the candidate port is occupied, THEN THE Project_Manifest SHALL try the next port in the range until a free port is found.
3. THE Project_Manifest SHALL start port scanning from port 8000 instead of port 3000, to avoid conflicts with common development server defaults.
4. THE `_find_free_port` method SHALL use a default range of 8000 to 9000.

### Requirement 10: Fix SessionManager._sessions Bug

**User Story:** As a system operator, I want the Reaction_Engine to correctly access session data, so that quality gate failure notifications are delivered instead of silently crashing.

#### Acceptance Criteria

1. WHEN the Reaction_Engine's notification callback accesses the Session_Manager, THE callback SHALL reference the `_cache` attribute instead of the non-existent `_sessions` attribute.
2. WHEN a `quality.gate_failed` event is emitted, THE Reaction_Engine SHALL deliver the notification without raising an AttributeError.

### Requirement 11: Lower Token Consolidation Trigger

**User Story:** As a developer, I want session context consolidation to trigger earlier, so that more context window capacity is available for the actual task instead of being consumed by stale history.

#### Acceptance Criteria

1. WHEN the Autopilot estimates token usage during execution, THE Autopilot SHALL trigger context summarization at 60% of the context window capacity instead of 80%.
2. THE summarization threshold SHALL be calculated as `int(context_window_tokens * 0.60)`.

### Requirement 12: Add Wait Tool

**User Story:** As a developer, I want the Autopilot to have a proper wait/sleep tool, so that it does not abuse `ping -n 60` or other shell hacks to pause execution.

#### Acceptance Criteria

1. THE Tool_Registry SHALL include a `wait` tool that accepts a duration parameter in seconds.
2. WHEN the `wait` tool is called with a duration, THE tool SHALL perform an `asyncio.sleep` for the specified duration and return a confirmation message.
3. THE `wait` tool SHALL enforce a maximum duration of 300 seconds per call to prevent indefinite blocking.
4. THE `wait` tool SHALL be available in both Mode_A and Mode_B.

### Requirement 13: Improve Manifest Tracking

**User Story:** As a team coordinator, I want the Project_Manifest to track file writes, architecture decisions, and agent assignments comprehensively, so that the manifest serves as a useful coordination tool rather than just a log of npm installs.

#### Acceptance Criteria

1. WHEN a subagent calls `write_file` or `edit_file` (or their MCP equivalents), THE `_update_manifest_from_tool` method SHALL record the file path in the agent's file claim list.
2. WHEN a subagent executes a command that indicates an architecture choice (framework selection, project scaffolding, dependency installation), THE `_update_manifest_from_tool` method SHALL record a descriptive decision entry in the manifest.
3. WHEN a subagent is assigned to a TaskNode, THE Team_Orchestrator SHALL call `manifest.claim_agent(agent_id, role)` to register the agent and its role in the manifest.
4. THE `_update_manifest_from_tool` method SHALL recognize architecture decision patterns beyond npm install, including: `npx create-`, `pip install`, `cargo init`, `go mod init`, `dotnet new`, `django-admin startproject`, `rails new`, `vite create`, and framework configuration file writes (e.g., `vite.config.js`, `tsconfig.json`, `Dockerfile`).

### Requirement 14: Workspace Resolution Before Work Starts

**User Story:** As a developer, I want the workspace to be resolved before any agent starts building, so that all file operations target the correct directory and the Autopilot does not create a competing folder.

#### Acceptance Criteria

1. WHEN the Autopilot enters Mode_A, THE Autopilot SHALL create a workspace folder derived from the task name and set all file operations relative to that folder before executing any tools.
2. WHEN the Autopilot enters Mode_B, THE Autopilot SHALL use the team's `project_root` from the Project_Manifest as the workspace, and all subagent file operations SHALL be relative to that project_root.
3. THE workspace path SHALL be resolved and created before the first tool call in the Autopilot execution loop.
4. THE workspace path SHALL be passed as an explicit parameter to subagents, not discovered at runtime.

### Requirement 15: Structured Task Completion Reporting

**User Story:** As a team coordinator, I want subagents to report what they built when they complete a task, so that the manifest has a complete record of all work done and other agents can see what was created.

#### Acceptance Criteria

1. WHEN a subagent completes a task successfully, THE subagent's final response SHALL include a structured summary listing: files created or modified, key architecture decisions, and a brief description of what was built.
2. THE Team_Orchestrator SHALL parse the subagent's completion response and update the Project_Manifest with the reported files and decisions.
3. WHEN all Task_DAG tasks are complete, THE Team_Orchestrator SHALL generate a consolidated summary from all TaskNode completion comments for the final team report.
