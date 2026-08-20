"""Feature A (novelty add-on) - reviewer oversight metrics.

Pure aggregation, no I/O - matches app/risk/scorer.py's pattern of
keeping business logic testable in isolation from FastAPI/the database.

Source of the per-decision "latency" figure: the audit log's own
"decision" event (app/main.py's decision() handler appends one on
EVERY decision, approve or reject), not the `approvals` table's
`decided_at` column. The approvals table only ever holds decision=
"approve" rows (app/main.py never calls store.set_approval() for a
reject) - aggregating over it alone would make approval_rate always
1.0 for any reviewer who has ever approved anything, silently hiding
every reject. Using the audit log's "decision" events instead captures
both outcomes uniformly, from one already-existing source, with no
schema change - documented here rather than silently deviating from
the "aggregated over the approvals table" framing.
"""

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel

# FROZEN by Feature A's own spec - the automation-bias heuristic.
AUTOMATION_BIAS_APPROVAL_RATE_THRESHOLD = 0.95
AUTOMATION_BIAS_LATENCY_THRESHOLD_SECONDS = 10.0
AUTOMATION_BIAS_MIN_DECISIONS = 5


@dataclass(frozen=True)
class DecisionEvent:
    """One reviewer decision on one action, already joined against that
    action's proposal time and current state - the raw input to
    compute_reviewer_metrics()."""

    action_id: str
    decision: str  # "approve" | "reject"
    decided_at: datetime
    proposed_at: datetime
    action_current_state: str  # ActionState.value of the action NOW


class ReviewerMetrics(BaseModel):
    decisions_total: int
    approval_rate: float | None
    median_decision_latency: float | None  # seconds
    p90_decision_latency: float | None  # seconds
    reversal_rate: float
    automation_bias_flag: bool


class OversightResponse(BaseModel):
    """GET /v1/oversight/reviewers."""

    reviewers: dict[str, ReviewerMetrics]
    review_queue_depth: int
    oldest_pending_age_seconds: float | None


def _percentile(sorted_values: list[float], fraction: float) -> float:
    index = min(len(sorted_values) - 1, int(len(sorted_values) * fraction))
    return sorted_values[index]


def compute_reviewer_metrics(decisions: list[DecisionEvent]) -> ReviewerMetrics:
    decisions_total = len(decisions)

    if decisions_total == 0:
        # E-6: approval_rate/latency are genuinely undefined with zero
        # decisions (0/0) - null, not 0.0. reversal_rate has its own
        # explicit spec fallback ("0.0 if not derivable"), so it alone
        # stays numeric even here.
        return ReviewerMetrics(
            decisions_total=0,
            approval_rate=None,
            median_decision_latency=None,
            p90_decision_latency=None,
            reversal_rate=0.0,
            automation_bias_flag=False,
        )

    approved = [d for d in decisions if d.decision == "approve"]
    approval_rate = len(approved) / decisions_total

    latencies = sorted((d.decided_at - d.proposed_at).total_seconds() for d in decisions)
    median_latency = _percentile(latencies, 0.5)
    p90_latency = _percentile(latencies, 0.9)

    if approved:
        reversed_count = sum(1 for d in approved if d.action_current_state in ("rejected", "expired"))
        reversal_rate = reversed_count / len(approved)
    else:
        # No approvals to ever reverse - explicit fallback per spec,
        # not an invented approximation.
        reversal_rate = 0.0

    automation_bias_flag = (
        approval_rate > AUTOMATION_BIAS_APPROVAL_RATE_THRESHOLD
        and median_latency < AUTOMATION_BIAS_LATENCY_THRESHOLD_SECONDS
        and decisions_total >= AUTOMATION_BIAS_MIN_DECISIONS
    )

    return ReviewerMetrics(
        decisions_total=decisions_total,
        approval_rate=approval_rate,
        median_decision_latency=median_latency,
        p90_decision_latency=p90_latency,
        reversal_rate=reversal_rate,
        automation_bias_flag=automation_bias_flag,
    )
