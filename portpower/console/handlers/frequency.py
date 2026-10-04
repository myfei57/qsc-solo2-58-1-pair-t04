"""Grid frequency field bus endpoints."""

from __future__ import annotations

from portpower.console.handlers._interlock import apply_open_orders
from portpower.console.http import BadRequest, Request, Response
from portpower.console.validation import require_number, valid_frequency
from portpower.freq.band import DEFAULT_TARGET, DEFAULT_TOLERANCE


def freq_get(runtime, request: Request) -> Response:
    return Response.json(
        200,
        {
            "current": runtime.syncer.current,
            "target": DEFAULT_TARGET,
            "tolerance": DEFAULT_TOLERANCE,
            "within": runtime.syncer.within(),
            "stable": runtime.syncer.stable(),
        },
    )


def freq_set(runtime, request: Request) -> Response:
    payload = request.json_object()
    value = require_number(payload, "value")
    if not valid_frequency(value):
        raise BadRequest("invalid frequency")
    runtime.syncer.refresh(value)
    runtime.syncer.record_sample(value)
    sample, verdicts = runtime.interlocks.observe_frequency(value)
    orders = runtime.interlocks.sweep(runtime.catalog.codes())
    opened = apply_open_orders(runtime, orders)
    return Response.json(
        200,
        {
            "current": runtime.syncer.current,
            "samples": len(runtime.syncer.samples()),
            "sample_at": sample.at,
            "verdicts": {
                berth: verdict.as_dict() for berth, verdict in verdicts.items()
            },
            "opened": opened,
        },
    )


def freq_samples(runtime, request: Request) -> Response:
    stamped = runtime.interlocks.samples.samples()
    return Response.json(
        200,
        {
            "samples": runtime.syncer.samples(),
            "stamped": [sample.as_dict() for sample in stamped],
            "latest": runtime.syncer.latest_sample(),
            "average": runtime.syncer.average(),
            "stable": runtime.syncer.stable(),
            "current": runtime.syncer.current,
        },
    )
