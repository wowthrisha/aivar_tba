"""Feature A (novelty add-on) - reviewer oversight metrics.

Tests the pure aggregation function directly (compute_reviewer_metrics),
matching tests/test_scoring.py and tests/test_floors.py's pattern of
testing business logic in isolation from FastAPI/the database.
"""

from datetime import datetime, timedelta, timezone

from app.oversight import DecisionEvent, compute_reviewer_metrics

_T0 = datetime(2026, 8, 20, 0, 0, 0, tzinfo=timezone.utc)


def _decision(decision: str, latency_seconds: float, state: str = "approved") -> DecisionEvent:
    return DecisionEvent(
        action_id="action-1",
        decision=decision,
        decided_at=_T0 + timedelta(seconds=latency_seconds),
        proposed_at=_T0,
        action_current_state=state,
    )


def test_oversight_metrics_computes_approval_rate():
    decisions = [
        _decision("approve", 30),
        _decision("approve", 30),
        _decision("approve", 30),
        _decision("reject", 30),
    ]

    result = compute_reviewer_metrics(decisions)

    assert result.decisions_total == 4
    assert result.approval_rate == 0.75


def test_automation_bias_flag_fires_on_rubber_stamping():
    """Seed 6 decisions, 6 approvals, 2s latency each -> flag true."""
    decisions = [_decision("approve", 2) for _ in range(6)]

    result = compute_reviewer_metrics(decisions)

    assert result.decisions_total == 6
    assert result.approval_rate == 1.0
    assert result.median_decision_latency == 2.0
    assert result.automation_bias_flag is True


def test_oversight_metrics_empty_reviewer_returns_nulls_not_zeros():
    result = compute_reviewer_metrics([])

    assert result.decisions_total == 0
    assert result.approval_rate is None
    assert result.median_decision_latency is None
    assert result.p90_decision_latency is None
    # reversal_rate has its own explicit spec fallback ("0.0 if not
    # derivable") - it alone stays numeric even with zero decisions.
    assert result.reversal_rate == 0.0
    assert result.automation_bias_flag is False


def test_automation_bias_flag_does_not_fire_below_five_decisions():
    decisions = [_decision("approve", 2) for _ in range(4)]

    result = compute_reviewer_metrics(decisions)

    assert result.decisions_total == 4
    assert result.approval_rate == 1.0
    assert result.automation_bias_flag is False


def test_automation_bias_flag_does_not_fire_on_slow_careful_review():
    decisions = [_decision("approve", 120) for _ in range(6)]

    result = compute_reviewer_metrics(decisions)

    assert result.approval_rate == 1.0
    assert result.automation_bias_flag is False


def test_reversal_rate_counts_only_expired_or_rejected_approved_actions():
    decisions = [
        _decision("approve", 30, state="executed"),
        _decision("approve", 30, state="expired"),
        _decision("approve", 30, state="expired"),
        _decision("reject", 30, state="rejected"),
    ]

    result = compute_reviewer_metrics(decisions)

    # 3 approvals total, 2 later expired -> 2/3
    assert result.reversal_rate == 2 / 3


def test_reversal_rate_is_zero_when_reviewer_never_approved():
    decisions = [_decision("reject", 30, state="rejected") for _ in range(3)]

    result = compute_reviewer_metrics(decisions)

    assert result.decisions_total == 3
    assert result.approval_rate == 0.0
    # No approvals exist to ever be reversed - explicit fallback, not
    # an invented approximation.
    assert result.reversal_rate == 0.0
