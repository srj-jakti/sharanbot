"""Chat channels module with plugin architecture."""

from sharanbot.channels.base import BaseChannel
from sharanbot.channels.manager import ChannelManager

__all__ = ["BaseChannel", "ChannelManager"]
