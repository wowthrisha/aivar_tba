"""Request/response models for the T-10 API."""

import json
import math
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.embeddings import PrecedentInfo
from app.risk.scorer import Regulatory, Reversibility
from app.state_machine import ActionState

# Final-defect-sweep fix pass (1A): bounds applied uniformly to every
# agent-controlled string/numeric field, not just the ones that happened
# to crash. See governance/plan/03-errors-and-fixes.md for the crash
# investigation - these validators reject bad input cleanly; the actual
# 500->422 fix for NaN/Infinity is the RequestValidationError handler in
# app/main.py (a validator alone can't prevent it - Pydantic's error
# object always echoes the raw offending value, which is what crashes
# Starlette's strict JSON rendering, regardless of which validator caught
# it).
MAX_STRING_LENGTH = 10_000
MAX_PARAMS_BYTES = 64_000
MAX_PARAMS_DEPTH = 20

# C0 control characters and DEL, excluding tab/newline/CR which are
# legitimate in free-text agent input.
_FORBIDDEN_CHARS = frozenset(chr(c) for c in range(0x20) if c not in (0x09, 0x0A, 0x0D)) | {chr(0x7F)}


def _reject_control_chars(value: str, field_name: str) -> str:
    if any(ch in _FORBIDDEN_CHARS for ch in value):
        raise ValueError(f"{field_name} must not contain null bytes or control characters")
    if len(value) > MAX_STRING_LENGTH:
        raise ValueError(f"{field_name} exceeds max length of {MAX_STRING_LENGTH} characters")
    return value


