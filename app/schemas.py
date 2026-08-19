"""Request/response models for the T-10 API."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from app.risk.scorer import Regulatory, Reversibility
from app.state_machine import ActionState


class EvaluateRequest(BaseModel):
    agent_id: str
    action_type: str
    resource: str
    params: dict[str, Any]
    reversibility: Reversibility
    affected_records: int
    regulatory: Regulatory
    idempotency_key: str | None = None


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
