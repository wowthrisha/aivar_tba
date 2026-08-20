"""T-10 — API and state machine. The 9 business endpoints are now backed
by SQLAlchemyStore/SQLAlchemyAuditLog at runtime (pre-T-14 persistence
fix) - these tests override get_store/get_audit_log with fresh
InMemoryStore()/AuditLog() instances per test, exactly like the
confidence provider and DB engine, so the suite stays hermetic and fast.

The real OpenAI-backed confidence provider and the real DB engine are
both overridden with fakes via FastAPI's dependency_overrides, so these
tests never call the live API or touch Postgres.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.audit import AuditLog
from app.llm import ConfidenceResult
from app.main import app, get_app_engine, get_audit_log, get_confidence_provider, get_embedding_provider, get_store
from app.store import InMemoryStore


class _FakeProvider:
    def __init__(self, confidence: float = 0.9):
        self._confidence = confidence

    async def get_confidence(self, action_type, resource, params):
        return ConfidenceResult(confidence=self._confidence, degraded=False, reason=None)

    async def health_check(self) -> bool:
        return True


class _FakeEmbeddingProvider:
    """Feature B: a fresh InMemoryStore() per test has 0 prior embedded
    actions, so novelty_floor_should_escalate never fires here (needs
    >=20) regardless of the vector's value - safe to hardcode."""

    async def embed(self, text: str) -> list[float] | None:
        return [1.0, 0.0, 0.0]


class _FakeConnection:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def exec_driver_sql(self, sql):
        return None


class _FakeEngine:
    """Stands in for the real AsyncEngine so tests never touch Postgres."""

    def connect(self):
        return _FakeConnection()


@pytest.fixture
def client():
    # store/audit_log must be created ONCE per test and reused across
    # every request in that test (a fresh instance per request would
    # mean evaluate() and a follow-up call never see the same data).
    store = InMemoryStore()
    audit_log = AuditLog()
    app.dependency_overrides[get_confidence_provider] = lambda: _FakeProvider(0.95)
    app.dependency_overrides[get_embedding_provider] = lambda: _FakeEmbeddingProvider()
    app.dependency_overrides[get_app_engine] = lambda: _FakeEngine()
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _evaluate(client, **overrides):
    body = {
        "agent_id": "agent-1",
        "action_type": "delete",
        "resource": "users/42",
        "params": {"resource_id": 42},
        "reversibility": "irreversible",
        "affected_records": 500,
        "regulatory": "none",
    }
    body.update(overrides)
    return client.post("/v1/actions/evaluate", json=body)


def test_livez_still_zero_io_and_200():
    with TestClient(app) as c:
        resp = c.get("/livez")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_reports_llm_and_db_checks(client):
    resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["checks"]["llm"] == "ok"
    assert body["checks"]["db"] == "ok"


def test_evaluate_bulk_delete_routes_to_full_review_via_floor(client):
    # irreversible + 500 records triggers the irreversible_bulk floor
    resp = _evaluate(client)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["state"] == "full_review"
    assert body["tier"] == "FULL_REVIEW"
    assert "params_hash" in body and len(body["params_hash"]) == 64  # sha256 hex


def test_evaluate_read_only_routes_autonomous(client):
    resp = _evaluate(
        client,
        action_type="read",
        params={},
        reversibility="read",
        affected_records=10,
    )
    assert resp.status_code == 201
    assert resp.json()["state"] == "autonomous"


def test_get_action_returns_current_state(client):
    created = _evaluate(client).json()
    resp = client.get(f"/v1/actions/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


def test_get_action_not_found_is_404(client):
    resp = client.get("/v1/actions/does-not-exist")
    assert resp.status_code == 404


def test_confirm_requires_matching_params_hash(client):
    created = _evaluate(
        client,
        action_type="update",
        params={"resource_id": 1, "fields": {"x": 1}},
        reversibility="update_without_snapshot",
        affected_records=1,
        regulatory="none",
    ).json()
    assert created["state"] == "confirm"

    bad = client.post(f"/v1/actions/{created['id']}/confirm", json={"params_hash": "wrong"})
    assert bad.status_code == 409

    good = client.post(
        f"/v1/actions/{created['id']}/confirm", json={"params_hash": created["params_hash"]}
    )
    assert good.status_code == 200
    assert good.json()["state"] == "approved"


def test_review_queue_lists_full_review_oldest_first(client):
    first = _evaluate(client).json()
    second = _evaluate(client, resource="users/43").json()

    resp = client.get("/v1/review-queue")
    assert resp.status_code == 200
    ids = [item["id"] for item in resp.json()]
    assert ids.index(first["id"]) < ids.index(second["id"])


def test_decision_requires_reviewer_id(client):
    created = _evaluate(client).json()
    resp = client.post(f"/v1/review-queue/{created['id']}/decision", json={"decision": "approve"})
    assert resp.status_code == 422  # reviewer_id missing


def test_decision_approve_transitions_to_approved(client):
    created = _evaluate(client).json()
    resp = client.post(
        f"/v1/review-queue/{created['id']}/decision",
        json={"decision": "approve", "reviewer_id": "reviewer-9"},
    )
    assert resp.status_code == 200
    assert resp.json()["state"] == "approved"


def test_decision_reject_transitions_to_rejected(client):
    created = _evaluate(client).json()
    resp = client.post(
        f"/v1/review-queue/{created['id']}/decision",
        json={"decision": "reject", "reviewer_id": "reviewer-9"},
    )
    assert resp.status_code == 200
    assert resp.json()["state"] == "rejected"


def test_execute_requires_approval_hash_match():
    pass  # covered by the two tests below


def test_execute_autonomous_action_directly(client):
    created = _evaluate(
        client, action_type="read", params={}, reversibility="read", affected_records=1
    ).json()
    assert created["state"] == "autonomous"

    resp = client.post(
        f"/v1/actions/{created['id']}/execute", json={"params_hash": created["params_hash"]}
    )
    assert resp.status_code == 200
    assert resp.json()["state"] == "executed"


def test_execute_approved_action_with_hash_match(client):
    created = _evaluate(client).json()
    client.post(
        f"/v1/review-queue/{created['id']}/decision",
        json={"decision": "approve", "reviewer_id": "reviewer-9"},
    )
    resp = client.post(
        f"/v1/actions/{created['id']}/execute", json={"params_hash": created["params_hash"]}
    )
    assert resp.status_code == 200
    assert resp.json()["state"] == "executed"


def test_execute_blocked_without_approval(client):
    created = _evaluate(client).json()  # FULL_REVIEW, never approved
    resp = client.post(
        f"/v1/actions/{created['id']}/execute", json={"params_hash": created["params_hash"]}
    )
    assert resp.status_code == 409


def test_execute_blocked_on_hash_mismatch(client):
    created = _evaluate(
        client, action_type="read", params={}, reversibility="read", affected_records=1
    ).json()
    resp = client.post(f"/v1/actions/{created['id']}/execute", json={"params_hash": "wrong"})
    assert resp.status_code == 409


def test_evaluate_never_executes_in_the_same_request(client):
    created = _evaluate(
        client, action_type="read", params={}, reversibility="read", affected_records=1
    ).json()
    assert created["state"] == "autonomous"  # not "executed"


def test_audit_list_and_verify(client):
    _evaluate(client)
    listing = client.get("/v1/audit")
    assert listing.status_code == 200
    assert len(listing.json()) >= 1

    verify = client.get("/v1/audit/verify")
    assert verify.status_code == 200
    assert verify.json()["valid"] is True
    assert verify.json()["records_checked"] >= 1
