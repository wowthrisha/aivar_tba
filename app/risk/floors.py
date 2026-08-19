"""Override floors — evaluated AFTER the weighted score. Pure Python, no I/O.

FROZEN — do not add, remove, or retune without explicit approval:
  irreversible AND affected_records >= 100   -> FULL_REVIEW
  regulatory == PHI_SOX AND is_mutation      -> FULL_REVIEW
  llm_confidence < 0.5                       -> CONFIRM
"""

from dataclasses import dataclass

from app.risk.scorer import Regulatory, Reversibility
from app.risk.tiers import Tier

# FROZEN
FLOOR_RECORDS_THRESHOLD = 100
FLOOR_CONFIDENCE_THRESHOLD = 0.5


@dataclass(frozen=True)
class FloorResult:
    tier: Tier
    floor_name: str | None
    reason: str | None


def evaluate_floors(
    reversibility: Reversibility,
    affected_records: int,
    regulatory: Regulatory,
    llm_confidence: float,
) -> FloorResult:
    # is_mutation derived from reversibility: anything other than a read
    # mutates state.
    is_mutation = reversibility != Reversibility.READ

    if reversibility == Reversibility.IRREVERSIBLE and affected_records >= FLOOR_RECORDS_THRESHOLD:
        return FloorResult(
            tier=Tier.FULL_REVIEW,
            floor_name="irreversible_bulk",
            reason=(
                f"floor: irreversible action affecting {affected_records} "
                f"records (>= {FLOOR_RECORDS_THRESHOLD})"
            ),
        )

    if regulatory == Regulatory.PHI_SOX and is_mutation:
        return FloorResult(
            tier=Tier.FULL_REVIEW,
            floor_name="regulated_mutation",
            reason="floor: PHI/SOX-regulated data mutation",
        )

    if llm_confidence < FLOOR_CONFIDENCE_THRESHOLD:
        return FloorResult(
            tier=Tier.CONFIRM,
            floor_name="low_confidence",
            reason=(
                f"floor: LLM confidence {llm_confidence:.2f} below "
                f"{FLOOR_CONFIDENCE_THRESHOLD}"
            ),
        )

    return FloorResult(tier=Tier.AUTONOMOUS, floor_name=None, reason=None)


def final_tier(weighted_tier: Tier, floor_result: FloorResult) -> Tier:
    result = max(weighted_tier, floor_result.tier)
    assert result >= weighted_tier  # structural invariant: floors only escalate
    return result
