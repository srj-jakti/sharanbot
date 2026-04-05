"""Watchdog / Patrol Mode — gives SharanAGI initiative.

A human doesn't wait to be told. They wake up, look around, and decide
what needs doing. The Watchdog does the same:

1. **Project Health Patrol** — periodically checks if tests pass, servers
   are up, builds succeed, and logs are clean.
2. **Incomplete Work Resume** — scans .team_state/ for unfinished teams
   and offers to resume them.
3. **External Signal Monitor** — watches for new GitHub issues, error logs,
   deployment failures, and acts on them.
4. **Proactive Suggestions** — based on project state, suggests next actions
   without being asked.

The Watchdog runs as a background asyncio task alongside the gateway.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Coroutine

from loguru import logger

if TYPE_CHECKING:
    from sharanbot.agi.decisions import DecisionStore
    from sharanbot.providers.base import LLMProvider


@dataclass
class PatrolFinding:
    """A single finding from a patrol sweep."""

    category: str  # health, incomplete_work, external_signal, suggestion
    severity: str  # critical, warning, info
    title: str
    details: str
    action_hint: str  # suggested action for the agent
    timestamp: float = field(default_factory=time.time)
    subject: str = ""  # machine-readable subject for decision matching

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "severity": self.severity,
            "title": self.title,
            "details": self.details,
            "action_hint": self.action_hint,
            "timestamp": self.timestamp,
            "subject": self.subject,
        }


class WatchdogPatrol:
    """Periodic patrol that checks project health and finds work to do.

    Unlike the heartbeat (which reads a static HEARTBEAT.md), the Watchdog
    actively inspects the workspace, running processes, and external signals.
    """

    _STATE_FILE = "watchdog_state.json"
    _FINDINGS_FILE = "watchdog_findings.jsonl"

    def __init__(
        self,
        workspace: Path,
        on_act: Callable[[str], Coroutine[Any, Any, str]] | None = None,
        on_notify: Callable[[str], Coroutine[Any, Any, None]] | None = None,
        interval_s: int = 10 * 60,  # default: every 10 minutes
        enabled: bool = True,
        decision_store: DecisionStore | None = None,
        task_ledger=None,
    ):
        self.workspace = workspace
        self.on_act = on_act
        self.on_notify = on_notify
        self.interval_s = interval_s
        self.enabled = enabled
        self.decision_store = decision_store
        self.task_ledger = task_ledger  # For cooldown checks
        self._running = False
        self._task: asyncio.Task | None = None
        self._state = self._load_state()
        self._findings_path = workspace / self._FINDINGS_FILE
        # Track recently reported findings to avoid duplicates
        self._reported_subjects: dict[str, float] = self._state.get("reported_subjects", {})
        self._DEDUP_WINDOW = 30 * 60  # Don't re-report same subject within 30 minutes
        self._COMPLETION_COOLDOWN = 30 * 60  # 30 min cooldown after task completion

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if not self.enabled:
            logger.info("Watchdog disabled")
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        self._task.add_done_callback(self._log_exception)
        logger.info("Watchdog patrol started (every {}s)", self.interval_s)

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    @staticmethod
    def _log_exception(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.error("Watchdog loop crashed: {}", exc)

    async def _run_loop(self) -> None:
        # Run first patrol shortly after startup (30s grace period)
        await asyncio.sleep(30)
        while self._running:
            try:
                await self._patrol()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Watchdog patrol error: {}", e)
            try:
                await asyncio.sleep(self.interval_s)
            except asyncio.CancelledError:
                break

    # ------------------------------------------------------------------
    # Core patrol
    # ------------------------------------------------------------------

    async def _patrol(self) -> None:
        """Run a full patrol sweep."""
        logger.info("Watchdog: starting patrol sweep...")

        # Cooldown check: if a task was recently completed, skip patrol
        if self.task_ledger:
            try:
                recent = self.task_ledger.get_recent_tasks(n=5)
                now = time.time()
                for t in recent:
                    if t.status == "completed" and t.completed_at:
                        elapsed = now - t.completed_at
                        if elapsed < self._COMPLETION_COOLDOWN:
                            logger.info(
                                "Watchdog: skipping patrol — task '{}' completed {:.0f}s ago (cooldown {}s)",
                                t.title[:40], elapsed, self._COMPLETION_COOLDOWN,
                            )
                            return
            except Exception as e:
                logger.debug("Watchdog: task ledger check failed: {}", e)

        findings: list[PatrolFinding] = []

        findings.extend(self._check_incomplete_teams())
        findings.extend(self._check_project_health())
        findings.extend(self._check_error_logs())
        findings.extend(self._check_stale_services())

        # Filter out findings that the user has decided to suppress/stop
        findings = self._filter_by_decisions(findings)

        # Deduplicate — don't re-report the same subject within the dedup window
        findings = self._deduplicate(findings)

        if not findings:
            logger.info("Watchdog: all clear, nothing to report")
            self._state["last_patrol"] = time.time()
            self._state["last_finding_count"] = 0
            self._save_state()
            return

        # Log findings
        for f in findings:
            self._append_finding(f)
            # Track reported subjects for dedup
            if f.subject:
                self._reported_subjects[f.subject] = time.time()

        self._state["last_patrol"] = time.time()
        self._state["last_finding_count"] = len(findings)
        self._state["total_patrols"] = self._state.get("total_patrols", 0) + 1
        self._state["reported_subjects"] = self._reported_subjects
        self._save_state()

        # Act on critical findings automatically
        critical = [f for f in findings if f.severity == "critical"]
        warnings = [f for f in findings if f.severity == "warning"]

        if critical and self.on_act:
            task_desc = self._build_action_prompt(critical)
            logger.info("Watchdog: acting on {} critical finding(s)", len(critical))
            result = await self.on_act(task_desc)
            if result and self.on_notify:
                await self.on_notify(
                    f"🔍 Watchdog found and acted on {len(critical)} issue(s):\n{result[:2000]}"
                )
        elif warnings and self.on_notify:
            summary = "\n".join(f"⚠️ {f.title}: {f.details[:100]}" for f in warnings)
            await self.on_notify(f"🔍 Watchdog patrol report:\n{summary}")

    # ------------------------------------------------------------------
    # Patrol checks
    # ------------------------------------------------------------------

    def _check_incomplete_teams(self) -> list[PatrolFinding]:
        """Check for incomplete team orchestrations that can be resumed."""
        findings: list[PatrolFinding] = []
        state_dir = self.workspace / ".team_state"
        if not state_dir.exists():
            return findings

        for state_file in state_dir.glob("*.json"):
            try:
                data = json.loads(state_file.read_text(encoding="utf-8"))
                status = data.get("status", "")

                # Skip completed/cancelled teams — they're done
                if status in ("completed", "cancelled"):
                    continue

                # Skip stale teams older than 2 hours — auto-clean
                created = data.get("created_at", "")
                if created:
                    try:
                        from datetime import datetime
                        created_dt = datetime.fromisoformat(created)
                        age_hours = (datetime.now() - created_dt).total_seconds() / 3600
                        if age_hours > 2 and status in ("running", "failed"):
                            # Auto-mark as stale and skip
                            data["status"] = "cancelled"
                            state_file.write_text(
                                json.dumps(data, indent=2, default=str),
                                encoding="utf-8",
                            )
                            logger.info("Watchdog: auto-cancelled stale team {} ({}h old)", data.get("team_id"), f"{age_hours:.1f}")
                            continue
                    except Exception:
                        pass

                if status in ("running", "failed"):
                    task_desc = data.get("task", "unknown")[:100]
                    dag = data.get("dag", {})
                    total = len(dag)
                    completed = sum(
                        1 for t in dag.values() if t.get("status") == "completed"
                    )
                    findings.append(PatrolFinding(
                        category="incomplete_work",
                        severity="warning",  # Suggest, don't auto-act — let user decide
                        title=f"Incomplete team: {data.get('team_id', '?')}",
                        details=(
                            f"Task: {task_desc}\n"
                            f"Status: {status}, Progress: {completed}/{total} tasks"
                        ),
                        action_hint=(
                            f"Resume incomplete team {data.get('team_id')}. "
                            f"Original task: {task_desc}. "
                            f"{completed}/{total} tasks were completed. "
                            f"Pick up where it left off."
                        ),
                        subject=f"team:{data.get('team_id', 'unknown')}",
                    ))
            except Exception as e:
                logger.debug("Watchdog: failed to read team state {}: {}", state_file, e)
        return findings

    def _check_project_health(self) -> list[PatrolFinding]:
        """Check for common project health indicators."""
        findings: list[PatrolFinding] = []

        # Check if package.json exists and node_modules is missing
        pkg_json = self.workspace / "package.json"
        if pkg_json.exists() and not (self.workspace / "node_modules").exists():
            findings.append(PatrolFinding(
                category="health",
                severity="warning",
                title="Missing node_modules",
                details="package.json exists but node_modules/ is missing",
                action_hint="Run 'npm install' to install dependencies",
                subject="node_modules",
            ))

        # Check for common error indicators in recent log files
        for log_pattern in ["*.log", "logs/*.log"]:
            for log_file in self.workspace.glob(log_pattern):
                if log_file.stat().st_size == 0:
                    continue
                try:
                    # Only check last 50 lines of each log
                    lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-50:]
                    error_lines = [l for l in lines if "ERROR" in l or "FATAL" in l or "Traceback" in l]
                    if error_lines:
                        log_subject = log_file.stem  # e.g. "bitcoin_scanner" from "bitcoin_scanner.log"
                        findings.append(PatrolFinding(
                            category="health",
                            severity="warning",
                            title=f"Errors in {log_file.name}",
                            details=f"{len(error_lines)} error(s) in last 50 lines: {error_lines[-1][:200]}",
                            action_hint=f"Investigate errors in {log_file.name} and fix the root cause",
                            subject=log_subject,
                        ))
                except Exception:
                    pass

        return findings

    def _check_error_logs(self) -> list[PatrolFinding]:
        """Check the learning store for recurring unresolved errors."""
        findings: list[PatrolFinding] = []
        learning_path = self.workspace / "learning_store.jsonl"
        if not learning_path.exists():
            return findings
        try:
            lines = learning_path.read_text(encoding="utf-8").strip().splitlines()
            recent = lines[-20:] if len(lines) > 20 else lines
            unresolved = 0
            for line in recent:
                try:
                    entry = json.loads(line)
                    if entry.get("fix") is None and entry.get("occurrences", 0) >= 3:
                        unresolved += 1
                except Exception:
                    pass
            if unresolved >= 3:
                findings.append(PatrolFinding(
                    category="health",
                    severity="warning",
                    title=f"{unresolved} recurring unresolved errors",
                    details="Multiple errors keep happening without a fix being recorded",
                    action_hint="Review the learning store and investigate recurring errors",
                    subject="recurring_errors",
                ))
        except Exception:
            pass
        return findings

    def _check_stale_services(self) -> list[PatrolFinding]:
        """Check for services that should be running but aren't."""
        findings: list[PatrolFinding] = []
        services_file = self.workspace / "services.json"
        if not services_file.exists():
            return findings
        try:
            data = json.loads(services_file.read_text(encoding="utf-8"))
            for svc in data.get("services", []):
                if svc.get("auto_restart") and svc.get("status") == "stopped":
                    findings.append(PatrolFinding(
                        category="health",
                        severity="critical",
                        title=f"Service '{svc.get('name', '?')}' is down",
                        details=f"Auto-restart service is stopped: {svc.get('command', '?')}",
                        action_hint=f"Restart service '{svc.get('name')}' — it should be running",
                        subject=svc.get("name", "unknown_service"),
                    ))
        except Exception:
            pass
        return findings

    # ------------------------------------------------------------------
    # Filtering and deduplication
    # ------------------------------------------------------------------

    def _filter_by_decisions(self, findings: list[PatrolFinding]) -> list[PatrolFinding]:
        """Remove findings that the user has decided to suppress or stop."""
        if not self.decision_store:
            return findings

        filtered = []
        for f in findings:
            subject = f.subject or f.title
            if self.decision_store.is_suppressed(subject):
                logger.debug(
                    "Watchdog: suppressed finding '{}' — user decision active",
                    f.title,
                )
                continue
            filtered.append(f)
        return filtered

    def _deduplicate(self, findings: list[PatrolFinding]) -> list[PatrolFinding]:
        """Remove findings that were already reported within the dedup window."""
        now = time.time()
        # Clean old entries from reported_subjects
        self._reported_subjects = {
            k: v for k, v in self._reported_subjects.items()
            if now - v < self._DEDUP_WINDOW
        }

        deduped = []
        for f in findings:
            subject = f.subject or f.title
            last_reported = self._reported_subjects.get(subject, 0)
            if now - last_reported < self._DEDUP_WINDOW:
                logger.debug(
                    "Watchdog: dedup skipped '{}' — reported {:.0f}s ago",
                    f.title, now - last_reported,
                )
                continue
            deduped.append(f)
        return deduped

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_action_prompt(self, findings: list[PatrolFinding]) -> str:
        parts = [
            "[Watchdog Patrol — Automatic Action Required]\n",
            "The Watchdog patrol found issues that need attention:\n",
        ]
        for i, f in enumerate(findings, 1):
            parts.append(f"{i}. [{f.severity.upper()}] {f.title}")
            parts.append(f"   Details: {f.details}")
            parts.append(f"   Action: {f.action_hint}\n")
        parts.append(
            "Handle these issues autonomously. For incomplete teams, "
            "resume them. For health issues, investigate and fix. "
            "Report what you did."
        )
        return "\n".join(parts)

    def _append_finding(self, finding: PatrolFinding) -> None:
        try:
            self._findings_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._findings_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(finding.to_dict(), ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _load_state(self) -> dict[str, Any]:
        path = self.workspace / self._STATE_FILE
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_state(self) -> None:
        try:
            path = self.workspace / self._STATE_FILE
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self._state, indent=2), encoding="utf-8")
        except Exception:
            pass

    def get_status(self) -> dict[str, Any]:
        """Return watchdog status for dashboard."""
        status = {
            "enabled": self.enabled,
            "running": self._running,
            "interval_s": self.interval_s,
            "last_patrol": self._state.get("last_patrol"),
            "last_finding_count": self._state.get("last_finding_count", 0),
            "total_patrols": self._state.get("total_patrols", 0),
            "suppressed_subjects": [],
        }
        if self.decision_store:
            suppressed = [
                d.subject for d in self.decision_store.get_active_decisions()
                if d.decision_type in ("stop_permanent", "stop_temporary", "suppress_alert")
            ]
            status["suppressed_subjects"] = suppressed
        return status
