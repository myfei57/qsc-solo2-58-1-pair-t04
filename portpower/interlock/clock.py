"""Time source used for verdict freshness, swappable in tests."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> float:
        """Monotonic epoch seconds."""


class SystemClock:
    def now(self) -> float:
        from time import time

        return time()


class ManualClock:
    """Deterministic clock that only advances on demand."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = float(start)

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> float:
        if seconds < 0:
            raise ValueError("cannot rewind the clock")
        self._now += float(seconds)
        return self._now
