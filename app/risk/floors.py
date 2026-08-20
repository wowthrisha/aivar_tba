"""Override floors — evaluated AFTER the weighted score. Pure Python, no I/O.

FROZEN — do not add, remove, or retune without explicit approval:
  irreversible AND affected_records >= 100              -> FULL_REVIEW
  regulatory in {PII_GDPR, PHI_SOX} AND is_mutation      -> FULL_REVIEW
  llm_confidence < 0.5 AND is_mutation                   -> CONFIRM
  reversibility in {UPDATE_WITHOUT_SNAPSHOT,
                     IRREVERSIBLE}                        -> at least CONFIRM

T-13 adversarial review, approved fixes (2026-08-20):
  - "regulated_mutation" broadened from PHI_SOX-only to PII_GDPR+PHI_SOX
    (Finding 2): PII_GDPR already scores 0.7 on the regulatory dimension,
    nearly as severe as PHI_SOX's 1.0, but was not guaranteed a floor.
  - "unrecoverable_mutation_requires_confirm" added (Finding 1): an
    unrecoverable mutation (no rollback path) must never execute fully
    autonomously, regardless of what the weighted score alone produces.
    Checked LAST so the FULL_REVIEW floors above still take precedence
    when they also apply.

D-14, approved fix (2026-08-20): "low_confidence" renamed
"low_confidence_on_mutation" and gated on is_mutation. Model uncertainty
is only a risk in proportion to the consequence of being wrong. A
read-only action is fully reversible with zero blast radius, so low
confidence about it does not justify consuming human oversight -
escalating reads on low confidence produces exactly the bottleneck the
problem statement warns against. This mirrors the reversibility weight
of 0.40: consequence, not uncertainty alone, drives oversight.
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

    if regulatory in (Regulatory.PII_GDPR, Regulatory.PHI_SOX) and is_mutation:
        return FloorResult(
            tier=Tier.FULL_REVIEW,
            floor_name="regulated_mutation",
            reason=f"floor: {regulatory.value}-regulated data mutation",
        )

    if llm_confidence < FLOOR_CONFIDENCE_THRESHOLD and is_mutation:
        return FloorResult(
            tier=Tier.CONFIRM,
            floor_name="low_confidence_on_mutation",
            reason=(
                f"floor: LLM confidence {llm_confidence:.2f} below "
                f"{FLOOR_CONFIDENCE_THRESHOLD} on a mutating action"
            ),
        )

    if reversibility in (Reversibility.UPDATE_WITHOUT_SNAPSHOT, Reversibility.IRREVERSIBLE):
        return FloorResult(
            tier=Tier.CONFIRM,
            floor_name="unrecoverable_mutation_requires_confirm",
            reason=(
                f"floor: reversibility={reversibility.value} has no rollback path; "
                "unrecoverable mutations may not execute autonomously"
            ),
        )

    return FloorResult(tier=Tier.AUTONOMOUS, floor_name=None, reason=None)


def final_tier(weighted_tier: Tier, floor_result: FloorResult) -> Tier:
    result = max(weighted_tier, floor_result.tier)
    assert result >= weighted_tier  # structural invariant: floors only escalate
    return result
