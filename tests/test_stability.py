"""FEATURE C — decision stability. Pure, deterministic, no LLM calls.

C4: an invariant action reports TIER_INVARIANT; a boundary action reports
the actual flip point; the tier is unchanged either way (compute_stability
never influences composite/tier - it only re-derives what-if outcomes).
"""

from app.risk.decision import compose_final_decision
from app.risk.scorer import Regulatory, Reversibility
from app.risk.stability import compute_stability


def _no_calibration_no_novelty(reversibility, affected_records, regulatory, llm_confidence):
    return compose_final_decision(
        reversibility,
        affected_records,
        regulatory,
        llm_confidence,
        calibration_mode="off",
        calibration_adjustment=0.0,
        calibration_degraded=False,
        novelty_should_escalate=False,
        novelty_prior_count=0,
    )


def test_tier_invariant_action_reports_invariant():
    # A read: reversibility/data_scope/regulatory all contribute 0.0, and
    # confidence's own weight (0.10) can never alone cross the 0.30
    # threshold. Confidence swings 0.0->1.0 (structural=1.0, so combined
    # confidence tracks the swept value directly) move composite by at
    # most 0.10, never reaching 0.30 - tier stays AUTONOMOUS throughout.
    reversibility, affected_records, regulatory = Reversibility.READ, 0, Regulatory.NONE

    result = compute_stability(
        reversibility,
        affected_records,
        regulatory,
        structural=1.0,
        calibration_mode="off",
        calibration_adjustment=0.0,
        calibration_degraded=False,
        novelty_should_escalate=False,
        novelty_prior_count=0,
    )

    assert result.stability == "TIER_INVARIANT"
    assert result.flips_below is None


def test_confidence_bound_action_reports_actual_flip_point():
    # An irreversible single-record mutation: floor
    # unrecoverable_mutation_requires_confirm always fires (>= CONFIRM),
    # AND low_confidence_on_mutation fires at llm_confidence < 0.5,
    # escalating floor tier itself no further (still CONFIRM) - so use a
    # regulated, larger-scope action instead where confidence swings the
    # WEIGHTED tier across the FULL_REVIEW threshold independent of floors.
    reversibility = Reversibility.UPDATE_WITH_SNAPSHOT
    affected_records = 1_000  # data_scope 0.8
    regulatory = Regulatory.INTERNAL  # 0.3
    # composite = 0.40*0.4 + 0.30*0.8 + 0.20*0.3 + 0.10*(1-conf)
    #           = 0.16 + 0.24 + 0.06 + 0.10*(1-conf) = 0.46 + 0.10*(1-conf)
    # conf=1.0 -> 0.46 (CONFIRM); conf=0.0 -> 0.56 (CONFIRM) - never crosses
    # 0.65. Use a case that DOES cross: affected_records=10_000 (1.0).
    affected_records = 10_000
    # composite = 0.16 + 0.30*1.0 + 0.06 + 0.10*(1-conf) = 0.52 + 0.10*(1-conf)
    # conf=1.0 -> 0.52 (CONFIRM); conf=0.0 -> 0.62 (CONFIRM) - still short.
    # Push regulatory up instead.
    regulatory = Regulatory.PII_GDPR  # but PII_GDPR+mutation is itself a FULL_REVIEW floor - avoid.
    regulatory = Regulatory.INTERNAL
    reversibility = Reversibility.UPDATE_WITHOUT_SNAPSHOT  # 0.7
    # composite = 0.40*0.7 + 0.30*1.0 + 0.20*0.3 + 0.10*(1-conf)
    #           = 0.28 + 0.30 + 0.06 + 0.10*(1-conf) = 0.64 + 0.10*(1-conf)
    # conf=1.0 -> 0.64 (CONFIRM, floor also CONFIRM); conf=0.0 -> 0.74 (FULL_REVIEW)
    # Crosses 0.65 when 0.10*(1-conf)=0.01 -> (1-conf)=0.1 -> conf=0.90.
    llm_confidence = 0.5  # composite = 0.64 + 0.05 = 0.69 -> FULL_REVIEW actual
    actual = _no_calibration_no_novelty(reversibility, affected_records, regulatory, llm_confidence)
    assert actual.tier.name == "FULL_REVIEW"

    result = compute_stability(
        reversibility,
        affected_records,
        regulatory,
        structural=1.0,
        calibration_mode="off",
        calibration_adjustment=0.0,
        calibration_degraded=False,
        novelty_should_escalate=False,
        novelty_prior_count=0,
    )

    assert result.stability == "CONFIDENCE_BOUND"
    assert result.flips_below is not None
    # Analytic crossing at conf=0.90 (composite==0.65 exactly at conf=0.90):
    # at/below that confidence the action is FULL_REVIEW, above it CONFIRM.
    assert 0.895 <= result.flips_below <= 0.905


def test_stability_never_changes_the_real_tier():
    # compute_stability is read-only: calling it must not be able to
    # perturb what compose_final_decision returns for the actual inputs.
    reversibility, affected_records, regulatory, conf = (
        Reversibility.IRREVERSIBLE,
        1,
        Regulatory.NONE,
        0.8,
    )
    before = _no_calibration_no_novelty(reversibility, affected_records, regulatory, conf)
    compute_stability(
        reversibility,
        affected_records,
        regulatory,
        structural=1.0,
        calibration_mode="off",
        calibration_adjustment=0.0,
        calibration_degraded=False,
        novelty_should_escalate=False,
        novelty_prior_count=0,
    )
    after = _no_calibration_no_novelty(reversibility, affected_records, regulatory, conf)
    assert before.tier == after.tier
    assert before.composite == after.composite
