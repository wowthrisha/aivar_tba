"""Pure risk-scoring logic. No FastAPI, no database, no I/O."""

from enum import Enum

from pydantic import BaseModel


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


class RiskAssessmentResult(BaseModel):
    composite: float
    reversibility: float
    data_scope: float
    regulatory: float
    confidence: float
    weights: dict[str, float]
    weights_version: str


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

    return RiskAssessmentResult(
        composite=composite,
        reversibility=reversibility_score,
        data_scope=data_scope_score,
        regulatory=regulatory_score,
        confidence=confidence_score,
        weights=dict(WEIGHTS),
        weights_version=WEIGHTS_VERSION,
    )
