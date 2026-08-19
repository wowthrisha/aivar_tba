import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Response
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncEngine

from app.audit import AuditLog
from app.db import make_app_engine
from app.llm import ConfidenceProvider, OpenAIConfidenceProvider
from app.risk.confidence import structural_completeness, two_signal_confidence
from app.risk.router import route_action
from app.schemas import (
    ActionResponse,
    AuditRecordResponse,
    AuditVerifyResponse,
    ConfirmRequest,
    DecisionRequest,
    EvaluateRequest,
    ExecuteRequest,
)
from app.state_machine import ActionState, transition
from app.store import ApprovalRecord, InMemoryStore, canonical_params_hash

app = FastAPI()

_store = InMemoryStore()
_audit = AuditLog()

# FROZEN (T-12/S-3): CONFIRM 30 min, FULL_REVIEW 4 hours.
CONFIRM_TTL = timedelta(minutes=30)
FULL_REVIEW_TTL = timedelta(hours=4)

_real_provider: ConfidenceProvider | None = None
_app_engine: AsyncEngine | None = None


def get_confidence_provider() -> ConfidenceProvider:
    global _real_provider
    if _real_provider is None:
        client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
        _real_provider = OpenAIConfidenceProvider(client, model=os.environ["OPENAI_MODEL"])
    return _real_provider


def get_app_engine() -> AsyncEngine:
    global _app_engine
    if _app_engine is None:
        _app_engine = make_app_engine()
    return _app_engine


async def _check_db(engine: AsyncEngine) -> bool:
    try:
        async with engine.connect() as conn:
            await conn.exec_driver_sql("SELECT 1")
        return True
    except Exception:
        return False


@app.get("/livez")
def livez():
    return {"status": "ok"}


@app.get("/readyz")
async def readyz(
    response: Response,
    provider: ConfidenceProvider = Depends(get_confidence_provider),
    engine: AsyncEngine = Depends(get_app_engine),
):
    llm_ok = await provider.health_check()
    db_ok = await _check_db(engine)
    checks = {
        "llm": "ok" if llm_ok else "unreachable",
        "db": "ok" if db_ok else "unreachable",
    }
    response.status_code = 200 if (llm_ok and db_ok) else 503
    return {"status": "ok" if (llm_ok and db_ok) else "degraded", "checks": checks}


def _to_action_response(record) -> ActionResponse:
    return ActionResponse(
        id=record.id,
        agent_id=record.agent_id,
        action_type=record.action_type,
        resource=record.resource,
        params=record.params,
        params_hash=record.params_hash,
        state=record.state,
        composite=record.composite,
        tier=record.tier,
        explanation=record.explanation,
        created_at=record.created_at,
    )


@app.post("/v1/actions/evaluate", response_model=ActionResponse, status_code=201)
async def evaluate(
    body: EvaluateRequest, provider: ConfidenceProvider = Depends(get_confidence_provider)
):
    action_id = str(uuid.uuid4())
    record = _store.create_action(
        id=action_id,
        agent_id=body.agent_id,
        action_type=body.action_type,
        resource=body.resource,
        params=body.params,
        idempotency_key=body.idempotency_key,
    )

    llm_result = await provider.get_confidence(body.action_type, body.resource, body.params)
    structural = structural_completeness(body.action_type, body.params)
    combined_confidence = two_signal_confidence(llm_result.confidence, structural)

    routing = route_action(body.reversibility, body.affected_records, body.regulatory, combined_confidence)

    transition(record.state, ActionState.EVALUATED)
    record.state = ActionState.EVALUATED
    target = {
        "AUTONOMOUS": ActionState.AUTONOMOUS,
        "CONFIRM": ActionState.CONFIRM,
        "FULL_REVIEW": ActionState.FULL_REVIEW,
    }[routing.tier.name]
    transition(record.state, target)
    record.state = target
    record.composite = routing.composite
    record.tier = routing.tier.name
    record.explanation = routing.explanation
    record.floor_name = routing.floor_name

    _audit.append(
        event_type="evaluated",
        actor=body.agent_id,
        payload={
            "action_id": action_id,
            "tier": routing.tier.name,
            "composite": routing.composite,
            "floor_name": routing.floor_name,
            "explanation": routing.explanation,
            "llm_degraded": llm_result.degraded,
        },
    )

    return _to_action_response(record)


@app.get("/v1/actions/{action_id}", response_model=ActionResponse)
def get_action(action_id: str):
    record = _store.get_action(action_id)
    if record is None:
        raise HTTPException(status_code=404, detail="action not found")
    return _to_action_response(record)


