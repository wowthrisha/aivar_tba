"""FEATURE B — session risk. B4: read model aggregates correctly;
fragmented deletes trigger the floor in shadow; SHADOW NEVER CHANGES A
TIER; window boundary excludes older actions; computation failure does
not fail the request (API-level, see tests/test_api.py additions).
"""

from datetime import datetime, timedelta, timezone

from app.risk.session_floor import evaluate_session_floor
from app.risk.session_read_model import compute_session_stats
from app.store import SessionActionRow

_NOW = datetime(2026, 8, 22, 12, 0, 0, tzinfo=timezone.utc)


def _row(
    action_id="a",
    resource="r",
    created_at=_NOW,
    tier="AUTONOMOUS",
    floor_name=None,
    reversibility_score=0.0,
    data_scope_score=0.0,
    embedding=None,
):
    return SessionActionRow(
        action_id=action_id,
        resource=resource,
        created_at=created_at,
        tier=tier,
        floor_name=floor_name,
        reversibility_score=reversibility_score,
        data_scope_score=data_scope_score,
        embedding=embedding,
    )


def test_empty_window_reports_zero_counts_and_null_rates():
    stats = compute_session_stats("agent-1", 300, [])
    assert stats.action_count == 0
    assert stats.cumulative_affected_records == 0
    assert stats.cumulative_irreversible_records == 0
    assert stats.tier_distribution == {}
    assert stats.mutation_count == 0
    assert stats.distinct_resource_count == 0
    assert stats.mean_pairwise_similarity is None
    assert stats.escalation_rate is None
    assert stats.novelty_rate is None


def test_aggregates_correctly_across_mixed_actions():
    rows = [
        _row(action_id="1", resource="users/1", tier="FULL_REVIEW", reversibility_score=1.0, data_scope_score=0.6),  # 100+ band
        _row(action_id="2", resource="users/2", tier="CONFIRM", reversibility_score=0.7, data_scope_score=0.2),
        _row(action_id="3", resource="users/1", tier="AUTONOMOUS", reversibility_score=0.0, data_scope_score=0.0),
        _row(action_id="4", resource="users/3", tier="FULL_REVIEW", floor_name="novelty_unprecedented", reversibility_score=1.0, data_scope_score=0.8),  # 1000+ band
    ]
    stats = compute_session_stats("agent-1", 300, rows)

    assert stats.action_count == 4
    # irreversible-band rows: 100 (row 1) + 1000 (row 4) = 1100
    assert stats.cumulative_irreversible_records == 1100
    # all rows' bands: 100 + 1 + 0 + 1000 = 1101
    assert stats.cumulative_affected_records == 1101
    assert stats.tier_distribution == {"FULL_REVIEW": 2, "CONFIRM": 1, "AUTONOMOUS": 1}
    assert stats.mutation_count == 3  # rows 1, 2, 4 have reversibility_score > 0
    assert stats.distinct_resource_count == 3  # users/1, users/2, users/3
    assert stats.escalation_rate == 0.75  # 3 of 4 (2 FULL_REVIEW + 1 CONFIRM) are non-AUTONOMOUS
    assert stats.novelty_rate == 0.25  # 1 of 4 has floor_name == novelty_unprecedented


def test_mean_pairwise_similarity_null_with_fewer_than_two_embeddings():
    rows = [_row(action_id="1", embedding=[1.0, 0.0])]
    stats = compute_session_stats("agent-1", 300, rows)
    assert stats.mean_pairwise_similarity is None


def test_mean_pairwise_similarity_computed_across_embedded_actions():
    rows = [
        _row(action_id="1", embedding=[1.0, 0.0]),
        _row(action_id="2", embedding=[1.0, 0.0]),  # identical -> similarity 1.0
        _row(action_id="3", embedding=[0.0, 1.0]),  # orthogonal -> similarity 0.0
    ]
    stats = compute_session_stats("agent-1", 300, rows)
    # pairs: (1,2)=1.0, (1,3)=0.0, (2,3)=0.0 -> mean = 1/3
    assert abs(stats.mean_pairwise_similarity - (1 / 3)) < 1e-9


# --- B3 shadow floor ---


def _stats(**overrides):
    base = dict(
        agent_id="agent-1",
        window_seconds=300,
        action_count=1,
        cumulative_affected_records=0,
        cumulative_irreversible_records=0,
        tier_distribution={},
        mutation_count=0,
        distinct_resource_count=1,
        mean_pairwise_similarity=None,
        escalation_rate=0.0,
        novelty_rate=0.0,
    )
    base.update(overrides)
    from app.risk.session_read_model import SessionStats

    return SessionStats(**base)


def test_no_floor_fires_when_under_every_threshold():
    result = evaluate_session_floor(_stats())
    assert result.would_fire is False
    assert result.floor is None
    assert result.applied is False


def test_cumulative_irreversible_floor_fires_at_threshold():
    result = evaluate_session_floor(_stats(cumulative_irreversible_records=100))
    assert result.would_fire is True
    assert result.floor == "session_cumulative_irreversible"
    assert result.applied is False  # shadow: never applied


def test_cumulative_irreversible_floor_does_not_fire_below_threshold():
    result = evaluate_session_floor(_stats(cumulative_irreversible_records=99))
    assert result.would_fire is False


def test_mutation_volume_floor_fires_at_threshold():
    result = evaluate_session_floor(_stats(mutation_count=20))
    assert result.would_fire is True
    assert result.floor == "session_mutation_volume"
    assert result.applied is False


def test_fragmentation_signature_floor_fires_at_threshold():
    result = evaluate_session_floor(
        _stats(action_count=10, mean_pairwise_similarity=0.90)
    )
    assert result.would_fire is True
    assert result.floor == "session_fragmentation_signature"
    assert result.applied is False


def test_fragmentation_signature_requires_both_conditions():
    # action_count high but similarity low -> no fire
    result = evaluate_session_floor(_stats(action_count=10, mean_pairwise_similarity=0.5))
    assert result.would_fire is False
    # similarity high but too few actions -> no fire
    result = evaluate_session_floor(_stats(action_count=5, mean_pairwise_similarity=0.99))
    assert result.would_fire is False


def test_most_severe_floor_named_when_multiple_conditions_met():
    result = evaluate_session_floor(
        _stats(cumulative_irreversible_records=100, mutation_count=20)
    )
    assert result.floor == "session_cumulative_irreversible"


# --- window boundary (store level) ---


async def test_window_boundary_excludes_older_actions():
    from app.store import InMemoryStore

    store = InMemoryStore()
    old = await store.get_or_create_action(
        id="old", agent_id="agent-1", action_type="delete", resource="r1",
        params={}, idempotency_key=None,
    )
    new = await store.get_or_create_action(
        id="new", agent_id="agent-1", action_type="delete", resource="r2",
        params={}, idempotency_key=None,
    )
    old_record = store._actions["old"]
    old_record.created_at = _NOW - timedelta(seconds=600)
    new_record = store._actions["new"]
    new_record.created_at = _NOW - timedelta(seconds=10)

    since = _NOW - timedelta(seconds=300)
    rows = await store.list_session_actions("agent-1", since)

    assert {r.action_id for r in rows} == {"new"}
