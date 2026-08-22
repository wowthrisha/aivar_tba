"""Pre-T-14 persistence fix — tests against the REAL Neon dev DB (approved
test-DB strategy: no separate test DB/rollback infra). Every test creates
rows tagged with a unique per-test id and deletes them in teardown.

Run with the real DATABASE_URL/DATABASE_URL_DIRECT in the environment.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, event

import cli
from app.calibration import compute_calibration_by_action_type
from app.db import make_app_engine
from app.db_models import ActionORM, ApprovalORM, AuditRecordORM, RiskAssessmentORM
from app.db_store import SQLAlchemyAuditLog, SQLAlchemyStore
from app.llm import ConfidenceResult
from app.main import _to_action_response, evaluate
from app.risk.scorer import WEIGHTS, Regulatory, Reversibility
from app.schemas import EvaluateRequest
from app.state_machine import ActionState
from app.store import ApprovalRecord

pytestmark = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="requires a real DATABASE_URL (Neon dev DB) in the environment",
)


@pytest.fixture
async def engine():
    # Function-scoped (not module-scoped): pytest-asyncio's default
    # per-function event loop means a module-scoped async engine gets
    # created in one loop and used from another, raising "attached to a
    # different loop" / "Event loop is closed". Each test pays real
    # connection-setup latency against live Neon (~15-20s) as a result -
    # accepted for this approved, minimal-infra test strategy.
    eng = make_app_engine()
    yield eng
    await eng.dispose()


@pytest.fixture
async def store(engine):
    return SQLAlchemyStore(engine)


@pytest.fixture
async def audit_log(engine):
    return SQLAlchemyAuditLog(engine)


@pytest.fixture
def action_id():
    return f"test-db-store-{uuid.uuid4()}"


@pytest.fixture(autouse=True)
async def cleanup(engine, action_id):
    yield
    async with engine.begin() as conn:
        await conn.execute(delete(RiskAssessmentORM).where(RiskAssessmentORM.action_id == action_id))
        await conn.execute(delete(ApprovalORM).where(ApprovalORM.action_id == action_id))
        await conn.execute(
            delete(AuditRecordORM).where(AuditRecordORM.payload["action_id"].astext == action_id)
        )
        await conn.execute(delete(ActionORM).where(ActionORM.id == action_id))


async def _make_action(store, action_id):
    record, created = await store.get_or_create_action(
        id=action_id,
        agent_id="test-agent",
        action_type="delete",
        resource="test-resource",
        params={"resource_id": 1},
        idempotency_key=None,
    )
    assert created is True
    return record


async def test_get_or_create_action_persists_and_evaluate_idempotency_replays(store, engine, action_id):
    record = await _make_action(store, action_id)
    assert record.state == ActionState.PROPOSED

    fetched = await store.get_action(action_id)
    assert fetched is not None
    assert fetched.id == action_id
    assert fetched.params_hash == record.params_hash

    # idempotency replay: same key, different id, must return the ORIGINAL row
    key = f"idem-{uuid.uuid4()}"
    extra_id = f"{action_id}-extra"
    try:
        a, created_a = await store.get_or_create_action(
            id=extra_id, agent_id="x", action_type="read", resource="r", params={}, idempotency_key=key
        )
        b, created_b = await store.get_or_create_action(
            id=f"{action_id}-extra2", agent_id="x", action_type="read", resource="r", params={}, idempotency_key=key
        )
        assert created_a is True
        assert created_b is False
        assert a.id == b.id
    finally:
        async with engine.begin() as conn:
            await conn.execute(delete(ActionORM).where(ActionORM.id == extra_id))


async def test_conditional_transition_succeeds_once_and_fails_on_stale_state(store, action_id):
    await _make_action(store, action_id)

    ok = await store.conditional_transition(action_id, ActionState.PROPOSED, ActionState.EVALUATED)
    assert ok is True

    # retry with the SAME (now stale) expected state must fail - the state
    # already moved on, this must not silently re-apply or lose the update
    stale = await store.conditional_transition(action_id, ActionState.PROPOSED, ActionState.EVALUATED)
    assert stale is False

    fetched = await store.get_action(action_id)
    assert fetched.state == ActionState.EVALUATED


async def test_save_risk_assessment_writes_both_tables_atomically(store, action_id):
    await _make_action(store, action_id)
    await store.conditional_transition(action_id, ActionState.PROPOSED, ActionState.EVALUATED)

    record = await store.save_risk_assessment(
        action_id=action_id,
        reversibility_score=1.0,
        data_scope_score=0.6,
        regulatory_score=0.0,
        confidence_score=0.05,
        weights_version="v1",
        composite=0.585,
        floor_fired="irreversible_bulk",
        tier="FULL_REVIEW",
        llm_model="gpt-5.6-luna",
        llm_latency_ms=123,
        degraded=False,
        rendered_explanation="0.58 -> FULL_REVIEW (floor: test)",
        new_state=ActionState.FULL_REVIEW,
    )
    assert record.state == ActionState.FULL_REVIEW
    assert record.composite == 0.585
    assert record.tier == "FULL_REVIEW"

    fetched = await store.get_action(action_id)
    assert fetched.state == ActionState.FULL_REVIEW
    assert fetched.composite == 0.585
    assert fetched.explanation == "0.58 -> FULL_REVIEW (floor: test)"


async def test_approval_round_trip(store, action_id):
    await _make_action(store, action_id)
    now = datetime.now(timezone.utc)
    await store.set_approval(
        ApprovalRecord(
            action_id=action_id,
            decision="approve",
            reviewer_id="reviewer-9",
            approved_params_hash="abc123",
            decided_at=now,
            expires_at=now + timedelta(hours=4),
        )
    )
    approval = await store.get_approval(action_id)
    assert approval is not None
    assert approval.reviewer_id == "reviewer-9"
    assert approval.approved_params_hash == "abc123"


async def test_execute_idempotency_round_trip(store, audit_log, action_id):
    await _make_action(store, action_id)
    key = f"exec-{uuid.uuid4()}"

    assert await store.was_executed_with_key(action_id, key) is False

    # the "executed" audit record itself is what was_executed_with_key
    # queries - remember_executed_key is a no-op for the DB-backed store
    await audit_log.append(
        event_type="executed", actor="test-agent", payload={"action_id": action_id, "idempotency_key": key}
    )
    await store.remember_executed_key(action_id, key)

    assert await store.was_executed_with_key(action_id, key) is True
    assert await store.was_executed_with_key(action_id, "different-key") is False


async def test_audit_append_and_verify(audit_log, action_id):
    await audit_log.append("evaluated", "test-agent", {"action_id": action_id, "n": 1})
    await audit_log.append("confirmed", "test-agent", {"action_id": action_id, "n": 2})

    records = await audit_log.list_records(action_id=action_id)
    assert len(records) == 2
    assert records[0].payload["n"] == 1
    assert records[1].prev_hash == records[0].entry_hash

    result = await audit_log.verify()
    assert result.valid is True


# ---------------------------------------------------------------------------
# L-I: standing 4-layer reconciliation test (API JSON / risk_assessments row /
# audit payload / CLI render). Previously verified manually, repeatedly -
# this makes it a standing, automated, DB-backed check.
# ---------------------------------------------------------------------------


class _FakeProvider:
    async def get_confidence(self, action_type, resource, params):
        return ConfidenceResult(confidence=0.9, degraded=False, reason=None)

    async def health_check(self) -> bool:
        return True


class _FakeEmbeddingProvider:
    async def embed(self, text: str) -> list[float] | None:
        return None  # keeps novelty/precedent out - not this test's concern


async def test_all_four_layers_agree(store, audit_log, engine, capsys, monkeypatch):
    # Calls app.main.evaluate()/get_action() DIRECTLY rather than through
    # TestClient - TestClient runs the FastAPI app in its own internal
    # event loop, which deadlocks against pytest-asyncio's function-scoped
    # loop that created the store/audit_log/engine fixtures (same class of
    # cross-loop issue as D-08, confirmed here by pg_stat_activity showing
    # ZERO active queries during the hang - the app never even reached the
    # DB). Every other test in this file calls store/audit_log methods
    # directly for the same reason; this just extends that to evaluate().
    #
    # CALIBRATION_MODE forced to "off": app/calibration.py's
    # calibration_for_action_type()/_historical_outcomes() does one
    # store.get_action() round-trip PER historical confirmed/decision audit
    # record (N+1) - confirmed live at 122s for just 17 records against
    # this session's now-large audit_records table. That's a real,
    # pre-existing performance defect, but fixing app/calibration.py is out
    # of scope for this fix pass (not one of L-B/L-G/L-I/the two closeout
    # gaps) - reported separately, not silently fixed here. This test
    # doesn't need calibration behavior at all (Fix 3 is about the four
    # sub-scores/composite/tier/floor_name/weights_version agreeing across
    # layers, which "off" mode provides identically), so forcing "off"
    # avoids the slow path entirely rather than working around it.
    monkeypatch.setenv("CALIBRATION_MODE", "off")
    affected_records = 1
    body = EvaluateRequest(
        agent_id="test-agent",
        action_type="update",
        resource="test-l-i-resource",
        params={"resource_id": 1, "fields": {"x": 1}},
        reversibility=Reversibility.UPDATE_WITHOUT_SNAPSHOT,
        affected_records=affected_records,
        regulatory=Regulatory.NONE,
    )
    action_id = None
    try:
        response = await evaluate(
            body=body, provider=_FakeProvider(), embedding_provider=_FakeEmbeddingProvider(),
            store=store, audit=audit_log,
        )
        api = response.model_dump(mode="json")
        action_id = api["id"]

        db_record = await store.get_action(action_id)
        db_row = _to_action_response(db_record).model_dump(mode="json")

        audit_records = await audit_log.list_records(action_id=action_id, event_type="evaluated")
    finally:
        if action_id is not None:
            async with engine.begin() as conn:
                await conn.execute(delete(RiskAssessmentORM).where(RiskAssessmentORM.action_id == action_id))
                await conn.execute(
                    delete(AuditRecordORM).where(AuditRecordORM.payload["action_id"].astext == action_id)
                )
                await conn.execute(delete(ActionORM).where(ActionORM.id == action_id))

    assert len(audit_records) == 1
    payload = audit_records[0].payload

    # four sub-scores + composite + tier + floor_name + weights_version:
    # API JSON and risk_assessments row (via a fresh store read) must
    # agree exactly.
    for field in (
        "reversibility_score", "data_scope_score", "regulatory_score", "confidence_score",
        "composite", "tier", "floor_name", "weights_version",
    ):
        assert api[field] == db_row[field], f"API vs DB-row mismatch on {field}: {api[field]!r} != {db_row[field]!r}"

    # API JSON and audit payload must also agree on these (all present in
    # the audit payload since D-28).
    for field in ("reversibility_score", "data_scope_score", "regulatory_score", "confidence_score", "composite", "weights_version"):
        assert api[field] == payload[field], f"API vs audit mismatch on {field}: {api[field]!r} != {payload[field]!r}"
    assert api["tier"] == payload["tier"]
    assert api["floor_name"] == payload["floor_name"]
    assert api["explanation"] == payload["explanation"] == db_row["explanation"]

    # floors_fired: API JSON and audit payload agree (both computed live);
    # the DB-row layer deliberately omits it (D-24's documented scope
    # decision - not persisted, same as `precedent`) - asserted as an
    # explicit, understood exception, not silently smoothed over.
    assert api["floors_fired"] == payload["floors_fired"]
    assert db_row["floors_fired"] is None

    # composite is exactly reconstructible from the sub-scores alone
    # (D-28), independently re-confirmed here across all three layers.
    recomputed = (
        WEIGHTS["reversibility"] * api["reversibility_score"]
        + WEIGHTS["data_scope"] * api["data_scope_score"]
        + WEIGHTS["regulatory"] * api["regulatory_score"]
        + WEIGHTS["confidence"] * api["confidence_score"]
    )
    assert recomputed == api["composite"] == db_row["composite"] == payload["composite"]

    # CLI render: the real render_result() function, not a reimplementation,
    # fed the actual API JSON - proves the CLI would display this data
    # faithfully.
    capsys.readouterr()  # discard anything already captured this test
    cli.render_result(api, affected_records=affected_records)
    printed = capsys.readouterr().out
    assert api["tier"] in printed
    assert f"{api['composite']:.2f}" in printed


# ---------------------------------------------------------------------------
# D-32: calibration N+1 fix
# ---------------------------------------------------------------------------


async def test_calibration_report_issues_a_bounded_number_of_queries(store, audit_log, engine):
    # The old app/calibration.py::_historical_outcomes() issued one
    # store.get_action() round trip PER historical confirmed/decision
    # audit record - O(n) in a log that only grows (measured at 122s for
    # just 17 records against this repo's live, already 600+-row
    # audit_records table). Asserting query COUNT stays bounded regardless
    # of table size catches a regression back to that pattern without
    # asserting on timing, which is flaky (per instruction). Run directly
    # against the real, already-large shared dev DB - no seeding needed:
    # if the implementation were still O(n) this would fail with
    # dozens/hundreds of queries, not the single-digit bound asserted below.
    query_count = 0

    def _count(*args, **kwargs):
        nonlocal query_count
        query_count += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _count)
    try:
        await compute_calibration_by_action_type(store, audit_log)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _count)

    assert query_count <= 3, f"expected O(1) queries, got {query_count}"
