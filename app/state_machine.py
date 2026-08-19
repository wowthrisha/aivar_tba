"""T-10 state machine. Transitions are the ONLY mutation path.

  PROPOSED -> EVALUATED -> AUTONOMOUS -> EXECUTED
                         -> CONFIRM | FULL_REVIEW
                           -> APPROVED -> EXECUTED
                           -> REJECTED (terminal)
                           -> EXPIRED (terminal)

REJECTED/EXPIRED/EXECUTED are terminal: the spec's diagram target
"TERMINAL" is realized as these states already having no outgoing
transitions, since no endpoint acts on them further.
"""

from enum import Enum


class ActionState(str, Enum):
    PROPOSED = "proposed"
    EVALUATED = "evaluated"
    AUTONOMOUS = "autonomous"
    CONFIRM = "confirm"
    FULL_REVIEW = "full_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTED = "executed"


VALID_TRANSITIONS: dict[ActionState, frozenset[ActionState]] = {
    ActionState.PROPOSED: frozenset({ActionState.EVALUATED}),
    ActionState.EVALUATED: frozenset(
        {ActionState.AUTONOMOUS, ActionState.CONFIRM, ActionState.FULL_REVIEW}
    ),
    ActionState.AUTONOMOUS: frozenset({ActionState.EXECUTED}),
    ActionState.CONFIRM: frozenset(
        {ActionState.APPROVED, ActionState.REJECTED, ActionState.EXPIRED}
    ),
    ActionState.FULL_REVIEW: frozenset(
        {ActionState.APPROVED, ActionState.REJECTED, ActionState.EXPIRED}
    ),
    ActionState.APPROVED: frozenset({ActionState.EXECUTED, ActionState.EXPIRED}),
    ActionState.REJECTED: frozenset(),
    ActionState.EXPIRED: frozenset(),
    ActionState.EXECUTED: frozenset(),
}


class InvalidTransition(Exception):
    pass


def transition(current: ActionState, target: ActionState) -> None:
    if target not in VALID_TRANSITIONS[current]:
        raise InvalidTransition(f"{current.value} -> {target.value} is not a valid transition")
