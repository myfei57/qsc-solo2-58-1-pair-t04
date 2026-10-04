"""Per-berth interlock policy: validity window and lanes required to close."""

from __future__ import annotations

from dataclasses import dataclass

from portpower.store.kvstore import RecordStore

LANES: tuple[str, ...] = ("plug", "phase", "frequency")
DEFAULT_WINDOW_SECONDS = 300.0
DEFAULT_REQUIRED_LANES = len(LANES)
POLICY_PREFIX = "interlock.policy."


def policy_key(berth: str) -> str:
    return POLICY_PREFIX + berth


@dataclass(frozen=True)
class InterlockPolicy:
    """How long a conclusion holds and how many lanes must agree to close."""

    window_seconds: float = DEFAULT_WINDOW_SECONDS
    required_lanes: int = DEFAULT_REQUIRED_LANES

    def __post_init__(self) -> None:
        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        if not 1 <= self.required_lanes <= len(LANES):
            raise ValueError(f"required_lanes must be between 1 and {len(LANES)}")

    def as_dict(self) -> dict[str, object]:
        return {
            "window_seconds": self.window_seconds,
            "required_lanes": self.required_lanes,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "InterlockPolicy | None":
        if not isinstance(payload, dict):
            return None
        window = payload.get("window_seconds")
        required = payload.get("required_lanes")
        if not isinstance(window, (int, float)) or isinstance(window, bool):
            return None
        if not isinstance(required, int) or isinstance(required, bool):
            return None
        try:
            return cls(window_seconds=float(window), required_lanes=required)
        except ValueError:
            return None


DEFAULT_POLICY = InterlockPolicy()


class PolicyStore:
    """Berth-scoped policies; berths without an entry use the default."""

    def __init__(self, store: RecordStore) -> None:
        self._store = store

    def for_berth(self, berth: str) -> InterlockPolicy:
        return InterlockPolicy.from_dict(self._store.get_json(policy_key(berth))) or DEFAULT_POLICY

    def set(self, berth: str, policy: InterlockPolicy) -> InterlockPolicy:
        self._store.put_json(policy_key(berth), policy.as_dict())
        return policy
