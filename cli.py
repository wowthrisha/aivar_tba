#!/usr/bin/env python3
"""T-17 CLI. DoD (task-board, complete spec - no detailed T-17 section
exists in the prompt pack): "CLI | pasted session showing risk table +
confirm prompt".

Talks to the real API over HTTP (stdlib urllib only, no new dependency
so a session against this script exercises the actual deployed system,
not a reimplementation of its logic.

OD-1 (CLI output redesign): the risk-table rendering below uses rich for
a five-second-readable verdict - colored banner, WHY block when a safety
floor fired, a score axis with the frozen thresholds marked, per-factor
bars, and a tier-dependent NEXT line. Presentation only: WEIGHTS and the
two thresholds are imported constants from app.risk (never hardcoded or
recomputed here), and every displayed number comes from the API response
or from a value the CLI itself sent in the request (affected_records).
"""

import argparse
import json
import urllib.error
import urllib.request

from rich.console import Console
from rich.panel import Panel

from app.risk.scorer import WEIGHTS
from app.risk.tiers import THRESHOLD_CONFIRM, THRESHOLD_FULL_REVIEW

DEFAULT_BASE_URL = "https://aivartba-production.up.railway.app"

_AXIS_WIDTH = 44

_TIER_STYLE = {
    "AUTONOMOUS": ("green", "\U0001f7e2"),
    "CONFIRM": ("yellow", "\U0001f7e1"),
    "FULL_REVIEW": ("red", "\U0001f534"),
}

_NEXT_TEXT = {
    "AUTONOMOUS": "Executed. No human in the path.",
    "CONFIRM": "User confirmation required.",
}


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


def _pos(value: float, width: int) -> int:
    return min(width - 1, max(0, round(value * (width - 1))))


def _label_line(positions: list[tuple[float, str]], width: int) -> str:
    buf = [" "] * width
    for value, text in positions:
        start = min(_pos(value, width), width - len(text))
        start = max(start, 0)
        for i, ch in enumerate(text):
            buf[start + i] = ch
    return "".join(buf)


def _axis_line(composite: float, width: int) -> str:
    buf = ["─"] * width
    buf[_pos(THRESHOLD_CONFIRM, width)] = "┼"
    buf[_pos(THRESHOLD_FULL_REVIEW, width)] = "┼"
    buf[_pos(composite, width)] = "●"
    return "".join(buf)


def _zone_line(width: int) -> str:
    t1 = _pos(THRESHOLD_CONFIRM, width)
    t2 = _pos(THRESHOLD_FULL_REVIEW, width)
    zones = [(0, t1, "autonomous"), (t1, t2, "confirm"), (t2, width, "full review")]
    buf = [" "] * width
    for start, end, text in zones:
        zone_width = max(end - start, 1)
        centered = text.center(zone_width)[:zone_width]
        for i, ch in enumerate(centered):
            idx = start + i
            if idx < width:
                buf[idx] = ch
    return "".join(buf)


def _factor_bar(raw: float) -> str:
    filled = min(12, max(0, round(raw * 12)))
    return "█" * filled + "░" * (12 - filled)


def render_result(action: dict, affected_records: int) -> None:
    """Renders one evaluate/confirm/etc. response per the OD-1 output
    design spec. Every number comes from `action` (the real API response)
    or `affected_records` (the value the CLI itself sent in the request -
    never part of the response)."""
    console = Console()
    tier = action["tier"]
    color, emoji = _TIER_STYLE.get(tier, ("white", "⚪"))
    verb = action["action_type"].upper()
    summary = f"{verb} · {action['resource']} · {affected_records:,} records · {action['agent_id']}"

    console.print()
    console.print(
        Panel(
            summary,
            title=f"{emoji} {tier.replace('_', ' ')}",
            border_style=color,
            title_align="left",
        )
    )

    floor_name = action.get("floor_name")
    explanation = action.get("explanation") or ""

    if floor_name is not None:
        console.print()
        console.print(f"  [bold]WHY[/bold]       {explanation}")
        console.print("            └─ safety floor overrode the weighted score")

    composite = action.get("composite")
    if composite is not None:
        console.print()
        console.print(
            "  [bold]SCORE[/bold]     "
            + _label_line(
                [(0.0, "0.0"), (THRESHOLD_CONFIRM, f"{THRESHOLD_CONFIRM:.2f}"),
                 (THRESHOLD_FULL_REVIEW, f"{THRESHOLD_FULL_REVIEW:.2f}"), (1.0, "1.0")],
                _AXIS_WIDTH,
            )
        )
        console.print(f"            {_axis_line(composite, _AXIS_WIDTH)}  {composite:.2f}")
        console.print(f"            {_zone_line(_AXIS_WIDTH)}")

        console.print()
        console.print("  [bold]FACTORS[/bold]")
        for key, weight in WEIGHTS.items():
            raw = action.get(f"{key}_score")
            if raw is None:
                continue
            contribution = raw * weight
            label = key.replace("_", " ")
            console.print(
                f"            {label:<14} {raw:.2f}  {_factor_bar(raw)}  "
                f"x{weight:.2f} -> {contribution:.3f}"
            )
        console.print(f"                                              composite  {composite:.3f}")

        if floor_name is None and "Would have been" in explanation:
            idx = explanation.find("Would have been")
            console.print()
            console.print(f"  [bold]IF...[/bold]     {explanation[idx:]}")

    console.print()
    if tier == "FULL_REVIEW":
        next_text = f"Senior review required · queued as #{action['id']}"
    else:
        next_text = _NEXT_TEXT.get(tier, "")
    console.print(f"  [bold]NEXT[/bold]      {next_text}")
    console.print()


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
    render_result(action, args.affected_records)

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
