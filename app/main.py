import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncEngine

from app.audit import AuditLog
from app.db import make_app_engine
from app.db_store import SQLAlchemyAuditLog, SQLAlchemyStore
from app.embeddings import (
    EmbeddingProvider,
    OpenAIEmbeddingProvider,
    PRECEDENT_WINDOW,
    canonical_action_string,
    escalate_one_tier,
    max_similarity,
    novelty_floor_should_escalate,
    novelty_reason,
    retrieve_precedent,
)
from app.llm import ConfidenceProvider, OpenAIConfidenceProvider
from app.logging_config import configure_logging, request_id_var
from app.oversight import DecisionEvent, OversightResponse, compute_reviewer_metrics
from app.risk.confidence import structural_completeness, two_signal_confidence
from app.risk.router import route_action
from app.risk.scorer import score_action
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
from app.store import ApprovalRecord, InMemoryStore

configure_logging()
logger = logging.getLogger("app")

app = FastAPI()


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    # Stored on request.state (not just the contextvar) because if
    # call_next raises, the `finally` below resets the contextvar BEFORE
    # the exception reaches unhandled_exception_handler - request.state
    # survives that unwind, so the error path can still recover it.
    request.state.request_id = str(uuid.uuid4())
    token = request_id_var.set(request.state.request_id)
    try:
        response = await call_next(request)
        logger.info(f"{request.method} {request.url.path} -> {response.status_code}")
        return response
    finally:
        request_id_var.reset(token)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # FAIL CLEAN: Starlette's default handler for a genuinely unhandled
    # exception returns a plain-text body, not JSON. This is the only
    # error path in the app that isn't already an HTTPException (which
    # FastAPI already renders as clean JSON).
    request_id_var.set(getattr(request.state, "request_id", None))
    logger.error(f"unhandled exception on {request.method} {request.url.path}", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "internal server error"})

# FROZEN (T-12/S-3): CONFIRM 30 min, FULL_REVIEW 4 hours.
CONFIRM_TTL = timedelta(minutes=30)
FULL_REVIEW_TTL = timedelta(hours=4)

_real_provider: ConfidenceProvider | None = None
_app_engine: AsyncEngine | None = None
_real_store: SQLAlchemyStore | None = None
_real_audit: SQLAlchemyAuditLog | None = None


def get_confidence_provider() -> ConfidenceProvider:
    global _real_provider
    if _real_provider is None:
        client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
        _real_provider = OpenAIConfidenceProvider(client, model=os.environ["OPENAI_MODEL"])
    return _real_provider


_real_embedding_provider: EmbeddingProvider | None = None


def get_embedding_provider() -> EmbeddingProvider:
    # Feature B: EMBEDDING_MODEL is the spec's own pinned model
    # (text-embedding-3-small), not OPENAI_MODEL - a separate, unrelated
    # model string.
    global _real_embedding_provider
    if _real_embedding_provider is None:
        client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
        _real_embedding_provider = OpenAIEmbeddingProvider(client)
    return _real_embedding_provider


def get_app_engine() -> AsyncEngine:
    global _app_engine
    if _app_engine is None:
        _app_engine = make_app_engine()
    return _app_engine


def get_store(engine: AsyncEngine = Depends(get_app_engine)) -> InMemoryStore | SQLAlchemyStore:
    global _real_store
    if _real_store is None:
        _real_store = SQLAlchemyStore(engine)
    return _real_store


def get_audit_log(engine: AsyncEngine = Depends(get_app_engine)) -> AuditLog | SQLAlchemyAuditLog:
    global _real_audit
    if _real_audit is None:
        _real_audit = SQLAlchemyAuditLog(engine)
    return _real_audit


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


def _to_action_response(record, precedent=None) -> ActionResponse:
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
        precedent=precedent,
    )


