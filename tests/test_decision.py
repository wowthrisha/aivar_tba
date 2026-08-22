"""L-B fix: app/risk/decision.py::compose_final_decision() becomes the
sole tier authority. route_action() (app/risk/router.py, untouched) stays
the base composition; compose_final_decision() wraps it with calibration
and novelty, so app/main.py can call ONE function and use its result
verbatim - no post-hoc tier adjustment anywhere in main.py.
"""

import itertools

from fastapi.testclient import TestClient

from app.audit import AuditLog
from app.embeddings import escalate_one_tier
from app.llm import ConfidenceResult
from app.main import app, get_app_engine, get_audit_log, get_confidence_provider, get_embedding_provider, get_store
from app.risk.decision import compose_final_decision
from app.risk.floors import highest_priority_floor, sort_by_priority
from app.risk.router import route_action
from app.risk.scorer import Regulatory, Reversibility
from app.risk.tiers import Tier
from app.store import InMemoryStore

_GRID_AFFECTED_RECORDS = (0, 1, 10, 50, 99, 100, 500, 1000, 10_000)
_GRID_CONFIDENCE = (0.0, 0.1, 0.3, 0.49, 0.5, 0.51, 0.7, 0.9, 1.0)


def test_compose_final_decision_is_sole_tier_authority():
    # off mode: must be byte-identical to route_action() alone.
    for reversibility, affected_records, regulatory, confidence in (
        (Reversibility.READ, 1, Regulatory.NONE, 0.9),
        (Reversibility.UPDATE_WITHOUT_SNAPSHOT, 1, Regulatory.NONE, 0.0),
        (Reversibility.IRREVERSIBLE, 500, Regulatory.NONE, 0.95),
        (Reversibility.IRREVERSIBLE, 500, Regulatory.PHI_SOX, 0.0),
    ):
        routing = route_action(reversibility, affected_records, regulatory, confidence)
        decision = compose_final_decision(
            reversibility, affected_records, regulatory, confidence,
            calibration_mode="off", calibration_adjustment=0.0, calibration_degraded=False,
            novelty_should_escalate=False, novelty_prior_count=0,
        )
        assert decision.tier == routing.tier
        assert decision.floor_name == routing.floor_name
        assert decision.floors_fired == routing.floors_fired
        assert decision.composite == routing.composite
        assert decision.calibration_applied is False

    # enforce mode: composite shifts, floors re-evaluated on raw inputs,
    # tier = max(new weighted tier, floor tier) - same formula main.py
    # used inline before this refactor.
    decision = compose_final_decision(
        Reversibility.UPDATE_WITHOUT_SNAPSHOT, 1, Regulatory.NONE, 0.9,
        calibration_mode="enforce", calibration_adjustment=-0.10, calibration_degraded=False,
        novelty_should_escalate=False, novelty_prior_count=0,
    )
    assert decision.calibration_applied is True
    assert decision.base_composite is not None
    assert decision.effective_composite == decision.base_composite - 0.10

    # novelty: escalates exactly one tier on top of whatever calibration left it at.
    base_decision = compose_final_decision(
        Reversibility.READ, 1, Regulatory.NONE, 0.9,
        calibration_mode="off", calibration_adjustment=0.0, calibration_degraded=False,
        novelty_should_escalate=False, novelty_prior_count=0,
    )
    escalated_decision = compose_final_decision(
        Reversibility.READ, 1, Regulatory.NONE, 0.9,
        calibration_mode="off", calibration_adjustment=0.0, calibration_degraded=False,
        novelty_should_escalate=True, novelty_prior_count=25,
    )
    assert escalated_decision.tier == escalate_one_tier(base_decision.tier)
    assert "novelty_unprecedented" in escalated_decision.floors_fired
    assert escalated_decision.floor_name == highest_priority_floor(escalated_decision.floors_fired)


