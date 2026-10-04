"""Per-berth evidence chain and the close decision derived from it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from portpower.interlock.evidence import STATUS_MISSING, Evidence
from portpower.interlock.policy import LANES, InterlockPolicy
from portpower.store.kvstore import RecordStore

CHAIN_PREFIX = "interlock.chain."


def chain_key(berth: str) -> str:
    return CHAIN_PREFIX + berth


@dataclass(frozen=True)
class LaneReport:
    lane: str
    status: str
    valid: bool
    evidence: Evidence | None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "lane": self.lane,
            "status": self.status,
            "valid": self.valid,
        }
        if self.evidence is not None:
            payload["evidence"] = self.evidence.as_dict()
            payload["expires_at"] = self.evidence.expires_at
        return payload


@dataclass(frozen=True)
class Decision:
    """Whether the chain currently permits the breaker to conduct."""

    berth: str
    allowed: bool
    required_lanes: int
    valid_lanes: tuple[str, ...]
    blocking: tuple[str, ...]
    reports: tuple[LaneReport, ...]

    def reasons(self) -> list[str]:
        """Explicit causes to cite when the chain does not permit closing."""

        causes = [f"{report.lane}:{report.status}" for report in self.reports if not report.valid]
        if len(self.valid_lanes) < self.required_lanes:
            causes.append(f"valid_lanes:{len(self.valid_lanes)}<{self.required_lanes}")
        if self.blocking:
            causes.append("disagreeing:" + ",".join(self.blocking))
        return causes

    def as_dict(self) -> dict[str, Any]:
        return {
            "berth": self.berth,
            "allowed": self.allowed,
            "required_lanes": self.required_lanes,
            "valid_lanes": list(self.valid_lanes),
            "blocking": list(self.blocking),
            "lanes": [report.as_dict() for report in self.reports],
        }


class EvidenceChain:
    """Latest conclusion per lane for one berth, persisted in the store."""

    def __init__(self, store: RecordStore) -> None:
        self._store = store

    def record(self, berth: str, evidence: Evidence) -> Evidence:
        def mutate(document: object) -> tuple[object, Evidence]:
            lanes = _normalize(document)
            lanes[evidence.lane] = evidence
            return {lane: item.as_dict() for lane, item in lanes.items()}, evidence

        return self._store.update_json(chain_key(berth), mutate)

    def revoke(self, berth: str, lane: str) -> Evidence | None:
        def mutate(document: object) -> tuple[object, Evidence | None]:
            lanes = _normalize(document)
            current = lanes.get(lane)
            if current is None:
                return None, None
            withdrawn = current.revoke()
            lanes[lane] = withdrawn
            return {name: item.as_dict() for name, item in lanes.items()}, withdrawn

        return self._store.update_json(chain_key(berth), mutate)

    def revoke_all(self, berth: str) -> list[Evidence]:
        withdrawn: list[Evidence] = []
        for lane in self.lanes(berth):
            evidence = self.revoke(berth, lane)
            if evidence is not None:
                withdrawn.append(evidence)
        return withdrawn

    def lanes(self, berth: str) -> dict[str, Evidence]:
        return _normalize(self._store.get_json(chain_key(berth)))

    def clear(self, berth: str) -> None:
        self._store.delete(chain_key(berth))

    def decision(self, berth: str, policy: InterlockPolicy, now: float) -> Decision:
        lanes = self.lanes(berth)
        reports: list[LaneReport] = []
        valid: list[str] = []
        blocking: list[str] = []
        for lane in LANES:
            evidence = lanes.get(lane)
            if evidence is None:
                reports.append(LaneReport(lane=lane, status=STATUS_MISSING, valid=False, evidence=None))
                continue
            status = evidence.status(now)
            is_valid = evidence.valid(now)
            if is_valid:
                valid.append(lane)
            elif evidence.live(now) and not evidence.passed:
                blocking.append(lane)
            reports.append(LaneReport(lane=lane, status=status, valid=is_valid, evidence=evidence))
        allowed = len(valid) >= policy.required_lanes and not blocking
        return Decision(
            berth=berth,
            allowed=allowed,
            required_lanes=policy.required_lanes,
            valid_lanes=tuple(valid),
            blocking=tuple(blocking),
            reports=tuple(reports),
        )


def _normalize(document: object) -> dict[str, Evidence]:
    if not isinstance(document, dict):
        return {}
    lanes: dict[str, Evidence] = {}
    for lane, payload in document.items():
        if lane not in LANES:
            continue
        evidence = Evidence.from_dict(payload)
        if evidence is not None:
            lanes[lane] = evidence
    return lanes
