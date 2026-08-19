"""T-16 - Observability. DoD: "JSON log line with request_id; forced
error returns clean JSON" (the complete spec - no detailed T-16 section
exists in the prompt pack, only this one-line task-board entry).
"""

import json
import logging

from fastapi.testclient import TestClient

from app.main import app, get_store


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