@app.post("/v1/actions/evaluate", response_model=ActionResponse, status_code=201)
async def evaluate(
    body: EvaluateRequest,
    provider: ConfidenceProvider = Depends(get_confidence_provider),
    embedding_provider: EmbeddingProvider = Depends(get_embedding_provider),
    store: InMemoryStore | SQLAlchemyStore = Depends(get_store),
    audit: AuditLog | SQLAlchemyAuditLog = Depends(get_audit_log),
):
    action_id = str(uuid.uuid4())
    record, created = await store.get_or_create_action(
        id=action_id,
        agent_id=body.agent_id,
        action_type=body.action_type,
        resource=body.resource,
        params=body.params,
        idempotency_key=body.idempotency_key,
    )
    if not created:
        # S-2: idempotency replay - return the original action, no new
        # scoring, no new audit entry, no second action created.
        return _to_action_response(record)

    llm_result = await provider.get_confidence(body.action_type, body.resource, body.params)
    structural = structural_completeness(body.action_type, body.params)
    combined_confidence = two_signal_confidence(llm_result.confidence, structural)

    weighted = score_action(body.reversibility, body.affected_records, body.regulatory, combined_confidence)
    routing = route_action(body.reversibility, body.affected_records, body.regulatory, combined_confidence)

    # Feature B (novelty add-on): a separate step AFTER route_action(),
    # not a change to app/risk/*.py - see app/embeddings.py's module
    # docstring. Fail-soft: embedding failure skips precedent/novelty
    # entirely rather than failing the request.
    final_tier = routing.tier
    final_explanation = routing.explanation
    final_floor_name = routing.floor_name
    precedent_info = None
    embedding = await embedding_provider.embed(
        canonical_action_string(body.action_type, body.resource, body.params)
    )
    if embedding is None:
        logger.info(f"embedding_degraded=true for evaluate {action_id}")
    else:
        candidates = await store.list_recent_embedded_terminal_actions(
            exclude_action_id=action_id, limit=PRECEDENT_WINDOW
        )
        precedent_info = retrieve_precedent(embedding, candidates)
        prior_count = len(candidates)
        if novelty_floor_should_escalate(max_similarity(embedding, candidates), prior_count):
            escalated = escalate_one_tier(routing.tier)
            if escalated != routing.tier:
                final_tier = escalated
                final_floor_name = "novelty_unprecedented"
                final_explanation = f"{routing.explanation} Escalated to {escalated.name} ({novelty_reason(prior_count)})."

    target = {
        "AUTONOMOUS": ActionState.AUTONOMOUS,
        "CONFIRM": ActionState.CONFIRM,
        "FULL_REVIEW": ActionState.FULL_REVIEW,
    }[final_tier.name]
    transition(ActionState.EVALUATED, target)  # validates the edge exists (PROPOSED->EVALUATED->target)

    record = await store.save_risk_assessment(
        action_id=action_id,
        reversibility_score=weighted.reversibility,
        data_scope_score=weighted.data_scope,
        regulatory_score=weighted.regulatory,
        confidence_score=weighted.confidence,
        weights_version=weighted.weights_version,
        composite=routing.composite,
        floor_fired=final_floor_name,
        tier=final_tier.name,
        llm_model=os.environ.get("OPENAI_MODEL"),
        llm_latency_ms=llm_result.latency_ms,
        degraded=llm_result.degraded,
        rendered_explanation=final_explanation,
        new_state=target,
    )

    if embedding is not None:
        await store.set_embedding(action_id, embedding)

    await audit.append(
        event_type="evaluated",
        actor=body.agent_id,
        payload={
            "action_id": action_id,
            "tier": final_tier.name,
            "composite": routing.composite,
            "floor_name": final_floor_name,
            "explanation": final_explanation,
            "llm_degraded": llm_result.degraded,
            "embedding_degraded": embedding is None,
        },
    )

    return _to_action_response(record, precedent=precedent_info)


@app.get("/v1/actions/{action_id}", response_model=ActionResponse)
async def get_action(action_id: str, store: InMemoryStore | SQLAlchemyStore = Depends(get_store)):
    record = await store.get_action(action_id)
    if record is None:
        raise HTTPException(status_code=404, detail="action not found")
    return _to_action_response(record)


