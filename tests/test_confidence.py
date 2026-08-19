"""T-09a — two-signal confidence."""

from app.risk.confidence import structural_completeness, two_signal_confidence


def test_high_self_report_low_completeness_yields_low_confidence():
    # the required test from the T-09a spec: a fluent, overconfident model
    # must not be able to talk the combined signal up.
    self_reported = 0.95
    structural = structural_completeness("delete", {})  # missing resource_id
    combined = two_signal_confidence(self_reported, structural)
    assert combined == 0.0
    assert combined < self_reported


def test_unknown_action_type_yields_zero_structural_completeness():
    assert structural_completeness("teleport", {"resource_id": 1}) == 0.0


def test_known_action_type_with_all_required_params_present():
    assert structural_completeness("delete", {"resource_id": 42}) == 1.0


def test_known_action_type_missing_a_required_param():
    assert structural_completeness("update", {"resource_id": 42}) == 0.0  # missing "fields"


def test_read_has_no_required_params():
    assert structural_completeness("read", {}) == 1.0


def test_two_signal_confidence_takes_the_minimum():
    assert two_signal_confidence(0.9, 0.6) == 0.6
    assert two_signal_confidence(0.3, 1.0) == 0.3
    assert two_signal_confidence(1.0, 1.0) == 1.0
    assert two_signal_confidence(0.0, 1.0) == 0.0
