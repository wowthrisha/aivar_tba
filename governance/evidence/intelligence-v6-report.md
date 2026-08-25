# intelligence-v6 — overnight feature pass, final report

Branch: `feature/intelligence-v6`, stacked on `feature/hardening-v5` (tip
`e41be06`). Pushed to `origin/feature/intelligence-v6`. **Not merged,
not deployed.** `master`, Railway, and Lambda are untouched — verified
(`git log master` shows no new commits; `git diff e41be06 -- infra/` is
empty; no deploy script was invoked this session).

## What shipped

All five features (A–E) shipped, green, each in its own commit:

| Feature | Commit | What it is |
|---|---|---|
| C — Decision stability | `b7b7ed0` | Sweeps llm_confidence 0.0→1.0 in 0.05 steps, deterministically (no LLM calls); reports `TIER_INVARIANT` or `CONFIDENCE_BOUND` with the actual flip point, additive to the response/audit payload and CLI. |
| B — Session risk | `a0f171e` | `GET /v1/sessions/{agent_id}?window=` read model + shadow-mode session floor (`SESSION_FLOOR_MODE`, default `shadow`). |
| D — Reviewer personalisation | `8bbb567` | New `GET /v1/review-queue/{action_id}?reviewer_id=` with similar-actions history, a consistency note, and reviewer stats. |
| A — MCP server | `49f0e22` | `app/mcp_server.py`, 4 tools, isolated venv (dependency conflict — see below), `tests_mcp/`. |
| E — Plain-language explanation | `cb63adf` | `GET /v1/actions/{id}/explain`, strictly grounded, cached, fail-soft. |

None were time-boxed out or abandoned under S6. The MCP dependency
conflict (below) needed a genuine architecture change, not a retry, so
S6 doesn't strictly apply to it — but it's the one place this pass hit
real friction and is worth reading closely before you rely on Feature A.

## Feature B's empirical result: does the fragmentation gap exist?

**Yes.** Full detail and raw responses in
`governance/evidence/intelligence-v6-feature-B1-probe.md`. Summary: a
first live probe (5 sequential single-record deletes, distinct fresh
resource names) was confounded by the pre-existing, unrelated
`novelty_unprecedented` escalation firing on each unprecedented name — a
known project pattern (D-27). A second, controlled probe (5 calls,
*identical* resource+params, repeated) came back byte-identical on tier,
composite, and floors every single time. Across all 12 live calls in
both rounds, `irreversible_bulk` never once appeared in `floors_fired`.
Nothing accumulates today — B2–B4 proceeded per B1's own instruction.

## The MCP spec version and where it was read

