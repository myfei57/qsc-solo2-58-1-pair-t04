"""Tests for the evidence-chain interlock."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portpower.interlock.board import InterlockBoard
from portpower.interlock.channel import Channel
from portpower.interlock.clock import ManualClock
from portpower.interlock.manager import EvidenceChainDenied, InterlockManager
from portpower.interlock.policy import InterlockPolicy
from portpower.interlock.verdict import Verdict, VerdictStatus
from portpower.store.kvstore import RecordStore


def make_manager(clock: ManualClock | None = None) -> tuple[InterlockManager, ManualClock]:
    directory = Path(tempfile.mkdtemp())
    record_store = RecordStore(directory)
    resolved_clock = clock or ManualClock(1000.0)
    manager = InterlockManager(record_store, clock=resolved_clock)
    manager.ensure_boards(["A1", "A2"])
    return manager, resolved_clock


def feed_chain(
    manager: InterlockManager,
    berth: str = "A1",
    connection: str = "conn-1",
    ship: str = "SHIP-1",
    sequence: str = "ABC",
    samples: int = 3,
    value: float = 50.0,
) -> None:
    manager.plug_confirmed(berth, connection, ship)
    manager.phase_observed(berth, sequence)
    for _ in range(samples):
        manager.observe_frequency(value)


class VerdictTests(unittest.TestCase):
    def test_verdict_carries_its_own_window(self) -> None:
        verdict = Verdict.issue(
            Channel.PLUG, VerdictStatus.GO, "basis", issued_at=0.0, ttl=10.0
        )
        self.assertTrue(verdict.is_fresh(9.9))
        self.assertFalse(verdict.is_fresh(10.0))
        self.assertTrue(verdict.is_go(5.0))

    def test_revoked_copy_keeps_history_record_immutable(self) -> None:
        verdict = Verdict.issue(
            Channel.PLUG, VerdictStatus.GO, "basis", issued_at=0.0, ttl=10.0
        )
        tombstone = verdict.revoked_copy()
        self.assertFalse(verdict.revoked)
        self.assertTrue(tombstone.revoked)
        self.assertEqual(tombstone.id, verdict.id)
        self.assertEqual(tombstone.expires_at, verdict.expires_at)

    def test_expired_no_go_does_not_block(self) -> None:
        verdict = Verdict.issue(
            Channel.FREQUENCY, VerdictStatus.NO_GO, "stale", 0.0, 5.0
        )
        self.assertTrue(verdict.is_blocking_no_go(4.0))
        self.assertFalse(verdict.is_blocking_no_go(6.0))


class PolicyTests(unittest.TestCase):
    def test_windows_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            InterlockPolicy(plug_ttl=0).validate()

    def test_required_channel_bounds(self) -> None:
        with self.assertRaises(ValueError):
            InterlockPolicy(required_channels=0).validate()
        with self.assertRaises(ValueError):
            InterlockPolicy(required_channels=4).validate()

    def test_update_bumps_revision_but_leaves_old_verdicts(self) -> None:
        manager, clock = make_manager()
        feed_chain(manager)
        issued = manager.board("A1").latest(Channel.FREQUENCY)
        self.assertEqual(issued.ttl, InterlockPolicy().frequency_ttl)

        updated = manager.update_policy("A1", frequency_ttl=0.5)
        self.assertEqual(updated.revision, 2)
        self.assertEqual(updated.frequency_ttl, 0.5)

        # The verdict already on the board keeps the window it was issued with.
        self.assertEqual(manager.board("A1").latest(Channel.FREQUENCY).ttl, 10.0)
        manager.permit_close("A1")
        # Basis of the close still holds; the new window governs future verdicts.
        holds, _ = manager.board("A1").basis_holds(clock.now())
        self.assertTrue(holds)

        clock.advance(1.0)
        new_verdict = manager.frequency_from_samples("A1")
        self.assertEqual(new_verdict.ttl, 0.5)


class EvidenceChainTests(unittest.TestCase):
    def test_close_denied_without_fresh_samples(self) -> None:
        # Regression: an expired frequency sample used to pass the close.
        manager, clock = make_manager()
        manager.plug_confirmed("A1", "c", "S")
        manager.phase_observed("A1", "ABC")
        for _ in range(3):
            manager.observe_frequency(50.0)
        clock.advance(6.0)  # beyond sample_freshness (5s) default
        with self.assertRaises(EvidenceChainDenied):
            manager.permit_close("A1")

    def test_close_denied_when_samples_unstable(self) -> None:
        manager, _ = make_manager()
        manager.plug_confirmed("A1", "c", "S")
        manager.phase_observed("A1", "ABC")
        for value in (50.0, 49.0, 51.0):  # 49 and 51 outside +-0.5 band edge? 49 is out
            manager.observe_frequency(value)
        with self.assertRaises(EvidenceChainDenied):
            manager.permit_close("A1")

    def test_close_needs_all_three_channels_by_default(self) -> None:
        manager, _ = make_manager()
        with self.assertRaises(EvidenceChainDenied):
            manager.permit_close("A1")
        manager.plug_confirmed("A1", "c", "S")
        with self.assertRaises(EvidenceChainDenied):
            manager.permit_close("A1")
        manager.phase_observed("A1", "ABC")
        with self.assertRaises(EvidenceChainDenied):
            manager.permit_close("A1")
        for _ in range(3):
            manager.observe_frequency(50.0)
        evaluation = manager.permit_close("A1")
        self.assertTrue(evaluation.allowed)
        self.assertEqual(len(manager.board("A1").close_basis), 3)

    def test_required_channel_count_is_per_berth(self) -> None:
        manager, _ = make_manager()
        manager.update_policy("A2", required_channels=2)
        manager.plug_confirmed("A2", "c", "S")
        manager.phase_observed("A2", "ABC")
        evaluation = manager.permit_close("A2")
        self.assertTrue(evaluation.allowed)
        # A1 still requires all three.
        with self.assertRaises(EvidenceChainDenied):
            manager.permit_close("A1")

    def test_two_required_routes_accept_missing_third(self) -> None:
        manager, _ = make_manager()
        manager.update_policy("A2", required_channels=2)
        manager.plug_confirmed("A2", "c", "S")
        manager.phase_observed("A2", "ABC")
        # No frequency evidence at all: plug + phase still suffice.
        evaluation = manager.evaluate("A2")
        self.assertTrue(evaluation.allowed)
        self.assertIn("frequency", evaluation.missing)

    def test_two_required_routes_with_no_go_third_route_still_closes(self) -> None:
        manager, _ = make_manager()
        manager.update_policy("A2", required_channels=2)
        manager.plug_confirmed("A2", "c", "S")
        manager.phase_observed("A2", "ABC")
        manager.frequency_from_samples("A2")  # NO_GO: no samples
        self.assertTrue(manager.evaluate("A2").allowed)

    def test_reversed_phase_blocks_close(self) -> None:
        manager, _ = make_manager()
        feed_chain(manager, sequence="CBA")
        with self.assertRaises(EvidenceChainDenied) as ctx:
            manager.permit_close("A1")
        self.assertIn("phase", str(ctx.exception))

    def test_chain_decisions_are_expiry_dated(self) -> None:
        manager, clock = make_manager()
        feed_chain(manager)
        manager.permit_close("A1")

        clock.advance(11.0)  # frequency ttl is 10s
        order = manager.enforce("A1")
        self.assertIsNotNone(order)
        self.assertEqual(order.reasons, ["frequency verdict expired"])
        # Acting on the order does not repeat on subsequent checks.
        self.assertIsNone(manager.enforce("A1"))

    def test_withdrawn_verdict_reopens_closed_chain(self) -> None:
        manager, _ = make_manager()
        feed_chain(manager)
        manager.permit_close("A1")
        manager.revoke("A1", "plug", "operator pulled the plug")
        order = manager.enforce("A1")
        self.assertIsNotNone(order)
        self.assertEqual(order.reasons, ["plug verdict was withdrawn"])

    def test_live_no_go_after_close_reopens(self) -> None:
        manager, _ = make_manager()
        feed_chain(manager)
        manager.permit_close("A1")
        manager.phase_observed("A1", "CBA")
        order = manager.enforce("A1")
        self.assertIsNotNone(order)
        self.assertIn("reversed phase", order.reasons[0])

    def test_expired_no_go_does_not_reopen(self) -> None:
        manager, clock = make_manager()
        feed_chain(manager)
        manager.permit_close("A1")
        manager.phase_observed("A1", "CBA")  # ttl 30s
        clock.advance(31.0)
        # The NO_GO itself expired, so it no longer contradicts the close basis
        # (whose phase verdict also expired at 30s -> that is the real reason).
        order = manager.enforce("A1")
        self.assertIsNotNone(order)
        self.assertNotIn("reversed phase", " ".join(order.reasons))

    def test_history_is_append_only(self) -> None:
        manager, _ = make_manager()
        feed_chain(manager)
        original = manager.board("A1").latest(Channel.PLUG)
        manager.revoke("A1", "plug", "gone")
        after = manager.history("A1")
        # Every verdict id present before the withdrawal is still present.
        self.assertIn(original.id, {v.id for v in after})
        # The original GO record at its first position is unchanged.
        first = next(v for v in after if v.id == original.id)
        self.assertEqual(first.status, VerdictStatus.GO)
        self.assertFalse(first.revoked)
        # A revoked tombstone with the same id follows it.
        self.assertTrue(any(v.id == original.id and v.revoked for v in after))

    def test_board_round_trips_through_document(self) -> None:
        manager, clock = make_manager()
        feed_chain(manager)
        manager.permit_close("A1")
        document = manager.board("A1").as_document()
        restored = InterlockBoard.from_document(document)
        self.assertIsNotNone(restored)
        assert restored is not None
        holds, _ = restored.basis_holds(clock.now())
        self.assertTrue(holds)

    def test_no_open_order_without_recorded_close(self) -> None:
        manager, _ = make_manager()
        self.assertIsNone(manager.enforce("A1"))

    def test_healthy_basis_does_not_order_open(self) -> None:
        manager, clock = make_manager()
        feed_chain(manager)
        manager.permit_close("A1")
        clock.advance(1.0)
        self.assertIsNone(manager.enforce("A1"))

    def test_open_order_is_acted_on_once(self) -> None:
        manager, clock = make_manager()
        feed_chain(manager)
        manager.permit_close("A1")
        clock.advance(40.0)
        first = manager.enforce("A1")
        second = manager.enforce("A1")
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_new_frequency_go_cannot_resurrect_expired_basis(self) -> None:
        manager, clock = make_manager()
        feed_chain(manager)
        manager.permit_close("A1")
        clock.advance(11.0)
        # A fresh GO arrives for the expired channel. Basis already failed;
        # a new close is required rather than silently holding the old one.
        manager.observe_frequency(50.0)
        order = manager.enforce("A1")
        self.assertIsNotNone(order)

    def test_two_of_three_basis_reopens_when_one_of_two_expires(self) -> None:
        manager, clock = make_manager()
        manager.update_policy("A2", required_channels=2)
        manager.plug_confirmed("A2", "c", "S")
        manager.phase_observed("A2", "ABC")
        manager.permit_close("A2")
        clock.advance(31.0)  # both plug and phase ttl are 30s
        order = manager.enforce("A2")
        self.assertIsNotNone(order)
        self.assertEqual(
            sorted(order.reasons),
            ["phase verdict expired", "plug verdict expired"],
        )


if __name__ == "__main__":
    unittest.main()