def _validate_params_value(value: Any, depth: int) -> None:
    if depth > MAX_PARAMS_DEPTH:
        raise ValueError(f"params nesting exceeds max depth of {MAX_PARAMS_DEPTH}")
    if isinstance(value, str):
        _reject_control_chars(value, "params value")
    elif isinstance(value, dict):
        for key, item in value.items():
            _reject_control_chars(key, "params key")
            _validate_params_value(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _validate_params_value(item, depth + 1)

# T-13 Finding 4b: action_type <-> reversibility consistency check, for
# exactly the action types T-06's own prompt already named explicitly
# ("read 0.0 | ... | delete/send/pay 1.0"). Deliberately NOT extended to
# "update" (legitimately either with- or without-snapshot - real caller
# information, not derivable from action_type) or to affected_records/
# regulatory (no textual basis in the original spec for those).
_REQUIRED_REVERSIBILITY_BY_ACTION_TYPE: dict[str, Reversibility] = {
    "read": Reversibility.READ,
    "delete": Reversibility.IRREVERSIBLE,
    "send": Reversibility.IRREVERSIBLE,
    "pay": Reversibility.IRREVERSIBLE,
}


class EvaluateRequest(BaseModel):
    agent_id: str
    action_type: str
    resource: str
    params: dict[str, Any]
    reversibility: Reversibility
    affected_records: int = Field(ge=0)
    regulatory: Regulatory
    idempotency_key: str | None = None

    @field_validator("agent_id", "action_type", "resource")
    @classmethod
    def _validate_plain_strings(cls, v: str, info) -> str:
        return _reject_control_chars(v, info.field_name)

    @field_validator("idempotency_key")
    @classmethod
    def _validate_idempotency_key(cls, v: str | None) -> str | None:
        if v is not None:
            _reject_control_chars(v, "idempotency_key")
        return v

    @field_validator("affected_records", mode="before")
    @classmethod
    def _validate_affected_records_finite(cls, v: Any) -> Any:
        # Defense in depth: Pydantic already rejects non-finite floats for
        # an int field on its own (confirmed). The actual 500->422 fix for
        # the live NaN/Infinity crash is the RequestValidationError handler
        # in app/main.py, not this validator - see the module docstring.
        if isinstance(v, float) and not math.isfinite(v):
            raise ValueError("affected_records must be a finite number")
        return v

    @field_validator("params")
    @classmethod
    def _validate_params(cls, v: dict[str, Any]) -> dict[str, Any]:
        serialized_size = len(json.dumps(v, default=str).encode("utf-8"))
        if serialized_size > MAX_PARAMS_BYTES:
            raise ValueError(f"params exceeds max serialized size of {MAX_PARAMS_BYTES} bytes")
        _validate_params_value(v, depth=0)
        return v

    @model_validator(mode="after")
    def _reversibility_matches_action_type(self) -> "EvaluateRequest":
        required = _REQUIRED_REVERSIBILITY_BY_ACTION_TYPE.get(self.action_type)
        if required is not None and self.reversibility != required:
            raise ValueError(
                f"action_type={self.action_type!r} requires "
                f"reversibility={required.value!r}, got {self.reversibility.value!r}"
            )
        return self


class CalibrationInfo(BaseModel):
    """BONUS: shadow/enforce-mode calibration info on one evaluate response."""

    mode: str
    adjustment: float
    base_composite: float
    effective_composite: float
    sample_size: int
    clean_confirmations: int
    modifications_rejections: int
    applied: bool
    degraded: bool


class ActionResponse(BaseModel):
    id: str
    agent_id: str
    action_type: str
    resource: str
    params: dict[str, Any]
    params_hash: str
    state: ActionState
    composite: float | None
    tier: str | None
    explanation: str | None
    created_at: datetime
    # Feature B (novelty add-on): only populated by evaluate() - not
    # persisted, so every other endpoint that reuses this model leaves
    # it null.
    precedent: PrecedentInfo | None = None
    # OD-1 (CLI output redesign): additive passthrough of values already
    # computed in app/risk/ and persisted in risk_assessments - these were
    # simply never returned before. None only when no risk assessment has
    # been saved yet (should not happen post-evaluate).
    reversibility_score: float | None = None
    data_scope_score: float | None = None
    regulatory_score: float | None = None
    # L-G: confidence_score is DEPRECATED - despite its name, this has
    # always held 1 - llm_confidence (uncertainty, not confidence). Kept
    # for backward compatibility; new integrations should read
    # uncertainty_score instead, which holds the identical value under an
    # honest name. The underlying risk_assessments.confidence_score DB
    # column is NOT renamed in this pass - that migration+backfill is a
    # separate, deferred change.
    confidence_score: float | None = None
    # L-G: same value as confidence_score, honestly named.
    uncertainty_score: float | None = None
    # L-G: the actual model self-report, uninverted by the two-signal
    # combination or the confidence->uncertainty flip. Same pattern as
    # `precedent`: only populated by evaluate()'s response, not persisted
    # (no DB column holds this raw value).
    llm_confidence_raw: float | None = None
    floor_name: str | None = None
    # D-24: all floors that fired, in priority order (floor_name is the
    # highest-priority one). Same pattern as `precedent` above: only
    # populated by evaluate()'s response, not persisted - every other
    # endpoint that reuses this model leaves it null.
    floors_fired: list[str] | None = None
    # BONUS (adaptive calibration): only populated when CALIBRATION_MODE
    # is shadow/enforce - omitted entirely in "off" mode.
    calibration: CalibrationInfo | None = None


class CalibrationActionTypeStats(BaseModel):
    """One row of GET /v1/calibration."""

    action_type: str
    clean_confirmations: int
    modifications_rejections: int
    sample_size: int
    has_min_evidence: bool
    adjustment: float


class CalibrationResponse(BaseModel):
    """GET /v1/calibration - read-only."""

    mode: str
    action_types: list[CalibrationActionTypeStats]


class ConfirmRequest(BaseModel):
    params_hash: str


class DecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    reviewer_id: str


class ExecuteRequest(BaseModel):
    params_hash: str
    idempotency_key: str | None = None


class AuditRecordResponse(BaseModel):
    id: str
    prev_hash: str
    entry_hash: str
    event_type: str
    actor: str
    payload: dict[str, Any]
    created_at: datetime


class AuditVerifyResponse(BaseModel):
    valid: bool
    records_checked: int
    first_invalid_id: str | None


class KeyDependencyVersions(BaseModel):
    """GET /v1/version - actual installed versions, read at runtime via
    importlib.metadata. Never sourced from requirements.txt."""

    pydantic: str
    fastapi: str
    openai: str
    sqlalchemy: str


class VersionResponse(BaseModel):
    """GET /v1/version - closes Axis 2 (dependencies) of staleness
    verification: one GET reports the commit, build time, and dependency
    versions a running deployment actually has."""

    git_sha: str
    build_time: str
    python_version: str
    key_dependencies: KeyDependencyVersions
