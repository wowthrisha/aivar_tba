"""BONUS — adaptive calibration, shadow mode only by default.

Pure-formula tests need no I/O (compute_adjustment). API-level tests reuse
the same fake-provider pattern as tests/test_api.py so they stay hermetic
(no live OpenAI/Postgres calls).
"""

import pytest
from fastapi.testclient import TestClient

from app.audit import AuditLog
from app.calibration import calibration_for_action_type, compute_adjustment
from app.llm import ConfidenceResult
from app.main import app, get_app_engine, get_audit_log, get_confidence_provider, get_embedding_provider, get_store
from app.store import InMemoryStore


# ---------- pure formula (no I/O) ----------


def test_min_sample_size_keeps_adjustment_zero():
    adjustment, has_min = compute_adjustment(clean=2, modified_rejected=2)  # total=4 < 5
    assert adjustment == 0.0
    assert has_min is False


def test_clean_confirmations_produce_negative_adjustment():
    adjustment, has_min = compute_adjustment(clean=8, modified_rejected=2)  # ratio=0.8
    assert has_min is True
    assert adjustment < 0


def test_rejections_produce_positive_adjustment():
    adjustment, has_min = compute_adjustment(clean=2, modified_rejected=8)  # ratio=0.2
    assert has_min is True
    assert adjustment > 0


def test_adjustment_clamped_to_point_one():
    down, _ = compute_adjustment(clean=20, modified_rejected=0)  # ratio=1.0 -> raw=-0.10
    up, _ = compute_adjustment(clean=0, modified_rejected=20)  # ratio=0.0 -> raw=+0.10
    assert down == -0.10
    assert up == 0.10


# ---------- fail-soft ----------


class _RaisingAudit:
    async def list_records(self, *args, **kwargs):
        raise RuntimeError("simulated DB failure")

    async def calibration_outcomes(self, *args, **kwargs):
        raise RuntimeError("simulated DB failure")

    async def append(self, *args, **kwargs):
        return None


@pytest.mark.asyncio
async def test_calibration_db_failure_is_fail_soft():
    stats, degraded = await calibration_for_action_type(InMemoryStore(), _RaisingAudit(), "read")
    assert degraded is True
    assert stats.adjustment == 0.0


# ---------- API-level (fake confidence provider, in-memory store) ----------


class _FakeProvider:
    def __init__(self, confidence: float = 0.9):
        self._confidence = confidence

    async def get_confidence(self, action_type, resource, params):
        return ConfidenceResult(confidence=self._confidence, degraded=False, reason=None)

    async def health_check(self) -> bool:
        return True


class _FakeEmbeddingProvider:
    async def embed(self, text: str) -> list[float] | None:
        return None  # embedding_degraded path - keeps novelty entirely out of these tests


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


def _make_client(confidence=0.9):
    store = InMemoryStore()
    audit_log = AuditLog()
    app.dependency_overrides[get_confidence_provider] = lambda: _FakeProvider(confidence)
    app.dependency_overrides[get_embedding_provider] = lambda: _FakeEmbeddingProvider()
    app.dependency_overrides[get_app_engine] = lambda: _FakeEngine()
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    return TestClient(app), store, audit_log


READ_ONLY = {
    "agent_id": "agent-1", "action_type": "read", "resource": "customers/1",
    "params": {}, "reversibility": "read", "affected_records": 1, "regulatory": "none",
}
SINGLE_UPDATE = {
    "agent_id": "agent-1", "action_type": "update", "resource": "orders/1",
    "params": {"resource_id": 1, "fields": {"x": 1}},
    "reversibility": "update_without_snapshot", "affected_records": 1, "regulatory": "none",
}
BULK_DELETE = {
    "agent_id": "agent-1", "action_type": "delete", "resource": "customers",
    "params": {"resource_id": 1}, "reversibility": "irreversible",
    "affected_records": 5000, "regulatory": "none",
}


def test_off_mode_omits_calibration_and_matches_baseline(monkeypatch):
    monkeypatch.setenv("CALIBRATION_MODE", "off")
    client, _, _ = _make_client()
    with client:
        resp = client.post("/v1/actions/evaluate", json=READ_ONLY)
    assert resp.status_code == 201
    body = resp.json()
    assert "calibration" not in body or body["calibration"] is None
    app.dependency_overrides.clear()


@pytest.mark.parametrize("payload,expected_tier", [
    (READ_ONLY, "AUTONOMOUS"),
    (SINGLE_UPDATE, "CONFIRM"),
    (BULK_DELETE, "FULL_REVIEW"),
])
def test_critical_regression_shadow_matches_off_baseline(monkeypatch, payload, expected_tier):
    """The three canonical demo scenarios must be byte-identical between
    off and shadow mode - obtained from THIS run, not assumed from memory."""
    monkeypatch.setenv("CALIBRATION_MODE", "off")
    off_client, _, _ = _make_client()
    with off_client:
        off_resp = off_client.post("/v1/actions/evaluate", json=payload)
    baseline = off_resp.json()
    app.dependency_overrides.clear()

    monkeypatch.setenv("CALIBRATION_MODE", "shadow")
    shadow_client, _, _ = _make_client()
    with shadow_client:
        shadow_resp = shadow_client.post("/v1/actions/evaluate", json=payload)
    shadowed = shadow_resp.json()
    app.dependency_overrides.clear()

    assert baseline["tier"] == expected_tier == shadowed["tier"]
    assert baseline["composite"] == shadowed["composite"]
    assert baseline["floor_name"] == shadowed["floor_name"]
    # shadow mode must expose calibration but never mark it applied
    assert shadowed["calibration"] is not None
    assert shadowed["calibration"]["applied"] is False
    assert shadowed["calibration"]["mode"] == "shadow"
    assert shadowed["calibration"]["base_composite"] == baseline["composite"]


