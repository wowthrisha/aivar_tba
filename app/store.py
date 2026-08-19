"""In-memory store for actions and approvals. T-11 replaces this with a
SQLAlchemy-backed store behind the same shape (actions, risk_assessments,
approvals map onto the four T-11 tables).
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.state_machine import ActionState


def canonical_params_hash(params: dict[str, Any]) -> str:
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class ActionRecord:
    id: str
    agent_id: str
    action_type: str
    resource: str
    params: dict[str, Any]
    params_hash: str
    idempotency_key: str | None
    created_at: datetime
    state: ActionState
    composite: float | None = None
    tier: str | None = None
    explanation: str | None = None
    floor_name: str | None = None


@dataclass
class ApprovalRecord:
    action_id: str
    decision: str  # "approve" | "reject"
    reviewer_id: str | None
    decided_at: datetime
    expires_at: datetime | None


class InMemoryStore:
    def __init__(self) -> None:
        self._actions: dict[str, ActionRecord] = {}
        self._approvals: dict[str, ApprovalRecord] = {}

    def create_action(
        self,
        id: str,
        agent_id: str,
        action_type: str,
        resource: str,
        params: dict[str, Any],
        idempotency_key: str | None,
    ) -> ActionRecord:
        record = ActionRecord(
            id=id,
            agent_id=agent_id,
            action_type=action_type,
            resource=resource,
            params=params,
            params_hash=canonical_params_hash(params),
            idempotency_key=idempotency_key,
            created_at=datetime.now(timezone.utc),
            state=ActionState.PROPOSED,
        )
        self._actions[id] = record
        return record

    def get_action(self, action_id: str) -> ActionRecord | None:
        return self._actions.get(action_id)

    def list_review_queue(self) -> list[ActionRecord]:
        pending = [a for a in self._actions.values() if a.state == ActionState.FULL_REVIEW]
        return sorted(pending, key=lambda a: a.created_at)

    def set_approval(self, approval: ApprovalRecord) -> None:
        self._approvals[approval.action_id] = approval

    def get_approval(self, action_id: str) -> ApprovalRecord | None:
        return self._approvals.get(action_id)
