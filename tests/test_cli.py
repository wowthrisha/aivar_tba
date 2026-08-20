"""OD-1 — CLI output redesign. render_result() is a pure presentation
function: given an action dict (shaped like a real ActionResponse) and
the affected_records the CLI itself sent, it must render without error
and the tier/composite must be visible in the output. The three sample
actions below are built from the REAL score_action()/route_action()
functions (not hand-picked numbers) so this also incidentally exercises
one AUTONOMOUS/CONFIRM/FULL_REVIEW example each.
"""

import pytest

from app.risk.router import route_action
from app.risk.scorer import Regulatory, Reversibility, score_action

import cli

_CASES = [
    # (reversibility, affected_records, regulatory, llm_confidence)
    (Reversibility.READ, 1, Regulatory.NONE, 0.95),
    (Reversibility.UPDATE_WITH_SNAPSHOT, 1, Regulatory.NONE, 0.9),
    (Reversibility.IRREVERSIBLE, 5000, Regulatory.NONE, 0.8),
]


def _make_action(reversibility, affected_records, regulatory, confidence) -> dict:
    weighted = score_action(reversibility, affected_records, regulatory, confidence)
    routing = route_action(reversibility, affected_records, regulatory, confidence)
    return {
        "id": "00000000-0000-0000-0000-000000000000",
        "agent_id": "agent-07",
        "action_type": "delete",
        "resource": "customers",
        "params": {},
        "params_hash": "0" * 64,
        "state": routing.tier.name.lower(),
        "composite": routing.composite,
        "tier": routing.tier.name,
        "explanation": routing.explanation,
        "reversibility_score": weighted.reversibility,
        "data_scope_score": weighted.data_scope,
        "regulatory_score": weighted.regulatory,
        "confidence_score": weighted.confidence,
        "floor_name": routing.floor_name,
        "precedent": None,
        "created_at": "2026-08-20T00:00:00Z",
    }


@pytest.mark.parametrize("reversibility,affected_records,regulatory,confidence", _CASES)
def test_cli_renders_all_three_tiers_without_error(
    reversibility, affected_records, regulatory, confidence, capsys
):
    action = _make_action(reversibility, affected_records, regulatory, confidence)
    cli.render_result(action, affected_records)
    out = capsys.readouterr().out
    assert out.strip() != ""


def test_cli_output_contains_composite_and_tier(capsys):
    reversibility, affected_records, regulatory, confidence = _CASES[2]  # FULL_REVIEW case
    action = _make_action(reversibility, affected_records, regulatory, confidence)
    cli.render_result(action, affected_records)
    out = capsys.readouterr().out
    assert action["tier"] in out
    assert f"{action['composite']:.2f}" in out
