"""FEATURE B (intelligence-v6) — session risk read model. Pure Python,
no I/O: takes rows already fetched by app.store/app.db_store's
list_session_actions() and aggregates them. New file; does not import
or modify app/risk/scorer.py, floors.py, tiers.py, router.py, or
decision.py (S4 - zero diff on every frozen/existing module).

An agent can fragment a bulk operation into many small actions and
never trigger irreversible_bulk (evaluated per-action, in isolation).
This model reports what a WINDOW of one agent's actions looked like in
aggregate, so a session-level signal (app/risk/session_floor.py) can be
computed from it - in shadow mode only, per S5.

Known approximation, documented rather than hidden: affected_records is
not persisted anywhere as an exact integer (see app/store.py's
SessionActionRow docstring) - cumulative_affected_records/
cumulative_irreversible_records use the LOWER BOUND of the persisted
data_scope_score's band as a conservative (undercounting, never
overcounting) proxy. Exact accounting would require an additive
migration this pass avoids per S3's caution against live-DB schema
changes; since this whole feature is shadow-only, an undercount here
never changes a real routing decision - only a shadow report.

novelty_rate is similarly a documented undercount: only floor_fired
(the single HIGHEST-priority floor persisted per action) is available,
not the full floors_fired list, so an action where novelty_unprecedented
fired but was outranked by a higher-priority floor is not counted here.
"""

from collections import Counter
from dataclasses import dataclass

from app.embeddings import cosine_similarity
from app.risk.scorer import DATA_SCOPE_THRESHOLDS
from app.store import SessionActionRow

# Reverse of DATA_SCOPE_THRESHOLDS (score -> band-floor affected_records),
# built from the existing frozen constant rather than a second hardcoded
# copy - if that table's bands ever change, this reverse map moves with it.
_MIN_RECORDS_FOR_SCORE: dict[float, int] = {score: threshold for threshold, score in DATA_SCOPE_THRESHOLDS}

_IRREVERSIBLE_SCORE = 1.0  # app/risk/scorer.py REVERSIBILITY_BAND[Reversibility.IRREVERSIBLE]


def _min_records(data_scope_score: float | None) -> int:
    if data_scope_score is None:
        return 0
    return _MIN_RECORDS_FOR_SCORE.get(data_scope_score, 0)


def _mean_pairwise_similarity(embeddings: list[list[float]]) -> float | None:
    if len(embeddings) < 2:
        return None
    total = 0.0
    pairs = 0
    for i in range(len(embeddings)):
        for j in range(i + 1, len(embeddings)):
            total += cosine_similarity(embeddings[i], embeddings[j])
            pairs += 1
    return total / pairs


@dataclass(frozen=True)
class SessionStats:
    agent_id: str
    window_seconds: int
    action_count: int
    cumulative_affected_records: int
    cumulative_irreversible_records: int
    tier_distribution: dict[str, int]
    mutation_count: int
    distinct_resource_count: int
    mean_pairwise_similarity: float | None
    escalation_rate: float | None
    novelty_rate: float | None


def compute_session_stats(agent_id: str, window_seconds: int, rows: list[SessionActionRow]) -> SessionStats:
    action_count = len(rows)

    cumulative_affected_records = sum(_min_records(r.data_scope_score) for r in rows)
    cumulative_irreversible_records = sum(
        _min_records(r.data_scope_score) for r in rows if r.reversibility_score == _IRREVERSIBLE_SCORE
    )
    tier_distribution = dict(Counter(r.tier for r in rows if r.tier is not None))
    mutation_count = sum(1 for r in rows if r.reversibility_score is not None and r.reversibility_score > 0.0)
    distinct_resource_count = len({r.resource for r in rows})
    mean_pairwise_similarity = _mean_pairwise_similarity(
        [r.embedding for r in rows if r.embedding is not None]
    )

    if action_count == 0:
        escalation_rate = None
        novelty_rate = None
    else:
        escalated = sum(1 for r in rows if r.tier is not None and r.tier != "AUTONOMOUS")
        escalation_rate = escalated / action_count
        novel = sum(1 for r in rows if r.floor_name == "novelty_unprecedented")
        novelty_rate = novel / action_count

    return SessionStats(
        agent_id=agent_id,
        window_seconds=window_seconds,
        action_count=action_count,
        cumulative_affected_records=cumulative_affected_records,
        cumulative_irreversible_records=cumulative_irreversible_records,
        tier_distribution=tier_distribution,
        mutation_count=mutation_count,
        distinct_resource_count=distinct_resource_count,
        mean_pairwise_similarity=mean_pairwise_similarity,
        escalation_rate=escalation_rate,
        novelty_rate=novelty_rate,
    )
