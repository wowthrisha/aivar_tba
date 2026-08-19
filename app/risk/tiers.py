"""Composite-score to routing-tier mapping. Pure Python, no I/O.

Shared by scorer.py (counterfactual explanation) and floors.py (final_tier
= max(weighted_tier, highest_floor_tier)).
"""

from enum import IntEnum

# FROZEN — the two thresholds. Do not change without explicit approval.
THRESHOLD_CONFIRM = 0.30
THRESHOLD_FULL_REVIEW = 0.65


class Tier(IntEnum):
    """Ordered so max(tier_a, tier_b) picks the more restrictive tier."""

    AUTONOMOUS = 0
    CONFIRM = 1
    FULL_REVIEW = 2


def tier_for_composite(composite: float) -> Tier:
    """Boundary semantics (approved 2026-08-19, for T-06a/T-07a):

    composite < 0.30            -> AUTONOMOUS
    0.30 <= composite < 0.65    -> CONFIRM
    composite >= 0.65           -> FULL_REVIEW
    """
    if composite >= THRESHOLD_FULL_REVIEW:
        return Tier.FULL_REVIEW
    if composite >= THRESHOLD_CONFIRM:
        return Tier.CONFIRM
    return Tier.AUTONOMOUS