def _check_expiry(action_id: str, record) -> None:
    approval = _store.get_approval(action_id)
    if (
        record.state == ActionState.APPROVED
        and approval is not None
        and approval.expires_at is not None
        and approval.expires_at < datetime.now(timezone.utc)
    ):
        transition(record.state, ActionState.EXPIRED)
        record.state = ActionState.EXPIRED


@app.post("/v1/actions/{action_id}/confirm", response_model=ActionResponse)
def confirm(action_id: str, body: ConfirmRequest):
    record = _store.get_action(action_id)
    if record is None:
        raise HTTPException(status_code=404, detail="action not found")
    if record.state != ActionState.CONFIRM:
        raise HTTPException(status_code=409, detail=f"action is in state {record.state.value}, not confirm")
    if body.params_hash != record.params_hash:
        raise HTTPException(status_code=409, detail="params_hash mismatch")

    now = datetime.now(timezone.utc)
    _store.set_approval(
        ApprovalRecord(
            action_id=action_id,
            decision="approve",
            reviewer_id=None,
            decided_at=now,
            expires_at=now + CONFIRM_TTL,
        )
    )
    transition(record.state, ActionState.APPROVED)
    record.state = ActionState.APPROVED

    _audit.append(
        event_type="confirmed",
        actor=record.agent_id,
        payload={"action_id": action_id, "params_hash": body.params_hash},
    )
    return _to_action_response(record)


@app.get("/v1/review-queue", response_model=list[ActionResponse])
def review_queue():
    return [_to_action_response(r) for r in _store.list_review_queue()]


@app.post("/v1/review-queue/{action_id}/decision", response_model=ActionResponse)
def decision(action_id: str, body: DecisionRequest):
    record = _store.get_action(action_id)
    if record is None:
        raise HTTPException(status_code=404, detail="action not found")
    if record.state != ActionState.FULL_REVIEW:
        raise HTTPException(
            status_code=409, detail=f"action is in state {record.state.value}, not full_review"
        )
    if body.reviewer_id == record.agent_id:
        # S-6 (separation of duties): an agent cannot approve its own action.
        raise HTTPException(status_code=403, detail="reviewer_id must differ from the proposing agent_id")

    now = datetime.now(timezone.utc)
    if body.decision == "approve":
        _store.set_approval(
            ApprovalRecord(
                action_id=action_id,
                decision="approve",
                reviewer_id=body.reviewer_id,
                decided_at=now,
                expires_at=now + FULL_REVIEW_TTL,
            )
        )
        transition(record.state, ActionState.APPROVED)
        record.state = ActionState.APPROVED
    else:
        transition(record.state, ActionState.REJECTED)
        record.state = ActionState.REJECTED

    _audit.append(
        event_type="decision",
        actor=body.reviewer_id,
        payload={"action_id": action_id, "decision": body.decision},
    )
    return _to_action_response(record)


@app.post("/v1/actions/{action_id}/execute", response_model=ActionResponse)
def execute(action_id: str, body: ExecuteRequest):
    record = _store.get_action(action_id)
    if record is None:
        raise HTTPException(status_code=404, detail="action not found")

    _check_expiry(action_id, record)

    if record.state == ActionState.AUTONOMOUS:
        pass  # no approval needed
    elif record.state == ActionState.APPROVED:
        approval = _store.get_approval(action_id)
        if approval is None or approval.expires_at is None or approval.expires_at < datetime.now(timezone.utc):
            raise HTTPException(status_code=409, detail="approval missing or expired")
    else:
        raise HTTPException(
            status_code=409, detail=f"action is in state {record.state.value}, not executable"
        )

    if body.params_hash != record.params_hash:
        raise HTTPException(status_code=409, detail="params_hash mismatch")

    transition(record.state, ActionState.EXECUTED)
    record.state = ActionState.EXECUTED

    _audit.append(
        event_type="executed",
        actor=record.agent_id,
        payload={"action_id": action_id, "params_hash": body.params_hash},
    )
    return _to_action_response(record)


@app.get("/v1/audit", response_model=list[AuditRecordResponse])
def audit_list(action_id: str | None = None, event_type: str | None = None, limit: int = 50, offset: int = 0):
    records = _audit.list_records(action_id=action_id, event_type=event_type, limit=limit, offset=offset)
    return [
        AuditRecordResponse(
            id=r.id,
            prev_hash=r.prev_hash,
            entry_hash=r.entry_hash,
            event_type=r.event_type,
            actor=r.actor,
            payload=r.payload,
            created_at=r.created_at,
        )
        for r in records
    ]


@app.get("/v1/audit/verify", response_model=AuditVerifyResponse)
def audit_verify():
    result = _audit.verify()
    return AuditVerifyResponse(
        valid=result.valid, records_checked=result.records_checked, first_invalid_id=result.first_invalid_id
    )
