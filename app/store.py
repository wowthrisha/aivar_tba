"""In-memory store for actions and approvals - the test double for
SQLAlchemyStore (app/db_store.py), which backs the real Postgres tables
at runtime. Methods are `async def` with no real awaiting inside, purely
so app/main.py's handlers can call the same interface uniformly
regardless of which backend is dependency-injected.

T-12: all mutation goes through a single lock so evaluate-idempotency and
decision transitions are genuine conditional updates, not read-then-write.
"""

import hashlib
import json
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.state_machine import ActionState


def canonical_params_hash(params: dict[str, Any]) -> str:
    # L-H: NFC-normalize before hashing so visually-identical strings that
    # arrive as different Unicode code-point sequences (e.g. a precomposed
    # accented character vs the base letter + combining accent) hash the
    # same. ensure_ascii=False is required here: with the default
    # ensure_ascii=True, json.dumps escapes non-ASCII characters into
    # \uXXXX sequences BEFORE normalization ever sees them, so NFC and NFD
    # forms would already have been flattened into two different (but now
    # all-ASCII, and therefore NFC-normalization-is-a-no-op) escape
    # sequences - normalizing after that point would silently do nothing.
    # This is the only params_hash computation site in the codebase
    # (app/db_store.py imports this function rather than redefining it),
    # so there is no separate compute-vs-compare path to fix - confirm/
    # execute compare the client-supplied hash against this stored value
    # with plain string equality, they don't recompute one.
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    canonical = unicodedata.normalize("NFC", canonical)
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
    embedding: list[float] | None = None
    # OD-1: per-dimension scores, additive passthrough (see ActionResponse).
    reversibility: float | None = None
    data_scope: float | None = None
    regulatory: float | None = None
    confidence: float | None = None
    # L-I: persisted since T-14 (risk_assessments.weights_version) but
    # never read back until now - needed so the API-JSON layer can be
    # compared against the DB-row layer in the 4-layer reconciliation test.
    weights_version: str | None = None


@dataclass
class ApprovalRecord:
    action_id: str
    decision: str  # "approve" | "reject"
    reviewer_id: str | None
    approved_params_hash: str
    decided_at: datetime
    expires_at: datetime | None


class InMemoryStore:
    def __init__(self) -> None:
        self._actions: dict[str, ActionRecord] = {}
        self._approvals: dict[str, ApprovalRecord] = {}
        self._evaluate_idempotency: dict[str, str] = {}  # key -> action_id
        self._executed_idempotency: set[tuple[str, str]] = set()  # (action_id, key)
        self._lock = threading.Lock()

    async def get_or_create_action(
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

    async def get_action(self, action_id: str) -> ActionRecord | None:
        return self._actions.get(action_id)

    async def list_review_queue(self) -> list[ActionRecord]:
        pending = [a for a in self._actions.values() if a.state == ActionState.FULL_REVIEW]
        return sorted(pending, key=lambda a: a.created_at)

    async def set_embedding(self, action_id: str, embedding: list[float]) -> None:
        record = self._actions.get(action_id)
        if record is not None:
            record.embedding = embedding

    async def list_recent_embedded_terminal_actions(
        self, exclude_action_id: str, limit: int = 200
    ) -> list["Candidate"]:
        from app.embeddings import Candidate

        terminal = (ActionState.EXECUTED, ActionState.REJECTED, ActionState.EXPIRED)
        candidates = [
            a
            for a in self._actions.values()
            if a.id != exclude_action_id and a.embedding is not None and a.state in terminal
        ]
        candidates.sort(key=lambda a: a.created_at, reverse=True)
        return [
            Candidate(action_id=a.id, embedding=a.embedding, outcome=a.state.value)
            for a in candidates[:limit]
        ]

    async def set_approval(self, approval: ApprovalRecord) -> None:
        self._approvals[approval.action_id] = approval

    async def get_approval(self, action_id: str) -> ApprovalRecord | None:
        return self._approvals.get(action_id)

    async def conditional_transition(
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

    async def was_executed_with_key(self, action_id: str, idempotency_key: str) -> bool:
        with self._lock:
            return (action_id, idempotency_key) in self._executed_idempotency

    async def remember_executed_key(self, action_id: str, idempotency_key: str) -> None:
        with self._lock:
            self._executed_idempotency.add((action_id, idempotency_key))

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
        """Atomically records the risk assessment and transitions the
        action's state (mirrors SQLAlchemyStore, which does both writes in
        one DB transaction). llm_model / llm_latency_ms / degraded are
        accepted for interface parity with the DB-backed store's
        risk_assessments table but have no field on ActionRecord to hold
        them in-memory - they're simply not persisted here, only in
        Postgres. The four per-dimension scores ARE held on ActionRecord
        (OD-1) since ActionResponse now passes them through."""
        with self._lock:
            record = self._actions[action_id]
            record.state = new_state
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
