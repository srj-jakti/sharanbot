# SharanBot Team Orchestration Plan

> Transform SharanBot from a **single-agent-at-a-time** system into a **multi-agent team orchestration** engine.

---

## Research Sources

| Repo | Stars | What It Is | How We Use It |
|------|-------|-----------|---------------|
| **agency-agents** | 59k | 144 specialized AI agent personalities in Markdown (12 divisions) | **Agent Profiles** — convert top agents to SharanBot YAML profiles. Adopt the **Agents Orchestrator** pattern for pipeline management with quality gates & retry logic. |
| **plano** | 6k | AI-native proxy with YAML-based agent routing, orchestration, tracing | **Design Pattern** — adopt YAML agent declarations with natural-language descriptions for intelligent routing. Adopt observability/tracing concepts. (NOT used as a dependency — too heavy.) |
| **system-prompts** | 133k | Real system prompts from Manus, Cursor, Devin, VSCode Agent, etc. | **Prompt Engineering** — adopt Manus's agent loop pattern (planner module, event stream, todo tracking, modular architecture). Use prompts as reference for crafting better agent profiles. |

---

## Current Architecture (Single-Agent)

```
User → Main Agent (16 tools) → LLM
                ├── spawn → Subagent (7 tools, leaf node, can't spawn)
                ├── worker_start → GSD Worker (isolated Node.js, can't spawn)
                └── No inter-agent communication
```

### Limitations
- Main agent is the **only** orchestrator
- Subagents can't spawn subagents or workers
- Workers can't communicate with other workers
- No task decomposition or DAG execution
- No pipeline patterns (researcher → coder → reviewer)
- No quality gates or retry loops
- No inter-agent messaging

---

## Target Architecture (Team Orchestration)

```
User → Main Agent (enhanced with team tools)
           │
           ├── team_create → TeamOrchestrator
           │       ├── Coordinator Agent (manages pipeline)
           │       ├── Researcher Subagent ──┐
           │       ├── Coder Subagent ────────┤── TeamBus (inter-agent messaging)
           │       ├── Reviewer Subagent ─────┘
           │       └── GSD Worker (engineering)
           │
           ├── pipeline → PipelineEngine
           │       Stage 1: Research → Stage 2: Implement → Stage 3: Review
           │                               ↕ (quality loop with retry)
           │
           └── task_plan → TaskPlanner (DAG decomposition)
                   ├── Parallel: Task A, Task B
                   ├── Sequential: Task C (depends on A+B)
                   └── Quality Gate: Validate all outputs
```

---

## Implementation Phases

### Phase 1: Team Communication Bus
**What**: Inter-agent messaging system allowing agents to communicate with each other.  
**Inspired by**: Manus's event stream architecture, Plano's agent-to-agent routing.

#### New Files
- `sharanbot/agent/team/bus.py` — **TeamBus**
- `sharanbot/agent/team/mailbox.py` — **AgentMailbox**

#### TeamBus Design
```python
class TeamBus:
    """Pub/sub messaging between agents within a team."""
    
    async def send(self, from_id: str, to_id: str, message: TeamMessage) -> None
    async def broadcast(self, from_id: str, message: TeamMessage) -> None
    async def receive(self, agent_id: str, timeout: float = 30) -> TeamMessage | None
    def subscribe(self, agent_id: str, topic: str, callback: Callable) -> None

class TeamMessage:
    from_agent: str
    to_agent: str | None  # None = broadcast
    msg_type: str  # "handoff", "result", "question", "status", "quality_gate"
    content: str
    attachments: dict[str, Any]  # files, data, references
    timestamp: datetime
```

#### Integration Points
- Modify `SubagentManager._run_subagent()` to accept a `team_bus` parameter
- Add `TeamMessageTool` to subagent tool registry when in team mode
- Workers get a `team_send` JSON-RPC method

---

