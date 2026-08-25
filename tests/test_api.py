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


def test_negative_affected_records_returns_422(client):
    # D-13: negative affected_records used to reach _data_scope_score's
    # `raise ValueError` unhandled -> bare 500. Boundary validation now
    # catches it at the API edge instead.
    resp = _evaluate(client, affected_records=-5)
    assert resp.status_code == 422
    assert "affected_records" in resp.text


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


def test_evaluate_response_includes_factor_scores_matching_persisted_assessment(client):
    """OD-1: reversibility_score/data_scope_score/regulatory_score/
    confidence_score/floor_name are additive passthrough of values
    app/risk/ already computes and app/db_store.py already persists to
    risk_assessments - never a CLI-side recomputation. This proves the
    evaluate response equals what the real scorer/router produce for the
    same inputs, and that GET round-trips the same persisted values."""
    from app.risk.confidence import structural_completeness, two_signal_confidence
    from app.risk.router import route_action
    from app.risk.scorer import Regulatory, Reversibility, score_action

    resp = _evaluate(client)  # irreversible, 500 records, none -> full_review via floor
    assert resp.status_code == 201, resp.text
    body = resp.json()

    # client fixture uses _FakeProvider(0.95); _evaluate's default body is
    # action_type=delete, params={"resource_id": 42}.
    structural = structural_completeness("delete", {"resource_id": 42})
    confidence = two_signal_confidence(0.95, structural)
    expected = score_action(Reversibility.IRREVERSIBLE, 500, Regulatory.NONE, confidence)
    expected_routing = route_action(Reversibility.IRREVERSIBLE, 500, Regulatory.NONE, confidence)

    assert body["reversibility_score"] == expected.reversibility
    assert body["data_scope_score"] == expected.data_scope
    assert body["regulatory_score"] == expected.regulatory
    assert body["confidence_score"] == expected.confidence
    assert body["floor_name"] == expected_routing.floor_name

    fetched = client.get(f"/v1/actions/{body['id']}").json()
    assert fetched["reversibility_score"] == body["reversibility_score"]
    assert fetched["data_scope_score"] == body["data_scope_score"]
    assert fetched["regulatory_score"] == body["regulatory_score"]
    assert fetched["confidence_score"] == body["confidence_score"]
    assert fetched["floor_name"] == body["floor_name"]


def test_evaluate_response_reports_tier_invariant_stability(client):
    # FEATURE C: default body (irreversible, 500 records) always fires
    # irreversible_bulk regardless of llm_confidence - tier is invariant.
    resp = _evaluate(client)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["stability"]["stability"] == "TIER_INVARIANT"
    assert body["stability"]["flips_below"] is None


