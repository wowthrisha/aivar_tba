#!/usr/bin/env python3
"""T-17 CLI. DoD (task-board, complete spec - no detailed T-17 section
exists in the prompt pack): "CLI | pasted session showing risk table +
confirm prompt".

Talks to the real API over HTTP (stdlib urllib only, no new dependency)
so a session against this script exercises the actual deployed system,
not a reimplementation of its logic.
"""

import argparse
import json
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://aivartba-production.up.railway.app"


def _post(base_url: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{base_url}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = json.loads(exc.read())
        raise SystemExit(f"HTTP {exc.code}: {detail}") from exc


def _print_risk_table(action: dict) -> None:
    print()
    print("  Risk assessment")
    print("  ---------------")
    print(f"  action_type:   {action['action_type']}")
    print(f"  resource:      {action['resource']}")
    print(f"  composite:     {action['composite']:.2f}")
    print(f"  tier:          {action['tier']}")
    print(f"  explanation:   {action['explanation']}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="PS-9.1 risk-evaluation CLI")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--agent-id", default="cli-agent")
    parser.add_argument("--action-type", required=True)
    parser.add_argument("--resource", required=True)
    parser.add_argument(
        "--reversibility",
        required=True,
        choices=["read", "update_with_snapshot", "update_without_snapshot", "irreversible"],
    )
    parser.add_argument("--affected-records", type=int, required=True)
    parser.add_argument(
        "--regulatory", default="none", choices=["none", "internal", "pii_gdpr", "phi_sox"]
    )
    parser.add_argument("--params", default="{}", help="JSON string")
    args = parser.parse_args()

    body = {
        "agent_id": args.agent_id,
        "action_type": args.action_type,
        "resource": args.resource,
        "params": json.loads(args.params),
        "reversibility": args.reversibility,
        "affected_records": args.affected_records,
        "regulatory": args.regulatory,
    }

    print(f"Evaluating action against {args.base_url} ...")
    action = _post(args.base_url, "/v1/actions/evaluate", body)
    _print_risk_table(action)

    if action["tier"] == "CONFIRM":
        answer = input("This action requires confirmation. Confirm? [y/N] ")
        if answer.strip().lower() == "y":
            result = _post(
                args.base_url,
                f"/v1/actions/{action['id']}/confirm",
                {"params_hash": action["params_hash"]},
            )
            print(f"Confirmed. New state: {result['state']}")
        else:
            print("Not confirmed. Action remains pending.")
    elif action["tier"] == "FULL_REVIEW":
        print(
            "This action requires FULL_REVIEW - a separate reviewer_id must "
            "decide via POST /v1/review-queue/{id}/decision (S-6: an agent "
            "cannot approve its own action, so this single-user CLI session "
            "does not attempt that step)."
        )
    else:
        print("AUTONOMOUS tier - no confirmation needed, already executed.")


if __name__ == "__main__":
    main()
