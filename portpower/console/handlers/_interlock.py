"""Helpers for turning evidence-chain open orders into breaker actions."""

from __future__ import annotations

from portpower.interlock.manager import OpenOrder


def apply_open_orders(runtime, orders: list[OpenOrder]) -> list[dict[str, object]]:
    """Open the shore breaker for each stale chain and audit the reason."""

    opened: list[dict[str, object]] = []
    for order in orders:
        was_closed = runtime.shore.closed
        if was_closed:
            runtime.shore.mark_open()
        for reason in order.reasons:
            runtime.ledger.append(
                "interlock.open",
                f"{runtime.harbor.qualified(order.berth)} {reason}",
            )
        opened.append(
            {
                "berth": order.berth,
                "breaker_was_closed": was_closed,
                "state": runtime.shore.state.value,
                "reasons": order.reasons,
                "basis": order.basis,
            }
        )
    return opened
