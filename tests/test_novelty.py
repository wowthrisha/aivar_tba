"""Feature B (novelty add-on) - precedent retrieval + novelty escalation.

Tests the pure functions directly (retrieve_precedent, novelty floor,
escalation), matching tests/test_floors.py's pattern - in particular
test_escalate_only_invariant_holds_with_novelty_floor extends the same
sweep style as tests/test_floors.py::test_escalate_only_sweep, but
proves MY new escalation layer never lowers a tier either, without
touching app/risk/*.py or tests/test_routing.py at all.
"""

import itertools
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.audit import AuditLog
from app.embeddings import (
    Candidate,
    escalate_one_tier,
    max_similarity,
    novelty_floor_should_escalate,
    retrieve_precedent,
)
from app.llm import ConfidenceResult
from app.main import app, get_app_engine, get_audit_log, get_confidence_provider, get_embedding_provider, get_store
from app.risk.floors import evaluate_floors, final_tier
from app.risk.scorer import Regulatory, Reversibility, score_action
from app.risk.tiers import Tier
from app.state_machine import ActionState
from app.store import ActionRecord, InMemoryStore, canonical_params_hash


def test_precedent_returns_similar_actions_with_outcomes():
    query = [1.0, 0.0, 0.0]
    candidates = [
        Candidate(action_id="a1", embedding=[1.0, 0.0, 0.0], outcome="rejected"),
        Candidate(action_id="a2", embedding=[0.9, 0.1, 0.0], outcome="executed"),
        Candidate(action_id="a3", embedding=[0.0, 1.0, 0.0], outcome="executed"),
        Candidate(action_id="a4", embedding=[0.8, 0.2, 0.0], outcome="rejected"),
    ]

    result = retrieve_precedent(query, candidates)

    assert result.k == 3
    assert len(result.matches) == 3
    # closest three by cosine similarity: a1, a2, a4 (a3 is orthogonal)
    assert {m.action_id for m in result.matches} == {"a1", "a2", "a4"}
    assert result.matches[0].action_id == "a1"
    assert result.matches[0].similarity == 1.0
    assert all(m.outcome in ("rejected", "executed") for m in result.matches)
    assert "Similar to 3 prior action" in result.summary
    assert "2 were rejected" in result.summary


def test_novelty_floor_escalates_unprecedented_action():
    # No close precedent (similarity well under 0.75) and >=20 prior
    # embedded actions -> escalates.
    candidates = [Candidate(action_id=f"a{i}", embedding=[1.0, 0.0], outcome="executed") for i in range(20)]
    query = [0.0, 1.0]  # orthogonal to every candidate -> similarity 0.0

    sim = max_similarity(query, candidates)
    assert novelty_floor_should_escalate(sim, len(candidates)) is True
    assert escalate_one_tier(Tier.AUTONOMOUS) == Tier.CONFIRM
    assert escalate_one_tier(Tier.CONFIRM) == Tier.FULL_REVIEW
    # never sets a tier absolutely - capped at FULL_REVIEW, not beyond
    assert escalate_one_tier(Tier.FULL_REVIEW) == Tier.FULL_REVIEW


def test_novelty_floor_inactive_below_20_prior_actions():
    # Same "no close precedent" similarity, but only 19 prior actions -
    # the floor must stay inactive (cold-system guard).
    candidates = [Candidate(action_id=f"a{i}", embedding=[1.0, 0.0], outcome="executed") for i in range(19)]
    query = [0.0, 1.0]

    sim = max_similarity(query, candidates)
    assert novelty_floor_should_escalate(sim, len(candidates)) is False

    # Also inactive with zero prior actions at all (cold start).
    assert novelty_floor_should_escalate(max_similarity(query, []), 0) is False


