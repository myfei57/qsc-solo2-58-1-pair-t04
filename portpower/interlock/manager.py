"""Application service issuing verdicts, gating closes and ordering re-opens."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from portpower.freq.band import DEFAULT_TARGET, DEFAULT_TOLERANCE
from portpower.interlock.board import Evaluation, InterlockBoard
from portpower.interlock.channel import ALL_CHANNELS, Channel
from portpower.interlock.clock import Clock, SystemClock
from portpower.interlock.policy import InterlockPolicy
from portpower.interlock.samples import StampedSample, StampedSampleLog
from portpower.interlock.verdict import Verdict, VerdictStatus
from portpower.store.kvstore import RecordStore, validate_key

BOARD_PREFIX = "interlock.board."


class EvidenceChainDenied(RuntimeError):
    """Raised when a close is requested without a complete, aligned chain."""


@dataclass(frozen=True)
class OpenOrder:
    berth: str
    reasons: list[str]
    basis: list[dict[str, Any]]


def board_key(berth: str) -> str:
    return BOARD_PREFIX + validate_key(berth)


class InterlockManager:
    def __init__(
        self,
        store: RecordStore,
        clock: Clock | None = None,
        frequency_target: float = DEFAULT_TARGET,
        frequency_tolerance: float = DEFAULT_TOLERANCE,
    ) -> None:
        self._store = store
        self._clock = clock or SystemClock()
        self._target = float(frequency_target)
        self._tolerance = float(frequency_tolerance)
        self._samples = StampedSampleLog(store)
        self._boards: dict[str, InterlockBoard] = {}

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def samples(self) -> StampedSampleLog:
        return self._samples

    # -- boards and policy -------------------------------------------------

    def board(self, berth: str) -> InterlockBoard:
        return self._load(berth)

    def ensure_boards(self, berths) -> None:
        for berth in berths:
            self._load(berth)

    def policy(self, berth: str) -> InterlockPolicy:
        return self._load(berth).policy

    def update_policy(self, berth: str, **changes: Any) -> InterlockPolicy:
        board = self._load(berth)
        updated = board.policy.updated(**changes)
        board.set_policy(updated)
        self._persist(board)
        return updated

    def evaluate(self, berth: str) -> Evaluation:
        board = self._load(berth)
        return board.evaluate(self._clock.now())

    def history(self, berth: str) -> list[Verdict]:
        return self._load(berth).history()

    # -- evidence intake ---------------------------------------------------

    def plug_confirmed(self, berth: str, connection_id: str, ship: str) -> Verdict:
        return self._admit(
            berth,
            Channel.PLUG,
            VerdictStatus.GO,
            "plug engaged on active meter binding",
            {"connection": connection_id, "ship": ship},
        )

    def plug_withdrawn(self, berth: str, reason: str = "plug released") -> Verdict | None:
        board = self._load(berth)
        verdict = board.revoke(Channel.PLUG, reason, self._clock.now())
        self._persist(board)
        return verdict

    def phase_observed(self, berth: str, sequence: Any) -> Verdict:
        value = getattr(sequence, "value", str(sequence))
        if value == "ABC":
            return self._admit(
                berth,
                Channel.PHASE,
                VerdictStatus.GO,
                "forward phase sequence observed",
                {"sequence": value},
            )
        return self._admit(
            berth,
            Channel.PHASE,
            VerdictStatus.NO_GO,
            "reversed phase sequence observed",
            {"sequence": value},
        )

    def observe_frequency(self, value: float) -> tuple[StampedSample, dict[str, Verdict]]:
        """Stamp one field-bus sample and update every known berth's chain."""

        sample = self._samples.record(value, self._clock.now())
        verdicts: dict[str, Verdict] = {}
        for berth in self._known_berths():
            verdicts[berth] = self.frequency_from_samples(berth)
        return sample, verdicts

    def frequency_from_samples(self, berth: str) -> Verdict:
        board = self._load(berth)
        policy = board.policy
        now = self._clock.now()
        latest = self._samples.latest_fresh(now, policy.sample_freshness)
        if latest is None:
            verdict = self._build(
                board,
                Channel.FREQUENCY,
                VerdictStatus.NO_GO,
                "no frequency sample within freshness window",
                {"sample_freshness": policy.sample_freshness},
            )
        elif not self._samples.stable(
            now,
            self._target,
            self._tolerance,
            freshness=policy.sample_freshness,
            count=policy.stable_samples,
        ):
            verdict = self._build(
                board,
                Channel.FREQUENCY,
                VerdictStatus.NO_GO,
                f"frequency samples not stable within {self._tolerance:g} Hz "
                f"of {self._target:g} Hz",
                {
                    "latest": latest.value,
                    "stable_samples": policy.stable_samples,
                    "sample_age": now - latest.at,
                },
            )
        else:
            verdict = self._build(
                board,
                Channel.FREQUENCY,
                VerdictStatus.GO,
                "fresh stable frequency samples inside sync band",
                {
                    "latest": latest.value,
                    "target": self._target,
                    "tolerance": self._tolerance,
                    "stable_samples": policy.stable_samples,
                    "sample_age": now - latest.at,
                },
            )
        board.admit(verdict)
        self._persist(board)
        return verdict

    def revoke(self, berth: str, channel_value: str, reason: str) -> Verdict:
        try:
            channel = Channel(channel_value)
        except ValueError as exc:
            raise ValueError(f"unknown channel {channel_value!r}") from exc
        board = self._load(berth)
        verdict = board.revoke(channel, reason, self._clock.now())
        if verdict is None:
            raise LookupError(
                f"no active {channel.value} verdict for berth {berth}"
            )
        self._persist(board)
        return verdict

    # -- close gating and re-opening --------------------------------------

    def permit_close(self, berth: str) -> Evaluation:
        # Frequency evidence is only as fresh as the field bus: re-derive it
        # from the stamped samples now, so a verdict issued seconds ago cannot
        # ride on data that has since aged out of the freshness window.
        # (Skipped when this berth does not require frequency evidence.)
        board = self._load(berth)
        if self._frequency_is_required(board):
            self.frequency_from_samples(berth)
        evaluation = board.evaluate(self._clock.now())
        if not evaluation.allowed:
            raise EvidenceChainDenied(
                f"evidence chain does not permit closing for {berth}: "
                + "; ".join(evaluation.reasons())
            )
        board.record_close(evaluation)
        self._persist(board)
        return evaluation

    def clear_close(self, berth: str) -> None:
        board = self._load(berth)
        board.clear_close()
        self._persist(board)

    @staticmethod
    def _frequency_is_required(board: InterlockBoard) -> bool:
        # Frequency is required unless the berth can be satisfied by all the
        # other channels without it.
        return board.policy.required_channels > len(ALL_CHANNELS) - 1

    def enforce(self, berth: str) -> OpenOrder | None:
        """Return an open order when a previously closed basis no longer holds.

        Acting on the order clears the recorded close basis so the same
        failure cannot re-open the breaker again and again.
        """

        board = self._load(berth)
        if not board.close_basis:
            return None
        holds, reasons = board.basis_holds(self._clock.now())
        if holds:
            return None
        basis = [verdict.as_dict() for verdict in board.basis_snapshots()]
        board.clear_close()
        self._persist(board)
        return OpenOrder(berth=berth, reasons=reasons, basis=basis)

    def sweep(self, berths) -> list[OpenOrder]:
        orders: list[OpenOrder] = []
        for berth in berths:
            order = self.enforce(berth)
            if order is not None:
                orders.append(order)
        return orders

    # -- internals ---------------------------------------------------------

    def _admit(
        self,
        berth: str,
        channel: Channel,
        status: str,
        basis: str,
        detail: dict[str, Any],
    ) -> Verdict:
        board = self._load(berth)
        verdict = self._build(board, channel, status, basis, detail)
        board.admit(verdict)
        self._persist(board)
        return verdict

    def _build(
        self,
        board: InterlockBoard,
        channel: Channel,
        status: str,
        basis: str,
        detail: dict[str, Any],
    ) -> Verdict:
        return Verdict.issue(
            channel=channel,
            status=status,
            basis=basis,
            issued_at=self._clock.now(),
            ttl=board.policy.ttl_for(channel),
            detail={**detail, "policy_revision": board.policy.revision},
        )

    def _known_berths(self) -> list[str]:
        prefix = BOARD_PREFIX
        return sorted(key[len(prefix) :] for key in self._board_keys())

    def _board_keys(self) -> list[str]:
        with self._store.transaction() as data:
            return sorted(key for key in data if key.startswith(BOARD_PREFIX))

    def _load(self, berth: str) -> InterlockBoard:
        cached = self._boards.get(berth)
        if cached is not None:
            return cached
        document = self._store.get_json(board_key(berth))
        board = InterlockBoard.from_document(document)
        if board is None:
            board = InterlockBoard(berth=berth, policy=InterlockPolicy())
            self._persist(board)
        self._boards[berth] = board
        return board

    def _persist(self, board: InterlockBoard) -> None:
        self._store.put_json(board_key(board.berth), board.as_document())
