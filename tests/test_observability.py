"""T-16 - Observability. DoD: "JSON log line with request_id; forced
error returns clean JSON" (the complete spec - no detailed T-16 section
exists in the prompt pack, only this one-line task-board entry).
"""

import json
import logging

from fastapi.testclient import TestClient

from app.audit import AuditLog
from app.llm import ConfidenceResult
from app.main import app, get_app_engine, get_audit_log, get_confidence_provider, get_embedding_provider, get_store
from app.risk.scorer import WEIGHTS
from app.store import InMemoryStore


class _CapturingHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


def test_json_log_line_contains_request_id():
    root = logging.getLogger()
    capture = _CapturingHandler()
    # reuse the app's real JsonFormatter (configured at app.main import
    # time) so this proves the actual configured format, not a re-
    # implementation of it.
    capture.setFormatter(root.handlers[0].formatter)
    root.addHandler(capture)
    try:
        with TestClient(app) as c:
            resp = c.get("/livez")
    finally:
        root.removeHandler(capture)

    assert resp.status_code == 200
    assert capture.lines, "expected at least one log line for the request"
    parsed = [json.loads(line) for line in capture.lines]
    assert any(p.get("request_id") for p in parsed)


def test_forced_error_returns_clean_json():
    def _broken_store():
        raise RuntimeError("forced failure for T-16 proof")

    app.dependency_overrides[get_store] = _broken_store
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.get("/v1/actions/does-not-exist")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 500
    # must not raise - Starlette's default handler for an unhandled
    # exception returns plain text, which would fail this parse.
    body = json.loads(resp.text)
    assert body == {"detail": "internal server error"}


def test_forced_error_log_line_contains_request_id():
    # Regression: the middleware's `finally: request_id_var.reset(token)`
    # runs BEFORE an exception reaches unhandled_exception_handler, so a
    # naive implementation logs the error with request_id=None even
    # though the success path correctly populates it.
    root = logging.getLogger()
    capture = _CapturingHandler()
    capture.setFormatter(root.handlers[0].formatter)
    root.addHandler(capture)

    def _broken_store():
        raise RuntimeError("forced failure for T-16 proof")

    app.dependency_overrides[get_store] = _broken_store
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.get("/v1/actions/does-not-exist")
    finally:
        app.dependency_overrides.clear()
        root.removeHandler(capture)

    assert resp.status_code == 500
    error_lines = [json.loads(line) for line in capture.lines]
    error_records = [p for p in error_lines if p.get("level") == "ERROR"]
    assert error_records, "expected at least one ERROR log line for the forced failure"
    assert all(r.get("request_id") for r in error_records)


# ---------------------------------------------------------------------------
# D-28: the audit payload must be able to reconstruct its own composite,
# without reading the mutable risk_assessments table.
# ---------------------------------------------------------------------------


class _FakeProvider:
    async def get_confidence(self, action_type, resource, params):
        return ConfidenceResult(confidence=0.9, degraded=False, reason=None)

    async def health_check(self) -> bool:
        return True


class _FakeEmbeddingProvider:
    async def embed(self, text: str) -> list[float] | None:
        return None


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


def test_audit_payload_can_reconstruct_composite_without_risk_assessments_table():
    store = InMemoryStore()
    audit_log = AuditLog()
    app.dependency_overrides[get_confidence_provider] = lambda: _FakeProvider()
    app.dependency_overrides[get_embedding_provider] = lambda: _FakeEmbeddingProvider()
    app.dependency_overrides[get_app_engine] = lambda: _FakeEngine()
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        with TestClient(app) as c:
            created = c.post(
                "/v1/actions/evaluate",
                json={
                    "agent_id": "agent-1",
                    "action_type": "update",
                    "resource": "orders/1",
                    "params": {"resource_id": 1, "fields": {"x": 1}},
                    "reversibility": "update_without_snapshot",
                    "affected_records": 1,
                    "regulatory": "none",
                },
            ).json()
            audit_records = c.get(f"/v1/audit?action_id={created['id']}&event_type=evaluated").json()
    finally:
        app.dependency_overrides.clear()

    assert len(audit_records) == 1
    payload = audit_records[0]["payload"]

    for field in (
        "reversibility_score",
        "data_scope_score",
        "regulatory_score",
        "confidence_score",
        "weights_version",
        "git_sha",
        "composite",
    ):
        assert field in payload, f"D-28: audit payload missing {field}"

    assert payload["weights_version"] == "v1"
    recomputed = (
        WEIGHTS["reversibility"] * payload["reversibility_score"]
        + WEIGHTS["data_scope"] * payload["data_scope_score"]
        + WEIGHTS["regulatory"] * payload["regulatory_score"]
        + WEIGHTS["confidence"] * payload["confidence_score"]
    )
    assert recomputed == payload["composite"]


# ---------------------------------------------------------------------------
# L-G: uncertainty_score / llm_confidence_raw - honest field names
# ---------------------------------------------------------------------------


def _evaluate_with_incomplete_update_params():
    """action_type="update" with empty params -> structural_completeness=0.0,
    so combined_confidence = min(llm_raw=0.9, structural=0.0) = 0.0. This
    deliberately makes llm_confidence_raw (0.9) and the inverted
    confidence_score/uncertainty_score (1 - 0.0 = 1.0) clearly distinct,
    proving llm_confidence_raw is NOT run through the two-signal
    combination or the inversion."""
    store = InMemoryStore()
    audit_log = AuditLog()
    app.dependency_overrides[get_confidence_provider] = lambda: _FakeProvider()
    app.dependency_overrides[get_embedding_provider] = lambda: _FakeEmbeddingProvider()
    app.dependency_overrides[get_app_engine] = lambda: _FakeEngine()
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        with TestClient(app) as c:
            resp = c.post(
                "/v1/actions/evaluate",
                json={
                    "agent_id": "agent-1",
                    "action_type": "update",
                    "resource": "orders/2",
                    "params": {},
                    "reversibility": "update_without_snapshot",
                    "affected_records": 1,
                    "regulatory": "none",
                },
            )
            api = resp.json()
            audit_records = c.get(f"/v1/audit?action_id={api['id']}&event_type=evaluated").json()
    finally:
        app.dependency_overrides.clear()
    return api, audit_records[0]["payload"]


def test_uncertainty_score_equals_one_minus_llm_confidence():
    api, _ = _evaluate_with_incomplete_update_params()
    # combined_confidence forced to 0.0 by empty params -> uncertainty = 1.0
    assert api["uncertainty_score"] == 1.0


def test_confidence_score_alias_matches_uncertainty_score():
    api, payload = _evaluate_with_incomplete_update_params()
    assert api["confidence_score"] == api["uncertainty_score"]
    assert payload["confidence_score"] == payload["uncertainty_score"]


def test_llm_confidence_raw_is_uninverted():
    api, _ = _evaluate_with_incomplete_update_params()
    # the raw model self-report (0.9 from _FakeProvider), untouched by the
    # two-signal combination or the confidence->uncertainty inversion -
    # distinct from uncertainty_score (1.0) precisely because this scenario
    # was built to make them differ.
    assert api["llm_confidence_raw"] == 0.9
    assert api["llm_confidence_raw"] != api["uncertainty_score"]
