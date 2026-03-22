"""Team communication bus — inter-agent messaging within a team."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Coroutine


@dataclass
class TeamMessage:
    """A message exchanged between agents in a team."""

    from_agent: str
    to_agent: str | None  # None = broadcast to all
    msg_type: str  # "handoff", "result", "question", "status", "quality_gate"
    content: str
    attachments: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)


class TeamBus:
    """Pub/sub messaging between agents within a team.

    Each agent has a dedicated asyncio.Queue (its mailbox).
    Messages can be sent point-to-point or broadcast.
    """

    def __init__(self) -> None:
        self._mailboxes: dict[str, asyncio.Queue[TeamMessage]] = {}
        self._subscribers: dict[str, list[Callable[[TeamMessage], Coroutine]]] = {}
        self._history: list[TeamMessage] = []

    def register_agent(self, agent_id: str) -> None:
        """Create a mailbox for an agent."""
        if agent_id not in self._mailboxes:
            self._mailboxes[agent_id] = asyncio.Queue()

    def unregister_agent(self, agent_id: str) -> None:
        """Remove an agent's mailbox."""
        self._mailboxes.pop(agent_id, None)
        self._subscribers.pop(agent_id, None)

    async def send(self, from_id: str, to_id: str, msg_type: str, content: str,
                   attachments: dict[str, Any] | None = None) -> None:
        """Send a message from one agent to another."""
        msg = TeamMessage(
            from_agent=from_id,
            to_agent=to_id,
            msg_type=msg_type,
            content=content,
            attachments=attachments or {},
        )
        self._history.append(msg)
        if to_id in self._mailboxes:
            await self._mailboxes[to_id].put(msg)
        # Fire callbacks for subscribers
        for cb in self._subscribers.get(to_id, []):
            asyncio.create_task(cb(msg))

    async def broadcast(self, from_id: str, msg_type: str, content: str,
                        attachments: dict[str, Any] | None = None) -> None:
        """Broadcast a message to all registered agents (except sender)."""
        msg = TeamMessage(
            from_agent=from_id,
            to_agent=None,
            msg_type=msg_type,
            content=content,
            attachments=attachments or {},
        )
        self._history.append(msg)
        for aid, q in self._mailboxes.items():
            if aid != from_id:
                await q.put(msg)

    async def receive(self, agent_id: str, timeout: float = 30.0) -> TeamMessage | None:
        """Wait for a message addressed to *agent_id*."""
        q = self._mailboxes.get(agent_id)
        if not q:
            return None
        try:
            return await asyncio.wait_for(q.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def get_pending(self, agent_id: str) -> list[TeamMessage]:
        """Non-blocking: drain all pending messages for an agent."""
        q = self._mailboxes.get(agent_id)
        if not q:
            return []
        msgs: list[TeamMessage] = []
        while not q.empty():
            try:
                msgs.append(q.get_nowait())
            except asyncio.QueueEmpty:
                break
        return msgs

    def subscribe(self, agent_id: str, callback: Callable[[TeamMessage], Coroutine]) -> None:
        """Register a callback for messages sent to *agent_id*."""
        self._subscribers.setdefault(agent_id, []).append(callback)

    def get_history(self, limit: int = 50) -> list[TeamMessage]:
        """Return recent message history."""
        return self._history[-limit:]

    def clear(self) -> None:
        """Reset all mailboxes and history."""
        self._mailboxes.clear()
        self._subscribers.clear()
        self._history.clear()
