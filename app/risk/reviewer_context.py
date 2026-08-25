"""FEATURE D (intelligence-v6) — reviewer personalisation. Pure Python,
no I/O: takes rows already fetched by app.audit/app.db_store's
reviewer_decisions_with_embeddings(). New file; does not import or
modify app/risk/scorer.py, floors.py, tiers.py, router.py, or
decision.py.

Rationale: inconsistency across similar cases is a real oversight
failure nothing currently instruments. Two reviewers (or the same
reviewer on two occasions) approving materially similar actions
differently is exactly the kind of drift a plain-English audit log
cannot surface on its own - this helps a reviewer stay consistent with
themselves, and gives whoever monitors reviewers a signal beyond raw
approval rate.

"Similar" reuses NOVELTY_SIMILARITY_THRESHOLD (0.75) from
app/embeddings.py rather than inventing a second, unrelated threshold -
one definition of "similar" throughout the codebase.
"""

from dataclasses import dataclass

from app.embeddings import NOVELTY_SIMILARITY_THRESHOLD, cosine_similarity
from app.store import ReviewerDecisionRow


@dataclass(frozen=True)
class SimilarActionsStats:
    count: int
    approved: int
    rejected: int


@dataclass(frozen=True)
class ReviewerContext:
    similar_actions_decided_by_this_reviewer: SimilarActionsStats | None
    consistency_note: str | None


def compute_reviewer_context(
    target_embedding: list[float] | None, decisions: list[ReviewerDecisionRow]
) -> ReviewerContext:
    """D3: nulls, not zeros, when there is nothing to compare - either
    the target action has no embedding (embedding_degraded=true at
    evaluate time) or this reviewer has decided on zero SIMILAR actions.
    A reviewer with decisions but none similar to this one is not the
    same as "consistent" - reporting a fabricated 0-of-0 would imply a
    judgement this data does not support."""
    if target_embedding is None:
        return ReviewerContext(similar_actions_decided_by_this_reviewer=None, consistency_note=None)

    similar = [
        d
        for d in decisions
        if d.embedding is not None
        and cosine_similarity(target_embedding, d.embedding) >= NOVELTY_SIMILARITY_THRESHOLD
    ]
    if not similar:
        return ReviewerContext(similar_actions_decided_by_this_reviewer=None, consistency_note=None)

    approved = sum(1 for d in similar if d.decision == "approve")
    rejected = sum(1 for d in similar if d.decision == "reject")
    count = len(similar)
    plural = "s" if count != 1 else ""
    note = f"You approved {approved} of {count} similar action{plural}."

    return ReviewerContext(
        similar_actions_decided_by_this_reviewer=SimilarActionsStats(
            count=count, approved=approved, rejected=rejected
        ),
        consistency_note=note,
    )
