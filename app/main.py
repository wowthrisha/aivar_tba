import importlib.metadata
import logging
import math
import os
import platform
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncEngine

from app.audit import AuditLog
from app.calibration import calibration_for_action_type, compute_calibration_by_action_type, get_calibration_mode
from app.db import make_app_engine
from app.db_store import SQLAlchemyAuditLog, SQLAlchemyStore
from app.embeddings import (
    EmbeddingProvider,
    OpenAIEmbeddingProvider,
    PRECEDENT_K,
    PRECEDENT_WINDOW,
    PrecedentInfo,
    canonical_action_string,
    max_similarity,
    novelty_floor_should_escalate,
    retrieve_precedent,
)
from app.llm import ConfidenceProvider, OpenAIConfidenceProvider
from app.logging_config import configure_logging, request_id_var
from app.oversight import DecisionEvent, OversightResponse, compute_reviewer_metrics
from app.risk.confidence import structural_completeness, two_signal_confidence
from app.risk.decision import compose_final_decision
from app.risk.reviewer_context import compute_reviewer_context
from app.risk.scorer import score_action
from app.risk.session_floor import evaluate_session_floor, get_session_floor_mode
from app.risk.session_read_model import compute_session_stats
from app.risk.stability import compute_stability
from app.schemas import (
    ActionResponse,
    AuditRecordResponse,
    AuditVerifyResponse,
    CalibrationActionTypeStats,
    CalibrationInfo,
    CalibrationResponse,
    ConfirmRequest,
    DecisionRequest,
    EvaluateRequest,
    ExecuteRequest,
    KeyDependencyVersions,
    PrecedentCheckRequest,
    ReviewerContextResponse,
    SessionFloorInfo,
    SessionStatsResponse,
    SimilarActionsStatsResponse,
    StabilityInfo,
    VersionResponse,
)
from app.state_machine import ActionState, transition
from app.store import ApprovalRecord, InMemoryStore

configure_logging()
logger = logging.getLogger("app")

app = FastAPI()

# No CORSMiddleware, deliberately: this API is a service-to-service
# governance endpoint (agents/CI/CLI call it directly), not consumed
# from browser JS on a different origin. Add CORSMiddleware with an
# explicit allow-list if a browser-based client ever needs it - never
# a wildcard origin on an endpoint that gates real actions.


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


def _json_safe(value):
    # Final-defect-sweep BLOCKING fix: Pydantic's ValidationError.errors()
    # always echoes the raw offending input value. When that value is a
    # non-finite float (NaN/Infinity - accepted by stdlib json.loads on
    # the way in), FastAPI's own default RequestValidationError handler
    # crashes trying to render it, because Starlette's JSONResponse
    # enforces allow_nan=False (RFC-compliant JSON). No per-field
    # validator prevents this - the crash is in the framework's own
    # error-rendering path, not in validation itself (confirmed by
    # reproducing the exact traceback locally before this fix).
    #
    # D-34 (round 2): the same "echoes the raw offending input" pathology
    # applies to strings, not just floats. Once app/schemas.py's params
    # validator started REJECTING an unpaired Unicode surrogate (correct -
    # that was the whole point of D-34), the surrogate-laden input got
    # echoed back verbatim inside exc.errors()["input"] (the WHOLE params
    # dict/value being validated, including any bad dict KEY, since the
    # field validator raises against the value as a unit, not per-key) -
    # and Starlette's JSONResponse.render() crashed trying to
    # .encode("utf-8") a string containing that surrogate. Confirmed via
    # live Railway traceback (starlette/responses.py:187, same
    # UnicodeEncodeError as the original DB-write crash, just relocated
    # to error rendering) before this fix, not assumed from the float
    # case alone. Keys are sanitized too, not just values, for the same
    # reason.
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)  # "nan" / "inf" / "-inf" - always JSON-safe, still debuggable
    if isinstance(value, str):
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            # backslashreplace keeps this ASCII/UTF-8-safe while staying
            # debuggable (shows exactly which code point was unencodable),
            # same intent as str(nan)/str(inf) above for floats.
            return value.encode("utf-8", errors="backslashreplace").decode("utf-8")
        return value
    if isinstance(value, dict):
        return {_json_safe(k) if isinstance(k, str) else k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": _json_safe(jsonable_encoder(exc.errors()))})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # FAIL CLEAN: Starlette's default handler for a genuinely unhandled
    # exception returns a plain-text body, not JSON. This is the only
    # error path in the app that isn't already an HTTPException (which
    # FastAPI already renders as clean JSON).
    request_id_var.set(getattr(request.state, "request_id", None))
    logger.error(f"unhandled exception on {request.method} {request.url.path}", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "internal server error"})

