"""Rolling log of every frequency sample pushed by the field bus."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from portpower.freq.band import DEFAULT_TARGET, DEFAULT_TOLERANCE, in_band
from portpower.store.kvstore import RecordStore

SAMPLES_KEY = "freq.samples"
MAX_SAMPLES = 512


class SampleLog:
    def __init__(self, store: RecordStore, clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._clock = clock

    def record(self, value: float, at: float | None = None) -> list[float]:
        stamp = self._clock() if at is None else float(at)

        def mutate(document: object) -> tuple[object, list[float]]:
            entries = _normalize_entries(document)
            entries.append({"value": float(value), "at": stamp})
            trimmed = entries[-MAX_SAMPLES:]
            return trimmed, [entry["value"] for entry in trimmed]

        return self._store.update_json(SAMPLES_KEY, mutate)

    def samples(self) -> list[float]:
        return [entry["value"] for entry in self.entries()]

    def entries(self) -> list[dict[str, Any]]:
        return _normalize_entries(self._store.get_json(SAMPLES_KEY))

    @property
    def latest(self) -> float | None:
        samples = self.samples()
        return samples[-1] if samples else None

    def latest_at(self) -> float | None:
        """Timestamp of the newest sample, or ``None`` if it predates stamping."""

        entries = self.entries()
        if not entries:
            return None
        return entries[-1]["at"]

    def average(self) -> float:
        samples = self.samples()
        return sum(samples) / len(samples) if samples else 0.0

    def stable(self, target: float = DEFAULT_TARGET, tolerance: float = DEFAULT_TOLERANCE) -> bool:
        samples = self.samples()
        if not samples:
            return False
        return all(in_band(sample, target, tolerance) for sample in samples[-5:])


def _normalize_entries(document: object) -> list[dict[str, Any]]:
    if not isinstance(document, list):
        return []
    entries: list[dict[str, Any]] = []
    for item in document:
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            entries.append({"value": float(item), "at": None})
        elif isinstance(item, dict):
            value = item.get("value")
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            at = item.get("at")
            entries.append(
                {
                    "value": float(value),
                    "at": float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) else None,
                }
            )
    return entries
