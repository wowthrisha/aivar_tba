"""FEATURE D — reviewer personalisation, pure logic. D3: returns only
THIS reviewer's decisions (enforced by the caller passing an
already-filtered row list - see tests/test_api.py for the DB-query-level
check); empty history returns nulls, not zeros.
"""

from app.risk.reviewer_context import compute_reviewer_context
from app.store import ReviewerDecisionRow


def test_no_target_embedding_returns_nulls():
    result = compute_reviewer_context(
        None, [ReviewerDecisionRow(action_id="a", decision="approve", embedding=[1.0, 0.0])]
    )
    assert result.similar_actions_decided_by_this_reviewer is None
    assert result.consistency_note is None


def test_no_decisions_returns_nulls():
    result = compute_reviewer_context([1.0, 0.0], [])
    assert result.similar_actions_decided_by_this_reviewer is None
    assert result.consistency_note is None


def test_no_similar_decisions_returns_nulls_not_zeros():
    # A decided action exists but its embedding is orthogonal (similarity 0.0
    # < 0.75 threshold) - not similar, so this must be null, not a 0-of-0 stat.
    result = compute_reviewer_context(
        [1.0, 0.0], [ReviewerDecisionRow(action_id="a", decision="approve", embedding=[0.0, 1.0])]
    )
    assert result.similar_actions_decided_by_this_reviewer is None
    assert result.consistency_note is None


def test_similar_decisions_aggregate_approve_reject_split():
    target = [1.0, 0.0]
    decisions = [
        ReviewerDecisionRow(action_id="a", decision="approve", embedding=[1.0, 0.0]),  # sim 1.0
        ReviewerDecisionRow(action_id="b", decision="approve", embedding=[0.99, 0.01]),  # sim ~1.0
        ReviewerDecisionRow(action_id="c", decision="reject", embedding=[0.98, 0.02]),  # sim ~1.0
        ReviewerDecisionRow(action_id="d", decision="approve", embedding=[0.0, 1.0]),  # sim 0.0 - excluded
        ReviewerDecisionRow(action_id="e", decision="reject", embedding=None),  # no embedding - excluded
    ]
    result = compute_reviewer_context(target, decisions)
    stats = result.similar_actions_decided_by_this_reviewer
    assert stats is not None
    assert stats.count == 3
    assert stats.approved == 2
    assert stats.rejected == 1
    assert result.consistency_note == "You approved 2 of 3 similar actions."


def test_consistency_note_singular_phrasing():
    target = [1.0, 0.0]
    decisions = [ReviewerDecisionRow(action_id="a", decision="reject", embedding=[1.0, 0.0])]
    result = compute_reviewer_context(target, decisions)
    assert result.consistency_note == "You approved 0 of 1 similar action."
