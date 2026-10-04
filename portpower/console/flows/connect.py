"""Bringing a vessel alongside and closing onto the shore supply."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from uuid import uuid4

from portpower.cable.rating import classify
from portpower.console.http import BadRequest
from portpower.console.validation import (
    optional_number,
    require_number,
    require_text,
    valid_berth_code,
    valid_frequency,
    valid_load,
    valid_ship_id,
    valid_temperature,
)
from portpower.disconn.limit import LoadExceedsProfile
from portpower.disconn.open import open_breaker
from portpower.interlock.enforce import close_with_chain
from portpower.interlock.lanes import (
    SYNC_TOLERANCE,
    evaluate_frequency,
    evaluate_phase,
    evaluate_plug,
)
from portpower.meter.binder import Binding
from portpower.meter.freeze import unfreeze
from portpower.meter.meter import Meter
from portpower.phase.parse import parse_sequence
from portpower.plug.engage import engage
from portpower.plug.plug import Plug
from portpower.quota.quota import Quota
from portpower.ship.profile import Profile


@dataclass(frozen=True)
class ConnectRequest:
    berth: str
    ship: str
    phase: str
    frequency: float
    load: float
    temperature: float

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ConnectRequest":
        return cls(
            berth=require_text(payload, "berth"),
            ship=require_text(payload, "ship"),
            phase=require_text(payload, "phase"),
            frequency=require_number(payload, "frequency"),
            load=require_number(payload, "load"),
            temperature=optional_number(payload, "temperature", 25.0),
        )


@dataclass(frozen=True)
class ConnectResult:
    connection_id: str
    berth: str
    ship: str
    plug: dict[str, object]
    demand: float
    headroom: float
    breaker_state: str
    phase: str
    cable_rating: str
    temperature: float
    interlock: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def run_connect(runtime, request: ConnectRequest) -> ConnectResult:
    _validate(runtime, request)

    connection_id = str(uuid4())
    berth = runtime.berths.add(request.berth)
    berth.assign(request.ship)
    vessel = runtime.fleet.add(request.ship)
    vessel.moor(request.berth)
    rollback = _Rollback(runtime, request)

    try:
        sequence = parse_sequence(request.phase)
        if sequence is None:
            raise BadRequest(f"unknown phase sequence {request.phase!r}")
        _enforce_existing_profile(runtime, request)
        profile = Profile(
            ship=request.ship,
            max_load=request.load,
            phase_seq=sequence.value,
            frequency=request.frequency,
        )
        if not profile.is_usable():
            raise BadRequest("invalid ship power profile")
        runtime.profiles.save(profile)
        runtime.history.append(profile)
        runtime.mapping.bind(request.berth, request.ship)
        runtime.binder.bind(Binding(berth=request.berth, ship=request.ship))

        meter = Meter(f"meter-{request.berth}", request.berth, runtime.store)
        unfreeze(meter)

        connector = Plug(connection_id)
        engage(connector, request.berth, runtime.binder)

        runtime.phase.set(sequence)

        now = runtime.clock()
        policy = runtime.interlock_policies.for_berth(request.berth)
        runtime.interlock_chains.record(
            request.berth,
            evaluate_plug(
                engaged=connector.engaged,
                bound_ship=connector.bound_ship,
                expected_ship=request.ship,
                policy=policy,
                now=now,
            ),
        )
        runtime.interlock_chains.record(
            request.berth,
            evaluate_phase(sequence=sequence, policy=policy, now=now),
        )
        runtime.interlock_chains.record(
            request.berth,
            evaluate_frequency(
                syncer=runtime.syncer,
                target=profile.frequency,
                tolerance=SYNC_TOLERANCE,
                policy=policy,
                now=now,
            ),
        )
        decision = close_with_chain(
            runtime.shore, runtime.interlock_chains, request.berth, policy, now
        )

        runtime.sensor.set(request.temperature)
        runtime.monitor.observe(request.temperature)
        runtime.protector.verdict(runtime.sensor.read())
        runtime.quotas.set(Quota(ship=request.ship, limit=request.load))
        runtime.ledger.append(
            "connect",
            f"{runtime.harbor.qualified(request.berth)} {request.ship} {connection_id}",
        )
    except Exception:
        rollback.undo()
        raise

    return ConnectResult(
        connection_id=connection_id,
        berth=request.berth,
        ship=request.ship,
        plug=connector.describe(),
        demand=runtime.profiles.demand(request.ship),
        headroom=runtime.profiles.headroom(request.ship),
        breaker_state=runtime.shore.state.value,
        phase=runtime.phase.current.value,
        cable_rating=classify(
            runtime.sensor.read(), runtime.cable.rated_temperature
        ).value,
        temperature=runtime.sensor.read(),
        interlock=decision.as_dict(),
    )


def _validate(runtime, request: ConnectRequest) -> None:
    if not valid_berth_code(request.berth) or not runtime.catalog.has(request.berth):
        raise BadRequest("invalid or unknown berth code")
    if not valid_ship_id(request.ship):
        raise BadRequest("invalid ship id")
    if not valid_frequency(request.frequency):
        raise BadRequest("invalid frequency")
    if not valid_load(request.load):
        raise BadRequest("invalid load")
    if not valid_temperature(request.temperature):
        raise BadRequest("invalid temperature")


def _enforce_existing_profile(runtime, request: ConnectRequest) -> None:
    existing = runtime.profiles.load(request.ship)
    if existing is None:
        return
    if not runtime.limiter.allow(request.ship, request.load):
        raise LoadExceedsProfile(
            f"load {request.load} exceeds declared {existing.max_load}"
        )


class _Rollback:
    """Releases the berth and binding when the closing chain fails."""

    def __init__(self, runtime, request: ConnectRequest) -> None:
        self._runtime = runtime
        self._request = request

    def undo(self) -> None:
        runtime = self._runtime
        request = self._request
        berth = runtime.berths.get(request.berth)
        if berth is not None and berth.ship_id == request.ship:
            runtime.berths.release(request.berth)
        runtime.binder.unbind(request.berth)
        runtime.mapping.unbind(request.berth)
        runtime.fleet.undock(request.ship)
        runtime.interlock_chains.revoke_all(request.berth)
        if runtime.shore.closed:
            open_breaker(runtime.shore)