# T-12/S-3: "both configurable" - env-var overridable, read once at
# startup (module load), same defaults as before (30 min / 4 h) when the
# var is absent. Not on CLAUDE.md's FROZEN LIST (that's risk weights/
# thresholds/floors/fail-closed direction) - these are approval-window
# durations, unrelated to tier routing.
def _ttl_from_env(var_name: str, default: int, unit: str) -> timedelta:
    raw = os.environ.get(var_name)
    value = int(raw) if raw is not None else default
    return timedelta(**{unit: value})


CONFIRM_TTL = _ttl_from_env("CONFIRM_TTL_MINUTES", 30, "minutes")
FULL_REVIEW_TTL = _ttl_from_env("FULL_REVIEW_TTL_HOURS", 4, "hours")

# FEATURE B: the window evaluate() itself uses for its own shadow-mode
# session-floor check (B3). GET /v1/sessions/{agent_id} accepts its own
# ?window= query param independently (B2) - these are unrelated knobs.
SESSION_FLOOR_WINDOW_SECONDS = int(os.environ.get("SESSION_FLOOR_WINDOW_SECONDS", "300"))

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


def _dep_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _git_sha_and_source() -> tuple[str, str]:
    """hardening-v5 / Phase 3: GIT_SHA is manually set (Railway service
    variable via infra/railway/deploy-railway.sh, or a Docker build-arg
    via infra/aws/deploy-lambda.sh) - it can drift from what's actually
    running and nothing catches that automatically (D-33). Checked
    whether Railway exposes RAILWAY_GIT_COMMIT_SHA before assuming a
    fix, per instruction not to guess: `railway run env`, full runtime
    environment, re-verified fresh (not assumed from a past session) -
    no such variable exists for this service (root-Dockerfile build,
    not Nixpacks). If it's ever added, this prefers it automatically
    and reports source="derived"; until then, falls back to the manual
    GIT_SHA var and reports which manual mechanism set it - "manual"
    (Railway: any RAILWAY_* var present means this is a
    `railway variables --set` runtime value) or "build-arg" (no
    RAILWAY_* vars present means this is a Docker ARG/ENV baked in at
    build time, e.g. Lambda) - so a reader knows how much to trust it:
    a stale "manual"/"build-arg" value can silently drift from what's
    deployed; a "derived" one is asserted correct by the platform
    itself.
    """
    railway_derived = os.environ.get("RAILWAY_GIT_COMMIT_SHA")
    if railway_derived:
        return railway_derived, "derived"
    manual_sha = os.environ.get("GIT_SHA", "unknown")
    if manual_sha == "unknown":
        return manual_sha, "unknown"
    is_railway = any(k.startswith("RAILWAY_") for k in os.environ)
    return manual_sha, ("manual" if is_railway else "build-arg")


