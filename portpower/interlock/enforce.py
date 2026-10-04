"""Closing and re-opening the breaker strictly on the evidence chain."""

from __future__ import annotations

from dataclasses import dataclass

from portpower.disconn.breaker import Breaker
from portpower.disconn.open import open_breaker
from portpower.interlock.chain import Decision, EvidenceChain
from portpower.interlock.policy import InterlockPolicy


class InterlockDenied(RuntimeError):
    """Raised when the evidence chain does not permit the breaker to close."""

    def __init__(self, decision: Decision) -> None:
        self.decision = decision
        super().__init__(
            f"interlock denied for berth {decision.berth}: "
            + "; ".join(decision.reasons())
        )


@dataclass(frozen=True)
class ReopenResult:
    reopened: bool
    decision: Decision
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "reopened": self.reopened,
            "reasons": list(self.reasons),
            "decision": self.decision.as_dict(),
        }


def close_with_chain(
    breaker: Breaker,
    chain: EvidenceChain,
    berth: str,
    policy: InterlockPolicy,
    now: float,
) -> Decision:
    """Close only while every required lane is valid and none disagrees."""

    if breaker.tripped:
        raise InterlockDenied(chain.decision(berth, policy, now))
    decision = chain.decision(berth, policy, now)
    if not decision.allowed:
        raise InterlockDenied(decision)
    breaker.mark_closed()
    return decision


def reopen_if_stale(
    breaker: Breaker,
    chain: EvidenceChain,
    berth: str,
    policy: InterlockPolicy,
    now: float,
) -> ReopenResult:
    """Re-open a closed breaker whose chain expired, was revoked, or disagrees.

    A closed breaker is never left conducting on a chain that no longer
    permits it; the explicit causes are returned with the decision.
    """

    decision = chain.decision(berth, policy, now)
    if not breaker.closed:
        return ReopenResult(False, decision, ("breaker not closed",))
    if decision.allowed:
        return ReopenResult(False, decision, ("evidence chain holds",))
    open_breaker(breaker)
    return ReopenResult(True, decision, tuple(decision.reasons()))
