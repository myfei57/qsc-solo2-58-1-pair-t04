"""End-to-end tests for the evidence chain wired into connect/disconnect."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs

from portpower.console.errors import error_response
from portpower.console.http import Request
from portpower.console.runtime import build_runtime
from portpower.console.routes import build_router
from portpower.interlock.clock import ManualClock
from portpower.ns.harbor import Harbor
from portpower.store.kvstore import RecordStore


def _request(method: str, path: str, body=None) -> Request:
    if "?" in path:
        base, query_text = path.split("?", 1)
        query = parse_qs(query_text, keep_blank_values=True)
    else:
        base, query = path, {}
    payload = json.dumps(body).encode() if body is not None else b""
    return Request(method, base, query, {}, payload)


class ConnectChainFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = RecordStore(Path(tempfile.mkdtemp()))
        self.runtime = build_runtime(Harbor("h", "north"), self.store)
        self.clock = ManualClock(1_000_000.0)
        self.runtime.interlocks._clock = self.clock
        self.router = build_router(self.runtime)

    def call(self, method: str, path: str, body=None):
        try:
            return self.router.dispatch(_request(method, path, body))
        except Exception as exc:  # mirrors the server's error boundary
            return error_response(exc)

    def connect(self, berth: str = "A1", ship: str = "SHIP-1", phase: str = "ABC"):
        return self.call(
            "POST",
            "/connect",
            {
                "berth": berth,
                "ship": ship,
                "phase": phase,
                "frequency": 50.0,
                "load": 100.0,
            },
        )

    def feed(self, value: float = 50.0, count: int = 3) -> None:
        for _ in range(count):
            self.call("POST", "/freq", {"value": value})

    def test_stale_frequency_sample_is_the_regression(self) -> None:
        self.feed()
        self.clock.advance(9.0)  # beyond 5s sample freshness
        response = self.connect()
        self.assertEqual(response.status, 409)
        payload = json.loads(response.payload)
        self.assertIn("freshness", payload["error"])
        self.assertFalse(self.runtime.shore.closed)

    def test_successful_close_records_basis(self) -> None:
        self.feed()
        response = self.connect()
        self.assertEqual(response.status, 200)
        payload = json.loads(response.payload)
        self.assertEqual(payload["breaker_state"], "closed")
        self.assertTrue(payload["interlock"]["allowed"])
        self.assertEqual(
            len(self.runtime.interlocks.board("A1").close_basis), 3
        )

    def test_expired_verdict_reopens_after_sweep(self) -> None:
        self.feed()
        self.assertEqual(self.connect().status, 200)
        self.clock.advance(11.0)
        response = self.call("POST", "/interlock/sweep", {})
        payload = json.loads(response.payload)
        self.assertEqual(payload["count"], 1)
        self.assertFalse(self.runtime.shore.closed)

    def test_new_bad_sample_reopens_closed_breaker(self) -> None:
        self.feed()
        self.assertEqual(self.connect().status, 200)
        response = self.call("POST", "/freq", {"value": 48.0})
        payload = json.loads(response.payload)
        self.assertEqual(len(payload["opened"]), 1)
        self.assertFalse(self.runtime.shore.closed)

    def test_disconnect_withdraws_plug_evidence(self) -> None:
        self.feed()
        self.assertEqual(self.connect().status, 200)
        response = self.call(
            "POST", "/disconnect", {"berth": "A1", "ship": "SHIP-1", "final_value": 3.0}
        )
        self.assertEqual(response.status, 200)
        chain = json.loads(
            self.call("GET", "/interlock/chain?berth=A1").payload
        )
        plug = next(
            channel
            for channel in chain["evaluation"]["channels"]
            if channel["channel"] == "plug"
        )
        self.assertTrue(plug["verdict"]["revoked"])
        self.assertEqual(chain["closed"]["basis"], [])

    def test_policy_change_does_not_touch_historical_verdicts(self) -> None:
        self.feed()
        self.assertEqual(self.connect().status, 200)
        response = self.call(
            "POST",
            "/interlock/policy?berth=A1",
            {"frequency_ttl": 2.0, "sample_freshness": 1.0},
        )
        self.assertEqual(response.status, 200)
        chain = json.loads(
            self.call("GET", "/interlock/chain?berth=A1").payload
        )
        history_ttls = {
            verdict["channel"]: verdict["ttl"] for verdict in chain["history"]
        }
        self.assertEqual(history_ttls.get("frequency"), 10.0)
        # The close basis still holds under its original windows.
        self.assertTrue(chain["closed"]["holds"])


if __name__ == "__main__":
    unittest.main()