@app.get("/v1/version")
def version():
    # Read-only, no auth, no DB, no LLM, no routing-decision dependency.
    # git_sha/build_time come from env vars set at deploy/build time
    # (Dockerfile ARG/ENV, Railway service variables) - "unknown" if
    # absent, never guessed. Dependency versions are read at runtime via
    # importlib.metadata, never from requirements.txt, so this reports
    # what the RUNNING process actually has.
    #
    # git_sha_short: AWS's build script (infra/aws/deploy-lambda.sh) and
    # Railway's (infra/railway/deploy-railway.sh) can pass GIT_SHA at
    # different lengths (short vs full) - a plain string comparison of
    # git_sha between deployments then fails on identical commits. Both
    # scripts now pass the full 40-char SHA (D-33/parity fix), but this
    # derives the short form defensively from whatever GIT_SHA actually
    # holds, so parity checks always have one directly comparable field
    # regardless of what any given deploy script passed.
    git_sha, git_sha_source = _git_sha_and_source()
    return VersionResponse(
        git_sha=git_sha,
        git_sha_short=git_sha[:7] if git_sha != "unknown" else "unknown",
        git_sha_source=git_sha_source,
        build_time=os.environ.get("BUILD_TIME", "unknown"),
        python_version=platform.python_version(),
        key_dependencies=KeyDependencyVersions(
            pydantic=_dep_version("pydantic"),
            fastapi=_dep_version("fastapi"),
            openai=_dep_version("openai"),
            sqlalchemy=_dep_version("sqlalchemy"),
        ),
    )


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