async def _check_expiry(store, action_id: str, record) -> None:
    if record.state != ActionState.APPROVED:
        return
    approval = await store.get_approval(action_id)
    if (
        approval is not None
        and approval.expires_at is not None
        and approval.expires_at < datetime.now(timezone.utc)
    ):
        # Use the same conditional-update path as every other transition -
        # mutating `record.state` locally does not persist for a DB-backed
        # store, whose get_action() reconstructs a fresh record each call.
        if await store.conditional_transition(action_id, ActionState.APPROVED, ActionState.EXPIRED):
            record.state = ActionState.EXPIRED


@app.post("/v1/actions/{action_id}/confirm", response_model=ActionResponse)
async def confirm(
    action_id: str,
    body: ConfirmRequest,
    store: InMemoryStore | SQLAlchemyStore = Depends(get_store),
    audit: AuditLog | SQLAlchemyAuditLog = Depends(get_audit_log),
):
    record = await store.get_action(action_id)
    if record is None:
        raise HTTPException(status_code=404, detail="action not found")
    if body.params_hash != record.params_hash:
        raise HTTPException(status_code=409, detail="params_hash mismatch")

    transition(ActionState.CONFIRM, ActionState.APPROVED)  # validates the edge exists
    if not await store.conditional_transition(action_id, ActionState.CONFIRM, ActionState.APPROVED):
        raise HTTPException(status_code=409, detail=f"action is in state {record.state.value}, not confirm")
    record.state = ActionState.APPROVED

    now = datetime.now(timezone.utc)
    await store.set_approval(
        ApprovalRecord(
            action_id=action_id,
            decision="approve",
            reviewer_id=None,
            approved_params_hash=body.params_hash,
            decided_at=now,
            expires_at=now + CONFIRM_TTL,
        )
    )

    await audit.append(
        event_type="confirmed",
        actor=record.agent_id,
        payload={"action_id": action_id, "params_hash": body.params_hash},
    )
    return _to_action_response(record)


@app.get("/v1/review-queue", response_model=list[ActionResponse])
async def review_queue(store: InMemoryStore | SQLAlchemyStore = Depends(get_store)):
    return [_to_action_response(r) for r in await store.list_review_queue()]


@app.post("/v1/review-queue/{action_id}/decision", response_model=ActionResponse)
async def decision(
    action_id: str,
    body: DecisionRequest,
    store: InMemoryStore | SQLAlchemyStore = Depends(get_store),
    audit: AuditLog | SQLAlchemyAuditLog = Depends(get_audit_log),
):
    record = await store.get_action(action_id)
    if record is None:
        raise HTTPException(status_code=404, detail="action not found")
    if body.reviewer_id == record.agent_id:
        # S-6 (separation of duties): an agent cannot approve its own action.
        raise HTTPException(status_code=403, detail="reviewer_id must differ from the proposing agent_id")

    target = ActionState.APPROVED if body.decision == "approve" else ActionState.REJECTED
    transition(ActionState.FULL_REVIEW, target)  # validates the edge exists

    # Race (T-12): a CONDITIONAL UPDATE, not read-then-write. Two concurrent
    # decisions on the same item: exactly one flips FULL_REVIEW -> target;
    # the other's compare-and-set sees a state that no longer matches and
    # gets zero rows affected -> 409, never a lost update. For the
    # DB-backed store this is a real `UPDATE ... WHERE state = :expected`,
    # safe across processes - not an in-process lock.
    if not await store.conditional_transition(action_id, ActionState.FULL_REVIEW, target):
        raise HTTPException(
            status_code=409, detail=f"action is in state {record.state.value}, not full_review"
        )
    record.state = target

    if body.decision == "approve":
        now = datetime.now(timezone.utc)
        await store.set_approval(
            ApprovalRecord(
                action_id=action_id,
                decision="approve",
                reviewer_id=body.reviewer_id,
                approved_params_hash=record.params_hash,
                decided_at=now,
                expires_at=now + FULL_REVIEW_TTL,
            )
        )

    await audit.append(
        event_type="decision",
        actor=body.reviewer_id,
        payload={"action_id": action_id, "decision": body.decision},
    )
    return _to_action_response(record)


