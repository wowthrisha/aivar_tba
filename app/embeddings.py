"""Feature B (novelty add-on) - precedent retrieval + novelty escalation.

Deliberately implemented as a step main.py's evaluate() handler applies
AFTER app/risk/router.route_action() returns, not inside app/risk/*.py.
That keeps every file under app/risk/ at zero diff, so T-08's frozen
criterion tests (tests/test_routing.py) are provably unaffected by
anything in this module, regardless of what it does - no signature
change to route_action()/evaluate_floors() was needed or made.

"NEW FLOOR - I am approving this addition explicitly" (product owner,
PS-9-1-Novelty-Addons-v1.0.md, Feature B prompt) - conceptually a floor
(override that can only escalate), architecturally a separate layer.
"""

import json
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from openai import OpenAIError
from pydantic import BaseModel

from app.risk.tiers import Tier

EMBEDDING_MODEL = "text-embedding-3-small"
# FROZEN by Feature B's own spec - the novelty-floor thresholds.
NOVELTY_SIMILARITY_THRESHOLD = 0.75
NOVELTY_MIN_PRIOR_ACTIONS = 20
PRECEDENT_K = 3
PRECEDENT_WINDOW = 200


def canonical_action_string(action_type: str, resource: str, params: dict[str, Any]) -> str:
    sorted_params = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return f"{action_type}|{resource}|{sorted_params}"


def cosine_similarity(a: list[float], b: list[float]) -> float:
    import numpy as np

    va, vb = np.array(a, dtype=float), np.array(b, dtype=float)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom == 0.0:
        return 0.0
    return float(np.dot(va, vb) / denom)


class EmbeddingProvider(ABC):
    @abstractmethod
    async def embed(self, text: str) -> list[float] | None:
        """Fail-soft: returns None on any failure, never raises - an
        embedding-provider outage must not fail the evaluate request."""
        ...


class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(self, client, model: str = EMBEDDING_MODEL) -> None:
        self._client = client
        self._model = model

    async def embed(self, text: str) -> list[float] | None:
        try:
            resp = await self._client.embeddings.create(model=self._model, input=text)
            return resp.data[0].embedding
        except OpenAIError:
            return None


@dataclass(frozen=True)
class Candidate:
    action_id: str
    embedding: list[float]
    outcome: str  # a terminal ActionState.value: "executed" | "rejected" | "expired"


class PrecedentMatch(BaseModel):
    action_id: str
    similarity: float
    outcome: str


class PrecedentInfo(BaseModel):
    k: int
    matches: list[PrecedentMatch]
    summary: str


def retrieve_precedent(query_embedding: list[float], candidates: list[Candidate]) -> PrecedentInfo:
    scored = sorted(
        ((cosine_similarity(query_embedding, c.embedding), c) for c in candidates),
        key=lambda pair: pair[0],
        reverse=True,
    )
    top = scored[:PRECEDENT_K]
    matches = [
        PrecedentMatch(action_id=c.action_id, similarity=round(sim, 4), outcome=c.outcome) for sim, c in top
    ]
    if matches:
        rejected_count = sum(1 for m in matches if m.outcome == "rejected")
        plural = "s" if len(matches) != 1 else ""
        summary = f"Similar to {len(matches)} prior action{plural}; {rejected_count} were rejected."
    else:
        summary = "No prior actions with embeddings to compare against."
    return PrecedentInfo(k=PRECEDENT_K, matches=matches, summary=summary)


def max_similarity(query_embedding: list[float], candidates: list[Candidate]) -> float:
    if not candidates:
        return 0.0
    return max(cosine_similarity(query_embedding, c.embedding) for c in candidates)


def novelty_floor_should_escalate(max_sim: float, prior_count: int) -> bool:
    return max_sim < NOVELTY_SIMILARITY_THRESHOLD and prior_count >= NOVELTY_MIN_PRIOR_ACTIONS


def escalate_one_tier(tier: Tier) -> Tier:
    """Escalates exactly one tier, capped at FULL_REVIEW - never sets a
    tier absolutely, matching the spec's own invariant."""
    result = Tier(min(tier.value + 1, Tier.FULL_REVIEW.value))
    assert result >= tier  # structural invariant: novelty escalation only escalates
    return result


def novelty_reason(prior_count: int) -> str:
    return f"novel action: no close precedent in {prior_count} prior actions"