def _to_action_response(
    record,
    precedent=None,
    calibration=None,
    floors_fired=None,
    llm_confidence_raw=None,
    stability=None,
    session_floor=None,
    reviewer_context=None,
) -> ActionResponse:
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
        reversibility_score=record.reversibility,
        data_scope_score=record.data_scope,
        regulatory_score=record.regulatory,
        confidence_score=record.confidence,
        uncertainty_score=record.confidence,
        llm_confidence_raw=llm_confidence_raw,
        weights_version=record.weights_version,
        floor_name=record.floor_name,
        floors_fired=floors_fired,
        calibration=calibration,
        stability=stability,
        session_floor=session_floor,
        reviewer_context=reviewer_context,
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

    # Persisted sub-scores (D-28 audit fields, risk_assessments row) -
    # deliberately a separate call from compose_final_decision()'s own
    # internal score_action() call (via route_action()), not consolidated,
    # to keep the L-B refactor a minimal, bit-identical relocation rather
    # than also opportunistically deduplicating this pre-existing
    # redundancy (route_action() already called score_action() a second
    # time before this refactor too - same redundancy, same place).
    weighted = score_action(body.reversibility, body.affected_records, body.regulatory, combined_confidence)

    # BONUS (adaptive calibration), SHADOW MODE BY DEFAULT: gather the I/O
    # (historical stats) here; app/risk/decision.py::compose_final_decision()
    # owns the pure decision logic (mode branching, threshold recomputation,
    # floor re-evaluation) - main.py hands it plain values, never applies
    # calibration itself.
    calibration_mode = get_calibration_mode()
    cal_stats = None
    cal_degraded = False
    calibration_adjustment = 0.0
    if calibration_mode != "off":
        cal_stats, cal_degraded = await calibration_for_action_type(store, audit, body.action_type)
        calibration_adjustment = 0.0 if cal_degraded else cal_stats.adjustment

    # Feature B (novelty add-on): gather the I/O (embedding + precedent)
    # here; compose_final_decision() owns the escalation decision. Fail-
    # soft: embedding failure skips precedent/novelty entirely rather than
    # failing the request.
    precedent_info = None
    novelty_should_escalate = False
    novelty_prior_count = 0
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
        novelty_prior_count = len(candidates)
        novelty_should_escalate = novelty_floor_should_escalate(
            max_similarity(embedding, candidates), novelty_prior_count
        )

    # L-B: the ONE call. app/main.py uses this result verbatim - no
    # post-hoc tier adjustment anywhere below.
    decision = compose_final_decision(
        body.reversibility,
        body.affected_records,
        body.regulatory,
        combined_confidence,
        calibration_mode=calibration_mode,
        calibration_adjustment=calibration_adjustment,
        calibration_degraded=cal_degraded,
        novelty_should_escalate=novelty_should_escalate,
        novelty_prior_count=novelty_prior_count,
    )
    final_tier = decision.tier
    final_composite = decision.composite
    final_explanation = decision.explanation
    final_floor_name = decision.floor_name
    final_floors_fired: list[str] = list(decision.floors_fired)

    # FEATURE C: deterministic, no LLM calls - re-derives the tier at
    # every 0.05 step of llm_confidence with everything else held fixed,
    # purely to characterize how close this decision was to flipping.
    # Never influences final_tier/final_composite above.
    stability_result = compute_stability(
        body.reversibility,
        body.affected_records,
        body.regulatory,
        structural,
        calibration_mode=calibration_mode,
        calibration_adjustment=calibration_adjustment,
        calibration_degraded=cal_degraded,
        novelty_should_escalate=novelty_should_escalate,
        novelty_prior_count=novelty_prior_count,
    )
    stability_info = StabilityInfo(
        stability=stability_result.stability, flips_below=stability_result.flips_below
    )

    calibration_info = None
    if calibration_mode != "off":
        calibration_info = CalibrationInfo(
            mode=calibration_mode,
            adjustment=calibration_adjustment,
            base_composite=decision.base_composite,
            effective_composite=decision.effective_composite,
            sample_size=cal_stats.total,
            clean_confirmations=cal_stats.clean_confirmations,
            modifications_rejections=cal_stats.modifications_rejections,
            applied=decision.calibration_applied,
            degraded=cal_degraded,
        )

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
        composite=final_composite,
        floor_fired=final_floor_name,
        tier=final_tier.name,
        llm_model=os.environ.get("OPENAI_MODEL"),
        llm_latency_ms=llm_result.latency_ms,
        degraded=llm_result.degraded,
        rendered_explanation=final_explanation,
        new_state=target,
        uncertainty_score=weighted.confidence,
        llm_confidence_raw=llm_result.confidence,
    )

    if embedding is not None:
        await store.set_embedding(action_id, embedding)

    # FEATURE B3: SHADOW MODE ONLY (S5) - computed and reported, never
    # applied. Fail-soft (B4): a computation error degrades to "not
    # fired" rather than failing the whole evaluate() request.
    session_floor_info = None
    session_floor_mode = get_session_floor_mode()
    if session_floor_mode != "off":
        try:
            since = datetime.now(timezone.utc) - timedelta(seconds=SESSION_FLOOR_WINDOW_SECONDS)
            session_rows = await store.list_session_actions(body.agent_id, since)
            session_stats = compute_session_stats(
                body.agent_id, SESSION_FLOOR_WINDOW_SECONDS, session_rows
            )
            session_floor_result = evaluate_session_floor(session_stats)
            session_floor_info = SessionFloorInfo(
                would_fire=session_floor_result.would_fire,
                floor=session_floor_result.floor,
                reason=session_floor_result.reason,
                applied=session_floor_result.applied,
            )
        except Exception:
            logger.error("session_floor_degraded=true - error computing session floor", exc_info=True)
            session_floor_info = SessionFloorInfo(would_fire=False, floor=None, reason=None, applied=False)

    await audit.append(
        event_type="evaluated",
        actor=body.agent_id,
        payload={
            "action_id": action_id,
            "tier": final_tier.name,
            "composite": final_composite,
            "floor_name": final_floor_name,
            "floors_fired": final_floors_fired,
            # D-28: additive - lets the hash-chained record reconstruct
            # its own composite (sum(WEIGHTS[k] * score[k]) for
            # weights_version, plus calibration_adjustment) without
            # reading the mutable risk_assessments table.
            "reversibility_score": weighted.reversibility,
            "data_scope_score": weighted.data_scope,
            "regulatory_score": weighted.regulatory,
            "confidence_score": weighted.confidence,
            "uncertainty_score": weighted.confidence,
            "weights_version": weighted.weights_version,
            "git_sha": os.environ.get("GIT_SHA", "unknown"),
            # Closeout gap fix: without these, D-28's reconstruction
            # guarantee only held under CALIBRATION_MODE=shadow (the
            # adjustment itself was never logged) - now holds under
            # enforce mode too, verified by
            # test_audit_payload_reconstructs_composite_under_enforce_mode.
            "calibration_mode": calibration_mode,
            "calibration_adjustment": calibration_adjustment,
            "base_composite": decision.base_composite,
            "effective_composite": decision.effective_composite,
            "explanation": final_explanation,
            "llm_degraded": llm_result.degraded,
            "embedding_degraded": embedding is None,
            # FEATURE C: additive - see stability_result comment above.
            "stability": stability_result.stability,
            "flips_below": stability_result.flips_below,
            # FEATURE B3: additive, shadow-mode only - never applied.
            "session_floor": (
                {
                    "would_fire": session_floor_info.would_fire,
                    "floor": session_floor_info.floor,
                    "reason": session_floor_info.reason,
                    "applied": session_floor_info.applied,
                }
                if session_floor_info is not None
                else None
            ),
        },
    )

    return _to_action_response(
        record,
        precedent=precedent_info,
        calibration=calibration_info,
        floors_fired=final_floors_fired,
        llm_confidence_raw=llm_result.confidence,
        stability=stability_info,
        session_floor=session_floor_info,
    )


