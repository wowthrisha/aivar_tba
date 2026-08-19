"""T-08 — the four criterion tests. THESE ARE THE ASSESSMENT SUCCESS
CRITERIA. Once written, READ-ONLY — never edit them to make code pass.

Source: PS-9-1-Prompt-Pack-v1.0.md, T-08.
"""

from app.risk.router import route_action
from app.risk.scorer import Regulatory, Reversibility
from app.risk.tiers import Tier


def test_bulk_delete_routes_to_review():
    """bulk database delete -> FULL_REVIEW"""
    result = route_action(Reversibility.IRREVERSIBLE, 500, Regulatory.NONE, 0.95)
    assert result.tier == Tier.FULL_REVIEW


def test_single_update_routes_to_confirm():
    """single record update -> CONFIRM"""
    result = route_action(
        Reversibility.UPDATE_WITHOUT_SNAPSHOT, 1, Regulatory.NONE, 0.9
    )
    assert result.tier == Tier.CONFIRM


def test_read_only_routes_autonomous():
    """read-only query -> AUTONOMOUS"""
    result = route_action(Reversibility.READ, 50, Regulatory.NONE, 0.95)
    assert result.tier == Tier.AUTONOMOUS


def test_audit_breakdown_is_human_readable():
    """the breakdown is accurate AND human-readable"""
    result = route_action(Reversibility.IRREVERSIBLE, 500, Regulatory.NONE, 0.95)
    # accurate: the exact composite and tier appear in the string
    assert f"{result.composite:.2f}" in result.explanation
    assert result.tier.name in result.explanation
    # human-readable: the triggering reason is named, not just a string
    assert "irreversible" in result.explanation
    assert "500 records" in result.explanation
