import itertools

from app.risk.floors import evaluate_floors, final_tier
from app.risk.scorer import Regulatory, Reversibility, score_action
from app.risk.tiers import Tier


def test_irreversible_bulk_floor_fires_at_100_records():
    result = evaluate_floors(Reversibility.IRREVERSIBLE, 100, Regulatory.NONE, 1.0)
    assert result.tier == Tier.FULL_REVIEW
    assert result.floor_name == "irreversible_bulk"
    assert result.reason is not None


def test_irreversible_bulk_floor_does_not_fire_below_100_records():
    result = evaluate_floors(Reversibility.IRREVERSIBLE, 99, Regulatory.NONE, 1.0)
    assert result.floor_name != "irreversible_bulk"


def test_regulated_mutation_floor_fires():
    result = evaluate_floors(
        Reversibility.UPDATE_WITH_SNAPSHOT, 0, Regulatory.PHI_SOX, 1.0
    )
    assert result.tier == Tier.FULL_REVIEW
    assert result.floor_name == "regulated_mutation"
    assert result.reason is not None


def test_regulated_mutation_floor_does_not_fire_on_read():
    result = evaluate_floors(Reversibility.READ, 0, Regulatory.PHI_SOX, 1.0)
    assert result.floor_name != "regulated_mutation"


def test_low_confidence_floor_fires_below_0_5():
    result = evaluate_floors(Reversibility.READ, 0, Regulatory.NONE, 0.49)
    assert result.tier == Tier.CONFIRM
    assert result.floor_name == "low_confidence"
    assert result.reason is not None


def test_low_confidence_floor_does_not_fire_at_exactly_0_5():
    result = evaluate_floors(Reversibility.READ, 0, Regulatory.NONE, 0.5)
    assert result.floor_name != "low_confidence"


def test_no_floor_fires_when_nothing_matches():
    result = evaluate_floors(Reversibility.READ, 0, Regulatory.NONE, 1.0)
    assert result.tier == Tier.AUTONOMOUS
    assert result.floor_name is None
    assert result.reason is None


def test_final_tier_equals_weighted_tier_when_no_floor_fires_but_weighted_is_high():
    # composite alone already >= 0.65 (irreversible + PHI_SOX + low confidence
    # weighted contributions) but affected_records=0 keeps floor 1 from
    # firing, and READ... use a case where weighted crosses FULL_REVIEW on
    # its own: irreversible (0.4) + PHI_SOX (1.0*0.2=0.2) + low llm_conf.
    weighted = score_action(Reversibility.IRREVERSIBLE, 0, Regulatory.PHI_SOX, 0.0)
    assert weighted.tier == Tier.FULL_REVIEW  # 0.40+0.20+0.10 = 0.70
    floors = evaluate_floors(Reversibility.IRREVERSIBLE, 0, Regulatory.PHI_SOX, 0.0)
    # regulated_mutation floor WILL fire here too (IRREVERSIBLE is a
    # mutation), but final tier must still equal weighted tier since both
    # land on FULL_REVIEW - proves max() doesn't overshoot.
    assert final_tier(weighted.tier, floors) == Tier.FULL_REVIEW


def test_escalate_only_sweep():
    """The invariant: a floor may only ESCALATE. No combination of inputs
    may produce a final_tier lower than the weighted tier alone."""
    affected_records_values = (0, 1, 10, 50, 99, 100, 500, 1000, 10_000)
    confidence_values = (0.0, 0.1, 0.3, 0.49, 0.5, 0.51, 0.7, 0.9, 1.0)

    checked = 0
    for reversibility, affected_records, regulatory, llm_confidence in itertools.product(
        Reversibility, affected_records_values, Regulatory, confidence_values
    ):
        weighted = score_action(reversibility, affected_records, regulatory, llm_confidence)
        floors = evaluate_floors(reversibility, affected_records, regulatory, llm_confidence)
        result = final_tier(weighted.tier, floors)
        assert result >= weighted.tier, (
            f"floor lowered tier: weighted={weighted.tier.name} "
            f"final={result.name} inputs={reversibility, affected_records, regulatory, llm_confidence}"
        )
        checked += 1

    assert checked == len(Reversibility) * len(affected_records_values) * len(Regulatory) * len(confidence_values)
