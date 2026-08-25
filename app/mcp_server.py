"""FEATURE A (intelligence-v6) — MCP server exposing the governance
engine so any MCP-compatible agent can consult the gate BEFORE acting.

Protocol: MCP spec 2026-07-28 (current stable, confirmed via a
documentation subagent against blog.modelcontextprotocol.io and the
python-sdk repo's own docs/whats-new.md - the release candidate locked
2026-05-21, finalized 2026-07-28). SDK: the official `mcp` PyPI package,
v2 (installed: 2.1.1), whose `MCPServer` class is the v1 SDK's
`FastMCP` renamed - `from mcp.server.fastmcp import FastMCP` no longer
exists in v2. Verified directly against the installed package's own
`inspect.signature()` output before writing this file (E-2/E-3), not
assumed from the subagent's report alone.

ARCHITECTURE NOTE: this module does NOT import app.main or anything
under app/risk/ - it is a thin HTTP client over the same REST API any
other caller uses (GOVERNANCE_API_BASE_URL, default the deployed
Railway URL, same convention as cli.py's DEFAULT_BASE_URL). This is a
deliberate, environment-forced design choice, not a stylistic one:
the `mcp` package (both the v2 and legacy v1.x lines, both attempted)
pulls in a starlette major version incompatible with this project's
pinned fastapi==0.115.0 (`Router.__init__() got an unexpected keyword
argument 'on_startup'` - confirmed by direct reproduction, twice, per
S6). Rather than upgrading a pinned, documented dependency mid-pass
(out of scope, and exactly the kind of scope change E-6 says to stop
and report rather than force through), the MCP server runs as a
separate process/venv that talks to the governance API exactly the way
any other MCP-compatible agent would - which also means the
propose/commit boundary and the binding-verdict semantics below are
enforced by the REAL API, not reimplemented here.

Run: see scripts/mcp/requirements.txt for the isolated venv this needs
(mcp[cli] + httpx - deliberately NOT this project's requirements.txt).
README.md documents the Claude Desktop config.
"""

import os
from typing import Any

import httpx
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

BASE_URL = os.environ.get("GOVERNANCE_API_BASE_URL", "https://aivartba-production.up.railway.app")
_TIMEOUT_SECONDS = 30.0

mcp = MCPServer(
    "PS-9.1 Governance Gate",
    instructions=(
        "Consult this server BEFORE an agent takes a real action. evaluate_action "
        "returns a routing tier (AUTONOMOUS / CONFIRM / FULL_REVIEW) that is BINDING: "
        "CONFIRM and FULL_REVIEW mean a human must approve before the action executes. "
        "This server never executes anything - it only proposes/scores."
    ),
)


async def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=_TIMEOUT_SECONDS) as client:
        resp = await client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()


async def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=_TIMEOUT_SECONDS) as client:
        resp = await client.post(path, json=body)
        resp.raise_for_status()
        return resp.json()


@mcp.tool(
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False),
)
async def evaluate_action(
    agent_id: str,
    action_type: str,
    resource: str,
    params: dict[str, Any],
    reversibility: str,
    affected_records: int,
    regulatory: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Propose an action to the governance gate and receive a routing tier
    plus plain-English reasoning. This does NOT execute the action - it
    only scores and records the proposal (the propose/commit boundary is
    absolute; execution is a separate, human-gated step the caller must
    never skip).

    THE VERDICT IS BINDING, NOT ADVISORY: tier=AUTONOMOUS means the
    caller may proceed. tier=CONFIRM or tier=FULL_REVIEW means a human
    must approve before this action may execute - a calling agent MUST
    NOT treat CONFIRM as a suggestion and proceed anyway. reversibility
    is one of "read","update_with_snapshot","update_without_snapshot",
    "irreversible"; regulatory is one of "none","internal","pii_gdpr",
    "phi_sox".
    """
    return await _post(
        "/v1/actions/evaluate",
        {
            "agent_id": agent_id,
            "action_type": action_type,
            "resource": resource,
            "params": params,
            "reversibility": reversibility,
            "affected_records": affected_records,
            "regulatory": regulatory,
            "idempotency_key": idempotency_key,
        },
    )


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True))
async def check_precedent(action_type: str, resource: str, params: dict[str, Any]) -> dict[str, Any]:
    """Look up similar prior actions and their outcomes for a
    hypothetical action WITHOUT proposing it - no action or audit record
    is created by this call, unlike evaluate_action. Use this to gauge
    precedent before deciding whether to propose an action at all. This
    is advisory context, not a verdict - it never returns a tier."""
    return await _post(
        "/v1/precedent/check", {"action_type": action_type, "resource": resource, "params": params}
    )


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True))
async def get_review_status(action_id: str) -> dict[str, Any]:
    """Get the current status of a previously-proposed action by id
    (state, tier, composite, explanation). If the action's tier was
    CONFIRM or FULL_REVIEW and state is not yet "approved" or "executed",
    that verdict is STILL BINDING - it has not been cleared, and the
    action must not be executed."""
    return await _get(f"/v1/actions/{action_id}")


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True))
async def list_pending(agent_id: str) -> dict[str, Any]:
    """List the caller's OWN pending actions - proposed and scored, but
    not yet in a terminal state (executed/rejected/expired). Every item
    with tier CONFIRM or FULL_REVIEW is still BINDING and requires human
    approval before it may execute."""
    items = await _get("/v1/actions/pending", params={"agent_id": agent_id})
    return {"items": items}


if __name__ == "__main__":
    mcp.run(transport="stdio")
