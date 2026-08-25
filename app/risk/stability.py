"""FEATURE C — decision stability sweep (intelligence-v6).

Recomputes the tier across llm_confidence 0.0 -> 1.0 in 0.05 steps,
deterministically, with NO LLM calls - every step just re-derives
two_signal_confidence() and re-runs compose_final_decision() with the
same reversibility/affected_records/regulatory/calibration/novelty
inputs the real evaluation already used, varying only the self-reported
confidence term.

New file, not an edit to any existing app/risk/*.py module: only
IMPORTS and CALLS score_action/compose_final_decision, never modifies
them. app/risk/scorer.py, floors.py, tiers.py, router.py, decision.py
all stay at zero diff (S4).

Serves EU AI Act Art. 14(4)(c): the overseer should be able to tell
when a decision was close to going the other way, not just what the
decision was.
"""

from dataclasses import dataclass

from app.risk.decision import ComposedDecision, compose_final_decision
from app.risk.scorer import Regulatory, Reversibility
from app.risk.tiers import Tier

# The sweep grid: 0.0, 0.05, ..., 1.0 - exactly as specified (21 points).
_SWEEP_STEPS = tuple(round(i * 0.05, 2) for i in range(21))

# Binary-search refinement precision for the reported flip point, once a
# flip has been located between two adjacent grid points. Purely a
# reporting-precision improvement over the raw 0.05 grid; still zero LLM
# calls, still deterministic.
_REFINE_TOLERANCE = 0.001


@dataclass(frozen=True)
class StabilityInfo:
    stability: str  # "TIER_INVARIANT" | "CONFIDENCE_BOUND"
    flips_below: float | None  # llm_confidence value; None when TIER_INVARIANT


def _tier_at(
    reversibility: Reversibility,
    affected_records: int,
    regulatory: Regulatory,
    structural: float,
    calibration_mode: str,
    calibration_adjustment: float,
    calibration_degraded: bool,
    novelty_should_escalate: bool,
    novelty_prior_count: int,
    self_reported_confidence: float,
) -> Tier:
    # Local import avoids a module-level cycle with app.risk.confidence,
    # which does not import app.risk.stability.
    from app.risk.confidence import two_signal_confidence

    combined = two_signal_confidence(self_reported_confidence, structural)
    decision: ComposedDecision = compose_final_decision(
        reversibility,
        affected_records,
        regulatory,
        combined,
        calibration_mode=calibration_mode,
        calibration_adjustment=calibration_adjustment,
        calibration_degraded=calibration_degraded,
        novelty_should_escalate=novelty_should_escalate,
        novelty_prior_count=novelty_prior_count,
    )
    return decision.tier


def compute_stability(
    reversibility: Reversibility,
    affected_records: int,
    regulatory: Regulatory,
    structural: float,
    calibration_mode: str,
    calibration_adjustment: float,
    calibration_degraded: bool,
    novelty_should_escalate: bool,
    novelty_prior_count: int,
) -> StabilityInfo:
    """Read-only: re-derives what compose_final_decision WOULD have
    returned at each swept self-reported confidence, never touches the
    real decision. The real evaluation's own self-reported confidence
    need not land exactly on the 0.05 grid, so this does not assert
    against it - it only characterizes the shape of the decision surface
    around the real inputs."""

    def tier_at(conf: float) -> Tier:
        return _tier_at(
            reversibility,
            affected_records,
            regulatory,
            structural,
            calibration_mode,
            calibration_adjustment,
            calibration_degraded,
            novelty_should_escalate,
            novelty_prior_count,
            conf,
        )

    tiers = [tier_at(c) for c in _SWEEP_STEPS]

    if len(set(tiers)) == 1:
        return StabilityInfo(stability="TIER_INVARIANT", flips_below=None)

    # Find the highest-confidence adjacent pair in the grid whose tiers
    # differ (confidence and tier are not guaranteed monotonic in
    # general, but scanning for ANY adjacent change and refining there
    # gives a concrete, honest flip point rather than an invented one).
    lo, hi = None, None
    for i in range(len(_SWEEP_STEPS) - 1):
        if tiers[i] != tiers[i + 1]:
            lo, hi = _SWEEP_STEPS[i], _SWEEP_STEPS[i + 1]
            # Keep scanning - report the LOWEST-confidence flip boundary
            # (the point below which the tier is more restrictive), which
            # is what "flips below X" means to a reviewer.
            break

    tier_below = tier_at(lo)
    while hi - lo > _REFINE_TOLERANCE:
        mid = round((lo + hi) / 2, 6)
        if tier_at(mid) == tier_below:
            lo = mid
        else:
            hi = mid

    return StabilityInfo(stability="CONFIDENCE_BOUND", flips_below=round(hi, 3))