def test_refactor_preserves_every_tier():
    """THE GATE. route_action() is untouched (confirmed by a separate
    empty git diff check) - it IS the pre-refactor ground truth.
    compose_final_decision() in off mode, no novelty, must reduce to
    EXACTLY route_action()'s output for every input in the full grid.
    If any differ, stop and revert - do not attempt a second variant."""
    checked = 0
    for reversibility, affected_records, regulatory, confidence in itertools.product(
        Reversibility, _GRID_AFFECTED_RECORDS, Regulatory, _GRID_CONFIDENCE
    ):
        routing = route_action(reversibility, affected_records, regulatory, confidence)
        decision = compose_final_decision(
            reversibility, affected_records, regulatory, confidence,
            calibration_mode="off", calibration_adjustment=0.0, calibration_degraded=False,
            novelty_should_escalate=False, novelty_prior_count=0,
        )
        assert decision.tier == routing.tier, (
            f"TIER CHANGED: inputs={reversibility, affected_records, regulatory, confidence} "
            f"before={routing.tier.name} after={decision.tier.name}"
        )
        assert decision.floor_name == routing.floor_name, (
            f"FLOOR_NAME CHANGED: inputs={reversibility, affected_records, regulatory, confidence} "
            f"before={routing.floor_name} after={decision.floor_name}"
        )
        assert decision.floors_fired == routing.floors_fired, (
            f"FLOORS_FIRED CHANGED: inputs={reversibility, affected_records, regulatory, confidence} "
            f"before={routing.floors_fired} after={decision.floors_fired}"
        )
        checked += 1

    assert checked == len(Reversibility) * len(_GRID_AFFECTED_RECORDS) * len(Regulatory) * len(_GRID_CONFIDENCE)


# ---------- endpoint-level: main.py must use compose_final_decision()'s result verbatim ----------


class _FakeProvider:
    def __init__(self, confidence: float):
        self._confidence = confidence

    async def get_confidence(self, action_type, resource, params):
        return ConfidenceResult(confidence=self._confidence, degraded=False, reason=None)

    async def health_check(self) -> bool:
        return True


class _FakeEmbeddingProvider:
    async def embed(self, text: str) -> list[float] | None:
        return None  # keeps novelty out - this test is about calibration/base composition only


class _FakeConnection:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def exec_driver_sql(self, sql):
        return None


class _FakeEngine:
    def connect(self):
        return _FakeConnection()


def _make_client(confidence: float):
    store = InMemoryStore()
    audit_log = AuditLog()
    app.dependency_overrides[get_confidence_provider] = lambda: _FakeProvider(confidence)
    app.dependency_overrides[get_embedding_provider] = lambda: _FakeEmbeddingProvider()
    app.dependency_overrides[get_app_engine] = lambda: _FakeEngine()
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    return TestClient(app)


def test_main_py_performs_no_tier_adjustment(monkeypatch):
    monkeypatch.setenv("CALIBRATION_MODE", "off")
    scenarios = [
        ({"agent_id": "a", "action_type": "read", "resource": "r1", "params": {},
          "reversibility": "read", "affected_records": 1, "regulatory": "none"}, 0.9,
         Reversibility.READ, 1, Regulatory.NONE),
        ({"agent_id": "a", "action_type": "update", "resource": "r2", "params": {},
          "reversibility": "update_without_snapshot", "affected_records": 1, "regulatory": "none"}, 0.0,
         Reversibility.UPDATE_WITHOUT_SNAPSHOT, 1, Regulatory.NONE),
        ({"agent_id": "a", "action_type": "delete", "resource": "r3", "params": {"resource_id": 1},
          "reversibility": "irreversible", "affected_records": 5000, "regulatory": "none"}, 0.95,
         Reversibility.IRREVERSIBLE, 5000, Regulatory.NONE),
    ]
    for body, confidence, reversibility, affected_records, regulatory in scenarios:
        client = _make_client(confidence)
        try:
            resp = client.post("/v1/actions/evaluate", json=body)
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 201
        api = resp.json()
        expected = compose_final_decision(
            reversibility, affected_records, regulatory, confidence,
            calibration_mode="off", calibration_adjustment=0.0, calibration_degraded=False,
            novelty_should_escalate=False, novelty_prior_count=0,
        )
        assert api["tier"] == expected.tier.name
        assert api["floor_name"] == expected.floor_name
        assert api["floors_fired"] == list(expected.floors_fired)
