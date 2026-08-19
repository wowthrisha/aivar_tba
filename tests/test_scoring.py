import pytest

from app.risk.scorer import (
    WEIGHTS,
    WEIGHTS_VERSION,
    Regulatory,
    Reversibility,
    score_action,
)


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