### Phase 2: Task Decomposition Engine
**What**: Break complex tasks into subtasks with dependencies (DAG).  
**Inspired by**: Manus's planner module, Plano's YAML-based agent routing, agency-agents Orchestrator's phased pipeline.

#### New Files
- `sharanbot/agent/team/planner.py` — **TaskPlanner**
- `sharanbot/agent/team/dag.py` — **TaskDAG**

#### TaskPlanner Design
```python
@dataclass
class TaskNode:
    id: str
    description: str
    agent_profile: str  # which profile should handle this
    depends_on: list[str]  # task IDs this depends on
    status: str  # "pending", "running", "completed", "failed", "retrying"
    result: str | None
    retry_count: int = 0
    max_retries: int = 3

class TaskPlanner:
    """LLM-powered task decomposition into a DAG."""
    
    async def decompose(self, task: str, available_profiles: list[str]) -> TaskDAG:
        """Use LLM to break a complex task into subtasks with dependencies."""
        # LLM generates a structured plan:
        # - Which subtasks exist
        # - Which profile handles each
        # - What depends on what
        # - Which can run in parallel
    
    async def replan(self, dag: TaskDAG, failed_task: TaskNode, feedback: str) -> TaskDAG:
        """Replan after a failure, incorporating feedback."""

class TaskDAG:
    """Directed Acyclic Graph of tasks."""
    
    def get_ready_tasks(self) -> list[TaskNode]:
        """Return tasks whose dependencies are all completed."""
    
    def mark_completed(self, task_id: str, result: str) -> None
    def mark_failed(self, task_id: str, error: str) -> None
    def is_complete(self) -> bool
    def get_progress(self) -> dict  # {total, completed, running, failed}
```

#### LLM Decomposition Prompt (inspired by Manus planner module)
```
You are a task planner. Break the following complex task into subtasks.
For each subtask, specify:
- id: unique short identifier
- description: what needs to be done
- profile: which agent profile should handle it (from: {available_profiles})
- depends_on: list of task IDs this depends on (empty = can start immediately)

Available agent profiles and their specialties:
{profile_descriptions}

Task to decompose:
{task}

Output as JSON array of task objects.
```

---

### Phase 3: Team Orchestrator
**What**: Spawn and manage a coordinated team of agents working on a complex task.  
**Inspired by**: agency-agents Orchestrator (PM → Architect → [Dev ↔ QA] → Integration), Plano's multi-agent routing.

#### New Files
- `sharanbot/agent/team/orchestrator.py` — **TeamOrchestrator**
- `sharanbot/agent/team/quality.py` — **QualityGate**
- `sharanbot/agent/tools/team.py` — **TeamCreateTool**, **TeamStatusTool**, **TeamSteerTool**

#### TeamOrchestrator Design
```python
class TeamOrchestrator:
    """Manages a team of agents working on a complex task."""
    
    def __init__(self, 
        planner: TaskPlanner,
        subagent_manager: SubagentManager,
        worker_manager: WorkerManager,
        team_bus: TeamBus,
        profile_registry: AgentProfileRegistry,
    ):
        self.teams: dict[str, Team] = {}
    
    async def create_team(self, task: str, strategy: str = "auto") -> Team:
        """Create a team for a complex task.
        
        Strategies:
        - "auto": LLM decides the team composition and pipeline
        - "pipeline": Sequential stages (research → implement → review)  
        - "parallel": Fan-out to multiple agents, merge results
        - "loop": Dev-QA loop until quality gate passes
        - "custom": User specifies team composition
        """
    
    async def run_team(self, team_id: str) -> TeamResult:
        """Execute the team's task DAG."""
        # 1. Get ready tasks from DAG
        # 2. Spawn appropriate agents for each task
        # 3. Monitor completion
        # 4. Evaluate quality gates
        # 5. Handle retries on failure
        # 6. Pass results to dependent tasks via TeamBus
        # 7. Continue until DAG is complete or max retries exceeded
    
    async def steer_team(self, team_id: str, instruction: str) -> str:
        """Send steering instruction to the team coordinator."""

@dataclass
class Team:
    id: str
    task: str
    strategy: str
    dag: TaskDAG
    agents: dict[str, str]  # task_id → agent_id (subagent or worker)
    bus: TeamBus
    status: str  # "planning", "running", "completed", "failed"
    created_at: datetime
    
@dataclass
class TeamResult:
    team_id: str
    status: str
    summary: str
    task_results: dict[str, str]  # task_id → result
    quality_score: float  # 0.0 - 1.0
    metrics: TeamMetrics
```

