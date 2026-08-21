"""GET /v1/version - closes Axis 2 (dependencies) of the staleness check.

Read-only, no auth, no DB, no LLM. Reports actual installed versions via
importlib.metadata at runtime - never read from requirements.txt, so this
proves what a given deployment's RUNNING process actually has, not what
the repo says it should have.
"""

import importlib.metadata
import platform

from fastapi.testclient import TestClient

from app.main import app


def test_version_endpoint_reports_actual_installed_versions(monkeypatch):
    monkeypatch.setenv("GIT_SHA", "abc1234")
    monkeypatch.setenv("BUILD_TIME", "2026-08-21T00:00:00Z")

    with TestClient(app) as c:
        resp = c.get("/v1/version")

    assert resp.status_code == 200
    body = resp.json()

    assert body["git_sha"] == "abc1234"
    assert body["build_time"] == "2026-08-21T00:00:00Z"
    assert body["python_version"] == platform.python_version()

    deps = body["key_dependencies"]
    for name in ("pydantic", "fastapi", "openai", "sqlalchemy"):
        assert deps[name] == importlib.metadata.version(name)


def test_version_endpoint_reports_unknown_when_env_vars_absent(monkeypatch):
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.delenv("BUILD_TIME", raising=False)

    with TestClient(app) as c:
        resp = c.get("/v1/version")

    assert resp.status_code == 200
    body = resp.json()
    assert body["git_sha"] == "unknown"
    assert body["build_time"] == "unknown"