def test_evaluate_response_reports_confidence_bound_stability_and_does_not_change_tier(client):
    # FEATURE C: update_with_snapshot/1 record/none isn't in any FULL_
    # REVIEW-tier floor; low_confidence_on_mutation (CONFIRM) fires only
    # below 0.5, and the weighted composite never reaches 0.30 at
    # llm_confidence >= 0.5 - a clean floor-driven flip at exactly 0.5.
    # client fixture's _FakeProvider(0.95) means the REAL tier here is
    # AUTONOMOUS; the additive stability field must not have changed that.
    resp = _evaluate(
        client,
        action_type="update",
        params={"resource_id": 1, "fields": {"x": 1}},
        reversibility="update_with_snapshot",
        affected_records=1,
        regulatory="none",
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["tier"] == "AUTONOMOUS"
    assert body["stability"]["stability"] == "CONFIDENCE_BOUND"
    assert 0.495 <= body["stability"]["flips_below"] <= 0.505


def test_evaluate_response_includes_session_floor_and_does_not_change_tier(client):
    # FEATURE B3: default SESSION_FLOOR_MODE=shadow means every evaluate()
    # response carries a session_floor block; a single read-only action is
    # far under every threshold, so would_fire is False and applied is
    # always False (shadow never applies).
    resp = _evaluate(
        client,
        action_type="read",
        params={},
        reversibility="read",
        affected_records=1,
        regulatory="none",
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["tier"] == "AUTONOMOUS"
    assert body["session_floor"]["would_fire"] is False
    assert body["session_floor"]["applied"] is False


def test_evaluate_response_reports_session_floor_would_fire_without_changing_tier(client):
    # FEATURE B3/B4 (critical): 20 mutating actions by the same agent in
    # one window crosses SESSION_MUTATION_COUNT_THRESHOLD - the 20th
    # call's session_floor must report would_fire=True, applied=False,
    # and its OWN tier must be exactly what a standalone evaluation of
    # the same input would produce (shadow never changes a tier).
    last_body = None
    for i in range(20):
        last_body = _evaluate(
            client,
            action_type="update",
            resource=f"users/{i}",
            params={"resource_id": i, "fields": {"x": 1}},
            reversibility="update_with_snapshot",
            affected_records=1,
            regulatory="none",
        ).json()

    assert last_body["session_floor"]["would_fire"] is True
    assert last_body["session_floor"]["floor"] == "session_mutation_volume"
    assert last_body["session_floor"]["applied"] is False
    # Same inputs, isolated agent (no session history) -> same tier as
    # the 20th call would have gotten standalone: proves the floor never
    # touched routing.
    isolated = _evaluate(
        client,
        agent_id="agent-isolated",
        action_type="update",
        resource="users/19",
        params={"resource_id": 19, "fields": {"x": 1}},
        reversibility="update_with_snapshot",
        affected_records=1,
        regulatory="none",
    ).json()
    assert last_body["tier"] == isolated["tier"]
    assert last_body["composite"] == isolated["composite"]


def test_session_stats_endpoint_aggregates_recent_actions(client):
    for i in range(3):
        _evaluate(
            client,
            agent_id="agent-session",
            action_type="delete",
            resource=f"item/{i}",
            params={"resource_id": i},
            reversibility="irreversible",
            affected_records=1,
            regulatory="none",
        )
    resp = client.get("/v1/sessions/agent-session?window=300")
    assert resp.status_code == 200
    body = resp.json()
    assert body["degraded"] is False
    assert body["action_count"] == 3
    assert body["distinct_resource_count"] == 3
    assert body["mutation_count"] == 3
    assert body["session_floor"] is not None


def test_session_stats_endpoint_window_excludes_agents_with_no_recent_actions(client):
    resp = client.get("/v1/sessions/never-seen-agent?window=300")
    assert resp.status_code == 200
    body = resp.json()
    assert body["action_count"] == 0
    assert body["escalation_rate"] is None
    assert body["novelty_rate"] is None


def test_session_stats_endpoint_degrades_gracefully_on_store_error(client, monkeypatch):
    from app.main import get_store

    class _BrokenStore:
        async def list_session_actions(self, agent_id, since):
            raise RuntimeError("boom")

    client.app.dependency_overrides[get_store] = lambda: _BrokenStore()
    resp = client.get("/v1/sessions/agent-x?window=300")
    assert resp.status_code == 200  # never fails the request (B4)
    body = resp.json()
    assert body["degraded"] is True
    assert body["action_count"] == 0


def test_list_pending_returns_only_this_agents_non_terminal_actions(client):
    mine = _evaluate(client, agent_id="agent-mine").json()  # full_review, pending
    _evaluate(client, agent_id="agent-other").json()  # different agent, excluded
    autonomous = _evaluate(
        client, agent_id="agent-mine", action_type="read", params={}, reversibility="read",
        affected_records=1,
    ).json()  # autonomous -> not "pending"

    resp = client.get("/v1/actions/pending?agent_id=agent-mine")
    assert resp.status_code == 200
    ids = {a["id"] for a in resp.json()}
    assert ids == {mine["id"]}
    assert autonomous["id"] not in ids


def test_precedent_check_is_read_only_no_action_or_audit_created(client):
    resp = client.post(
        "/v1/precedent/check",
        json={"action_type": "delete", "resource": "users/1", "params": {"resource_id": 1}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "matches" in body and "summary" in body

    audit_before = client.get("/v1/audit").json()
    resp2 = client.post(
        "/v1/precedent/check",
        json={"action_type": "delete", "resource": "users/2", "params": {"resource_id": 2}},
    )
    assert resp2.status_code == 200
    audit_after = client.get("/v1/audit").json()
    assert len(audit_after) == len(audit_before)  # no new audit entry


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


def test_review_queue_item_without_reviewer_id_is_unpersonalised(client):
    created = _evaluate(client).json()  # default body -> full_review
    resp = client.get(f"/v1/review-queue/{created['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == created["id"]
    assert body["reviewer_context"] is None


def test_review_queue_item_not_found_is_404(client):
    resp = client.get("/v1/review-queue/does-not-exist")
    assert resp.status_code == 404


def test_review_queue_item_reviewer_context_only_this_reviewers_decisions(client):
    # client fixture's _FakeEmbeddingProvider returns the SAME fixed
    # vector for every action, so every action is "similar" to every
    # other by construction here - isolates the test to reviewer
    # SCOPING (D3's "only THIS reviewer's decisions"), not similarity math
    # (already covered by tests/test_reviewer_context.py).
    a = _evaluate(client, resource="a").json()
    client.post(f"/v1/review-queue/{a['id']}/decision", json={"decision": "approve", "reviewer_id": "rev1"})

    b = _evaluate(client, resource="b").json()
    client.post(f"/v1/review-queue/{b['id']}/decision", json={"decision": "reject", "reviewer_id": "rev2"})

    target = _evaluate(client, resource="target").json()

    resp = client.get(f"/v1/review-queue/{target['id']}?reviewer_id=rev1")
    assert resp.status_code == 200
    ctx = resp.json()["reviewer_context"]
    assert ctx["similar_actions_decided_by_this_reviewer"]["count"] == 1
    assert ctx["similar_actions_decided_by_this_reviewer"]["approved"] == 1
    assert ctx["similar_actions_decided_by_this_reviewer"]["rejected"] == 0
    assert ctx["consistency_note"] == "You approved 1 of 1 similar action."
    assert ctx["this_reviewer_stats"]["decisions_total"] == 1


def test_review_queue_item_empty_reviewer_history_returns_nulls_not_zeros(client):
    target = _evaluate(client, resource="target-2").json()
    resp = client.get(f"/v1/review-queue/{target['id']}?reviewer_id=never-decided-anything")
    assert resp.status_code == 200
    ctx = resp.json()["reviewer_context"]
    assert ctx["similar_actions_decided_by_this_reviewer"] is None
    assert ctx["consistency_note"] is None
    assert ctx["this_reviewer_stats"]["decisions_total"] == 0
    assert ctx["this_reviewer_stats"]["approval_rate"] is None


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
