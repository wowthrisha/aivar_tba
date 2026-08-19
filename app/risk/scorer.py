"""Pure risk-scoring logic. No FastAPI, no database, no I/O."""

from enum import Enum

from pydantic import BaseModel

from app.risk.tiers import Tier, tier_for_composite


class Reversibility(str, Enum):
    READ = "read"
    UPDATE_WITH_SNAPSHOT = "update_with_snapshot"
    UPDATE_WITHOUT_SNAPSHOT = "update_without_snapshot"
    IRREVERSIBLE = "irreversible"  # delete / send / pay


class Regulatory(str, Enum):
    NONE = "none"
    INTERNAL = "internal"
    PII_GDPR = "pii_gdpr"
    PHI_SOX = "phi_sox"


REVERSIBILITY_BAND: dict[Reversibility, float] = {
    Reversibility.READ: 0.0,
    Reversibility.UPDATE_WITH_SNAPSHOT: 0.4,
    Reversibility.UPDATE_WITHOUT_SNAPSHOT: 0.7,
    Reversibility.IRREVERSIBLE: 1.0,
}

REGULATORY_BAND: dict[Regulatory, float] = {
    Regulatory.NONE: 0.0,
    Regulatory.INTERNAL: 0.3,
    Regulatory.PII_GDPR: 0.7,
    Regulatory.PHI_SOX: 1.0,
}

# data_scope is "log-scaled on affected records" per spec, given as anchor
# points rather than a continuous formula. Modeled as step bands (lower-bound
# thresholds, highest match wins), consistent with the other three dimensions.
DATA_SCOPE_THRESHOLDS: tuple[tuple[int, float], ...] = (
    (10_000, 1.0),
    (1_000, 0.8),
    (100, 0.6),
    (10, 0.4),
    (1, 0.2),
    (0, 0.0),
)

WEIGHTS: dict[str, float] = {
    "reversibility": 0.40,
    "data_scope": 0.30,
    "regulatory": 0.20,
    "confidence": 0.10,
}
WEIGHTS_VERSION = "v1"


def _data_scope_score(affected_records: int) -> float:
    if affected_records < 0:
        raise ValueError("affected_records must be >= 0")
    for threshold, value in DATA_SCOPE_THRESHOLDS:
        if affected_records >= threshold:
            return value
    return 0.0  # unreachable: the 0 threshold always matches


_REVERSIBILITY_ORDER: tuple[Reversibility, ...] = (
    Reversibility.READ,
    Reversibility.UPDATE_WITH_SNAPSHOT,
    Reversibility.UPDATE_WITHOUT_SNAPSHOT,
    Reversibility.IRREVERSIBLE,
)

_REGULATORY_ORDER: tuple[Regulatory, ...] = (
    Regulatory.NONE,
    Regulatory.INTERNAL,
    Regulatory.PII_GDPR,
    Regulatory.PHI_SOX,
)

_DATA_SCOPE_ASCENDING: tuple[tuple[int, float], ...] = tuple(
    sorted(DATA_SCOPE_THRESHOLDS)
)


class RiskAssessmentResult(BaseModel):
    composite: float
    reversibility: float
    data_scope: float
    regulatory: float
    confidence: float
    weights: dict[str, float]
    weights_version: str
    tier: Tier
    explanation: str


def _next_lower_reversibility(current: Reversibility) -> Reversibility | None:
    idx = _REVERSIBILITY_ORDER.index(current)
    return _REVERSIBILITY_ORDER[idx - 1] if idx > 0 else None


def _next_lower_regulatory(current: Regulatory) -> Regulatory | None:
    idx = _REGULATORY_ORDER.index(current)
    return _REGULATORY_ORDER[idx - 1] if idx > 0 else None


def _next_lower_data_scope(affected_records: int) -> tuple[float, int] | None:
    """Returns (next_lower_score, band_ceiling): band_ceiling is the
    threshold that starts the CURRENT band, i.e. the lever is
    "affected_records < band_ceiling"."""
    current_score = _data_scope_score(affected_records)
    scores_ascending = [value for _, value in _DATA_SCOPE_ASCENDING]
    idx = scores_ascending.index(current_score)
    if idx == 0:
        return None
    lower_score = scores_ascending[idx - 1]
    band_ceiling = _DATA_SCOPE_ASCENDING[idx][0]
    return lower_score, band_ceiling


def _counterfactual_explanation(
    tier: Tier,
    composite: float,
    reversibility: Reversibility,
    affected_records: int,
    regulatory: Regulatory,
    reversibility_score: float,
    data_scope_score: float,
    regulatory_score: float,
) -> str:
    # confidence is continuous (1.0 - llm_confidence), not banded, so it has
    # no "next-lower band" and is excluded from this sweep.
    candidates: list[tuple[float, str]] = []

    lower_rev = _next_lower_reversibility(reversibility)
    if lower_rev is not None:
        new_composite = composite - WEIGHTS["reversibility"] * (
            reversibility_score - REVERSIBILITY_BAND[lower_rev]
        )
        candidates.append(
            (new_composite, f"reversibility were {lower_rev.value} instead of {reversibility.value}")
        )

    lower_data = _next_lower_data_scope(affected_records)
    if lower_data is not None:
        lower_score, band_ceiling = lower_data
        new_composite = composite - WEIGHTS["data_scope"] * (data_scope_score - lower_score)
        candidates.append((new_composite, f"affected_records were < {band_ceiling}"))

    lower_reg = _next_lower_regulatory(regulatory)
    if lower_reg is not None:
        new_composite = composite - WEIGHTS["regulatory"] * (
            regulatory_score - REGULATORY_BAND[lower_reg]
        )
        candidates.append(
            (new_composite, f"regulatory were {lower_reg.value} instead of {regulatory.value}")
        )

    for new_composite, lever in candidates:
        new_tier = tier_for_composite(new_composite)
        if new_tier < tier:
            return f"{composite:.2f} -> {tier.name}. Would have been {new_tier.name} if {lever}."

    return f"{composite:.2f} -> {tier.name}. No single dimension change would alter the tier."


def score_action(
    reversibility: Reversibility,
    affected_records: int,
    regulatory: Regulatory,
    llm_confidence: float,
) -> RiskAssessmentResult:
    reversibility_score = REVERSIBILITY_BAND[reversibility]
    data_scope_score = _data_scope_score(affected_records)
    regulatory_score = REGULATORY_BAND[regulatory]
    confidence_score = 1.0 - llm_confidence

    composite = (
        WEIGHTS["reversibility"] * reversibility_score
        + WEIGHTS["data_scope"] * data_scope_score
        + WEIGHTS["regulatory"] * regulatory_score
        + WEIGHTS["confidence"] * confidence_score
    )
    tier = tier_for_composite(composite)
    explanation = _counterfactual_explanation(
        tier,
        composite,
        reversibility,
        affected_records,
        regulatory,
        reversibility_score,
        data_scope_score,
        regulatory_score,
    )

    return RiskAssessmentResult(
        composite=composite,
        reversibility=reversibility_score,
        data_scope=data_scope_score,
        regulatory=regulatory_score,
        confidence=confidence_score,
        weights=dict(WEIGHTS),
        weights_version=WEIGHTS_VERSION,
        tier=tier,
        explanation=explanation,
    )
