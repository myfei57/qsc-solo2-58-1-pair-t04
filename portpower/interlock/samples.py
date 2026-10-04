"""Time-stamped frequency samples used to judge sample freshness and stability."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from portpower.freq.band import DEFAULT_TARGET, DEFAULT_TOLERANCE, in_band
from portpower.store.kvstore import RecordStore

SAMPLES_KEY = "interlock.freq.samples"
MAX_SAMPLES = 512


@dataclass(frozen=True)
class StampedSample:
    value: float
    at: float

    def as_dict(self) -> dict[str, Any]:
        return {"value": self.value, "at": self.at}

    @classmethod
    def from_dict(cls, payload: Any) -> "StampedSample | None":
        if not isinstance(payload, dict):
            return None
        try:
            return cls(value=float(payload["value"]), at=float(payload["at"]))
        except (KeyError, TypeError, ValueError):
            return None


class StampedSampleLog:
    """Append-only log; entries are never rewritten or re-timestamped."""

    def __init__(self, store: RecordStore) -> None:
        self._store = store

    def record(self, value: float, at: float) -> StampedSample:
        sample = StampedSample(value=float(value), at=float(at))

        def mutate(document: object) -> tuple[object, StampedSample]:
            samples = self._normalize(document)
            samples.append(sample)
            trimmed = samples[-MAX_SAMPLES:]
            return [item.as_dict() for item in trimmed], sample

        return self._store.update_json(SAMPLES_KEY, mutate)

    def samples(self) -> list[StampedSample]:
        return self._normalize(self._store.get_json(SAMPLES_KEY))

    def latest(self) -> StampedSample | None:
        samples = self.samples()
        return samples[-1] if samples else None

    def latest_fresh(self, now: float, freshness: float) -> StampedSample | None:
        latest = self.latest()
        if latest is None or now - latest.at > freshness:
            return None
        return latest

    def stable(
        self,
        now: float,
        target: float = DEFAULT_TARGET,
        tolerance: float = DEFAULT_TOLERANCE,
        freshness: float | None = None,
        count: int = 3,
    ) -> bool:
        """True when the last ``count`` samples are recent and all in band."""

        samples = self.samples()
        if len(samples) < count:
            return False
        window = samples[-count:]
        if freshness is not None and any(now - item.at > freshness for item in window):
            return False
        return all(in_band(item.value, target, tolerance) for item in window)

    @staticmethod
    def _normalize(document: object) -> list[StampedSample]:
        if not isinstance(document, list):
            return []
        return [
            sample
            for item in document
            if (sample := StampedSample.from_dict(item)) is not None
        ]
