"""AGI tool — exposes SharanAGI capabilities to the agent.

This tool gives the agent access to:
- Project management (register, switch, update context, get next work)
- Knowledge base (learn, recall, study, assess skills)
- Escalation (ask human for help)
- Watchdog status
- Stuck detection context
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sharanbot.agent.tools.base import Tool

if TYPE_CHECKING:
    from sharanbot.agi.escalation import EscalationManager
    from sharanbot.agi.knowledge import KnowledgeBase
    from sharanbot.agi.projects import ProjectManager
    from sharanbot.agi.stuck import StuckDetector
    from sharanbot.agi.watchdog import WatchdogPatrol


class AGITool(Tool):
    """Meta-tool for SharanAGI human-like capabilities."""

    def __init__(
        self,
        project_manager: ProjectManager | None = None,
        knowledge_base: KnowledgeBase | None = None,
        escalation: EscalationManager | None = None,
        stuck_detector: StuckDetector | None = None,
        watchdog: WatchdogPatrol | None = None,
        evolution_engine: "SelfEvolutionEngine | None" = None,
        decision_store: "DecisionStore | None" = None,
        task_ledger: "TaskLedger | None" = None,
    ):
        self._projects = project_manager
        self._kb = knowledge_base
        self._escalation = escalation
        self._stuck = stuck_detector
        self._watchdog = watchdog
        self._evolution = evolution_engine
        self._decisions = decision_store
        self._ledger = task_ledger

    @property
    def name(self) -> str:
        return "agi"

    @property
    def description(self) -> str:
        return (
            "SharanAGI human-like capabilities. Actions: "
            "project_register, project_list, project_context, project_update, "
            "project_todo_add, project_todo_done, project_next_work, "
            "knowledge_learn, knowledge_recall, knowledge_skills, "
            "ask_human, stuck_status, watchdog_status, "
            "decision_record, decision_list, decision_revoke, decision_status, "
            "task_create, task_start, task_complete, task_fail, task_list, task_status, "
            "service_register, service_start, service_stop, service_list"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "project_register", "project_list", "project_context",
                        "project_update", "project_todo_add", "project_todo_done",
                        "project_next_work", "project_set_status",
                        "knowledge_learn", "knowledge_recall", "knowledge_skills",
                        "ask_human", "stuck_status", "watchdog_status",
                        "evolve_diagnose", "evolve_run", "evolve_status",
                        "evolve_approve", "evolve_reject",
                        "decision_record", "decision_list", "decision_revoke",
                        "decision_status",
                        "task_create", "task_start", "task_complete", "task_fail",
                        "task_list", "task_status",
                        "service_register", "service_start", "service_stop",
                        "service_list",
                    ],
                    "description": "Action to perform",
                },
                "name": {"type": "string", "description": "Project name or knowledge topic"},
                "path": {"type": "string", "description": "Project path"},
                "description": {"type": "string", "description": "Description text"},
                "priority": {"type": "integer", "description": "Priority 1-10 (1=highest)"},
                "deadline": {"type": "string", "description": "ISO date deadline"},
                "tags": {"type": "string", "description": "Comma-separated tags"},
                "context": {"type": "string", "description": "Context or content text"},
                "source": {"type": "string", "description": "Knowledge source (URL, file, etc.)"},
                "query": {"type": "string", "description": "Search query for knowledge recall"},
                "question": {"type": "string", "description": "Question for human escalation"},
                "project_id": {"type": "string", "description": "Project ID"},
                "todo": {"type": "string", "description": "TODO item text"},
                "index": {"type": "integer", "description": "TODO index to complete"},
                "status": {"type": "string", "description": "Project status"},
                "decision": {"type": "string", "description": "Decision to record"},
                "tech_stack": {"type": "string", "description": "Comma-separated tech stack"},
                "confidence": {"type": "number", "description": "Confidence 0.0-1.0"},
                "decision_type": {
                    "type": "string",
                    "enum": [
                        "stop_permanent", "stop_temporary", "pause",
                        "resume", "suppress_alert", "configuration", "preference",
                    ],
                    "description": "Type of decision (for decision_record)",
                },
                "subject": {"type": "string", "description": "Subject of the decision (e.g. 'bitcoin_scanner')"},
                "expires_in_hours": {"type": "number", "description": "Hours until decision expires (for temporary decisions)"},
                "task_type": {
                    "type": "string",
                    "enum": ["build", "service", "research", "automation", "chat", "maintenance", "other"],
                    "description": "Type of task (for task_create)",
                },
                "outcome": {"type": "string", "description": "Outcome summary (for task_complete)"},
                "error_msg": {"type": "string", "description": "Error message (for task_fail)"},
                "task_id": {"type": "string", "description": "Task ID (for task operations)"},
                "service_name": {"type": "string", "description": "Service name (for service operations)"},
                "command": {"type": "string", "description": "Command to run (for service_register)"},
                "permanent": {"type": "boolean", "description": "Whether to stop permanently (for service_stop)"},
                "auto_restart_flag": {"type": "boolean", "description": "Auto-restart on gateway restart (for service_register)"},
            },
            "required": ["action"],
        }

    async def execute(self, action: str, **kwargs: Any) -> str:
        try:
            if action.startswith("project_"):
                return await self._handle_project(action, **kwargs)
            elif action.startswith("knowledge_"):
                return self._handle_knowledge(action, **kwargs)
            elif action.startswith("decision_"):
                return self._handle_decision(action, **kwargs)
            elif action.startswith("task_"):
                return self._handle_task(action, **kwargs)
            elif action.startswith("service_"):
                return self._handle_service(action, **kwargs)
            elif action == "ask_human":
                return await self._handle_ask_human(**kwargs)
            elif action == "stuck_status":
                return self._handle_stuck_status()
            elif action == "watchdog_status":
                return self._handle_watchdog_status()
            elif action.startswith("evolve_"):
                return await self._handle_evolution(action, **kwargs)
            return f"Unknown action: {action}"
        except Exception as e:
            return f"AGI tool error: {e}"

    # ------------------------------------------------------------------
    # Project actions
    # ------------------------------------------------------------------

    async def _handle_project(self, action: str, **kwargs: Any) -> str:
        if not self._projects:
            return "Project manager not available"

        if action == "project_register":
            name = kwargs.get("name", "")
            path = kwargs.get("path", ".")
            if not name:
                return "Error: name is required"
            tags = [t.strip() for t in kwargs.get("tags", "").split(",") if t.strip()]
            tech = [t.strip() for t in kwargs.get("tech_stack", "").split(",") if t.strip()]
            proj = self._projects.register(
                name=name, path=path,
                description=kwargs.get("description", ""),
                priority=kwargs.get("priority", 5),
                deadline=kwargs.get("deadline"),
                tags=tags or None, tech_stack=tech or None,
            )
            return f"Project '{proj.name}' registered (id: {proj.id}, priority: {proj.priority})"

        if action == "project_list":
            projects = self._projects.list_all(status=kwargs.get("status"))
            if not projects:
                return "No projects registered."
            lines = []
            for p in projects:
                todo_count = f", {len(p.todos)} TODOs" if p.todos else ""
                dl = f", deadline: {p.deadline}" if p.deadline else ""
                lines.append(f"  [{p.priority}] {p.name} ({p.status}{todo_count}{dl})")
            return "Projects:\n" + "\n".join(lines)

        if action == "project_context":
            pid = kwargs.get("project_id") or kwargs.get("name", "")
            ctx = self._projects.get_project_context_for_prompt(pid)
            return ctx or f"Project '{pid}' not found"

        if action == "project_update":
            pid = kwargs.get("project_id") or kwargs.get("name", "")
            ctx = kwargs.get("context", "")
            decision = kwargs.get("decision", "")
            results = []
            if ctx:
                self._projects.update_context(pid, ctx)
                results.append("context updated")
            if decision:
                self._projects.add_decision(pid, decision)
                results.append("decision recorded")
            return f"Project '{pid}': {', '.join(results)}" if results else "Nothing to update"

        if action == "project_todo_add":
            pid = kwargs.get("project_id") or kwargs.get("name", "")
            todo = kwargs.get("todo", "")
            if not todo:
                return "Error: todo text is required"
            self._projects.add_todo(pid, todo)
            return f"TODO added to '{pid}': {todo}"

        if action == "project_todo_done":
            pid = kwargs.get("project_id") or kwargs.get("name", "")
            idx = kwargs.get("index", 0)
            if self._projects.complete_todo(pid, idx):
                return f"TODO #{idx} completed in '{pid}'"
            return "Failed to complete TODO (invalid project or index)"

        if action == "project_next_work":
            proj = self._projects.get_next_work()
            if not proj:
                return "No active projects need work."
            ctx = self._projects.get_project_context_for_prompt(proj.id)
            return f"Next work: {proj.name} (priority {proj.priority})\n{ctx or ''}"

        if action == "project_set_status":
            pid = kwargs.get("project_id") or kwargs.get("name", "")
            status = kwargs.get("status", "active")
            if self._projects.set_status(pid, status):
                return f"Project '{pid}' status set to '{status}'"
            return f"Project '{pid}' not found"

        return f"Unknown project action: {action}"

    # ------------------------------------------------------------------
    # Knowledge actions
    # ------------------------------------------------------------------

    def _handle_knowledge(self, action: str, **kwargs: Any) -> str:
        if not self._kb:
            return "Knowledge base not available"

        if action == "knowledge_learn":
            topic = kwargs.get("name", "")
            content = kwargs.get("context", "") or kwargs.get("description", "")
            if not topic or not content:
                return "Error: name (topic) and context (content) are required"
            source = kwargs.get("source", "agent experience")
            tags = [t.strip() for t in kwargs.get("tags", "").split(",") if t.strip()]
            confidence = kwargs.get("confidence", 0.8)
            entry = self._kb.learn(
                topic=topic, content=content,
                source=source, tags=tags, confidence=confidence,
            )
            return f"Learned: '{entry.topic}' (confidence: {entry.confidence:.1f})"

        if action == "knowledge_recall":
            query = kwargs.get("query", "") or kwargs.get("name", "")
            if not query:
                return "Error: query is required"
            results = self._kb.recall(query)
            if not results:
                return f"No knowledge found for '{query}'"
            lines = []
            for e in results:
                lines.append(f"[{e.topic}] (confidence: {e.confidence:.1f}, used: {e.times_used}x)")
                lines.append(f"  {e.content[:300]}")
            return "Knowledge found:\n" + "\n".join(lines)

        if action == "knowledge_skills":
            skills = self._kb.list_skills()
            if not skills:
                return "No skills assessed yet."
            lines = []
            for s in sorted(skills, key=lambda x: -x.confidence):
                rate = (
                    f"{s.tasks_completed}/{s.tasks_completed + s.tasks_failed}"
                    if (s.tasks_completed + s.tasks_failed) > 0 else "no data"
                )
                lines.append(f"  {s.domain}: {s.level} (confidence: {s.confidence:.1f}, success: {rate})")
            return "Skill assessment:\n" + "\n".join(lines)

        return f"Unknown knowledge action: {action}"

    # ------------------------------------------------------------------
    # Escalation
    # ------------------------------------------------------------------

    async def _handle_ask_human(self, **kwargs: Any) -> str:
        if not self._escalation:
            return "Escalation not available — no channel connected"

        question = kwargs.get("question", "")
        if not question:
            return "Error: question is required"

        from sharanbot.agi.escalation import EscalationType
        response = await self._escalation.ask_human(
            type=EscalationType.JUDGMENT,
            question=question,
            context=kwargs.get("context", ""),
        )
        if response is None:
            return "Human did not respond within the timeout. Proceed with your best judgment."
        return f"Human response: {response}"

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _handle_stuck_status(self) -> str:
        if not self._stuck:
            return "Stuck detector not available"
        diag = self._stuck.diagnose()
        stats = self._stuck.get_stats()
        return (
            f"Stuck: {diag.is_stuck} (confidence: {diag.confidence:.1f})\n"
            f"Reason: {diag.reason}\n"
            f"Suggestion: {diag.suggestion}\n"
            f"Stats: {stats}"
        )

    def _handle_watchdog_status(self) -> str:
        if not self._watchdog:
            return "Watchdog not available"
        status = self._watchdog.get_status()
        return (
            f"Watchdog: {'running' if status['running'] else 'stopped'}\n"
            f"Last patrol: {status.get('last_patrol', 'never')}\n"
            f"Findings last patrol: {status.get('last_finding_count', 0)}\n"
            f"Total patrols: {status.get('total_patrols', 0)}"
        )

    # ------------------------------------------------------------------
    # Task Ledger
    # ------------------------------------------------------------------

    def _handle_task(self, action: str, **kwargs: Any) -> str:
        if not self._ledger:
            return "Task ledger not available"

        if action == "task_create":
            title = kwargs.get("name", "") or kwargs.get("description", "")[:100]
            if not title:
                return "Error: name or description is required"
            task = self._ledger.create_task(
                title=title,
                description=kwargs.get("description", ""),
                task_type=kwargs.get("task_type", "other"),
                strategy=kwargs.get("status", ""),
                project_id=kwargs.get("project_id", ""),
                tags=[t.strip() for t in kwargs.get("tags", "").split(",") if t.strip()],
            )
            return f"✅ Task created: {task.id} — {task.title}"

        if action == "task_start":
            task_id = kwargs.get("task_id", "")
            if not task_id:
                return "Error: task_id is required"
            if self._ledger.start_task(task_id):
                return f"▶️ Task started: {task_id}"
            return f"Task '{task_id}' not found"

        if action == "task_complete":
            task_id = kwargs.get("task_id", "")
            if not task_id:
                return "Error: task_id is required"
            outcome = kwargs.get("outcome", "")
            if self._ledger.complete_task(task_id, outcome=outcome):
                task = self._ledger.get_task(task_id)
                dur = f" ({task.duration_s:.0f}s)" if task else ""
                return f"✅ Task completed: {task_id}{dur}\n{outcome}"
            return f"Task '{task_id}' not found"

        if action == "task_fail":
            task_id = kwargs.get("task_id", "")
            if not task_id:
                return "Error: task_id is required"
            error = kwargs.get("error_msg", "") or kwargs.get("description", "")
            if self._ledger.fail_task(task_id, error=error):
                return f"❌ Task failed: {task_id}\n{error}"
            return f"Task '{task_id}' not found"

        if action == "task_list":
            return self._ledger.format_task_status()

        if action == "task_status":
            task_id = kwargs.get("task_id", "")
            if task_id:
                task = self._ledger.get_task(task_id)
                if not task:
                    return f"Task '{task_id}' not found"
                lines = [
                    f"📋 Task: {task.title}",
                    f"  ID: {task.id}",
                    f"  Status: {task.status}",
                    f"  Type: {task.task_type}",
                    f"  Strategy: {task.strategy or 'N/A'}",
                    f"  Duration: {task.duration_s:.0f}s",
                    f"  LLM calls: {task.llm_calls}, Tokens: {task.tokens_used}",
                ]
                if task.tools_used:
                    lines.append(f"  Tools: {', '.join(task.tools_used)}")
                if task.files_created:
                    lines.append(f"  Files created: {len(task.files_created)}")
                if task.artifacts:
                    lines.append(f"  Artifacts: {len(task.artifacts)}")
                if task.outcome:
                    lines.append(f"  Outcome: {task.outcome[:200]}")
                if task.error:
                    lines.append(f"  Error: {task.error[:200]}")
                return "\n".join(lines)
            # No task_id — show overall stats
            stats = self._ledger.get_stats()
            return (
                f"📊 Task Ledger Stats:\n"
                f"  Total: {stats['total_tasks']} | Active: {stats['active']} | "
                f"Completed: {stats['completed']} | Failed: {stats['failed']}\n"
                f"  Services: {stats['total_services']} ({stats['running_services']} running)\n"
                f"  Total tokens: {stats['total_tokens']:,} | Artifacts: {stats['total_artifacts']}"
            )

        return f"Unknown task action: {action}"

    # ------------------------------------------------------------------
    # Service Registry
    # ------------------------------------------------------------------

    def _handle_service(self, action: str, **kwargs: Any) -> str:
        if not self._ledger:
            return "Task ledger not available"

        if action == "service_register":
            name = kwargs.get("service_name", "") or kwargs.get("name", "")
            command = kwargs.get("command", "")
            if not name:
                return "Error: service_name is required"
            svc = self._ledger.register_service(
                name=name,
                command=command,
                description=kwargs.get("description", ""),
                auto_restart=kwargs.get("auto_restart_flag", False),
                tags=[t.strip() for t in kwargs.get("tags", "").split(",") if t.strip()],
            )
            return f"✅ Service registered: {svc.id} — {svc.name}"

        if action == "service_start":
            name = kwargs.get("service_name", "") or kwargs.get("name", "")
            if not name:
                return "Error: service_name is required"
            svc = self._ledger._find_service(name)
            if not svc:
                return f"Service '{name}' not found"
            if svc.is_permanently_stopped:
                return f"🛑 Service '{svc.name}' is permanently stopped. Use decision_revoke to re-enable."
            if self._ledger.start_service(svc.id):
                return f"▶️ Service started: {svc.name}"
            return f"Failed to start service '{name}'"

        if action == "service_stop":
            name = kwargs.get("service_name", "") or kwargs.get("name", "")
            if not name:
                return "Error: service_name is required"
            svc = self._ledger._find_service(name)
            if not svc:
                return f"Service '{name}' not found"
            permanent = kwargs.get("permanent", False)
            reason = kwargs.get("description", "") or kwargs.get("context", "")
            if self._ledger.stop_service(svc.id, permanent=permanent, reason=reason):
                status = "permanently stopped" if permanent else "stopped"
                return f"{'🛑' if permanent else '🔴'} Service {status}: {svc.name}"
            return f"Failed to stop service '{name}'"

        if action == "service_list":
            return self._ledger.format_service_status()

        return f"Unknown service action: {action}"

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------

    def _handle_decision(self, action: str, **kwargs: Any) -> str:
        if not self._decisions:
            return "Decision store not available"

        if action == "decision_record":
            subject = kwargs.get("subject", "") or kwargs.get("name", "")
            decision_type = kwargs.get("decision_type", "")
            description = kwargs.get("description", "")
            if not subject or not decision_type:
                return "Error: subject and decision_type are required"
            if not description:
                description = f"User decision: {decision_type} for {subject}"

            expires_at = None
            expires_in = kwargs.get("expires_in_hours")
            if expires_in:
                import time
                expires_at = time.time() + float(expires_in) * 3600

            d = self._decisions.record(
                subject=subject,
                decision_type=decision_type,
                description=description,
                expires_at=expires_at,
                source=kwargs.get("source", "user"),
            )
            expiry_str = ""
            if d.expires_at:
                remaining_h = (d.expires_at - d.created_at) / 3600
                expiry_str = f" (expires in {remaining_h:.1f}h)"
            return f"✅ Decision recorded: {d.decision_type} — {d.subject}{expiry_str}\n{d.description}"

        if action == "decision_list":
            subject = kwargs.get("subject") or kwargs.get("name")
            active = self._decisions.get_active_decisions(subject)
            if not active:
                return "No active decisions." + (f" (filtered by: {subject})" if subject else "")
            lines = ["Active decisions:"]
            for d in active:
                import time
                expiry = ""
                if d.expires_at:
                    remaining = d.expires_at - time.time()
                    if remaining > 0:
                        hours = remaining / 3600
                        expiry = f" (expires in {hours:.1f}h)"
                lines.append(f"  [{d.decision_type}] {d.subject}: {d.description}{expiry}")
            return "\n".join(lines)

        if action == "decision_revoke":
            subject = kwargs.get("subject", "") or kwargs.get("name", "")
            if not subject:
                return "Error: subject is required"
            decision_type = kwargs.get("decision_type")
            count = self._decisions.revoke(subject, decision_type)
            if count:
                return f"✅ Revoked {count} decision(s) for '{subject}'"
            return f"No active decisions found for '{subject}'"

        if action == "decision_status":
            return self._decisions.format_status()

        return f"Unknown decision action: {action}"

    # ------------------------------------------------------------------
    # Self-Evolution
    # ------------------------------------------------------------------

    async def _handle_evolution(self, action: str, **kwargs: Any) -> str:
        if not self._evolution:
            return "Self-evolution engine not available"

        if action == "evolve_diagnose":
            diagnoses = self._evolution._diagnosis.run_diagnosis()
            if not diagnoses:
                return "✅ No issues diagnosed — system is healthy"
            lines = [f"🔍 Found {len(diagnoses)} issue(s):"]
            for d in diagnoses[:10]:
                lines.append(
                    f"  [{d.severity:.1f}] {d.trigger.value}: {d.description}"
                )
            return "\n".join(lines)

        elif action == "evolve_run":
            from sharanbot.agi.evolution import EvolutionTrigger
            desc = kwargs.get("description", "")
            if desc:
                result = await self._evolution.trigger_from_event(
                    EvolutionTrigger.MANUAL, description=desc,
                )
            else:
                result = await self._evolution.run_cycle(
                    trigger=EvolutionTrigger.MANUAL,
                )
            if not result:
                return "Evolution cycle skipped (rate-limited or no issues found)"
            status = "✅ SUCCESS" if result.success else "❌ FAILED"
            parts = [
                f"{status}: {result.plan_id}",
                f"  Changes: {result.changes_applied}",
                f"  Tests: {'passed' if result.tests_passed else 'not passed'}",
            ]
            if result.rolled_back:
                parts.append("  Rolled back: YES")
            if result.error:
                parts.append(f"  Error: {result.error}")
            if result.new_capabilities:
                parts.append(f"  New: {', '.join(result.new_capabilities[:5])}")
            return "\n".join(parts)

        elif action == "evolve_status":
            status = self._evolution.get_status()
            growth = status["growth"]
            rl = status["rate_limit"]
            lines = [
                f"🧬 Self-Evolution Status",
                f"  Total evolutions: {growth['total_attempts']}",
                f"  Successes: {growth['successes']}",
                f"  Success rate: {growth['success_rate']}",
                f"  Capabilities added: {growth['capabilities_added']}",
                f"  Daily cycles: {rl['daily_cycles']}/{rl['max_daily']}",
                f"  Pending approval: {'YES' if status['pending_approval'] else 'no'}",
            ]
            if growth.get("capabilities"):
                lines.append(f"  Recent capabilities: {', '.join(growth['capabilities'][-5:])}")
            return "\n".join(lines)

        elif action == "evolve_approve":
            result = await self._evolution.approve_pending()
            if not result:
                return "No pending evolution plan to approve"
            status = "✅ SUCCESS" if result.success else "❌ FAILED"
            return f"{status}: {result.plan_id} — {result.changes_applied} changes"

        elif action == "evolve_reject":
            if self._evolution.reject_pending():
                return "Pending evolution plan rejected"
            return "No pending plan to reject"

        return f"Unknown evolution action: {action}"
