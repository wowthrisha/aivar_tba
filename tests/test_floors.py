import itertools

from app.risk.floors import (
    FLOOR_CONFIDENCE_THRESHOLD,
    FLOOR_RECORDS_THRESHOLD,
    evaluate_floors,
    final_tier,
)
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
    # D-14: the floor only applies to mutations - a read has no blast
    # radius, so a mutating reversibility is required to exercise it.
    result = evaluate_floors(Reversibility.UPDATE_WITH_SNAPSHOT, 0, Regulatory.NONE, 0.49)
    assert result.tier == Tier.CONFIRM
    assert result.floor_name == "low_confidence_on_mutation"
    assert result.reason is not None


def test_low_confidence_floor_does_not_fire_at_exactly_0_5():
    result = evaluate_floors(Reversibility.UPDATE_WITH_SNAPSHOT, 0, Regulatory.NONE, 0.5)
    assert result.floor_name != "low_confidence_on_mutation"


def test_low_confidence_floor_does_not_fire_on_read_only():
    # D-14: model uncertainty about a fully-reversible, zero-blast-radius
    # action does not justify consuming human oversight.
    result = evaluate_floors(Reversibility.READ, 0, Regulatory.NONE, 0.0)
    assert result.floor_name != "low_confidence_on_mutation"
    assert result.tier == Tier.AUTONOMOUS


def test_low_confidence_floor_still_fires_on_mutation():
    result = evaluate_floors(Reversibility.UPDATE_WITH_SNAPSHOT, 0, Regulatory.NONE, 0.0)
    assert result.tier == Tier.CONFIRM
    assert result.floor_name == "low_confidence_on_mutation"


def test_read_only_is_autonomous_across_full_confidence_range():
    # D-14: proves the read-only criterion is now variance-immune to the
    # live LLM's self-reported confidence.
    confidence = 0.0
    while confidence <= 1.0:
        result = evaluate_floors(Reversibility.READ, 1, Regulatory.NONE, confidence)
        assert result.tier == Tier.AUTONOMOUS, f"failed at llm_confidence={confidence}"
        confidence = round(confidence + 0.05, 2)


def test_single_update_is_confirm_across_full_confidence_range():
    confidence = 0.0
    while confidence <= 1.0:
        result = evaluate_floors(
            Reversibility.UPDATE_WITHOUT_SNAPSHOT, 1, Regulatory.NONE, confidence
        )
        assert result.tier == Tier.CONFIRM, f"failed at llm_confidence={confidence}"
        confidence = round(confidence + 0.05, 2)


def test_bulk_delete_is_full_review_across_full_confidence_range():
    confidence = 0.0
    while confidence <= 1.0:
        result = evaluate_floors(Reversibility.IRREVERSIBLE, 5000, Regulatory.NONE, confidence)
        assert result.tier == Tier.FULL_REVIEW, f"failed at llm_confidence={confidence}"
        confidence = round(confidence + 0.05, 2)


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


# ---------- D-24: evaluate-all-and-collect, not first-match/return ----------
# All four inputs below make EVERY floor condition fire simultaneously:
# IRREVERSIBLE -> irreversible_bulk (affected_records>=100) AND
# unrecoverable_mutation_requires_confirm (irreversible has no rollback);
# PHI_SOX + mutation -> regulated_mutation; llm_confidence 0.0 + mutation
# -> low_confidence_on_mutation.
_ALL_FLOORS_FIRE = (Reversibility.IRREVERSIBLE, 500, Regulatory.PHI_SOX, 0.0)


def test_all_matching_floors_are_recorded():
    result = evaluate_floors(*_ALL_FLOORS_FIRE)
    assert set(result.floors_fired) == {
        "irreversible_bulk",
        "unrecoverable_mutation_requires_confirm",
        "regulated_mutation",
        "low_confidence_on_mutation",
    }


def test_floor_name_is_highest_priority_match():
    result = evaluate_floors(*_ALL_FLOORS_FIRE)
    assert result.floor_name == "irreversible_bulk"


def test_low_confidence_never_masks_an_unconditional_floor():
    # low_confidence_on_mutation also fires here (confidence 0.0), but
    # must never be the reported name when a higher-priority, action-
    # derived floor also fired - it's the most transient, least
    # informative signal to show a reviewer.
    result = evaluate_floors(*_ALL_FLOORS_FIRE)
    assert result.floor_name != "low_confidence_on_mutation"
    assert "low_confidence_on_mutation" in result.floors_fired


