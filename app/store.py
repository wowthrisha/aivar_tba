"""In-memory store for actions and approvals. T-11 built the real Postgres
schema (see app/db_models.py) but the business endpoints still read/write
here - see progress-log/03-errors-and-fixes.md for that scope decision.

T-12: all mutation goes through a single lock so evaluate-idempotency and
decision transitions are genuine conditional updates, not read-then-write.
"""

import hashlib
import json
import threading
from dataclasses import dataclass
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
        self._evaluate_idempotency: dict[str, str] = {}  # key -> action_id
        self._executed_idempotency: set[tuple[str, str]] = set()  # (action_id, key)
        self._lock = threading.Lock()

    def get_or_create_action(
        self,
        id: str,
        agent_id: str,
        action_type: str,
        resource: str,
        params: dict[str, Any],
        idempotency_key: str | None,
    ) -> tuple[ActionRecord, bool]:
        """S-2: if idempotency_key matches a prior evaluate call, returns
        that action with created=False instead of making a new one. Whole
        check-then-create is one atomic operation, not read-then-write."""
        with self._lock:
            if idempotency_key is not None:
                existing_id = self._evaluate_idempotency.get(idempotency_key)
                if existing_id is not None:
                    return self._actions[existing_id], False
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
            if idempotency_key is not None:
                self._evaluate_idempotency[idempotency_key] = id
            return record, True

    def get_action(self, action_id: str) -> ActionRecord | None:
        return self._actions.get(action_id)

    def list_review_queue(self) -> list[ActionRecord]:
        pending = [a for a in self._actions.values() if a.state == ActionState.FULL_REVIEW]
        return sorted(pending, key=lambda a: a.created_at)

    def set_approval(self, approval: ApprovalRecord) -> None:
        self._approvals[approval.action_id] = approval

    def get_approval(self, action_id: str) -> ApprovalRecord | None:
        return self._approvals.get(action_id)

    def conditional_transition(
        self, action_id: str, expected_state: ActionState, new_state: ActionState
    ) -> bool:
        """S-race: atomic check-and-set. Returns False (no mutation) if the
        action is missing or not currently in expected_state - a genuine
        conditional UPDATE, never a read-then-write."""
        with self._lock:
            record = self._actions.get(action_id)
            if record is None or record.state != expected_state:
                return False
            record.state = new_state
            return True

    def was_executed_with_key(self, action_id: str, idempotency_key: str) -> bool:
        with self._lock:
            return (action_id, idempotency_key) in self._executed_idempotency

    def remember_executed_key(self, action_id: str, idempotency_key: str) -> None:
        with self._lock:
            self._executed_idempotency.add((action_id, idempotency_key))
