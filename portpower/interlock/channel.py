"""The three independent evidence channels feeding the close decision."""

from __future__ import annotations

from enum import Enum


class Channel(str, Enum):
    PLUG = "plug"
    PHASE = "phase"
    FREQUENCY = "frequency"


ALL_CHANNELS: tuple[Channel, ...] = (Channel.PLUG, Channel.PHASE, Channel.FREQUENCY)
