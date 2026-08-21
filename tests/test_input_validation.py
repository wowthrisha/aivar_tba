"""Fix pass for the final-defect-sweep's 4 confirmed BLOCKING fuzz crashes.

NaN/Infinity in affected_records and 500-deep-nested params were traced to
two DIFFERENT root causes (see the approved plan / governance/plan/03-errors-and-fixes.md
for the investigation): NaN/Infinity crashes at FastAPI's own
RequestValidationError-rendering step (Starlette's JSONResponse enforces
allow_nan=False, and Pydantic's error object always echoes the raw
offending input), not at schema validation - so those two tests exercise
the full HTTP endpoint, not just the schema, to prove the actual crash is
fixed. The params tests (null byte / control char / size / depth) are
schema-level - Pydantic parses deep nesting and null bytes fine on its
own, so a request-time validator rejecting them before the handler ever
runs is sufficient and is tested directly against the schema.
"""

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.audit import AuditLog
from app.llm import ConfidenceResult
from app.main import app, get_app_engine, get_audit_log, get_confidence_provider, get_embedding_provider, get_store
from app.schemas import EvaluateRequest
from app.store import InMemoryStore

_BASE_FIELDS = {
    "agent_id": "fuzz-agent",
    "action_type": "read",
    "resource": "customers/1",
    "reversibility": "read",
    "affected_records": 1,
    "regulatory": "none",
}


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


def _make_client():
    app.dependency_overrides[get_confidence_provider] = lambda: _FakeProvider()
    app.dependency_overrides[get_embedding_provider] = lambda: _FakeEmbeddingProvider()
    app.dependency_overrides[get_app_engine] = lambda: _FakeEngine()
    app.dependency_overrides[get_store] = lambda: InMemoryStore()
    app.dependency_overrides[get_audit_log] = lambda: AuditLog()
    return TestClient(app, raise_server_exceptions=False)


# ---------- HTTP-level: proves the RequestValidationError handler fix, not just the schema ----------


def test_affected_records_rejects_nan_with_clean_422_not_500():
    client = _make_client()
    try:
        raw = json.dumps(_BASE_FIELDS | {"params": {}}).replace('"affected_records": 1', '"affected_records": NaN')
        resp = client.post(
            "/v1/actions/evaluate", content=raw.encode(), headers={"content-type": "application/json"}
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 422
    body = resp.json()  # must parse cleanly - a 500 would return {"detail": "internal server error"}
    assert "detail" in body


def test_affected_records_rejects_infinity_with_clean_422_not_500():
    client = _make_client()
    try:
        raw = json.dumps(_BASE_FIELDS | {"params": {}}).replace(
            '"affected_records": 1', '"affected_records": Infinity'
        )
        resp = client.post(
            "/v1/actions/evaluate", content=raw.encode(), headers={"content-type": "application/json"}
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 422
    body = resp.json()
    assert "detail" in body


# ---------- schema-level: params / string-field bounds ----------


def test_params_rejects_null_byte():
    with pytest.raises(ValidationError):
        EvaluateRequest.model_validate(_BASE_FIELDS | {"params": {"x": "abc\x00def"}})


def test_params_rejects_control_character():
    with pytest.raises(ValidationError):
        EvaluateRequest.model_validate(_BASE_FIELDS | {"params": {"x": "abc\x07def"}})


def test_params_allows_tab_newline_and_carriage_return():
    # the control-character rejection must not be so broad it blocks
    # ordinary whitespace agents legitimately send.
    req = EvaluateRequest.model_validate(_BASE_FIELDS | {"params": {"x": "line1\nline2\ttabbed\r"}})
    assert req.params["x"] == "line1\nline2\ttabbed\r"


def test_params_rejects_oversized_payload():
    with pytest.raises(ValidationError):
        EvaluateRequest.model_validate(_BASE_FIELDS | {"params": {"x": "a" * 100_000}})


def test_params_rejects_excessive_nesting_depth():
    nested: dict = {}
    cur = nested
    for _ in range(499):
        cur["n"] = {}
        cur = cur["n"]
    cur["n"] = 1

    with pytest.raises(ValidationError):
        EvaluateRequest.model_validate(_BASE_FIELDS | {"params": nested})


def test_params_allows_reasonable_nesting_depth():
    nested: dict = {}
    cur = nested
    for _ in range(4):
        cur["n"] = {}
        cur = cur["n"]
    cur["n"] = 1

    req = EvaluateRequest.model_validate(_BASE_FIELDS | {"params": nested})
    assert req.params == nested


def test_string_fields_reject_null_bytes():
    for field in ("agent_id", "action_type", "resource", "idempotency_key"):
        with pytest.raises(ValidationError):
            EvaluateRequest.model_validate(_BASE_FIELDS | {"params": {}, field: "abc\x00def"})


def test_string_fields_cap_length():
    with pytest.raises(ValidationError):
        EvaluateRequest.model_validate(_BASE_FIELDS | {"params": {}, "resource": "a" * 10_001})