#### QualityGate Design (from agency-agents Orchestrator)
```python
class QualityGate:
    """Validates task outputs before allowing pipeline progression."""
    
    async def evaluate(self, 
        task: TaskNode,
        result: str,
        criteria: str | None = None,
    ) -> QualityResult:
        """LLM-powered quality evaluation.
        
        Returns PASS/FAIL with specific feedback.
        On FAIL, provides actionable feedback for retry.
        """
    
    max_retries: int = 3  # From agency-agents pattern

@dataclass
class QualityResult:
    passed: bool
    score: float  # 0.0 - 1.0
    feedback: str  # Specific feedback for improvement
    evidence: list[str]  # What was checked
```

#### Team Tools (exposed to main agent)
```python
class TeamCreateTool(Tool):
    """Create a team of agents to handle a complex task."""
    name = "team_create"
    parameters = {
        "task": "Detailed description of the complex task",
        "strategy": "auto|pipeline|parallel|loop|custom",
        "profiles": "Optional list of specific profiles to use",
    }

class TeamStatusTool(Tool):
    """Check team progress and status."""
    name = "team_status"

class TeamSteerTool(Tool):
    """Send instructions to a running team."""
    name = "team_steer"
```

---

### Phase 4: Pipeline Patterns
**What**: Pre-built pipeline templates for common multi-agent workflows.  
**Inspired by**: agency-agents Orchestrator's 4-phase pipeline, Plano's agent routing, Manus's phased execution.

#### New File
- `sharanbot/agent/team/pipelines.py` — **PipelineRegistry**

#### Built-in Pipelines

##### 1. Research → Implement → Review
```yaml
name: build
description: "Full development pipeline with research, implementation, and code review"
stages:
  - id: research
    profile: researcher
    task_template: "Research: {task}. Find best practices, existing solutions, and technical requirements."
    
  - id: implement
    profile: coder
    depends_on: [research]
    task_template: "Based on research findings:\n{research.result}\n\nImplement: {task}"
    
  - id: review
    profile: reviewer  # new profile
    depends_on: [implement]
    task_template: "Review the implementation for: {task}\n\nCode changes:\n{implement.result}"
    quality_gate: true
    on_fail: implement  # retry implementation with review feedback
```

##### 2. Parallel Research
```yaml
name: deep_research
description: "Fan-out research across multiple sources, then synthesize"
stages:
  - id: web_research
    profile: researcher
    task_template: "Search the web for: {task}"
    
  - id: code_research
    profile: coder
    task_template: "Search the codebase for patterns related to: {task}"
    
  - id: synthesize
    profile: general
    depends_on: [web_research, code_research]
    task_template: "Synthesize findings:\nWeb: {web_research.result}\nCode: {code_research.result}"
```

##### 3. Dev-QA Loop (from agency-agents)
```yaml
name: dev_qa
description: "Implementation with automated quality assurance loop"
stages:
  - id: implement
    profile: coder
    task_template: "Implement: {task}"
    
  - id: test
    profile: tester  # new profile
    depends_on: [implement]
    task_template: "Test the implementation:\n{implement.result}\nRun tests, check for bugs."
    quality_gate: true
    max_retries: 3
    on_fail: implement
```

