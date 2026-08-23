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
    assert body["git_sha_short"] == "abc1234"  # already <=7 chars, unchanged
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
    assert body["git_sha_short"] == "unknown"
    assert body["build_time"] == "unknown"


def test_version_endpoint_derives_short_sha_from_full_sha(monkeypatch):
    # D-33/parity fix: deployments passing the full 40-char SHA (Railway,
    # via infra/railway/deploy-railway.sh) and ones passing the 7-char
    # short form (previously AWS, via infra/aws/deploy-lambda.sh) must
    # produce the SAME git_sha_short for the same commit, so a parity
    # check can compare one normalized field regardless of what either
    # deploy script passed.
    monkeypatch.setenv("GIT_SHA", "8797d92d5643489e602ca0aed60bf886c8c4e494")
    monkeypatch.setenv("BUILD_TIME", "2026-08-23T00:00:00Z")

    with TestClient(app) as c:
        resp = c.get("/v1/version")

    body = resp.json()
    assert body["git_sha"] == "8797d92d5643489e602ca0aed60bf886c8c4e494"
    assert body["git_sha_short"] == "8797d92"
