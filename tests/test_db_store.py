"""Pre-T-14 persistence fix — tests against the REAL Neon dev DB (approved
test-DB strategy: no separate test DB/rollback infra). Every test creates
rows tagged with a unique per-test id and deletes them in teardown.

Run with the real DATABASE_URL/DATABASE_URL_DIRECT in the environment.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from app.db import make_app_engine
from app.db_models import ActionORM, ApprovalORM, AuditRecordORM, RiskAssessmentORM
from app.db_store import SQLAlchemyAuditLog, SQLAlchemyStore
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