##### 4. Full Agency Pipeline (from agency-agents Orchestrator)
```yaml
name: agency
description: "Complete agency pipeline: plan → architect → implement → QA → integrate"
stages:
  - id: plan
    profile: project_manager
    task_template: "Create a detailed task list for: {task}"
    
  - id: architect
    profile: architect
    depends_on: [plan]
    task_template: "Design architecture based on plan:\n{plan.result}"
    
  - id: implement
    profile: coder
    depends_on: [architect]
    task_template: "Implement based on architecture:\n{architect.result}"
    
  - id: qa
    profile: tester
    depends_on: [implement]
    task_template: "QA test the implementation:\n{implement.result}"
    quality_gate: true
    max_retries: 3
    on_fail: implement
    
  - id: integrate
    profile: reviewer
    depends_on: [qa]
    task_template: "Final integration review:\nAll tasks passed QA.\nSummary: {qa.result}"
```

---

### Phase 5: Enhanced Agent Profiles (from agency-agents)
**What**: Convert top agency-agents Markdown personalities to SharanBot YAML profiles.  
**Source**: `msitarzewski/agency-agents` — 144 agents across 12 divisions.

#### Profiles to Import (Top 20)

| # | Profile Name | Division | Source Agent | Purpose |
|---|-------------|----------|-------------|---------|
| 1 | `frontend_developer` | Engineering | Frontend Developer | React/Vue/Angular UI implementation |
| 2 | `backend_architect` | Engineering | Backend Architect | API design, database architecture |
| 3 | `code_reviewer` | Engineering | Code Reviewer | PR reviews, quality gates |
| 4 | `security_engineer` | Engineering | Security Engineer | Threat modeling, secure code review |
| 5 | `devops` | Engineering | DevOps Automator | CI/CD, infrastructure |
| 6 | `rapid_prototyper` | Engineering | Rapid Prototyper | Fast MVPs and POCs |
| 7 | `software_architect` | Engineering | Software Architect | System design, DDD |
| 8 | `tester` | Testing | Reality Checker | Evidence-based QA certification |
| 9 | `api_tester` | Testing | API Tester | API validation and testing |
| 10 | `project_manager` | PM | Senior Project Manager | Spec-to-task conversion |
| 11 | `orchestrator` | Specialized | Agents Orchestrator | Multi-agent pipeline management |
| 12 | `ui_designer` | Design | UI Designer | Visual design, component libraries |
| 13 | `ux_researcher` | Design | UX Researcher | User testing, behavior analysis |
| 14 | `content_creator` | Marketing | Content Creator | Multi-platform content |
| 15 | `seo_specialist` | Marketing | SEO Specialist | Technical SEO, content strategy |
| 16 | `data_engineer` | Engineering | Data Engineer | Data pipelines, ETL |
| 17 | `technical_writer` | Engineering | Technical Writer | Developer docs, API reference |
| 18 | `mcp_builder` | Specialized | MCP Builder | Building MCP servers |
| 19 | `workflow_architect` | Specialized | Workflow Architect | Workflow mapping and design |
| 20 | `database_optimizer` | Engineering | Database Optimizer | Schema design, query optimization |

#### Profile YAML Format (example)
```yaml
# plugins/agents/frontend_developer.yaml
name: frontend_developer
display_name: "Frontend Developer"
description: "Expert frontend developer specializing in React/Vue/Angular, UI implementation, and performance optimization"
system_prompt: |
  You are Frontend Developer, an expert in modern web technologies.
  
  ## Identity
  - Role: Modern web application and UI implementation specialist
  - Personality: Detail-oriented, performance-focused, user-centric
  
  ## Core Mission
  - Build responsive, performant web applications
  - Implement pixel-perfect designs with modern CSS
  - Create component libraries and design systems
  - Optimize Core Web Vitals
  
  ## Critical Rules
  - Performance-first: LCP < 2.5s, FID < 100ms, CLS < 0.1
  - Accessibility: WCAG 2.1 AA compliance
  - Mobile-first responsive design
  - TypeScript with proper typing
  
  ## Communication Style
  - Be precise: "Implemented virtualized table reducing render time by 80%"
  - Focus on UX and performance metrics
  
allowed_tools: ["*"]
denied_tools: []
model: null  # use default
max_iterations: 25
temperature: 0.1
```

