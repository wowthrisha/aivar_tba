#!/usr/bin/env python3
"""S10 (intelligence-v6): tier-invariance gate. Sweeps a fixed grid of
representative inputs through /v1/actions/evaluate (in-process, via
TestClient with fakes - no live DB/LLM) and prints, per input, only
(tier, floor_name, floors_fired) as one JSON line each - the fields
that must be byte-identical to clean-v4 with every new flag off.

Grid covers: every Reversibility x a representative affected_records
value on each side of the irreversible_bulk/data_scope band edges x
every Regulatory value x both sides of the low_confidence floor
boundary (0.5) - enough to exercise every floor and every weighted-only
band, without being the full combinatorial product.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from app.audit import AuditLog
from app.llm import ConfidenceResult
from app.main import app, get_app_engine, get_audit_log, get_confidence_provider, get_embedding_provider, get_store
from app.store import InMemoryStore

REVERSIBILITIES = ["read", "update_with_snapshot", "update_without_snapshot", "irreversible"]
AFFECTED_RECORDS = [0, 1, 9, 10, 99, 100, 999, 1000, 9999, 10000, 50000]
REGULATORY = ["none", "internal", "pii_gdpr", "phi_sox"]
CONFIDENCES = [0.0, 0.3, 0.49, 0.5, 0.51, 0.7, 0.95, 1.0]

_ACTION_TYPE_FOR_REVERSIBILITY = {
    "read": "read",
    "update_with_snapshot": "update",
    "update_without_snapshot": "update",
    "irreversible": "delete",
}
_PARAMS_FOR_ACTION_TYPE = {
    "read": {},
    "update": {"resource_id": 1, "fields": {"x": 1}},
    "delete": {"resource_id": 1},
}


class _FakeProvider:
    def __init__(self, confidence: float):
        self.confidence = confidence

    async def get_confidence(self, action_type, resource, params):
        return ConfidenceResult(confidence=self.confidence, degraded=False, reason=None)

    async def health_check(self) -> bool:
        return True


class _FakeEmbeddingProvider:
    async def embed(self, text: str):
        return None  # no embedding provider -> novelty/precedent inert, isolates the sweep to weights/floors/thresholds


class _FakeConnection:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def exec_driver_sql(self, sql):
        return None


class _FakeEngine:
    def connect(self):
        return _FakeConnection()


def run_sweep():
    # One store/audit_log/provider set for the whole sweep: routing
    # decisions here never depend on prior actions (embedding_provider
    # returns None, so novelty/precedent is inert) - only the confidence
    # value varies per call, and _FakeProvider reads a mutable attribute
    # rather than needing a fresh override object per iteration.
    results = []
    store = InMemoryStore()
    audit_log = AuditLog()
    provider = _FakeProvider(0.0)
    app.dependency_overrides[get_embedding_provider] = lambda: _FakeEmbeddingProvider()
    app.dependency_overrides[get_app_engine] = lambda: _FakeEngine()
    app.dependency_overrides[get_confidence_provider] = lambda: provider
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    with TestClient(app) as client:
        for reversibility in REVERSIBILITIES:
            action_type = _ACTION_TYPE_FOR_REVERSIBILITY[reversibility]
            params = _PARAMS_FOR_ACTION_TYPE[action_type]
            for affected_records in AFFECTED_RECORDS:
                for regulatory in REGULATORY:
                    for confidence in CONFIDENCES:
                        provider.confidence = confidence
                        resp = client.post(
                            "/v1/actions/evaluate",
                            json={
                                "agent_id": "sweep-agent",
                                "action_type": action_type,
                                "resource": "sweep/1",
                                "params": params,
                                "reversibility": reversibility,
                                "affected_records": affected_records,
                                "regulatory": regulatory,
                            },
                        )
                        body = resp.json()
                        key = {
                            "reversibility": reversibility,
                            "affected_records": affected_records,
                            "regulatory": regulatory,
                            "confidence": confidence,
                        }
                        value = {
                            "status": resp.status_code,
                            "tier": body.get("tier"),
                            "floor_name": body.get("floor_name"),
                            "floors_fired": body.get("floors_fired"),
                        }
                        results.append({"input": key, "output": value})
    app.dependency_overrides.clear()
    return results


if __name__ == "__main__":
    for row in run_sweep():
        print(json.dumps(row, sort_keys=True))
