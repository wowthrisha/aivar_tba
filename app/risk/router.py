"""Combines the weighted scorer and the override floors into the final
routing decision. Pure Python, no I/O, no FastAPI, no database.
"""

from dataclasses import dataclass

from app.risk.floors import evaluate_floors, final_tier
from app.risk.scorer import Regulatory, Reversibility, score_action
from app.risk.tiers import Tier


@dataclass(frozen=True)
class RoutingResult:
    composite: float
    tier: Tier
    explanation: str
    floor_name: str | None


def route_action(
    reversibility: Reversibility,
    affected_records: int,
    regulatory: Regulatory,
    llm_confidence: float,
) -> RoutingResult:
    weighted = score_action(reversibility, affected_records, regulatory, llm_confidence)
    floors = evaluate_floors(reversibility, affected_records, regulatory, llm_confidence)
    tier = final_tier(weighted.tier, floors)

    if floors.floor_name is not None:
        # a floor fired: its reason is the triggering reason for the final
        # tier, which may differ from the weighted-only tier/explanation.
        explanation = f"{weighted.composite:.2f} -> {tier.name} ({floors.reason})."
    else:
        explanation = weighted.explanation

    return RoutingResult(
        composite=weighted.composite,
        tier=tier,
        explanation=explanation,
        floor_name=floors.floor_name,
    )
