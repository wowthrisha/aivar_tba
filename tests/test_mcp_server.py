"""FEATURE A tests. Part of the main `pytest -q` suite (D-37: mcp and
fastapi==0.115.0 coexist fine in one venv - see requirements.txt's own
comment on the `mcp[cli]` pin; there was never a real dependency
conflict, only a resolver-ordering mistake in how this was first
installed).

A4: each tool returns a valid response; evaluate_action via MCP returns
the SAME tier as the HTTP endpoint for identical input (the important
test).
"""

import os
import socket
import subprocess
import sys
import time

import httpx
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server_url():
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, os.path.join(REPO_ROOT, "tests", "_mcp_http_test_server.py"), str(port)],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                if httpx.get(f"{url}/livez", timeout=1.0).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        else:
            proc.terminate()
            raise RuntimeError(f"test server on {url} did not become ready")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.fixture
def mcp_module(server_url, monkeypatch):
    monkeypatch.setenv("GOVERNANCE_API_BASE_URL", server_url)
    import importlib

    import app.mcp_server as mcp_server

    importlib.reload(mcp_server)  # re-read GOVERNANCE_API_BASE_URL for this test's server_url
    return mcp_server


_EVAL_BODY = dict(
    agent_id="mcp-test-agent",
    action_type="delete",
    resource="users/1",
    params={"resource_id": 1},
    reversibility="irreversible",
    affected_records=500,
    regulatory="none",
)


async def test_evaluate_action_matches_http_endpoint_for_identical_input(mcp_module, server_url):
    mcp_result = await mcp_module.evaluate_action(**_EVAL_BODY)

    async with httpx.AsyncClient(base_url=server_url) as client:
        http_resp = await client.post("/v1/actions/evaluate", json=_EVAL_BODY)
    http_result = http_resp.json()

    assert mcp_result["tier"] == http_result["tier"]
    assert mcp_result["floor_name"] == http_result["floor_name"]
    assert mcp_result["composite"] == http_result["composite"]


async def test_evaluate_action_never_executes(mcp_module):
    result = await mcp_module.evaluate_action(**_EVAL_BODY)
    assert result["state"] in ("full_review", "confirm", "autonomous")
    assert result["state"] != "executed"


async def test_check_precedent_returns_valid_response(mcp_module):
    result = await mcp_module.check_precedent(
        action_type="delete", resource="users/2", params={"resource_id": 2}
    )
    assert "matches" in result
    assert "summary" in result


async def test_get_review_status_returns_valid_response(mcp_module):
    evaluated = await mcp_module.evaluate_action(**_EVAL_BODY)
    result = await mcp_module.get_review_status(action_id=evaluated["id"])
    assert result["id"] == evaluated["id"]
    assert result["tier"] == evaluated["tier"]


async def test_list_pending_returns_valid_response(mcp_module):
    await mcp_module.evaluate_action(**_EVAL_BODY)
    result = await mcp_module.list_pending(agent_id="mcp-test-agent")
    assert "items" in result
    assert any(item["agent_id"] == "mcp-test-agent" for item in result["items"])
