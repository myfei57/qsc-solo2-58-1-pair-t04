"""One channel's conclusion: decision, basis and validity window."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from portpower.interlock.channel import Channel


class VerdictStatus:
    """Verdict state constants (kept as plain strings for JSON)."""

    GO = "go"
    NO_GO = "no_go"

    ALL = (GO, NO_GO)


@dataclass(frozen=True)
class Verdict:
    """Immutable conclusion produced by a single channel.

    The validity window (``ttl``) is copied from the berth policy in force
    when the verdict is issued, so later policy edits leave it unchanged.
    Revocation does not mutate the record: a new tombstone instance with the
    same ``id`` and ``revoked=True`` replaces it on the board while the
    original stays in history.
    """

    id: str
    channel: Channel
    status: str
    basis: str
    issued_at: float
    expires_at: float
    ttl: float
    detail: dict[str, Any]
    revoked: bool = False

    @classmethod
    def issue(
        cls,
        channel: Channel,
        status: str,
        basis: str,
        issued_at: float,
        ttl: float,
        detail: dict[str, Any] | None = None,
    ) -> "Verdict":
        if status not in VerdictStatus.ALL:
            raise ValueError(f"unknown verdict status {status!r}")
        if ttl <= 0:
            raise ValueError("verdict ttl must be positive")
        return cls(
            id=str(uuid4()),
            channel=channel,
            status=status,
            basis=basis,
            issued_at=float(issued_at),
            expires_at=float(issued_at) + float(ttl),
            ttl=float(ttl),
            detail=dict(detail or {}),
        )

    def is_fresh(self, now: float) -> bool:
        return not self.revoked and now < self.expires_at

    def is_go(self, now: float) -> bool:
        return self.status == VerdictStatus.GO and self.is_fresh(now)

    def is_blocking_no_go(self, now: float) -> bool:
        """A live NO_GO blocks; an expired or revoked one does not."""

        return self.status == VerdictStatus.NO_GO and self.is_fresh(now)

    def revoked_copy(self) -> "Verdict":
        return Verdict(
            id=self.id,
            channel=self.channel,
            status=self.status,
            basis=self.basis,
            issued_at=self.issued_at,
            expires_at=self.expires_at,
            ttl=self.ttl,
            detail=dict(self.detail),
            revoked=True,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "channel": self.channel.value,
            "status": self.status,
            "basis": self.basis,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "ttl": self.ttl,
            "detail": self.detail,
            "revoked": self.revoked,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "Verdict | None":
        if not isinstance(payload, dict):
            return None
        try:
            channel = Channel(str(payload["channel"]))
            verdict_id = payload["id"]
            status = payload["status"]
            basis = payload["basis"]
            issued_at = float(payload["issued_at"])
            expires_at = float(payload["expires_at"])
            ttl = float(payload["ttl"])
        except (KeyError, TypeError, ValueError):
            return None
        if not isinstance(verdict_id, str) or not isinstance(basis, str):
            return None
        if status not in VerdictStatus.ALL:
            return None
        detail = payload.get("detail")
        if not isinstance(detail, dict):
            detail = {}
        revoked = bool(payload.get("revoked", False))
        return cls(
            id=verdict_id,
            channel=channel,
            status=status,
            basis=basis,
            issued_at=issued_at,
            expires_at=expires_at,
            ttl=ttl,
            detail=dict(detail),
            revoked=revoked,
        )
