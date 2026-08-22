"""SQLAlchemy-backed store and audit log, persisting to the real Postgres
tables (app/db_models.py) via the POOLED engine (app/db.py). Same method
surface as app/store.py's InMemoryStore / app/audit.py's AuditLog - those
classes are the test doubles for these.

conditional_transition issues a real `UPDATE ... WHERE state = :expected`
and checks rowcount - a genuine DB-level conditional update, safe across
multiple processes (unlike InMemoryStore's in-process lock, which only
proves single-process correctness).

SQLAlchemyAuditLog.append() serializes concurrent writers with a Postgres
advisory transaction lock before reading "the last record" and writing
the next one - found and fixed during this work: with no auto-incrementing
sequence on audit_records (only created_at/id, an unordered UUID), two
concurrent appends racing on "last record by created_at" could both read
the same prev_hash and fork the chain. This is the same class of
correctness requirement T-12's conditional_transition already
established (a real atomic operation, not read-then-write) applied to
the audit log.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.audit import GENESIS_HASH, AuditRecord, AuditVerifyResult, _entry_hash
from app.db_models import ActionORM, ApprovalORM, AuditRecordORM, RiskAssessmentORM
from app.state_machine import ActionState
from app.store import ActionRecord, ApprovalRecord, canonical_params_hash

logger = logging.getLogger("app")

_AUDIT_LOCK_KEY = 727_002_026  # arbitrary fixed key for pg_advisory_xact_lock


class SQLAlchemyStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    @staticmethod
    def _base_record(row: ActionORM) -> ActionRecord:
        return ActionRecord(
            id=row.id,
            agent_id=row.agent_id,
            action_type=row.action_type,
            resource=row.resource,
            params=row.params,
            params_hash=row.params_hash,
            idempotency_key=row.idempotency_key,
            created_at=row.created_at,
            state=ActionState(row.state),
        )

    async def _hydrate(self, session, row: ActionORM) -> ActionRecord:
        record = self._base_record(row)
        result = await session.execute(
            select(RiskAssessmentORM)
            .where(RiskAssessmentORM.action_id == row.id)
            .order_by(RiskAssessmentORM.id.desc())
            .limit(1)
        )
        ra = result.scalar_one_or_none()
        if ra is not None:
            record.composite = ra.composite
            record.tier = ra.tier
            record.explanation = ra.rendered_explanation
            record.floor_name = ra.floor_fired
            record.reversibility = ra.reversibility_score
            record.data_scope = ra.data_scope_score
            record.regulatory = ra.regulatory_score
            record.confidence = ra.confidence_score
            record.weights_version = ra.weights_version
        return record

    async def get_or_create_action(
        self,
        id: str,
        agent_id: str,
        action_type: str,
        resource: str,
        params: dict[str, Any],
        idempotency_key: str | None,
    ) -> tuple[ActionRecord, bool]:
        async with self._sessionmaker() as session:
            if idempotency_key is not None:
                result = await session.execute(
                    select(ActionORM).where(ActionORM.idempotency_key == idempotency_key)
                )
                existing = result.scalar_one_or_none()
                if existing is not None:
                    return await self._hydrate(session, existing), False

            row = ActionORM(
                id=id,
                agent_id=agent_id,
                action_type=action_type,
                resource=resource,
                params=params,
                params_hash=canonical_params_hash(params),
                idempotency_key=idempotency_key,
                created_at=datetime.now(timezone.utc),
                state=ActionState.PROPOSED.value,
            )
            session.add(row)
            await session.commit()
            return self._base_record(row), True

    async def get_action(self, action_id: str) -> ActionRecord | None:
        async with self._sessionmaker() as session:
            row = await session.get(ActionORM, action_id)
            if row is None:
                return None
            return await self._hydrate(session, row)

    async def list_review_queue(self) -> list[ActionRecord]:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(ActionORM)
                .where(ActionORM.state == ActionState.FULL_REVIEW.value)
                .order_by(ActionORM.created_at)
            )
            rows = result.scalars().all()
            return [await self._hydrate(session, row) for row in rows]

    async def set_embedding(self, action_id: str, embedding: list[float]) -> None:
        async with self._sessionmaker() as session:
            await session.execute(update(ActionORM).where(ActionORM.id == action_id).values(embedding=embedding))
            await session.commit()

    async def list_recent_embedded_terminal_actions(self, exclude_action_id: str, limit: int = 200):
        from app.embeddings import Candidate

        terminal = (ActionState.EXECUTED.value, ActionState.REJECTED.value, ActionState.EXPIRED.value)
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(ActionORM.id, ActionORM.embedding, ActionORM.state)
                .where(
                    ActionORM.id != exclude_action_id,
                    ActionORM.embedding.is_not(None),
                    ActionORM.state.in_(terminal),
                )
                .order_by(ActionORM.created_at.desc())
                .limit(limit)
            )
            rows = result.all()
            return [Candidate(action_id=r.id, embedding=r.embedding, outcome=r.state) for r in rows]

    async def set_approval(self, approval: ApprovalRecord) -> None:
        async with self._sessionmaker() as session:
            row = ApprovalORM(
                action_id=approval.action_id,
                decision=approval.decision,
                reviewer_id=approval.reviewer_id,
                approved_params_hash=approval.approved_params_hash,
                decided_at=approval.decided_at,
                expires_at=approval.expires_at,
            )
            session.add(row)
            await session.commit()

    async def get_approval(self, action_id: str) -> ApprovalRecord | None:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(ApprovalORM)
                .where(ApprovalORM.action_id == action_id)
                .order_by(ApprovalORM.id.desc())
                .limit(1)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return ApprovalRecord(
                action_id=row.action_id,
                decision=row.decision,
                reviewer_id=row.reviewer_id,
                approved_params_hash=row.approved_params_hash,
                decided_at=row.decided_at,
                expires_at=row.expires_at,
            )

    async def conditional_transition(
        self, action_id: str, expected_state: ActionState, new_state: ActionState
    ) -> bool:
        async with self._sessionmaker() as session:
            result = await session.execute(
                update(ActionORM)
                .where(ActionORM.id == action_id, ActionORM.state == expected_state.value)
                .values(state=new_state.value)
            )
            await session.commit()
            succeeded = result.rowcount == 1
            if not succeeded:
                # Not necessarily a bug: a lost race under normal concurrent
                # operation looks identical to an invalid-transition attempt
                # from here. Logged either way so a caller's resulting 409
                # is traceable back to which action/expected-state it was.
                logger.warning(
                    f"conditional_transition_failed action_id={action_id} "
                    f"expected_state={expected_state.value} new_state={new_state.value}"
                )
            return succeeded

    async def was_executed_with_key(self, action_id: str, idempotency_key: str) -> bool:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(AuditRecordORM)
                .where(
                    AuditRecordORM.event_type == "executed",
                    AuditRecordORM.payload["action_id"].astext == action_id,
                    AuditRecordORM.payload["idempotency_key"].astext == idempotency_key,
                )
                .limit(1)
            )
            return result.scalar_one_or_none() is not None

    async def remember_executed_key(self, action_id: str, idempotency_key: str) -> None:
        # No-op: the "executed" audit record itself (appended right after
        # this call, carrying idempotency_key in its payload) is what
        # was_executed_with_key queries. Kept for interface parity with
        # InMemoryStore, which needs an explicit in-memory set instead.
        return None

    async def save_risk_assessment(
        self,
        action_id: str,
        reversibility_score: float,
        data_scope_score: float,
        regulatory_score: float,
        confidence_score: float,
        weights_version: str,
        composite: float,
        floor_fired: str | None,
        tier: str,
        llm_model: str | None,
        llm_latency_ms: int | None,
        degraded: bool,
        rendered_explanation: str,
        new_state: ActionState,
    ) -> ActionRecord:
        async with self._sessionmaker() as session:
            async with session.begin():
                session.add(
                    RiskAssessmentORM(
                        action_id=action_id,
                        reversibility_score=reversibility_score,
                        data_scope_score=data_scope_score,
                        regulatory_score=regulatory_score,
                        confidence_score=confidence_score,
                        weights_version=weights_version,
                        composite=composite,
                        floor_fired=floor_fired,
                        tier=tier,
                        llm_model=llm_model,
                        llm_latency_ms=llm_latency_ms,
                        degraded=degraded,
                        rendered_explanation=rendered_explanation,
                    )
                )
                await session.execute(
                    update(ActionORM).where(ActionORM.id == action_id).values(state=new_state.value)
                )
                action_row = await session.get(ActionORM, action_id)
            record = self._base_record(action_row)
            record.composite = composite
            record.tier = tier
            record.explanation = rendered_explanation
            record.floor_name = floor_fired
            record.reversibility = reversibility_score
            record.data_scope = data_scope_score
            record.regulatory = regulatory_score
            record.confidence = confidence_score
            record.weights_version = weights_version
            return record


class SQLAlchemyAuditLog:
    def __init__(self, engine: AsyncEngine) -> None:
        self._sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async def append(self, event_type: str, actor: str, payload: dict[str, Any]) -> AuditRecord:
        async with self._sessionmaker() as session:
            async with session.begin():
                # Serialize concurrent writers so "read the last record,
                # write the next" is genuinely atomic - see module docstring.
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _AUDIT_LOCK_KEY})
                result = await session.execute(
                    select(AuditRecordORM).order_by(AuditRecordORM.created_at.desc()).limit(1)
                )
                last = result.scalar_one_or_none()
                prev_hash = last.entry_hash if last is not None else GENESIS_HASH
                created_at = datetime.now(timezone.utc)
                entry_hash = _entry_hash(prev_hash, event_type, actor, payload, created_at)
                row = AuditRecordORM(
                    id=str(uuid.uuid4()),
                    prev_hash=prev_hash,
                    entry_hash=entry_hash,
                    event_type=event_type,
                    actor=actor,
                    payload=payload,
                    created_at=created_at,
                )
                session.add(row)
            return AuditRecord(
                id=row.id,
                prev_hash=prev_hash,
                entry_hash=entry_hash,
                event_type=event_type,
                actor=actor,
                payload=payload,
                created_at=created_at,
            )

    async def list_records(
        self,
        action_id: str | None = None,
        event_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditRecord]:
        async with self._sessionmaker() as session:
            stmt = select(AuditRecordORM).order_by(AuditRecordORM.created_at)
            if action_id is not None:
                stmt = stmt.where(AuditRecordORM.payload["action_id"].astext == action_id)
            if event_type is not None:
                stmt = stmt.where(AuditRecordORM.event_type == event_type)
            stmt = stmt.offset(offset).limit(limit)
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [
                AuditRecord(
                    id=r.id,
                    prev_hash=r.prev_hash,
                    entry_hash=r.entry_hash,
                    event_type=r.event_type,
                    actor=r.actor,
                    payload=r.payload,
                    created_at=r.created_at,
                )
                for r in rows
            ]

    async def verify(self) -> AuditVerifyResult:
        async with self._sessionmaker() as session:
            result = await session.execute(select(AuditRecordORM).order_by(AuditRecordORM.created_at))
            rows = result.scalars().all()
        prev_hash = GENESIS_HASH
        for i, row in enumerate(rows):
            if row.prev_hash != prev_hash:
                return AuditVerifyResult(valid=False, records_checked=i, first_invalid_id=row.id)
            expected = _entry_hash(row.prev_hash, row.event_type, row.actor, row.payload, row.created_at)
            if row.entry_hash != expected:
                return AuditVerifyResult(valid=False, records_checked=i, first_invalid_id=row.id)
            prev_hash = row.entry_hash
        return AuditVerifyResult(valid=True, records_checked=len(rows), first_invalid_id=None)
