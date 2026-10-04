"""One lane's immutable conclusion plus the basis it was reached from."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_EXPIRED = "expired"
STATUS_REVOKED = "revoked"
STATUS_MISSING = "missing"


@dataclass(frozen=True)
class Evidence:
    """A lane verdict stamped with the validity window in force when issued.

    The window is frozen into the conclusion itself, so later policy changes
    never rewrite the expiry of conclusions already on record.
    """

    lane: str
    passed: bool
    basis: dict[str, Any]
    issued_at: float
    window: float
    revoked: bool = False

    @property
    def expires_at(self) -> float:
        return self.issued_at + self.window

    def expired(self, now: float) -> bool:
        return now > self.expires_at

    def valid(self, now: float) -> bool:
        return self.passed and not self.revoked and not self.expired(now)

    def live(self, now: float) -> bool:
        """Whether the conclusion still speaks, regardless of direction."""

        return not self.revoked and not self.expired(now)

    def status(self, now: float) -> str:
        if self.revoked:
            return STATUS_REVOKED
        if self.expired(now):
            return STATUS_EXPIRED
        return STATUS_PASS if self.passed else STATUS_FAIL

    def revoke(self) -> "Evidence":
        return replace(self, revoked=True)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: object) -> "Evidence | None":
        if not isinstance(payload, dict):
            return None
        lane = payload.get("lane")
        passed = payload.get("passed")
        basis = payload.get("basis")
        issued_at = payload.get("issued_at")
        window = payload.get("window")
        if not isinstance(lane, str) or not lane:
            return None
        if not isinstance(passed, bool):
            return None
        if not isinstance(basis, dict):
            return None
        if not isinstance(issued_at, (int, float)) or isinstance(issued_at, bool):
            return None
        if not isinstance(window, (int, float)) or isinstance(window, bool):
            return None
        return cls(
            lane=lane,
            passed=passed,
            basis=dict(basis),
            issued_at=float(issued_at),
            window=float(window),
            revoked=bool(payload.get("revoked", False)),
        )
