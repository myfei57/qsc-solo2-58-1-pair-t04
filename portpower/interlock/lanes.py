"""Lane evaluators that turn field state into stamped evidence."""

from __future__ import annotations

from portpower.freq.band import in_band
from portpower.freq.syncer import Syncer
from portpower.interlock.evidence import Evidence
from portpower.interlock.policy import InterlockPolicy
from portpower.phase.sequence import Sequence

LANE_PLUG = "plug"
LANE_PHASE = "phase"
LANE_FREQUENCY = "frequency"

SYNC_TOLERANCE = 0.5


def evaluate_plug(
    *,
    engaged: bool,
    bound_ship: str | None,
    expected_ship: str,
    policy: InterlockPolicy,
    now: float,
) -> Evidence:
    """The engagement confirmation must be active and name the moored vessel."""

    ship_matches = bound_ship is not None and bound_ship == expected_ship
    passed = engaged and ship_matches
    basis: dict[str, object] = {
        "engaged": engaged,
        "bound_ship": bound_ship,
        "expected_ship": expected_ship,
        "ship_matches": ship_matches,
    }
    return Evidence(
        lane=LANE_PLUG,
        passed=passed,
        basis=basis,
        issued_at=now,
        window=policy.window_seconds,
    )


def evaluate_phase(
    *,
    sequence: Sequence,
    policy: InterlockPolicy,
    now: float,
) -> Evidence:
    """Only a forward rotation is acceptable to close onto the vessel."""

    passed = sequence is Sequence.FORWARD
    basis: dict[str, object] = {
        "observed": sequence.value,
        "required": Sequence.FORWARD.value,
    }
    return Evidence(
        lane=LANE_PHASE,
        passed=passed,
        basis=basis,
        issued_at=now,
        window=policy.window_seconds,
    )


def evaluate_frequency(
    *,
    syncer: Syncer,
    target: float,
    tolerance: float,
    policy: InterlockPolicy,
    now: float,
) -> Evidence:
    """The supply must be in band, stable, and backed by a fresh sample."""

    current = syncer.current
    within = in_band(current, target, tolerance)
    stable = syncer.stable(target, tolerance)
    latest_at = syncer.latest_sample_at()
    sample_age = None if latest_at is None else max(0.0, now - latest_at)
    fresh = sample_age is not None and sample_age <= policy.window_seconds
    passed = within and stable and fresh
    basis: dict[str, object] = {
        "current": current,
        "target": target,
        "tolerance": tolerance,
        "within": within,
        "stable": stable,
        "samples": len(syncer.samples()),
        "sample_age": sample_age,
        "fresh": fresh,
    }
    return Evidence(
        lane=LANE_FREQUENCY,
        passed=passed,
        basis=basis,
        issued_at=now,
        window=policy.window_seconds,
    )