def test_shadow_mode_does_not_change_composite_or_tier_even_with_history(monkeypatch):
    """Build up enough 'clean confirmation' history for action_type=update
    to produce a non-zero adjustment, then prove shadow mode still leaves
    composite/tier untouched - only exposes the would-be adjustment."""
    monkeypatch.setenv("CALIBRATION_MODE", "off")
    client, store, audit_log = _make_client()
    with client:
        for i in range(6):
            payload = dict(SINGLE_UPDATE, resource=f"orders/{i}")
            resp = client.post("/v1/actions/evaluate", json=payload)
            action = resp.json()
            confirm = client.post(
                f"/v1/actions/{action['id']}/confirm", json={"params_hash": action["params_hash"]}
            )
            assert confirm.status_code == 200

    app.dependency_overrides.clear()
    # Reuse the SAME store/audit (now with 6 clean confirmations for
    # action_type=update) but switch to shadow mode for a fresh evaluate.
    monkeypatch.setenv("CALIBRATION_MODE", "shadow")
    app.dependency_overrides[get_confidence_provider] = lambda: _FakeProvider(0.9)
    app.dependency_overrides[get_embedding_provider] = lambda: _FakeEmbeddingProvider()
    app.dependency_overrides[get_app_engine] = lambda: _FakeEngine()
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    with TestClient(app) as c2:
        baseline_resp = c2.post("/v1/actions/evaluate", json=dict(SINGLE_UPDATE, resource="orders/baseline-check"))
    body = baseline_resp.json()
    app.dependency_overrides.clear()

    cal = body["calibration"]
    assert cal["sample_size"] >= 5
    assert cal["adjustment"] != 0.0  # real signal is present...
    assert cal["applied"] is False  # ...but shadow mode never applies it
    assert body["composite"] == pytest.approx(cal["base_composite"])  # untouched
    assert body["tier"] == "CONFIRM"  # the floor-driven baseline tier, unchanged


def test_enforce_mode_cannot_suppress_full_review_floor(monkeypatch):
    """Even with the maximum downward adjustment (-0.10) from a history of
    all-clean confirmations, an irreversible-bulk floor-driven FULL_REVIEW
    must still be FULL_REVIEW - floors are evaluated on raw inputs, not on
    the calibrated composite, and final_tier() only ever escalates."""
    monkeypatch.setenv("CALIBRATION_MODE", "off")
    client, store, audit_log = _make_client()
    with client:
        for i in range(6):
            payload = dict(BULK_DELETE, resource=f"customers-{i}")
            resp = client.post("/v1/actions/evaluate", json=payload)
            action = resp.json()
            decision = client.post(
                f"/v1/review-queue/{action['id']}/decision",
                json={"decision": "approve", "reviewer_id": "reviewer-9"},
            )
            assert decision.status_code == 200
    app.dependency_overrides.clear()

    monkeypatch.setenv("CALIBRATION_MODE", "enforce")
    app.dependency_overrides[get_confidence_provider] = lambda: _FakeProvider(0.9)
    app.dependency_overrides[get_embedding_provider] = lambda: _FakeEmbeddingProvider()
    app.dependency_overrides[get_app_engine] = lambda: _FakeEngine()
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    with TestClient(app) as c2:
        resp = c2.post("/v1/actions/evaluate", json=dict(BULK_DELETE, resource="customers-final-check"))
    body = resp.json()
    app.dependency_overrides.clear()

    assert body["calibration"]["mode"] == "enforce"
    assert body["calibration"]["applied"] is True
    assert body["calibration"]["adjustment"] == -0.10  # max downward, all-clean history
    assert body["tier"] == "FULL_REVIEW"  # floor NOT suppressed
    assert body["floor_name"] == "irreversible_bulk"


def test_calibration_db_failure_does_not_fail_evaluate(monkeypatch):
    monkeypatch.setenv("CALIBRATION_MODE", "shadow")
    store = InMemoryStore()
    app.dependency_overrides[get_confidence_provider] = lambda: _FakeProvider(0.9)
    app.dependency_overrides[get_embedding_provider] = lambda: _FakeEmbeddingProvider()
    app.dependency_overrides[get_app_engine] = lambda: _FakeEngine()
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: _RaisingAudit()
    with TestClient(app) as c:
        resp = c.post("/v1/actions/evaluate", json=READ_ONLY)
    app.dependency_overrides.clear()

    assert resp.status_code == 201  # evaluate must still succeed
    body = resp.json()
    assert body["calibration"]["degraded"] is True
    assert body["calibration"]["adjustment"] == 0.0


def test_get_calibration_endpoint_is_read_only_and_reports_action_types(monkeypatch):
    monkeypatch.setenv("CALIBRATION_MODE", "off")
    client, store, audit_log = _make_client()
    with client:
        for i in range(7):
            payload = dict(SINGLE_UPDATE, resource=f"orders/get-endpoint-{i}")
            resp = client.post("/v1/actions/evaluate", json=payload)
            action = resp.json()
            client.post(f"/v1/actions/{action['id']}/confirm", json={"params_hash": action["params_hash"]})
    app.dependency_overrides.clear()

    monkeypatch.setenv("CALIBRATION_MODE", "shadow")
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    with TestClient(app) as c2:
        resp = c2.get("/v1/calibration")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "shadow"
    update_row = next(row for row in body["action_types"] if row["action_type"] == "update")
    assert update_row["sample_size"] == 7
    assert update_row["has_min_evidence"] is True
    assert update_row["adjustment"] != 0.0  # non-zero, since >=5 clean confirmations
