"""T-12 — security controls S-1, S-2, S-3, S-5, S-6, plus the race test.

S-3 and S-5 manipulate internal store/audit state directly to simulate
elapsed time / tampering, since there is no injectable clock and no
external tamperer available in-process. Documented here rather than
silently done. `store`/`audit_log` fixtures are dependency-injected into
the app (pre-T-14 persistence fix swapped the runtime default to
SQLAlchemyStore/SQLAlchemyAuditLog; these tests override back to the
in-memory versions to stay hermetic and fast).
"""

import threading

import pytest
from fastapi.testclient import TestClient

from app.audit import AuditLog
from app.llm import ConfidenceResult
from app.main import app, get_app_engine, get_audit_log, get_confidence_provider, get_embedding_provider, get_store
from app.store import InMemoryStore, canonical_params_hash


class _FakeProvider:
    async def get_confidence(self, action_type, resource, params):
        return ConfidenceResult(confidence=0.95, degraded=False, reason=None)

    async def health_check(self) -> bool:
        return True


class _FakeEmbeddingProvider:
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
    def connect(self):
        return _FakeConnection()


@pytest.fixture
def store():
    return InMemoryStore()


@pytest.fixture
def audit_log():
    return AuditLog()


@pytest.fixture
def client(store, audit_log):
    app.dependency_overrides[get_confidence_provider] = lambda: _FakeProvider()
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


# ---------------------------------------------------------------------------
# S-1: payload hash pinning
# ---------------------------------------------------------------------------


def test_s1_hash_is_computed_over_canonicalised_json_key_order_independent():
    a = canonical_params_hash({"resource_id": 42, "note": "x"})
    b = canonical_params_hash({"note": "x", "resource_id": 42})
    assert a == b


def test_s1_confirm_and_execute_both_reject_hash_drift(client):
    created = _evaluate(
        client,
        action_type="update",
        params={"resource_id": 1, "fields": {"x": 1}},
        reversibility="update_without_snapshot",
        affected_records=1,
    ).json()
    assert created["state"] == "confirm"

    bad_confirm = client.post(
        f"/v1/actions/{created['id']}/confirm", json={"params_hash": "drifted"}
    )
    assert bad_confirm.status_code == 409

    good_confirm = client.post(
        f"/v1/actions/{created['id']}/confirm", json={"params_hash": created["params_hash"]}
    )
    assert good_confirm.status_code == 200

    bad_execute = client.post(
        f"/v1/actions/{created['id']}/execute", json={"params_hash": "drifted"}
    )
    assert bad_execute.status_code == 409


# ---------------------------------------------------------------------------
# S-2: idempotency keys
# ---------------------------------------------------------------------------


def test_s2_evaluate_replay_returns_the_original_action_not_a_new_one(client):
    first = _evaluate(client, idempotency_key="ev-key-1").json()
    second = _evaluate(client, idempotency_key="ev-key-1").json()
    assert first["id"] == second["id"]

    all_actions_with_this_resource = [
        r for r in client.get("/v1/audit").json() if r["payload"].get("action_id") == first["id"]
    ]
    # exactly one "evaluated" event was ever recorded for this idempotency key
    evaluated_events = [r for r in all_actions_with_this_resource if r["event_type"] == "evaluated"]
    assert len(evaluated_events) == 1


def test_s2_execute_replay_returns_original_result_without_reexecuting(client):
    created = _evaluate(
        client, action_type="read", params={}, reversibility="read", affected_records=1
    ).json()
    assert created["state"] == "autonomous"

    body = {"params_hash": created["params_hash"], "idempotency_key": "exec-key-1"}
    first = client.post(f"/v1/actions/{created['id']}/execute", json=body)
    second = client.post(f"/v1/actions/{created['id']}/execute", json=body)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()

    executed_events = [
        r
        for r in client.get(f"/v1/audit?action_id={created['id']}").json()
        if r["event_type"] == "executed"
    ]
    assert len(executed_events) == 1  # not executed twice


# ---------------------------------------------------------------------------
# S-3: approval TTL
# ---------------------------------------------------------------------------


async def test_s3_expired_approval_cannot_execute(client, store):
    from datetime import datetime, timedelta, timezone

    created = _evaluate(client).json()  # FULL_REVIEW
    client.post(
        f"/v1/review-queue/{created['id']}/decision",
        json={"decision": "approve", "reviewer_id": "reviewer-9"},
    )
    # simulate elapsed time: no injectable clock exists, so force the
    # stored approval's expires_at into the past directly.
    approval = await store.get_approval(created["id"])
    approval.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    resp = client.post(
        f"/v1/actions/{created['id']}/execute", json={"params_hash": created["params_hash"]}
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# S-5: hash-chained audit, tampering detected
# ---------------------------------------------------------------------------


async def test_s5_tampered_middle_record_is_detected():
    # Uses a fresh, isolated AuditLog rather than the shared per-test
    # fixture instance, for full control over exactly what's in it.
    log = AuditLog()
    await log.append("evaluated", "agent-1", {"action_id": "a1", "tier": "CONFIRM"})
    middle = await log.append("evaluated", "agent-1", {"action_id": "a2", "tier": "FULL_REVIEW"})
    await log.append("evaluated", "agent-1", {"action_id": "a3", "tier": "AUTONOMOUS"})

    assert (await log.verify()).valid is True

    # tamper with the middle record's payload directly (an external
    # attacker editing the row would look the same: entry_hash no longer
    # matches the recomputed hash of the mutated payload).
    object.__setattr__(middle, "payload", {**middle.payload, "tier": "AUTONOMOUS"})

    result = await log.verify()
    assert result.valid is False
    assert result.first_invalid_id == middle.id


# ---------------------------------------------------------------------------
# S-6: separation of duties
# ---------------------------------------------------------------------------


def test_s6_agent_cannot_approve_its_own_action(client):
    created = _evaluate(client, agent_id="agent-x").json()
    resp = client.post(
        f"/v1/review-queue/{created['id']}/decision",
        json={"decision": "approve", "reviewer_id": "agent-x"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Race: two concurrent decisions on the same queue item
# ---------------------------------------------------------------------------


def test_race_concurrent_decisions_resolve_via_conditional_update(client):
    created = _evaluate(client).json()

    results = []

    def decide(decision: str):
        resp = client.post(
            f"/v1/review-queue/{created['id']}/decision",
            json={"decision": decision, "reviewer_id": "reviewer-9"},
        )
        results.append(resp.status_code)

    t1 = threading.Thread(target=decide, args=("approve",))
    t2 = threading.Thread(target=decide, args=("reject",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert sorted(results) == [200, 409]
