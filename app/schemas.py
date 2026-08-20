"""Request/response models for the T-10 API."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.embeddings import PrecedentInfo
from app.risk.scorer import Regulatory, Reversibility
from app.state_machine import ActionState

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
    confidence_score: float | None = None
    floor_name: str | None = None
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
