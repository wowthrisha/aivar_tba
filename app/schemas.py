"""Request/response models for the T-10 API."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, model_validator

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
    affected_records: int
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
