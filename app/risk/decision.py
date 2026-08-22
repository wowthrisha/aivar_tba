"""L-B fix (2026-08-22): single source of truth for the enforced tier.

Before this file existed, route_action() returned a BASE tier and
app/main.py's evaluate() independently composed the ENFORCED tier from
calibration, floors, and novelty - two decision points. D-24 fixed the
consequence (novelty silently overwriting floor_name); this file fixes
the structure: compose_final_decision() is the ONE function that owns
composite -> calibration -> thresholds -> floors -> novelty -> final
tier/floors_fired/explanation. app/main.py calls it once and uses the
result verbatim.

route_action() (app/risk/router.py) is UNCHANGED - same signature, same
behaviour, same callers. compose_final_decision() wraps it rather than
reimplementing it, so app/risk/router.py, app/risk/floors.py,
app/risk/scorer.py, and app/risk/tiers.py all stay at zero diff.

Calibration and novelty are pure decision logic (mode branching,
threshold recomputation, floor re-evaluation, escalation ordering) but
their INPUTS (historical stats, embedding similarity) require I/O -
app/main.py still does that I/O and hands this function plain values
(calibration_adjustment, novelty_should_escalate), exactly as it always
computed them, just no longer applying them itself.
"""

from dataclasses import dataclass

from app.embeddings import escalate_one_tier, novelty_reason
from app.risk.floors import evaluate_floors, final_tier, highest_priority_floor, sort_by_priority
from app.risk.router import route_action
from app.risk.scorer import Regulatory, Reversibility
from app.risk.tiers import Tier, tier_for_composite


@dataclass(frozen=True)
class ComposedDecision:
    tier: Tier
    composite: float
    floor_name: str | None
    floors_fired: tuple[str, ...]
    explanation: str
    base_composite: float
    effective_composite: float
    calibration_applied: bool


def compose_final_decision(
    reversibility: Reversibility,
    affected_records: int,
    regulatory: Regulatory,
    llm_confidence: float,
    calibration_mode: str,
    calibration_adjustment: float,
    calibration_degraded: bool,
    novelty_should_escalate: bool,
    novelty_prior_count: int,
) -> ComposedDecision:
    routing = route_action(reversibility, affected_records, regulatory, llm_confidence)

    tier = routing.tier
    composite = routing.composite
    explanation = routing.explanation
    floor_name = routing.floor_name
    floors_fired = routing.floors_fired

    base_composite = routing.composite
    effective_composite = base_composite + calibration_adjustment
    applied = calibration_mode == "enforce" and not calibration_degraded

    if applied:
        floors = evaluate_floors(reversibility, affected_records, regulatory, llm_confidence)
        adjusted_weighted_tier = tier_for_composite(effective_composite)
        tier = final_tier(adjusted_weighted_tier, floors)
        composite = effective_composite
        floor_name = floors.floor_name
        floors_fired = floors.floors_fired
        if floors.floor_name is not None:
            explanation = f"{effective_composite:.2f} -> {tier.name} ({floors.reason})."
        else:
            explanation = f"{effective_composite:.2f} -> {tier.name} (calibration-adjusted)."

    if novelty_should_escalate:
        escalated = escalate_one_tier(tier)
        if escalated != tier:
            tier = escalated
            floors_fired = sort_by_priority(tuple(floors_fired) + ("novelty_unprecedented",))
            floor_name = highest_priority_floor(floors_fired)
            explanation = f"{explanation} Escalated to {escalated.name} ({novelty_reason(novelty_prior_count)})."

    return ComposedDecision(
        tier=tier,
        composite=composite,
        floor_name=floor_name,
        floors_fired=floors_fired,
        explanation=explanation,
        base_composite=base_composite,
        effective_composite=effective_composite,
        calibration_applied=applied,
    )
