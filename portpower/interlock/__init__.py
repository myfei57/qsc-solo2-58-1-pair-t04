"""Evidence-chain interlock for closing the shore breaker.

Each gating channel (plug engagement, phase sequence, frequency) produces
its own :class:`~portpower.interlock.verdict.Verdict` carrying the decision,
its basis and an expiry. Closing needs every required channel to hold a
fresh, unrevoked, same-direction verdict; any later expiry, withdrawal or
contradiction re-opens the breaker. Validity windows and the number of
required channels are configured per berth and snapshotted into every
verdict at issue time, so changing a window never rewrites history.
"""

from portpower.interlock.channel import Channel
from portpower.interlock.clock import Clock, ManualClock, SystemClock
from portpower.interlock.board import InterlockBoard
from portpower.interlock.manager import (
    EvidenceChainDenied,
    InterlockManager,
    OpenOrder,
)
from portpower.interlock.policy import InterlockPolicy
from portpower.interlock.samples import StampedSample, StampedSampleLog
from portpower.interlock.verdict import Verdict, VerdictStatus

__all__ = [
    "Channel",
    "Clock",
    "EvidenceChainDenied",
    "InterlockBoard",
    "InterlockManager",
    "InterlockPolicy",
    "ManualClock",
    "OpenOrder",
    "StampedSample",
    "StampedSampleLog",
    "SystemClock",
    "Verdict",
    "VerdictStatus",
]
