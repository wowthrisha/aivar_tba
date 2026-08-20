"""BONUS — adaptive calibration, SHADOW MODE ONLY by default.

Pure aggregation + a pure formula, matching app/oversight.py's pattern:
derive historical feedback from EXISTING persisted audit + approval
records (no new table, no migration, no writes caused by this module).

"Clean" confirmation: an approval (via /confirm for CONFIRM-tier, or
"approve" via /v1/review-queue/{id}/decision for FULL_REVIEW-tier) whose
approved_params_hash equals the action's own (evaluated) params_hash.
"Modified/rejected": a "reject" decision, OR an approval whose
approved_params_hash differs from the action's params_hash.

CALIBRATION_MODE governs whether this module's output is even computed
(off), computed and exposed but never applied to routing (shadow, the
default), or computed and applied to the composite BEFORE tier/floor/
novelty logic runs (enforce, never enabled by default here). Floors are
evaluated independently of composite (app/risk/floors.py takes the raw
inputs, not the composite) and floor escalation is a max() over the
weighted tier and the floor tier (app/risk/floors.py:final_tier) - so an
enforce-mode adjustment can only ever move the WEIGHTED tier, never
suppress a floor. Nothing in app/risk/ is modified to achieve this; this
module only imports and calls the existing pure functions there.
"""

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger("app")

MIN_EVIDENCE = 5
ADJUSTMENT_SCALE = 0.20
ADJUSTMENT_CLAMP = 0.10

VALID_MODES = ("off", "shadow", "enforce")


def get_calibration_mode() -> str:
    raw = os.environ.get("CALIBRATION_MODE", "shadow").strip().lower()
    return raw if raw in VALID_MODES else "off"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def compute_adjustment(clean: int, modified_rejected: int) -> tuple[float, bool]:
    """Returns (adjustment, has_min_evidence). Recomputed from history each
    call - never accumulates a previous adjustment."""
    total = clean + modified_rejected
    if total < MIN_EVIDENCE:
        return 0.0, False
    clean_ratio = clean / total
    raw = (0.5 - clean_ratio) * ADJUSTMENT_SCALE
    return _clamp(raw, -ADJUSTMENT_CLAMP, ADJUSTMENT_CLAMP), True


@dataclass(frozen=True)
class CalibrationStats:
    action_type: str
    clean_confirmations: int
    modifications_rejections: int
    total: int
    adjustment: float
    has_min_evidence: bool


async def _historical_outcomes(store, audit):
    """Yields (action_type, is_clean) for every historical approval or
    rejection, derived only from existing audit/approval/action records."""
    confirmed = await audit.list_records(event_type="confirmed", limit=1_000_000)
    for rec in confirmed:
        action_id = rec.payload.get("action_id")
        approved_hash = rec.payload.get("params_hash")
        if action_id is None:
            continue
        action = await store.get_action(action_id)
        if action is None:
            continue
        yield action.action_type, approved_hash == action.params_hash

    decisions = await audit.list_records(event_type="decision", limit=1_000_000)
    for rec in decisions:
        action_id = rec.payload.get("action_id")
        decision = rec.payload.get("decision")
        if action_id is None or decision is None:
            continue
        action = await store.get_action(action_id)
        if action is None:
            continue
        if decision == "reject":
            yield action.action_type, False
            continue
        approval = await store.get_approval(action_id)
        approved_hash = approval.approved_params_hash if approval is not None else None
        yield action.action_type, approved_hash == action.params_hash


async def compute_calibration_by_action_type(store, audit) -> dict[str, CalibrationStats]:
    """May raise - callers MUST wrap this (see calibration_for_action_type
    below) so a DB error/timeout/malformed record never fails evaluate()."""
    counts: dict[str, list[int]] = {}
    async for action_type, is_clean in _historical_outcomes(store, audit):
        bucket = counts.setdefault(action_type, [0, 0])
        bucket[0 if is_clean else 1] += 1

    stats: dict[str, CalibrationStats] = {}
    for action_type, (clean, modified_rejected) in counts.items():
        adjustment, has_min = compute_adjustment(clean, modified_rejected)
        stats[action_type] = CalibrationStats(
            action_type=action_type,
            clean_confirmations=clean,
            modifications_rejections=modified_rejected,
            total=clean + modified_rejected,
            adjustment=adjustment,
            has_min_evidence=has_min,
        )
    return stats


async def calibration_for_action_type(store, audit, action_type: str) -> tuple[CalibrationStats, bool]:
    """FAIL-SOFT entry point for evaluate(): on ANY error (DB error,
    malformed data, unexpected calculation error), returns a zero
    adjustment and degraded=True rather than propagating - the evaluation
    result must continue normally regardless of calibration health."""
    try:
        stats = await compute_calibration_by_action_type(store, audit)
    except Exception:
        # Fail-soft: log safely (no payload contents, which may carry
        # user-supplied params) and never let calibration fail evaluate().
        logger.error("calibration_degraded=true - error computing calibration", exc_info=True)
        return (
            CalibrationStats(
                action_type=action_type,
                clean_confirmations=0,
                modifications_rejections=0,
                total=0,
                adjustment=0.0,
                has_min_evidence=False,
            ),
            True,
        )
    found = stats.get(action_type)
    if found is None:
        found = CalibrationStats(
            action_type=action_type,
            clean_confirmations=0,
            modifications_rejections=0,
            total=0,
            adjustment=0.0,
            has_min_evidence=False,
        )
    return found, False