#### Orchestrator Profile (key profile)
```yaml
# plugins/agents/orchestrator.yaml
name: orchestrator
display_name: "Team Orchestrator"
description: "Autonomous pipeline manager that coordinates multiple specialist agents through quality-gated workflows"
system_prompt: |
  You are the Team Orchestrator, an autonomous pipeline manager.
  
  ## Core Mission
  - Decompose complex tasks into subtasks
  - Assign each subtask to the best specialist agent
  - Manage handoffs between agents with proper context
  - Enforce quality gates with retry logic
  - Track progress and report status
  
  ## Pipeline Management
  - Phase 1: Analysis & Planning (project_manager profile)
  - Phase 2: Architecture & Design (software_architect profile)
  - Phase 3: Implementation with QA loops (coder ↔ tester)
  - Phase 4: Final Integration & Review (code_reviewer profile)
  
  ## Quality Gate Rules
  - Every task must pass QA before proceeding
  - Maximum 3 retry attempts per task
  - Each retry includes specific QA feedback
  - Escalate after max retries with failure report
  
  ## Decision Logic
  IF QA = PASS: advance to next task
  IF QA = FAIL and retries < 3: loop back with feedback
  IF QA = FAIL and retries >= 3: escalate with report
  
allowed_tools: ["*"]
denied_tools: []
max_iterations: 40
temperature: 0.1
```

---

### Phase 6: Enhanced Subagent Capabilities
**What**: Allow subagents to spawn sub-subagents and access worker tools.  
**Why**: Currently subagents are leaf nodes with only 7 tools. Team members need more power.

#### Changes to `SubagentManager._run_subagent()`
```python
# Current: subagents get 7 tools (read_file, write_file, edit_file, list_dir, exec, web_search, web_fetch)
# New: team-member subagents get additional tools based on their team role

if team_mode:
    # Add team communication tool
    tools.register(TeamMessageTool(team_bus=team_bus, agent_id=task_id))
    
    # Coordinator gets spawn capability (can delegate to sub-agents)
    if is_coordinator:
        tools.register(SpawnTool(manager=self))
        tools.register(TeamStatusTool(orchestrator=orchestrator))
    
    # Workers get steering capability
    if has_workers:
        tools.register(WorkerStartTool(manager=worker_manager))
        tools.register(WorkerStatusTool(manager=worker_manager))
        tools.register(WorkerSteerTool(manager=worker_manager))
        tools.register(WorkerStopTool(manager=worker_manager))
```

#### Depth Limit
- Maximum subagent depth: 3 levels (main → coordinator → specialist → sub-specialist)
- Prevents infinite recursion
- Each level reduces max_iterations (40 → 25 → 15)

---

### Phase 7: Observability & Tracing
**What**: Track team execution with traces, metrics, and quality reports.  
**Inspired by**: Plano's zero-code OTEL tracing, Manus's planner status updates.

#### New Files
- `sharanbot/agent/team/trace.py` — **TeamTracer**
- `sharanbot/agent/team/metrics.py` — **TeamMetrics**

