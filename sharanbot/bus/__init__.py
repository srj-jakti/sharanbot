"""Message bus module for decoupled channel-agent communication."""

from sharanbot.bus.events import InboundMessage, OutboundMessage
from sharanbot.bus.queue import MessageBus

__all__ = ["MessageBus", "InboundMessage", "OutboundMessage"]