@app.post("/v1/actions/{action_id}/execute", response_model=ActionResponse)
async def execute(
    action_id: str,
    body: ExecuteRequest,
    store: InMemoryStore | SQLAlchemyStore = Depends(get_store),
    audit: AuditLog | SQLAlchemyAuditLog = Depends(get_audit_log),
):
    record = await store.get_action(action_id)
    if record is None:
        raise HTTPException(status_code=404, detail="action not found")

    if body.idempotency_key is not None and await store.was_executed_with_key(action_id, body.idempotency_key):
        # S-2: replay of an already-completed execute - return the
        # original (terminal, unchanged) result. Not re-executed.
        return _to_action_response(record)

    await _check_expiry(store, action_id, record)

    if record.state == ActionState.AUTONOMOUS:
        expected_state = ActionState.AUTONOMOUS
    elif record.state == ActionState.APPROVED:
        approval = await store.get_approval(action_id)
        if approval is None or approval.expires_at is None or approval.expires_at < datetime.now(timezone.utc):
            raise HTTPException(status_code=409, detail="approval missing or expired")
        expected_state = ActionState.APPROVED
    else:
        raise HTTPException(
            status_code=409, detail=f"action is in state {record.state.value}, not executable"
        )

    if body.params_hash != record.params_hash:
        raise HTTPException(status_code=409, detail="params_hash mismatch")

    transition(expected_state, ActionState.EXECUTED)  # validates the edge exists
    if not await store.conditional_transition(action_id, expected_state, ActionState.EXECUTED):
        raise HTTPException(
            status_code=409, detail=f"action is in state {record.state.value}, not executable"
        )
    record.state = ActionState.EXECUTED

    audit_payload = {"action_id": action_id, "params_hash": body.params_hash}
    if body.idempotency_key is not None:
        audit_payload["idempotency_key"] = body.idempotency_key
        await store.remember_executed_key(action_id, body.idempotency_key)

    await audit.append(event_type="executed", actor=record.agent_id, payload=audit_payload)
    return _to_action_response(record)


@app.get("/v1/audit", response_model=list[AuditRecordResponse])
async def audit_list(
    action_id: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
    audit: AuditLog | SQLAlchemyAuditLog = Depends(get_audit_log),
):
    records = await audit.list_records(action_id=action_id, event_type=event_type, limit=limit, offset=offset)
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
async def audit_verify(audit: AuditLog | SQLAlchemyAuditLog = Depends(get_audit_log)):
    result = await audit.verify()
    return AuditVerifyResponse(
        valid=result.valid, records_checked=result.records_checked, first_invalid_id=result.first_invalid_id
    )


@app.get("/v1/oversight/reviewers", response_model=OversightResponse)
async def oversight_reviewers(
    store: InMemoryStore | SQLAlchemyStore = Depends(get_store),
    audit: AuditLog | SQLAlchemyAuditLog = Depends(get_audit_log),
):
    # No default limit=50 trap here (T-19's lesson) - every "decision"
    # event is needed for a correct aggregation.
    decision_records = await audit.list_records(event_type="decision", limit=1_000_000)

    by_reviewer: dict[str, list[DecisionEvent]] = {}
    action_cache: dict[str, object] = {}
    for rec in decision_records:
        action_id = rec.payload.get("action_id")
        decision = rec.payload.get("decision")
        if action_id is None or decision is None:
            continue
        if action_id not in action_cache:
            action = await store.get_action(action_id)
            if action is None:
                continue
            action_cache[action_id] = action
        action = action_cache[action_id]
        by_reviewer.setdefault(rec.actor, []).append(
            DecisionEvent(
                action_id=action_id,
                decision=decision,
                decided_at=rec.created_at,
                proposed_at=action.created_at,
                action_current_state=action.state.value,
            )
        )

    reviewers = {reviewer_id: compute_reviewer_metrics(events) for reviewer_id, events in by_reviewer.items()}

    queue = await store.list_review_queue()
    oldest_pending_age_seconds = (
        (datetime.now(timezone.utc) - queue[0].created_at).total_seconds() if queue else None
    )

    return OversightResponse(
        reviewers=reviewers,
        review_queue_depth=len(queue),
        oldest_pending_age_seconds=oldest_pending_age_seconds,
    )