def test_escalate_only_invariant_holds_with_novelty_floor():
    """Extends test_floors.py::test_escalate_only_sweep's invariant: no
    combination of inputs - including the new novelty floor stacked on
    top of route_action()'s own result - may produce a tier lower than
    the weighted tier alone. app/risk/*.py itself is untouched; this
    proves the ADDITIONAL escalation layer preserves the same
    guarantee.
    """
    affected_records_values = (0, 1, 10, 50, 99, 100, 500, 1000, 10_000)
    confidence_values = (0.0, 0.1, 0.3, 0.49, 0.5, 0.51, 0.7, 0.9, 1.0)
    novelty_cases = (
        (0.0, 0),  # no candidates at all - floor must stay inactive
        (0.0, 19),  # below the 20-action minimum - inactive
        (0.0, 20),  # no precedent, enough history - active
        (0.9, 20),  # close precedent exists - inactive
    )

    checked = 0
    for reversibility, affected_records, regulatory, llm_confidence in itertools.product(
        Reversibility, affected_records_values, Regulatory, confidence_values
    ):
        weighted = score_action(reversibility, affected_records, regulatory, llm_confidence)
        floors = evaluate_floors(reversibility, affected_records, regulatory, llm_confidence)
        base_final = final_tier(weighted.tier, floors)

        for sim, prior_count in novelty_cases:
            tier_after_novelty = base_final
            if novelty_floor_should_escalate(sim, prior_count):
                tier_after_novelty = escalate_one_tier(base_final)

            assert tier_after_novelty >= base_final, (
                f"novelty floor lowered tier: base={base_final.name} "
                f"after={tier_after_novelty.name} sim={sim} prior_count={prior_count}"
            )
            assert tier_after_novelty >= weighted.tier
            checked += 1

    expected = (
        len(Reversibility) * len(affected_records_values) * len(Regulatory) * len(confidence_values) * len(novelty_cases)
    )
    assert checked == expected


# ---------- D-24: novelty must APPEND to floors_fired, never overwrite floor_name ----------


class _FakeConfidenceProvider:
    def __init__(self, confidence: float):
        self._confidence = confidence

    async def get_confidence(self, action_type, resource, params):
        return ConfidenceResult(confidence=self._confidence, degraded=False, reason=None)

    async def health_check(self) -> bool:
        return True


class _FakeNoveltyEmbeddingProvider:
    async def embed(self, text: str) -> list[float] | None:
        return [0.0, 1.0]  # orthogonal to every seeded candidate below -> similarity 0.0


class _UnusedEngine:
    def connect(self):
        raise AssertionError("get_app_engine should not be reached - store/audit are overridden directly")


def _seed_prior_actions_with_no_precedent(store: InMemoryStore, count: int = 20) -> None:
    now = datetime.now(timezone.utc)
    for i in range(count):
        params: dict = {}
        store._actions[f"prior-{i}"] = ActionRecord(
            id=f"prior-{i}",
            agent_id="seed-agent",
            action_type="read",
            resource=f"seed-resource-{i}",
            params=params,
            params_hash=canonical_params_hash(params),
            idempotency_key=None,
            created_at=now,
            state=ActionState.EXECUTED,
            embedding=[1.0, 0.0],  # orthogonal to the query embedding above
        )


def test_novelty_appends_rather_than_overwrites():
    """D-24: app/main.py's novelty step must APPEND "novelty_unprecedented"
    to floors_fired, never overwrite floor_name. Scenario: update_without_
    snapshot + llm_confidence 0.0, so app/risk/floors.py already fires
    BOTH unrecoverable_mutation_requires_confirm (higher priority) and
    low_confidence_on_mutation (lower priority) before novelty ever runs.
    Forcing novelty escalation on top must not erase the true floor_name -
    the pre-fix code set final_floor_name = "novelty_unprecedented"
    unconditionally, which this proves is no longer the case.
    """
    store = InMemoryStore()
    _seed_prior_actions_with_no_precedent(store)
    audit_log = AuditLog()

    app.dependency_overrides[get_confidence_provider] = lambda: _FakeConfidenceProvider(0.0)
    app.dependency_overrides[get_embedding_provider] = lambda: _FakeNoveltyEmbeddingProvider()
    app.dependency_overrides[get_app_engine] = lambda: _UnusedEngine()
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        with TestClient(app) as c:
            resp = c.post(
                "/v1/actions/evaluate",
                json={
                    "agent_id": "agent-1",
                    "action_type": "update",
                    "resource": "orders/1",
                    "params": {"resource_id": 1, "fields": {"x": 1}},
                    "reversibility": "update_without_snapshot",
                    "affected_records": 1,
                    "regulatory": "none",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 201
    body = resp.json()
    assert body["floors_fired"] == [
        "unrecoverable_mutation_requires_confirm",
        "novelty_unprecedented",
        "low_confidence_on_mutation",
    ]
    assert body["floor_name"] == "unrecoverable_mutation_requires_confirm"
    assert body["tier"] == "FULL_REVIEW"  # CONFIRM (floor) escalated one tier by novelty
