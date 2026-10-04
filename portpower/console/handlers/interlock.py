"""Evidence-chain endpoints: per-berth chain view, policy, withdrawal, sweep."""

from __future__ import annotations

from portpower.console.handlers._interlock import apply_open_orders
from portpower.console.http import BadRequest, Request, Response
from portpower.console.validation import require_text
from portpower.interlock.channel import ALL_CHANNELS

_POLICY_FIELDS = {
    "required_channels": int,
    "plug_ttl": float,
    "phase_ttl": float,
    "frequency_ttl": float,
    "sample_freshness": float,
    "stable_samples": int,
}


def _berth_param(runtime, request: Request, payload: dict[str, object] | None = None) -> str:
    berth = request.query_one("berth") or (
        payload.get("berth") if payload else None
    )
    if not isinstance(berth, str) or not berth.strip():
        raise BadRequest("berth is required")
    berth = berth.strip()
    if not runtime.catalog.has(berth):
        raise LookupError(f"unknown berth {berth!r}")
    return berth


def chain(runtime, request: Request) -> Response:
    berth = _berth_param(runtime, request)
    evaluation = runtime.interlocks.evaluate(berth)
    board = runtime.interlocks.board(berth)
    holds, basis_reasons = board.basis_holds(runtime.interlocks.clock.now())
    return Response.json(
        200,
        {
            "berth": berth,
            "allowed": evaluation.allowed,
            "evaluation": evaluation.as_dict(),
            "policy": board.policy.as_dict(),
            "closed": {
                "basis": list(board.close_basis),
                "closed_at": board.closed_at,
                "holds": holds if board.close_basis else None,
                "reasons": basis_reasons if board.close_basis else None,
            },
            "history": [verdict.as_dict() for verdict in board.history()],
        },
    )


def policy_get(runtime, request: Request) -> Response:
    berth = _berth_param(runtime, request)
    return Response.json(
        200,
        {"berth": berth, "policy": runtime.interlocks.policy(berth).as_dict()},
    )


def policy_set(runtime, request: Request) -> Response:
    payload = request.json_object()
    berth = _berth_param(runtime, request, payload)
    changes: dict[str, object] = {}
    for field, caster in _POLICY_FIELDS.items():
        if field not in payload or payload[field] is None:
            continue
        value = payload[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise BadRequest(f"{field} must be a number")
        changes[field] = caster(float(value))
    if not changes:
        raise BadRequest("no policy fields supplied")
    updated = runtime.interlocks.update_policy(berth, **changes)
    return Response.json(
        200,
        {
            "berth": berth,
            "policy": updated.as_dict(),
            "note": "existing verdicts keep the windows they were issued with",
        },
    )


def revoke(runtime, request: Request) -> Response:
    payload = request.json_object()
    berth = _berth_param(runtime, request, payload)
    channel = payload.get("channel")
    if not isinstance(channel, str):
        raise BadRequest("channel is required")
    if channel not in {item.value for item in ALL_CHANNELS}:
        raise BadRequest(f"unknown channel {channel!r}")
    reason = payload.get("reason")
    reason_text = reason.strip() if isinstance(reason, str) and reason.strip() else "revoked by operator"
    verdict = runtime.interlocks.revoke(berth, channel, reason_text)
    order = runtime.interlocks.enforce(berth)
    opened = apply_open_orders(runtime, [order]) if order is not None else []
    return Response.json(
        200,
        {"verdict": verdict.as_dict(), "opened": opened},
    )


def sweep(runtime, request: Request) -> Response:
    orders = runtime.interlocks.sweep(runtime.catalog.codes())
    opened = apply_open_orders(runtime, orders)
    return Response.json(200, {"count": len(opened), "opened": opened})


def phase(runtime, request: Request) -> Response:
    """Admit an operator/field phase observation for one berth."""

    payload = request.json_object()
    berth = _berth_param(runtime, request, payload)
    sequence = require_text(payload, "sequence").upper()
    if sequence not in ("ABC", "CBA"):
        raise BadRequest("sequence must be ABC or CBA")
    verdict = runtime.interlocks.phase_observed(berth, sequence)
    order = runtime.interlocks.enforce(berth)
    opened = apply_open_orders(runtime, [order]) if order is not None else []
    return Response.json(
        200,
        {"verdict": verdict.as_dict(), "opened": opened},
    )