def test_update_scenario_floor_name_stable_across_confidence_range():
    # D-24: before this fix, floor_name flipped to low_confidence_on_mutation
    # below 0.5. unrecoverable_mutation_requires_confirm now outranks it at
    # every confidence level, so the reported name never changes - only
    # floors_fired grows to include low_confidence_on_mutation below 0.5.
    confidence = 0.0
    while confidence <= 1.0:
        result = evaluate_floors(Reversibility.UPDATE_WITHOUT_SNAPSHOT, 1, Regulatory.NONE, confidence)
        assert result.floor_name == "unrecoverable_mutation_requires_confirm", (
            f"failed at llm_confidence={confidence}: floor_name={result.floor_name}"
        )
        if confidence < FLOOR_CONFIDENCE_THRESHOLD:
            assert "low_confidence_on_mutation" in result.floors_fired
        confidence = round(confidence + 0.05, 2)


def _pre_d24_floor_tier(
    reversibility: Reversibility, affected_records: int, regulatory: Regulatory, llm_confidence: float
) -> Tier:
    """Oracle reproducing the exact pre-D-24 first-match/return
    evaluate_floors() logic (order matters here, unlike the refactored
    version), to prove the D-24 refactor changes only floor NAMING,
    never the resulting tier."""
    is_mutation = reversibility != Reversibility.READ

    if reversibility == Reversibility.IRREVERSIBLE and affected_records >= FLOOR_RECORDS_THRESHOLD:
        return Tier.FULL_REVIEW
    if regulatory in (Regulatory.PII_GDPR, Regulatory.PHI_SOX) and is_mutation:
        return Tier.FULL_REVIEW
    if llm_confidence < FLOOR_CONFIDENCE_THRESHOLD and is_mutation:
        return Tier.CONFIRM
    if reversibility in (Reversibility.UPDATE_WITHOUT_SNAPSHOT, Reversibility.IRREVERSIBLE):
        return Tier.CONFIRM
    return Tier.AUTONOMOUS


def test_final_tier_unchanged_by_refactor():
    """THE GATE. Every input combination must yield an identical tier
    before and after the D-24 refactor. If this fails anywhere, the
    refactor changed behavior beyond floor naming - stop and revert."""
    affected_records_values = (0, 1, 10, 50, 99, 100, 500, 1000, 10_000)
    confidence_values = (0.0, 0.1, 0.3, 0.49, 0.5, 0.51, 0.7, 0.9, 1.0)

    checked = 0
    for reversibility, affected_records, regulatory, llm_confidence in itertools.product(
        Reversibility, affected_records_values, Regulatory, confidence_values
    ):
        expected_tier = _pre_d24_floor_tier(reversibility, affected_records, regulatory, llm_confidence)
        result = evaluate_floors(reversibility, affected_records, regulatory, llm_confidence)
        assert result.tier == expected_tier, (
            f"D-24 refactor changed tier: inputs={reversibility, affected_records, regulatory, llm_confidence} "
            f"expected={expected_tier.name} got={result.tier.name}"
        )
        checked += 1

    assert checked == len(Reversibility) * len(affected_records_values) * len(Regulatory) * len(confidence_values)


# ---------------------------------------------------------------------------
# 2C (T-08 counterfactual gap): floor-fired explanations name a real,
# changeable input - presentation only, tiers unchanged (proven by
# test_final_tier_unchanged_by_refactor above, which still passes
# unmodified against this same evaluate_floors()).
# ---------------------------------------------------------------------------


def test_irreversible_bulk_reason_contains_counterfactual():
    result = evaluate_floors(Reversibility.IRREVERSIBLE, 500, Regulatory.NONE, 1.0)
    assert result.floor_name == "irreversible_bulk"
    assert "affected_records" in result.reason


def test_unrecoverable_mutation_reason_contains_counterfactual():
    result = evaluate_floors(Reversibility.UPDATE_WITHOUT_SNAPSHOT, 1, Regulatory.NONE, 1.0)
    assert result.floor_name == "unrecoverable_mutation_requires_confirm"
    assert "reversibility" in result.reason


def test_regulated_mutation_reason_contains_counterfactual():
    result = evaluate_floors(Reversibility.UPDATE_WITH_SNAPSHOT, 0, Regulatory.PHI_SOX, 1.0)
    assert result.floor_name == "regulated_mutation"
    assert "regulatory" in result.reason


def test_low_confidence_reason_contains_counterfactual():
    result = evaluate_floors(Reversibility.UPDATE_WITH_SNAPSHOT, 0, Regulatory.NONE, 0.1)
    assert result.floor_name == "low_confidence_on_mutation"
    assert "llm_confidence" in result.reason