#### TeamTracer Design
```python
@dataclass
class TraceEvent:
    timestamp: datetime
    event_type: str  # "task_start", "task_complete", "task_fail", "handoff", "quality_gate", "retry"
    agent_id: str
    task_id: str
    detail: str
    duration_ms: float | None = None

class TeamTracer:
    """Zero-code execution tracing for team orchestration."""
    
    def __init__(self):
        self.events: list[TraceEvent] = []
    
    def trace_start(self, agent_id: str, task_id: str, description: str) -> None
    def trace_complete(self, agent_id: str, task_id: str, result_summary: str) -> None
    def trace_fail(self, agent_id: str, task_id: str, error: str) -> None
    def trace_handoff(self, from_agent: str, to_agent: str, context: str) -> None
    def trace_quality_gate(self, task_id: str, passed: bool, feedback: str) -> None
    
    def get_summary(self) -> str:
        """Human-readable execution summary."""
    
    def get_timeline(self) -> list[dict]:
        """Chronological event timeline."""

@dataclass
class TeamMetrics:
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    total_retries: int
    avg_task_duration_s: float
    quality_gate_pass_rate: float
    agents_used: list[str]
    total_duration_s: float
```

#### Progress Reporting (inspired by Manus's notify pattern)
- Team sends periodic status updates to the user via MessageTool
- Format: "Team progress: 5/8 tasks completed, 2 running, 1 pending"
- Configurable verbosity: silent, summary, detailed

---

### Phase 8: Agent Profile Import System
**What**: Automated system to import agent profiles from agency-agents repository.  
**Source**: The 144 markdown agent files in `msitarzewski/agency-agents`.

#### New Tool
```python
class ImportAgentTool(Tool):
    """Import agent profiles from the agency-agents repository."""
    name = "import_agent"
    
    async def execute(self, source: str, agent_name: str, **kwargs) -> str:
        """
        1. Fetch the markdown file from agency-agents repo
        2. Parse the structured sections (Identity, Mission, Rules, etc.)
        3. Convert to SharanBot YAML profile format
        4. Save to plugins/agents/{name}.yaml
        5. Trigger hot-reload
        """
```

This allows SharanBot to:
- `evolve agent "Import the Frontend Developer agent from agency-agents"`
- `reload agents` — picks up new YAML profiles instantly
- Access 144+ specialist personalities on demand

---

## File Structure (New Files)

```
sharanbot/agent/team/
├── __init__.py
├── bus.py              # TeamBus — inter-agent messaging
├── mailbox.py          # AgentMailbox — per-agent message queue
├── planner.py          # TaskPlanner — LLM-powered task decomposition
├── dag.py              # TaskDAG — directed acyclic graph execution
├── orchestrator.py     # TeamOrchestrator — team lifecycle management
├── quality.py          # QualityGate — automated quality evaluation
├── pipelines.py        # PipelineRegistry — pre-built pipeline templates
├── trace.py            # TeamTracer — execution tracing
└── metrics.py          # TeamMetrics — performance metrics

sharanbot/agent/tools/
├── team.py             # TeamCreateTool, TeamStatusTool, TeamSteerTool (NEW)
└── import_agent.py     # ImportAgentTool (NEW)

~/.sharanbot/workspace/plugins/agents/
├── frontend_developer.yaml    # From agency-agents
├── backend_architect.yaml     # From agency-agents
├── code_reviewer.yaml         # From agency-agents
├── orchestrator.yaml          # From agency-agents (Agents Orchestrator)
├── tester.yaml                # From agency-agents (Reality Checker)
├── project_manager.yaml       # From agency-agents
├── ... (20+ profiles)
└── custom_profile.yaml        # User-created profiles
```

## Modified Files

| File | Change |
|------|--------|
| `sharanbot/agent/loop.py` | Register team tools (team_create, team_status, team_steer), pass orchestrator to tools |
| `sharanbot/agent/subagent.py` | Accept team_bus parameter, team_mode flag, depth tracking, add TeamMessageTool |
| `sharanbot/agent/worker.py` | Accept team_bus parameter, add team_send JSON-RPC method |
| `sharanbot/agent/tools/spawn.py` | Add depth parameter, pass team context |
| `sharanbot/bus/events.py` | Add TeamEvent dataclass |
| `sharanbot/cli/commands.py` | Create TeamOrchestrator, pass to AgentLoop |

---

## Execution Order

