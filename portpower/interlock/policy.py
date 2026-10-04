"""Per-berth configuration of the evidence-chain criteria.

Windows are expressed in seconds. Every value is snapshotted into the
verdicts issued while this revision is current: updating the policy bumps
``revision`` and only affects later verdicts.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

DEFAULT_PLUG_TTL = 30.0
DEFAULT_PHASE_TTL = 30.0
DEFAULT_FREQUENCY_TTL = 10.0
DEFAULT_SAMPLE_FRESHNESS = 5.0
DEFAULT_STABLE_SAMPLES = 3
DEFAULT_REQUIRED_CHANNELS = 3
MIN_CHANNELS = 1
MAX_CHANNELS = 3


@dataclass(frozen=True)
class InterlockPolicy:
    required_channels: int = DEFAULT_REQUIRED_CHANNELS
    plug_ttl: float = DEFAULT_PLUG_TTL
    phase_ttl: float = DEFAULT_PHASE_TTL
    frequency_ttl: float = DEFAULT_FREQUENCY_TTL
    sample_freshness: float = DEFAULT_SAMPLE_FRESHNESS
    stable_samples: int = DEFAULT_STABLE_SAMPLES
    revision: int = 1

    def ttl_for(self, channel) -> float:
        from portpower.interlock.channel import Channel

        return {
            Channel.PLUG: self.plug_ttl,
            Channel.PHASE: self.phase_ttl,
            Channel.FREQUENCY: self.frequency_ttl,
        }[channel]

    def validate(self) -> None:
        if not (MIN_CHANNELS <= self.required_channels <= MAX_CHANNELS):
            raise ValueError(
                f"required_channels must be between {MIN_CHANNELS} and {MAX_CHANNELS}"
            )
        for name, value in (
            ("plug_ttl", self.plug_ttl),
            ("phase_ttl", self.phase_ttl),
            ("frequency_ttl", self.frequency_ttl),
            ("sample_freshness", self.sample_freshness),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.stable_samples < 1:
            raise ValueError("stable_samples must be at least 1")
        if self.revision < 1:
            raise ValueError("revision must be positive")

    def updated(self, **changes: Any) -> "InterlockPolicy":
        """Return a new policy with the given fields changed (revision +1)."""

        changes.pop("revision", None)
        candidate = replace(self, **changes)
        candidate.validate()
        return replace(candidate, revision=self.revision + 1)

    def as_dict(self) -> dict[str, Any]:
        return {
            "required_channels": self.required_channels,
            "plug_ttl": self.plug_ttl,
            "phase_ttl": self.phase_ttl,
            "frequency_ttl": self.frequency_ttl,
            "sample_freshness": self.sample_freshness,
            "stable_samples": self.stable_samples,
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "InterlockPolicy | None":
        if not isinstance(payload, dict):
            return None
        try:
            policy = cls(
                required_channels=int(payload["required_channels"]),
                plug_ttl=float(payload["plug_ttl"]),
                phase_ttl=float(payload["phase_ttl"]),
                frequency_ttl=float(payload["frequency_ttl"]),
                sample_freshness=float(payload["sample_freshness"]),
                stable_samples=int(payload["stable_samples"]),
                revision=int(payload.get("revision", 1)),
            )
        except (KeyError, TypeError, ValueError):
            return None
        policy.validate()
        return policy
