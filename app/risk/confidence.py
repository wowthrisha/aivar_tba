"""T-09a — two-signal confidence. Pure Python, no I/O.

LLM self-reported confidence is poorly calibrated: models are fluent about
being wrong. We take the MINIMUM of two independent signals so a model
cannot talk its way to a high confidence score when the proposed action
itself is structurally incomplete - the structural signal is derived from
plain presence/membership checks on the proposal, not from the model, so
it cannot be talked into being high.
"""

from typing import Any

# Minimal known-action catalogue: action_type -> required param names.
# Deliberately small; extend as real action types are added in later tasks.
ACTION_CATALOGUE: dict[str, frozenset[str]] = {
    "read": frozenset(),
    "update": frozenset({"resource_id", "fields"}),
    "delete": frozenset({"resource_id"}),
    "send": frozenset({"recipient", "payload"}),
    "pay": frozenset({"amount", "recipient"}),
}


def structural_completeness(action_type: str, params: dict[str, Any]) -> float:
    """1.0 if action_type is in the known catalogue AND all its required
    params are present (first-pass schema-validation proxy); 0.0 otherwise.
    """
    required = ACTION_CATALOGUE.get(action_type)
    if required is None:
        return 0.0
    if not required.issubset(params.keys()):
        return 0.0
    return 1.0


def two_signal_confidence(self_reported: float, structural: float) -> float:
    return min(self_reported, structural)