@app.get("/v1/actions/pending", response_model=list[ActionResponse])
async def list_pending(agent_id: str, store: InMemoryStore | SQLAlchemyStore = Depends(get_store)):
    """FEATURE A (MCP list_pending tool): the caller's OWN pending items
    - evaluated but not yet terminal. Read-only. Registered BEFORE
    GET /v1/actions/{action_id} - Starlette matches routes in
    registration order, and "pending" would otherwise be swallowed as an
    {action_id} path value."""
    records = await store.list_pending_for_agent(agent_id)
    return [_to_action_response(r) for r in records]


@app.post("/v1/precedent/check", response_model=PrecedentInfo)
async def precedent_check(
    body: PrecedentCheckRequest,
    embedding_provider: EmbeddingProvider = Depends(get_embedding_provider),
    store: InMemoryStore | SQLAlchemyStore = Depends(get_store),
):
    """FEATURE A (MCP check_precedent tool): similar prior actions and
    their outcomes for a PROPOSED action, without proposing it - no
    action row, no audit entry. Genuinely read-only, unlike
    evaluate_action (which does propose/score, just never execute)."""
    embedding = await embedding_provider.embed(
        canonical_action_string(body.action_type, body.resource, body.params)
    )
    if embedding is None:
        return PrecedentInfo(
            k=PRECEDENT_K, matches=[], summary="Embedding unavailable - cannot compute precedent."
        )
    candidates = await store.list_recent_embedded_terminal_actions(
        exclude_action_id="", limit=PRECEDENT_WINDOW
    )
    return retrieve_precedent(embedding, candidates)


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


@app.get("/v1/review-queue/{action_id}", response_model=ActionResponse)
async def review_queue_item(
    action_id: str,
    reviewer_id: str | None = None,
    store: InMemoryStore | SQLAlchemyStore = Depends(get_store),
    audit: AuditLog | SQLAlchemyAuditLog = Depends(get_audit_log),
):
    """FEATURE D1: GET /v1/review-queue/{id}, extended with
    reviewer_context when ?reviewer_id= is given. D2: read-only
    aggregation over existing tables, no new table. D3: no reviewer_id
    -> the plain unpersonalised payload (reviewer_context stays None)."""
    record = await store.get_action(action_id)
    if record is None:
        raise HTTPException(status_code=404, detail="action not found")

    reviewer_context = None
    if reviewer_id is not None:
        decision_records = await audit.list_records(event_type="decision", limit=1_000_000)
        events = []
        for rec in decision_records:
            if rec.actor != reviewer_id:
                continue
            decided_action_id = rec.payload.get("action_id")
            rec_decision = rec.payload.get("decision")
            if decided_action_id is None or rec_decision is None:
                continue
            decided_action = await store.get_action(decided_action_id)
            if decided_action is None:
                continue
            events.append(
                DecisionEvent(
                    action_id=decided_action_id,
                    decision=rec_decision,
                    decided_at=rec.created_at,
                    proposed_at=decided_action.created_at,
                    action_current_state=decided_action.state.value,
                )
            )
        reviewer_stats = compute_reviewer_metrics(events)

        decisions_with_embeddings = await audit.reviewer_decisions_with_embeddings(store, reviewer_id)
        context = compute_reviewer_context(record.embedding, decisions_with_embeddings)
        reviewer_context = ReviewerContextResponse(
            similar_actions_decided_by_this_reviewer=(
                SimilarActionsStatsResponse(
                    count=context.similar_actions_decided_by_this_reviewer.count,
                    approved=context.similar_actions_decided_by_this_reviewer.approved,
                    rejected=context.similar_actions_decided_by_this_reviewer.rejected,
                )
                if context.similar_actions_decided_by_this_reviewer is not None
                else None
            ),
            consistency_note=context.consistency_note,
            this_reviewer_stats=reviewer_stats,
        )

    return _to_action_response(record, reviewer_context=reviewer_context)


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


