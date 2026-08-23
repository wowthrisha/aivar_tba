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

D-24, approved fix (2026-08-21): evaluate_floors() was a first-match/
return chain, so when multiple floors independently fired, only one was
ever named and the rest were silently lost. Rewritten to evaluate ALL
four conditions and collect every match - no trigger condition,
threshold, or produced tier changed (tier is still max() across
whatever fires, which is exactly what the old first-match order already
produced, since it happened to check both FULL_REVIEW-tier floors before
either CONFIRM-tier floor). FLOOR_PRIORITY below governs only which
matched floor gets NAMED as floor_name.
"""

from dataclasses import dataclass
from typing import Iterable

from app.risk.scorer import Regulatory, Reversibility
from app.risk.tiers import Tier

# FROZEN
FLOOR_RECORDS_THRESHOLD = 100
FLOOR_CONFIDENCE_THRESHOLD = 0.5

# D-24: naming priority when multiple floors fire simultaneously - an
# explicit product decision (2026-08-21), not derived from tier severity
# or trigger order. Do not reorder without the same explicit approval.
# Action-derived reasons rank above model-state reasons: low confidence
# is the most transient, least informative signal to show a reviewer.
# "novelty_unprecedented" is evaluated in app/main.py (Feature B), not
# here - included so app/main.py can rank it against these four via the
# same list.
FLOOR_PRIORITY: tuple[str, ...] = (
    "irreversible_bulk",
    "unrecoverable_mutation_requires_confirm",
    "regulated_mutation",
    "novelty_unprecedented",
    "low_confidence_on_mutation",
)


def sort_by_priority(names: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(names, key=FLOOR_PRIORITY.index))


def highest_priority_floor(names: Iterable[str]) -> str | None:
    sorted_names = sort_by_priority(names)
    return sorted_names[0] if sorted_names else None


@dataclass(frozen=True)
class FloorResult:
    tier: Tier
    floor_name: str | None
    reason: str | None
    floors_fired: tuple[str, ...] = ()


def evaluate_floors(
    reversibility: Reversibility,
    affected_records: int,
    regulatory: Regulatory,
    llm_confidence: float,
) -> FloorResult:
    # is_mutation derived from reversibility: anything other than a read
    # mutates state.
    is_mutation = reversibility != Reversibility.READ

    candidates: list[tuple[str, Tier, str]] = []

    if reversibility == Reversibility.IRREVERSIBLE and affected_records >= FLOOR_RECORDS_THRESHOLD:
        candidates.append((
            "irreversible_bulk",
            Tier.FULL_REVIEW,
            f"floor: irreversible action affecting {affected_records} "
            f"records (>= {FLOOR_RECORDS_THRESHOLD}). "
            f"Would not have fired with affected_records < {FLOOR_RECORDS_THRESHOLD} "
            "(other floors may still apply).",
        ))

    if reversibility in (Reversibility.UPDATE_WITHOUT_SNAPSHOT, Reversibility.IRREVERSIBLE):
        candidates.append((
            "unrecoverable_mutation_requires_confirm",
            Tier.CONFIRM,
            f"floor: reversibility={reversibility.value} has no rollback path; "
            "unrecoverable mutations may not execute autonomously. "
            "Would not have fired if reversibility were read or "
            "update_with_snapshot (other floors may still apply).",
        ))

    if regulatory in (Regulatory.PII_GDPR, Regulatory.PHI_SOX) and is_mutation:
        candidates.append((
            "regulated_mutation",
            Tier.FULL_REVIEW,
            f"floor: {regulatory.value}-regulated data mutation. "
            "Would not have fired if regulatory were not PII_GDPR or PHI_SOX "
            "(other floors may still apply).",
        ))

    if llm_confidence < FLOOR_CONFIDENCE_THRESHOLD and is_mutation:
        candidates.append((
            "low_confidence_on_mutation",
            Tier.CONFIRM,
            f"floor: LLM confidence {llm_confidence:.2f} below "
            f"{FLOOR_CONFIDENCE_THRESHOLD} on a mutating action. "
            f"Would not have fired with llm_confidence >= {FLOOR_CONFIDENCE_THRESHOLD} "
            "(other floors may still apply).",
        ))

    if not candidates:
        return FloorResult(tier=Tier.AUTONOMOUS, floor_name=None, reason=None, floors_fired=())

    floors_fired = sort_by_priority(name for name, _, _ in candidates)
    reason_by_name = {name: reason for name, _, reason in candidates}
    top_name = floors_fired[0]
    escalation_tier = max(tier for _, tier, _ in candidates)

    return FloorResult(
        tier=escalation_tier,
        floor_name=top_name,
        reason=reason_by_name[top_name],
        floors_fired=floors_fired,
    )


def final_tier(weighted_tier: Tier, floor_result: FloorResult) -> Tier:
    result = max(weighted_tier, floor_result.tier)
    assert result >= weighted_tier  # structural invariant: floors only escalate
    return result
