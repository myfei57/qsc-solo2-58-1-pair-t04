"""Per-berth evidence board: verdicts, policy snapshot and close basis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from portpower.interlock.channel import ALL_CHANNELS, Channel
from portpower.interlock.policy import InterlockPolicy
from portpower.interlock.verdict import Verdict, VerdictStatus

HISTORY_KEEP = 64


@dataclass(frozen=True)
class ChannelView:
    channel: Channel
    verdict: Verdict | None
    fresh: bool
    go: bool
    blocking_no_go: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel.value,
            "verdict": self.verdict.as_dict() if self.verdict is not None else None,
            "fresh": self.fresh,
            "go": self.go,
            "blocking_no_go": self.blocking_no_go,
        }


@dataclass(frozen=True)
class Evaluation:
    now: float
    policy: InterlockPolicy
    channels: dict[Channel, ChannelView]
    fresh_go_count: int
    blocking: list[str]
    missing: list[str]

    @property
    def allowed(self) -> bool:
        # Any ``required_channels`` fresh, same-direction verdicts permit the
        # close. A live NO_GO on another channel simply is not picked — but
        # with all three required it leaves too few GO verdicts.
        return (
            not self.blocking
            and self.fresh_go_count >= self.policy.required_channels
        )

    def view(self, channel: Channel) -> ChannelView:
        return self.channels[channel]

    def reasons(self) -> list[str]:
        reasons = list(self.blocking)
        missing = self.policy.required_channels - self.fresh_go_count
        if not self.blocking and missing > 0:
            reasons.append(
                f"only {self.fresh_go_count} of {self.policy.required_channels} "
                "required channels carry a fresh go verdict"
            )
        return reasons

    def as_dict(self) -> dict[str, Any]:
        return {
            "now": self.now,
            "allowed": self.allowed,
            "fresh_go_count": self.fresh_go_count,
            "required_channels": self.policy.required_channels,
            "reasons": self.reasons(),
            "blocking": self.blocking,
            "missing": self.missing,
            "channels": [
                self.channels[channel].as_dict() for channel in ALL_CHANNELS
            ],
        }


class InterlockBoard:
    """Holds one berth's chain of evidence.

    ``latest`` is replaced per channel as verdicts arrive; ``history`` is
    append-only (revocations append a revoked tombstone). A close records
    the exact verdict ids it relied on in ``close_basis``.
    """

    def __init__(
        self,
        berth: str,
        policy: InterlockPolicy | None = None,
        history_keep: int = HISTORY_KEEP,
    ) -> None:
        self._berth = berth
        self._policy = policy or InterlockPolicy()
        self._policy.validate()
        self._latest: dict[Channel, Verdict] = {}
        self._history: list[Verdict] = []
        self._close_basis: list[Verdict] = []
        self._closed_at: float | None = None
        self._keep = max(1, history_keep)

    @property
    def berth(self) -> str:
        return self._berth

    @property
    def policy(self) -> InterlockPolicy:
        return self._policy

    @property
    def close_basis(self) -> tuple[str, ...]:
        return tuple(verdict.id for verdict in self._close_basis)

    def basis_snapshots(self) -> list[Verdict]:
        return list(self._close_basis)

    @property
    def closed_at(self) -> float | None:
        return self._closed_at

    def set_policy(self, policy: InterlockPolicy) -> None:
        policy.validate()
        self._policy = policy

    def admit(self, verdict: Verdict) -> Verdict:
        self._latest[verdict.channel] = verdict
        self._append_history(verdict)
        return verdict

    def revoke(self, channel: Channel, reason: str, now: float) -> Verdict | None:
        verdict = self._latest.get(channel)
        if verdict is None or verdict.revoked:
            return None
        tombstone = verdict.revoked_copy()
        # Record why the withdrawal happened without touching the original.
        self._latest[channel] = tombstone
        self._append_history(tombstone)
        return tombstone

    def latest(self, channel: Channel) -> Verdict | None:
        return self._latest.get(channel)

    def history(self) -> list[Verdict]:
        return list(self._history)

    def evaluate(self, now: float) -> Evaluation:
        views: dict[Channel, ChannelView] = {}
        fresh_go = 0
        live_no_go: list[str] = []
        missing: list[str] = []
        for channel in ALL_CHANNELS:
            verdict = self._latest.get(channel)
            fresh = verdict is not None and verdict.is_fresh(now)
            go = verdict is not None and verdict.is_go(now)
            no_go = verdict is not None and verdict.is_blocking_no_go(now)
            if go:
                fresh_go += 1
            elif no_go:
                live_no_go.append(f"{channel.value}: {verdict.basis}")
            else:
                missing.append(channel.value)
            views[channel] = ChannelView(
                channel=channel,
                verdict=verdict,
                fresh=fresh,
                go=go,
                blocking_no_go=no_go,
            )
        # When every channel is required, any live contradiction blocks.
        # With fewer required routes, a contradiction on a non-required
        # channel only matters if it leaves too few remaining GO channels.
        others = len(ALL_CHANNELS) - 1
        blocking: list[str] = []
        if self._policy.required_channels > others:
            blocking.extend(live_no_go)
        elif fresh_go < self._policy.required_channels:
            blocking.extend(live_no_go)
        return Evaluation(
            now=now,
            policy=self._policy,
            channels=views,
            fresh_go_count=fresh_go,
            blocking=blocking,
            missing=missing,
        )

    def record_close(self, evaluation: Evaluation) -> tuple[str, ...]:
        self._close_basis = [
            view.verdict for view in evaluation.channels.values() if view.go
        ]
        self._closed_at = evaluation.now
        return tuple(verdict.id for verdict in self._close_basis)

    def clear_close(self) -> None:
        self._close_basis = []
        self._closed_at = None

    def basis_holds(self, now: float) -> tuple[bool, list[str]]:
        """Check the verdicts a close relied on.

        A basis verdict is a snapshot, but withdrawal is tracked through the
        matching id in ``latest``: the breaker re-opens when any basis
        verdict was withdrawn, has expired, or is contradicted by a live
        NO_GO from the same channel.
        """

        if not self._close_basis:
            return False, ["no recorded close basis"]
        reasons: list[str] = []
        go_directions = 0
        for basis in self._close_basis:
            latest = self._latest.get(basis.channel)
            withdrawn = latest is not None and latest.revoked
            superseded_no_go = (
                latest is not None
                and latest.id != basis.id
                and latest.is_blocking_no_go(now)
            )
            if withdrawn:
                reasons.append(f"{basis.channel.value} verdict was withdrawn")
            elif superseded_no_go:
                reasons.append(f"{basis.channel.value}: {latest.basis}")
            elif now >= basis.expires_at:
                reasons.append(f"{basis.channel.value} verdict expired")
            elif basis.status == VerdictStatus.GO:
                go_directions += 1
        if not reasons and go_directions < self._policy.required_channels:
            reasons.append("close no longer meets required channel count")
        return not reasons, reasons

    def as_document(self) -> dict[str, Any]:
        return {
            "berth": self._berth,
            "policy": self._policy.as_dict(),
            "latest": {
                channel.value: verdict.as_dict()
                for channel, verdict in self._latest.items()
            },
            "history": [verdict.as_dict() for verdict in self._history],
            "close_basis": [verdict.as_dict() for verdict in self._close_basis],
            "closed_at": self._closed_at,
        }

    @classmethod
    def from_document(cls, document: Any) -> "InterlockBoard | None":
        if not isinstance(document, dict):
            return None
        berth = document.get("berth")
        if not isinstance(berth, str):
            return None
        policy = InterlockPolicy.from_dict(document.get("policy"))
        if policy is None:
            return None
        board = cls(berth=berth, policy=policy)
        raw_latest = document.get("latest")
        if isinstance(raw_latest, dict):
            for key, raw in raw_latest.items():
                try:
                    channel = Channel(str(key))
                except ValueError:
                    continue
                verdict = Verdict.from_dict(raw)
                if verdict is not None:
                    board._latest[channel] = verdict
        raw_history = document.get("history")
        if isinstance(raw_history, list):
            board._history = [
                verdict
                for item in raw_history
                if (verdict := Verdict.from_dict(item)) is not None
            ]
        raw_basis = document.get("close_basis")
        if isinstance(raw_basis, list):
            board._close_basis = [
                verdict
                for item in raw_basis
                if (verdict := Verdict.from_dict(item)) is not None
            ]
        closed_at = document.get("closed_at")
        if isinstance(closed_at, (int, float)):
            board._closed_at = float(closed_at)
        return board

    def _append_history(self, verdict: Verdict) -> None:
        self._history.append(verdict)
        if len(self._history) > self._keep:
            del self._history[: len(self._history) - self._keep]