@app.get("/v1/sessions/{agent_id}", response_model=SessionStatsResponse)
async def session_stats(
    agent_id: str,
    window: int = 300,
    store: InMemoryStore | SQLAlchemyStore = Depends(get_store),
):
    """FEATURE B2/B3: a READ MODEL, derived on request from existing
    tables (actions/risk_assessments) - no new table, no cache, no
    writes. session_floor is SHADOW MODE ONLY (B3/S5): would_fire/floor/
    reason describe what WOULD happen, applied is always False.

    B4: a computation error degrades this response (degraded=true, every
    numeric field a zero/null fallback) rather than failing the request.
    """
    try:
        since = datetime.now(timezone.utc) - timedelta(seconds=window)
        rows = await store.list_session_actions(agent_id, since)
        stats = compute_session_stats(agent_id, window, rows)
        session_floor_mode = get_session_floor_mode()
        floor_info = None
        if session_floor_mode != "off":
            floor_result = evaluate_session_floor(stats)
            floor_info = SessionFloorInfo(
                would_fire=floor_result.would_fire,
                floor=floor_result.floor,
                reason=floor_result.reason,
                applied=floor_result.applied,
            )
        return SessionStatsResponse(
            agent_id=stats.agent_id,
            window_seconds=stats.window_seconds,
            action_count=stats.action_count,
            cumulative_affected_records=stats.cumulative_affected_records,
            cumulative_irreversible_records=stats.cumulative_irreversible_records,
            tier_distribution=stats.tier_distribution,
            mutation_count=stats.mutation_count,
            distinct_resource_count=stats.distinct_resource_count,
            mean_pairwise_similarity=stats.mean_pairwise_similarity,
            escalation_rate=stats.escalation_rate,
            novelty_rate=stats.novelty_rate,
            session_floor=floor_info,
            degraded=False,
        )
    except Exception:
        logger.error(f"session_stats_degraded=true agent_id={agent_id}", exc_info=True)
        return SessionStatsResponse(
            agent_id=agent_id,
            window_seconds=window,
            action_count=0,
            cumulative_affected_records=0,
            cumulative_irreversible_records=0,
            tier_distribution={},
            mutation_count=0,
            distinct_resource_count=0,
            mean_pairwise_similarity=None,
            escalation_rate=None,
            novelty_rate=None,
            session_floor=None,
            degraded=True,
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


@app.get("/v1/calibration", response_model=CalibrationResponse)
async def calibration(
    store: InMemoryStore | SQLAlchemyStore = Depends(get_store),
    audit: AuditLog | SQLAlchemyAuditLog = Depends(get_audit_log),
):
    """BONUS (adaptive calibration): read-only. Same fail-soft derivation
    evaluate() uses, just surfaced directly - never writes anything."""
    mode = get_calibration_mode()
    try:
        stats_by_type = await compute_calibration_by_action_type(store, audit)
    except Exception:
        logger.error("calibration_degraded=true - error computing /v1/calibration", exc_info=True)
        stats_by_type = {}

    return CalibrationResponse(
        mode=mode,
        action_types=[
            CalibrationActionTypeStats(
                action_type=stats.action_type,
                clean_confirmations=stats.clean_confirmations,
                modifications_rejections=stats.modifications_rejections,
                sample_size=stats.total,
                has_min_evidence=stats.has_min_evidence,
                adjustment=stats.adjustment,
            )
            for stats in stats_by_type.values()
        ],
    )
