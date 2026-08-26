"""Runs app.main:app on a local port with the SAME fakes tests/test_api.py
uses (no live DB, no live OpenAI), as a subprocess of the same
interpreter running the tests - so tests/test_mcp_server.py has a real
HTTP server to call. Named with a leading underscore, not `test_*`, so
pytest's default collection does not pick this up as a test module.

D-37: this subprocess split remains for a good reason even now that mcp
and fastapi coexist in one venv (see requirements.txt's own comment) -
it's a REAL local HTTP server, exercising the exact propose/commit
boundary a real MCP client would see over a real socket, not an
in-process ASGI shortcut. It is NOT a workaround for a dependency
conflict; that conflict never existed.

Usage: python3 tests/_mcp_http_test_server.py <port>
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
