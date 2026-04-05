"""SharanAGI — modules that transform SharanBot into a digital human.

Subsystems:
    watchdog      — Proactive patrol mode (initiative)
    escalation    — Human-in-the-loop for CAPTCHAs, stuck moments, judgment calls
    projects      — Persistent multi-project context and priority management
    knowledge     — Proactive knowledge accumulation and skill self-assessment
    stuck         — Stuck detection, confidence scoring, smart escalation
    reactions     — Event-driven reaction engine with retry + escalation
    observability — Structured metrics, traces, and health monitoring
    evolution     — REAL autonomous self-improvement: diagnose → plan → modify → test → grow
    decisions     — Structured user decision store (stop/pause/resume/suppress)
    task_ledger   — Proof-of-work task ledger and service registry
"""
