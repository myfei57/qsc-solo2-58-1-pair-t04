"""Evidence-chain interlock endpoints: status, policy, evaluate, revoke, enforce."""

from __future__ import annotations

from typing import Any

from portpower.console.http import BadRequest, Request, Response
from portpower.console.validation import require_text, valid_berth_code
from portpower.freq.band import DEFAULT_TARGET
from portpower.interlock.enforce import reopen_if_stale
from portpower.interlock.lanes import (
    SYNC_TOLERANCE,
    evaluate_frequency,
    evaluate_phase,
    evaluate_plug,
)
from portpower.interlock.policy import LANES, InterlockPolicy


def interlock_status(runtime, request: Request) -> Response:
    berth = _berth_from_query(runtime, request)
    policy = runtime.interlock_policies.for_berth(berth)
    decision = runtime.interlock_chains.decision(berth, policy, runtime.clock())
    return Response.json(
        200,
        {
            "berth": berth,
            "policy": policy.as_dict(),
            "breaker_closed": runtime.shore.closed,
            "decision": decision.as_dict(),
        },
    )


def interlock_policy_get(runtime, request: Request) -> Response:
    berth = _berth_from_query(runtime, request)
    policy = runtime.interlock_policies.for_berth(berth)
    return Response.json(200, {"berth": berth, "policy": policy.as_dict()})


def interlock_policy_set(runtime, request: Request) -> Response:
    payload = request.json_object()
    berth = _berth_from_payload(runtime, payload)
    policy = InterlockPolicy(
        window_seconds=_require_window(payload),
        required_lanes=_require_required_lanes(payload),
    )
    runtime.interlock_policies.set(berth, policy)
    runtime.ledger.append(
        "interlock-policy",
        f"{runtime.harbor.qualified(berth)} window={policy.window_seconds} "
        f"required={policy.required_lanes}",
    )
    return Response.json(200, {"berth": berth, "policy": policy.as_dict()})


def interlock_evaluate(runtime, request: Request) -> Response:
    payload = request.json_object()
    berth = _berth_from_payload(runtime, payload)
    now = runtime.clock()
    policy = runtime.interlock_policies.for_berth(berth)

    binding = runtime.binder.load(berth)
    ship = runtime.berths.ship_at(berth)
    expected_ship = ship or (binding.ship if binding is not None else "")
    engaged = (
        binding is not None and ship is not None and binding.ship == ship
    )

    target = DEFAULT_TARGET
    if ship:
        profile = runtime.profiles.load(ship)
        if profile is not None:
            target = profile.frequency

    runtime.interlock_chains.record(
        berth,
        evaluate_plug(
            engaged=engaged,
            bound_ship=binding.ship if binding is not None else None,
            expected_ship=expected_ship,
            policy=policy,
            now=now,
        ),
    )
    runtime.interlock_chains.record(
        berth,
        evaluate_phase(sequence=runtime.phase.current, policy=policy, now=now),
    )
    runtime.interlock_chains.record(
        berth,
        evaluate_frequency(
            syncer=runtime.syncer,
            target=target,
            tolerance=SYNC_TOLERANCE,
            policy=policy,
            now=now,
        ),
    )
    decision = runtime.interlock_chains.decision(berth, policy, now)
    runtime.ledger.append(
        "interlock-evaluate",
        f"{runtime.harbor.qualified(berth)} allowed={decision.allowed}",
    )
    return Response.json(200, {"berth": berth, "decision": decision.as_dict()})


def interlock_revoke(runtime, request: Request) -> Response:
    payload = request.json_object()
    berth = _berth_from_payload(runtime, payload)
    lane = require_text(payload, "lane")
    if lane not in LANES:
        raise BadRequest(f"unknown lane {lane!r}")
    evidence = runtime.interlock_chains.revoke(berth, lane)
    if evidence is None:
        raise LookupError(f"no evidence for lane {lane!r} at berth {berth}")
    runtime.ledger.append(
        "interlock-revoke",
        f"{runtime.harbor.qualified(berth)} {lane}",
    )
    return Response.json(200, {"berth": berth, "revoked": evidence.as_dict()})


def interlock_enforce(runtime, request: Request) -> Response:
    payload = request.json_object()
    berth = _berth_from_payload(runtime, payload)
    policy = runtime.interlock_policies.for_berth(berth)
    result = reopen_if_stale(
        runtime.shore, runtime.interlock_chains, berth, policy, runtime.clock()
    )
    if result.reopened:
        runtime.ledger.append(
            "interlock-reopen",
            f"{runtime.harbor.qualified(berth)} {'; '.join(result.reasons)}",
        )
    return Response.json(
        200,
        {
            "berth": berth,
            "breaker_state": runtime.shore.state.value,
            **result.as_dict(),
        },
    )


def _berth_from_query(runtime, request: Request) -> str:
    berth = (request.query_one("berth") or "").strip()
    return _check_berth(runtime, berth)


def _berth_from_payload(runtime, payload: dict[str, Any]) -> str:
    return _check_berth(runtime, require_text(payload, "berth"))


def _check_berth(runtime, berth: str) -> str:
    if not valid_berth_code(berth) or not runtime.catalog.has(berth):
        raise BadRequest("invalid or unknown berth code")
    return berth


def _require_window(payload: dict[str, Any]) -> float:
    value = payload.get("window_seconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BadRequest("window_seconds must be a number")
    return float(value)


def _require_required_lanes(payload: dict[str, Any]) -> int:
    value = payload.get("required_lanes")
    if isinstance(value, bool) or not isinstance(value, int):
        raise BadRequest("required_lanes must be an integer")
    return value
