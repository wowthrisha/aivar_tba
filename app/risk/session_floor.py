"""FEATURE B3 (intelligence-v6) — session floor, SHADOW MODE ONLY.

Same discipline as app/calibration.py (S5): computed, reported, audited,
NEVER applied to a real tier. SESSION_FLOOR_MODE defaults to "off" -
matching S5's own instruction ("behind an env flag defaulting off"),
corrected from an earlier default of "shadow" (which had been justified
by analogy to CALIBRATION_MODE's precedent, but that analogy doesn't
hold: unlike calibration, shadow-mode here isn't free even when it
changes nothing - every evaluate() call was paying a DB round-trip
(list_session_actions) plus a full session-stats aggregation with no
flag set at all, unconditionally, before this fix). Shadow mode is now
strictly opt-in. There is no "enforce" mode in this pass - only "off"
and "shadow" are valid; anything else (including "enforce") falls back
to "off", the same fail-safe pattern get_calibration_mode() already
uses for an unrecognized value.

New file - does not import or modify app/risk/scorer.py, floors.py,
tiers.py, router.py, or decision.py.
"""

import os
from dataclasses import dataclass

from app.risk.session_read_model import SessionStats

VALID_MODES = ("off", "shadow")

SESSION_IRREVERSIBLE_RECORDS_THRESHOLD = 100
SESSION_MUTATION_COUNT_THRESHOLD = 20
SESSION_FRAGMENTATION_ACTION_COUNT_THRESHOLD = 10
SESSION_FRAGMENTATION_SIMILARITY_THRESHOLD = 0.90


def get_session_floor_mode() -> str:
    raw = os.environ.get("SESSION_FLOOR_MODE", "off").strip().lower()
    return raw if raw in VALID_MODES else "off"


@dataclass(frozen=True)
class SessionFloorResult:
    would_fire: bool
    floor: str | None
    reason: str | None
    applied: bool


def evaluate_session_floor(stats: SessionStats) -> SessionFloorResult:
    """Never mutates a real tier - `applied` is always False in this
    pass (no enforce mode exists yet). Checks the FULL_REVIEW-tier
    condition first so `floor` names the most severe match, mirroring
    app/risk/floors.py's own "highest tier wins" convention."""

    if stats.cumulative_irreversible_records >= SESSION_IRREVERSIBLE_RECORDS_THRESHOLD:
        return SessionFloorResult(
            would_fire=True,
            floor="session_cumulative_irreversible",
            reason=(
                f"floor: {stats.cumulative_irreversible_records} cumulative irreversible "
                f"records across {stats.action_count} actions in the last "
                f"{stats.window_seconds}s window (>= {SESSION_IRREVERSIBLE_RECORDS_THRESHOLD}). "
                "Would escalate to at least FULL_REVIEW."
            ),
            applied=False,
        )

    if stats.mutation_count >= SESSION_MUTATION_COUNT_THRESHOLD:
        return SessionFloorResult(
            would_fire=True,
            floor="session_mutation_volume",
            reason=(
                f"floor: {stats.mutation_count} mutating actions in the last "
                f"{stats.window_seconds}s window (>= {SESSION_MUTATION_COUNT_THRESHOLD}). "
                "Would escalate to at least CONFIRM."
            ),
            applied=False,
        )

    if (
        stats.action_count >= SESSION_FRAGMENTATION_ACTION_COUNT_THRESHOLD
        and stats.mean_pairwise_similarity is not None
        and stats.mean_pairwise_similarity >= SESSION_FRAGMENTATION_SIMILARITY_THRESHOLD
    ):
        return SessionFloorResult(
            would_fire=True,
            floor="session_fragmentation_signature",
            reason=(
                f"floor: {stats.action_count} highly similar actions "
                f"(mean pairwise similarity {stats.mean_pairwise_similarity:.2f} >= "
                f"{SESSION_FRAGMENTATION_SIMILARITY_THRESHOLD}) in the last "
                f"{stats.window_seconds}s window - fragmentation signature. "
                "Would escalate to at least CONFIRM."
            ),
            applied=False,
        )

    return SessionFloorResult(would_fire=False, floor=None, reason=None, applied=False)
