import pytest

from app.risk.scorer import (
    WEIGHTS,
    WEIGHTS_VERSION,
    Regulatory,
    Reversibility,
    score_action,
)
from app.risk.tiers import Tier


@pytest.mark.parametrize(
    "reversibility,expected",
    [
        (Reversibility.READ, 0.0),
        (Reversibility.UPDATE_WITH_SNAPSHOT, 0.4),
        (Reversibility.UPDATE_WITHOUT_SNAPSHOT, 0.7),
        (Reversibility.IRREVERSIBLE, 1.0),
    ],
)
def test_reversibility_band(reversibility, expected):
    result = score_action(reversibility, 0, Regulatory.NONE, 1.0)
    assert result.reversibility == expected


@pytest.mark.parametrize(
    "affected_records,expected",
    [
        (0, 0.0),
        (1, 0.2),
        (9, 0.2),
        (10, 0.4),
        (99, 0.4),
        (100, 0.6),
        (999, 0.6),
        (1_000, 0.8),
        (9_999, 0.8),
        (10_000, 1.0),
        (999_999, 1.0),
    ],
)
def test_data_scope_band(affected_records, expected):
    result = score_action(Reversibility.READ, affected_records, Regulatory.NONE, 1.0)
    assert result.data_scope == expected


@pytest.mark.parametrize(
    "regulatory,expected",
    [
        (Regulatory.NONE, 0.0),
        (Regulatory.INTERNAL, 0.3),
        (Regulatory.PII_GDPR, 0.7),
        (Regulatory.PHI_SOX, 1.0),
    ],
)
def test_regulatory_band(regulatory, expected):
    result = score_action(Reversibility.READ, 0, regulatory, 1.0)
    assert result.regulatory == expected


@pytest.mark.parametrize(
    "llm_confidence,expected",
    [(1.0, 0.0), (0.0, 1.0), (0.75, 0.25)],
)
def test_confidence_band(llm_confidence, expected):
    result = score_action(Reversibility.READ, 0, Regulatory.NONE, llm_confidence)
    assert result.confidence == pytest.approx(expected)


def test_weights_are_frozen():
    assert WEIGHTS == {
        "reversibility": 0.40,
        "data_scope": 0.30,
        "regulatory": 0.20,
        "confidence": 0.10,
    }


def test_composite_is_weighted_sum():
    result = score_action(
        Reversibility.UPDATE_WITHOUT_SNAPSHOT, 100, Regulatory.PII_GDPR, 0.8
    )
    expected = 0.40 * 0.7 + 0.30 * 0.6 + 0.20 * 0.7 + 0.10 * 0.2
    assert result.composite == pytest.approx(expected)
    assert result.weights == WEIGHTS
    assert result.weights_version == WEIGHTS_VERSION


def test_counterfactual_names_a_real_dimension_when_tier_would_change():
    # irreversible + 100 records + PII + confident -> composite 0.40*1.0 +
    # 0.30*0.6 + 0.20*0.7 + 0.10*0.0 = 0.72 -> FULL_REVIEW. Dropping
    # reversibility to its next-lower band (0.7) alone brings composite to
    # 0.40*0.7+0.30*0.6+0.20*0.7+0.10*0.0 = 0.6 -> CONFIRM: a real lever.
    result = score_action(
        Reversibility.IRREVERSIBLE, 100, Regulatory.PII_GDPR, 1.0
    )
    assert result.tier == Tier.FULL_REVIEW
    assert "Would have been" in result.explanation
    assert any(
        name in result.explanation
        for name in ("reversibility", "data_scope", "regulatory")
    )


def test_counterfactual_states_none_explicitly_when_no_lever_changes_tier():
    # every dimension already at its floor band -> composite 0.0, and no
    # dimension has a next-lower band to drop to.
    result = score_action(Reversibility.READ, 0, Regulatory.NONE, 1.0)
    assert result.tier == Tier.AUTONOMOUS
    assert "No single dimension change would alter the tier" in result.explanation


# ---------------------------------------------------------------------------
# L-G: direction contract - the direction implied by each dimension's NAME
# must match its actual effect on composite. Parameterised so a future
# dimension wired with the wrong sign fails on day one, not silently.
# ---------------------------------------------------------------------------


def _composite(reversibility=Reversibility.READ, affected_records=0, regulatory=Regulatory.NONE, llm_confidence=1.0):
    return score_action(reversibility, affected_records, regulatory, llm_confidence).composite


@pytest.mark.parametrize(
    "dimension,sequence",
    [
        (
            "reversibility",
            [Reversibility.READ, Reversibility.UPDATE_WITH_SNAPSHOT,
             Reversibility.UPDATE_WITHOUT_SNAPSHOT, Reversibility.IRREVERSIBLE],
        ),
        ("affected_records", [0, 1, 10, 100, 1_000, 10_000]),
        ("regulatory", [Regulatory.NONE, Regulatory.INTERNAL, Regulatory.PII_GDPR, Regulatory.PHI_SOX]),
        # llm_confidence DESCENDING = uncertainty ASCENDING = risk ascending -
        # this is the exact direction L-G's naming bug inverted.
        ("llm_confidence", [1.0, 0.7, 0.3, 0.0]),
    ],
)
def test_direction_contract_per_dimension(dimension, sequence):
    composites = [_composite(**{dimension: value}) for value in sequence]

    for earlier, later in zip(composites, composites[1:]):
        assert later >= earlier, (
            f"{dimension}: composite decreased along an increasing-risk sequence {sequence} -> {composites}"
        )
    assert composites[-1] > composites[0], (
        f"{dimension}: composite never actually moved across the full risk-increasing sequence {sequence}"
    )