```
Phase 1: Team Communication Bus     [Foundation — all other phases need this]
   ↓
Phase 2: Task Decomposition Engine  [Planner — breaks tasks into DAGs]
   ↓
Phase 3: Team Orchestrator          [Core — spawns and manages teams]
   ↓
Phase 4: Pipeline Patterns          [Templates — pre-built workflow patterns]
   ↓
Phase 5: Agent Profiles Import      [Skills — 20+ specialist profiles from agency-agents]
   ↓
Phase 6: Enhanced Subagents         [Power — subagents can spawn sub-agents]
   ↓
Phase 7: Observability & Tracing    [Visibility — execution traces and metrics]
   ↓
Phase 8: Agent Import System        [Scale — import any of 144+ agents on demand]
```

---

## Usage Examples

### Example 1: Build a Full-Stack App
```
User: "Build a todo app with React frontend and FastAPI backend"

SharanBot (auto-detects complexity): 
→ team_create(task="...", strategy="pipeline")
→ TaskPlanner decomposes into DAG:
    1. [researcher] Research best practices for React+FastAPI stack
    2. [backend_architect] Design API schema and database (depends: 1)  
    3. [frontend_developer] Build React UI (depends: 1)
    4. [coder] Implement FastAPI backend (depends: 2)
    5. [coder] Integrate frontend with backend (depends: 3, 4)
    6. [tester] Run tests and QA (depends: 5)
    7. [code_reviewer] Final review (depends: 6)
→ Tasks 2 & 3 run in PARALLEL
→ Quality gate at step 6 with retry loop
→ Reports: "Team completed in 12 minutes. 7/7 tasks passed. Quality: 0.92"
```

### Example 2: Deep Research
```
User: "Research the best database for our real-time analytics use case"

SharanBot:
→ team_create(task="...", strategy="parallel")
→ Fan out 3 researchers:
    1. [researcher] "Research PostgreSQL + TimescaleDB for real-time analytics"
    2. [researcher] "Research ClickHouse for real-time analytics"
    3. [researcher] "Research Apache Druid for real-time analytics"
→ All 3 run in PARALLEL
→ [general] Synthesize all findings into comparison report
```

### Example 3: Code Review Pipeline
```
User: "Review and improve the authentication module"

SharanBot:
→ team_create(task="...", strategy="loop")
→ Dev-QA loop:
    1. [security_engineer] Review auth module for vulnerabilities
    2. [coder] Fix identified issues (depends: 1)
    3. [tester] Test the fixes (depends: 2)
    → If test fails: retry step 2 with feedback (max 3 retries)
    4. [code_reviewer] Final approval (depends: 3)
```

---

## Key Design Decisions

1. **Teams are ephemeral**: Created for a task, cleaned up after completion. No persistent teams.
2. **Profiles are the skill layer**: Instead of building complex agent types, we use YAML profiles to specialize subagents.
3. **DAG is the execution model**: All team work is expressed as a task DAG with dependencies.
4. **Quality gates are opt-in**: Not every pipeline needs QA loops, but the infrastructure is there.
5. **LLM does the planning**: Task decomposition uses the LLM, not hardcoded rules.
6. **Depth-limited recursion**: Max 3 levels of agent nesting prevents runaway spawning.
7. **Observable by default**: All team execution is traced for debugging and improvement.
8. **Backward compatible**: Single-agent mode (spawn, worker_start) still works exactly as before.

---

## What This Unlocks (∞ Possibilities)

| Before | After |
|--------|-------|
| Single agent does everything sequentially | Team of specialists work in parallel |
| No quality checks between steps | Quality gates with automated retry |
| Subagents are isolated leaf nodes | Agents can coordinate and communicate |
| One-size-fits-all agent prompt | 144+ specialized personality profiles |
| Manual task breakdown | LLM-powered automatic task decomposition |
| No visibility into multi-step work | Full execution traces and metrics |
| Can't build complex software reliably | Pipeline patterns for full dev lifecycle |
| Must start from scratch each time | Import proven agent profiles on demand |
