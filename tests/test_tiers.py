"""T-07a — boundary escalation band.

Approved interpretation (2026-08-19): this tests the two FROZEN composite
thresholds (0.30, 0.65) that separate AUTONOMOUS / CONFIRM / FULL_REVIEW,
not the override floors (that's T-07's job). Approved boundary semantics:

  composite < 0.30            -> AUTONOMOUS
  0.30 <= composite < 0.65    -> CONFIRM
  composite >= 0.65           -> FULL_REVIEW

Tests sweep a +/- 0.04 band around each threshold and assert the tier
escalates at the correct point and never regresses as composite rises.
"""

import pytest

from app.risk.tiers import THRESHOLD_CONFIRM, THRESHOLD_FULL_REVIEW, Tier, tier_for_composite


@pytest.mark.parametrize(
    "composite,expected",
    [
        (THRESHOLD_CONFIRM - 0.04, Tier.AUTONOMOUS),
        (THRESHOLD_CONFIRM - 0.02, Tier.AUTONOMOUS),
        (THRESHOLD_CONFIRM, Tier.CONFIRM),  # exactly at threshold: escalates
        (THRESHOLD_CONFIRM + 0.02, Tier.CONFIRM),
        (THRESHOLD_CONFIRM + 0.04, Tier.CONFIRM),
        (THRESHOLD_FULL_REVIEW - 0.04, Tier.CONFIRM),
        (THRESHOLD_FULL_REVIEW - 0.02, Tier.CONFIRM),
        (THRESHOLD_FULL_REVIEW, Tier.FULL_REVIEW),  # exactly at threshold: escalates
        (THRESHOLD_FULL_REVIEW + 0.02, Tier.FULL_REVIEW),
        (THRESHOLD_FULL_REVIEW + 0.04, Tier.FULL_REVIEW),
    ],
)
def test_boundary_band_tier(composite, expected):
    assert tier_for_composite(composite) == expected


@pytest.mark.parametrize("threshold", [THRESHOLD_CONFIRM, THRESHOLD_FULL_REVIEW])
def test_tier_never_regresses_across_the_band(threshold):
    """Sweep threshold-0.04 to threshold+0.04 in small steps: tier must be
    monotonically non-decreasing as composite increases."""
    steps = 80  # 0.001 resolution across the 0.08-wide band
    prev_tier = None
    for i in range(steps + 1):
        composite = (threshold - 0.04) + (0.08 * i / steps)
        tier = tier_for_composite(composite)
        if prev_tier is not None:
            assert tier >= prev_tier, (
                f"tier regressed at composite={composite:.4f}: "
                f"{prev_tier.name} -> {tier.name}"
            )
        prev_tier = tier