**2026-07-28** (current stable, not a release candidate — the RC locked
2026-05-21 per `blog.modelcontextprotocol.io`'s own release-candidate
post; that post plus the `modelcontextprotocol/python-sdk` repo's
`docs/whats-new.md`, `docs/servers/*.md`, and `docs_src/*/*.py` were
read via a documentation subagent before any code was written (A1).
The subagent's claims about the installed `mcp` package's actual API
(`MCPServer`, `.tool()`, `ToolAnnotations`) were then independently
re-verified against the real installed package (`mcp==2.1.1`) via
`inspect.signature()` before writing `app/mcp_server.py` — not taken on
the subagent's word alone (E-2/E-3).

## The MCP dependency conflict — read this before merging

Installing `mcp` (both its v2 line, `mcp==2.1.1`, and the legacy
`mcp>=1.28,<2` line — both tried) pulls in a starlette version
incompatible with this project's pinned `fastapi==0.115.0`. Confirmed
by direct reproduction, twice: `from app.main import app` fails with
`TypeError: Router.__init__() got an unexpected keyword argument
'on_startup'` whenever `mcp` and this project's fastapi/starlette are
installed in the same Python environment.

**A mistake happened here and was corrected within the same turn**: the
first `pip install "mcp[cli]"` was run into the shared global/base conda
environment (not a project-isolated venv), which broke `app.main`
imports system-wide until fixed (`pip install starlette==0.38.6`, then
`pip uninstall mcp`, then a full green `pytest -q` re-run confirmed
restoration before anything else proceeded). All subsequent MCP work
used a dedicated `.venv-mcp/` that never touches the project's own
dependency tree.

Per S6 (two-attempt rule), a third attempt at making them coexist in
one process was not made. Instead, `app/mcp_server.py` runs as a thin
HTTP client over the same REST API any other MCP-compatible agent would
use — it does not import `app.main` or anything under `app/risk/`. This
is architecturally sound (arguably *more* honest than an in-process
integration, since it proves the tool boundary works the way an
external caller would actually see it), but it does mean:

- Running the MCP server requires a **second, separate venv**
  (`scripts/mcp/requirements.txt`), not just `pip install -r
  requirements.txt`.
- `tests_mcp/` is **not part of the main `pytest -q` suite** — the main
  venv structurally cannot have `mcp` installed alongside fastapi 0.115.
  It's a separate, documented test run (see README §14).

## What needs you

- **Nothing is deployed.** Railway/Lambda still run `clean-v4` exactly
  as before this pass, per S2.
- **Merge decision**: the branch is safe to merge as code (see below),
  but Feature A's two-venv requirement is a real operational choice —
  worth deciding deliberately (upgrade fastapi/starlette project-wide to
  get `mcp` in-process someday? keep it a satellite process forever?)
  rather than something this pass should have decided unilaterally.
- **If you want Feature A live**, someone needs to actually run
  `app/mcp_server.py` somewhere (it's not part of the Railway/Lambda
  deploy — it's a separate process pointing `GOVERNANCE_API_BASE_URL`
  at whichever deployment you want it to gate).
- **B2's known approximation**: `cumulative_affected_records` /
  `cumulative_irreversible_records` are derived from `data_scope_score`'s
  band lower-bound (a documented undercount), not an exact persisted
  integer — no live-DB migration was made for this shadow-only feature.
  If B3's session floor is ever promoted out of shadow mode, this
  approximation should be revisited first.
- **E's cache is in-process only** — does not survive a restart or a
  second worker process. Fine for a presentation-only feature today;
  worth persisting if this ever needs to be reliably identical across
  a multi-worker deployment.

## Is the branch safe to merge, and what to verify after

Safe to merge as code: full suite green (236 passed, 9 pre-existing
skips, 0 failed), the four frozen criterion tests are byte-identical to
their original commit (`git diff a573663 -- tests/test_routing.py` is
empty), every pre-existing file under `app/risk/` is at **zero diff**
against the branch base (`scorer.py`, `floors.py`, `tiers.py`,
`router.py`, `decision.py` — confirmed individually, not just via
`--stat`), and the tier-invariance sweep (1408-point grid: every
`Reversibility` × 11 `affected_records` values spanning every band edge
× every `Regulatory` × 8 `llm_confidence` values spanning the 0.5 floor
boundary) shows **zero differences** against `clean-v4` with every new
flag at its default (`SESSION_FLOOR_MODE=shadow`,
`CALIBRATION_MODE=shadow` — both non-applying by construction).

After merging, verify:
1. `pytest -q` still green on whatever machine/CI runs it (this pass's
   suite ran locally against the base conda environment — never against
   a clean container build).
2. The two new read-only endpoints (`GET /v1/actions/pending`,
   `POST /v1/precedent/check`) and the new singular
   `GET /v1/review-queue/{id}` don't collide with anything added to
   `master` independently since `hardening-v5`'s tip.
3. If Feature A goes live, confirm `GOVERNANCE_API_BASE_URL` is pointed
   at the correct deployment before distributing the Claude Desktop
   config in the README.

This report is not a claim that the branch is free of every possible
defect — only what was specifically verified above, verified the way
described. Three explicit, documented approximations exist (B2's
affected-records undercount, B2/D's novelty-rate undercount from
persisting only the top floor rather than the full `floors_fired`, and
E's in-process-only cache) — none of them touch a real routing decision
today, all shadow or presentation-only, all called out rather than
hidden.
