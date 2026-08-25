"""Runs app.main:app on a local port with the SAME fakes
tests/test_api.py uses (no live DB, no live OpenAI) - started by the
MAIN project venv (which has fastapi/sqlalchemy but not `mcp`), as a
subprocess, so tests_mcp/ (run from the ISOLATED .venv-mcp, which has
`mcp` but not fastapi/starlette-compatible) has a real HTTP server to
call. See app/mcp_server.py's module docstring for why these two venvs
cannot be the same process.

Usage: python3 scripts/mcp/_test_server.py <port>
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import uvicorn

from app.audit import AuditLog
from app.llm import ConfidenceResult
from app.main import app, get_app_engine, get_audit_log, get_confidence_provider, get_embedding_provider, get_store
from app.store import InMemoryStore


class _FakeProvider:
    async def get_confidence(self, action_type, resource, params):
        return ConfidenceResult(confidence=0.95, degraded=False, reason=None)

    async def health_check(self) -> bool:
        return True


class _FakeEmbeddingProvider:
    async def embed(self, text: str):
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


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8123
    store = InMemoryStore()
    audit_log = AuditLog()
    app.dependency_overrides[get_confidence_provider] = lambda: _FakeProvider()
    app.dependency_overrides[get_embedding_provider] = lambda: _FakeEmbeddingProvider()
    app.dependency_overrides[get_app_engine] = lambda: _FakeEngine()
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
