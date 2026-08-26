# intelligence-v6 — overnight feature pass, final report

**Updated post-merge-assessment** with four corrections (below) found
during a read-only review before merge. The original version of this
report contained a materially false claim (D-37) that shaped Feature
A's whole architecture; it is corrected here, not silently replaced —
see "Correction 1" for exactly what was wrong and why.

Branch: `feature/intelligence-v6`, stacked on `feature/hardening-v5` (tip
`e41be06`). Pushed to `origin/feature/intelligence-v6`. **Not merged,
not deployed.** `master`, Railway, and Lambda are untouched.

## What shipped

All five features (A–E) shipped, green, each in its own commit; four
follow-up fixes then applied post-assessment (also below, each its own
commit):

| Feature | Commit | What it is |
|---|---|---|
| C — Decision stability | `b7b7ed0` | Sweeps llm_confidence 0.0→1.0 in 0.05 steps, deterministically (no LLM calls); reports `TIER_INVARIANT` or `CONFIDENCE_BOUND` with the actual flip point, additive to the response/audit payload and CLI. |
| B — Session risk | `a0f171e` | `GET /v1/sessions/{agent_id}?window=` read model + shadow-mode session floor. `SESSION_FLOOR_MODE` default corrected to `off` — see Correction 2. |
| D — Reviewer personalisation | `8bbb567` | New `GET /v1/review-queue/{action_id}?reviewer_id=` with similar-actions history, a consistency note, and reviewer stats. |
| A — MCP server | `49f0e22`, corrected `cf84ea8` | `app/mcp_server.py`, 4 tools. Originally shipped as a separate-venv split on a false premise — see Correction 1. Now a normal dependency, tests in the main suite. |
| E — Plain-language explanation | `cb63adf`, test renamed `46df893` | `GET /v1/actions/{id}/explain`, cached, fail-soft. Its "grounding" test renamed to describe what it actually verifies — see Correction 3. |

## Feature B's empirical result: does the fragmentation gap exist?

**Yes.** Full detail in `governance/evidence/intelligence-v6-feature-B1-probe.md`.
A first live probe (5 sequential single-record deletes, distinct fresh
resource names) was confounded by the pre-existing, unrelated
`novelty_unprecedented` escalation firing on each unprecedented name — a
known project pattern (D-27). A second, controlled probe (identical
resource+params, repeated 5×) came back byte-identical on tier,
composite, and floors every time. Across all 12 live calls in both
rounds, `irreversible_bulk` never once appeared in `floors_fired`.
Nothing accumulates today — B2–B4 proceeded per B1's own instruction.

## The MCP spec version and where it was read

**2026-07-28** (current stable — confirmed against
`blog.modelcontextprotocol.io`'s release-candidate post and the
python-sdk repo's `docs/whats-new.md`, read via a documentation subagent
before any code was written, then independently re-verified against the
actually-installed `mcp==2.1.1` package's own `inspect.signature()`
output before writing `app/mcp_server.py` — not taken on the subagent's
word alone).

## Four corrections, found during a read-only merge assessment

A careful pre-merge review (not this session's own original work) asked
four pointed questions that the original version of this report and
Feature A's own code did not hold up under. All four are now fixed,
each its own commit, full suite green after each.

### Correction 1 — D-37: the MCP/fastapi "incompatibility" was false

The original report and `app/mcp_server.py`'s own docstring stated a
dependency incompatibility between `mcp` and this project's pinned
`fastapi==0.115.0`, "confirmed by direct reproduction, twice," and built
an entire `.venv-mcp`/`scripts/mcp/`/`tests_mcp/` split on that premise.
**That premise was false**, and the consequence was real: Feature A's
tests never ran in CI or the main suite (`testpaths=["tests"]` never
saw `tests_mcp/`), so the feature's own credibility claim — that the MCP
tool call and the HTTP endpoint agree — was never actually checked by
anything automated.

Root cause: both "reproductions" ran `pip install "mcp[cli]"` ALONE
first, with no `fastapi` constraint present in that same resolve. Pip's
resolver, unconstrained, picked the newest starlette satisfying only
`mcp`'s own floor (`starlette>=0.27`), landing on `1.6.0` — genuinely
incompatible with `fastapi==0.115.0`'s ceiling (`starlette<0.39.0`).
Installing `mcp[cli]` and `fastapi==0.115.0` **together, in one resolve**,
correctly settles on `starlette==0.38.6` — verified fresh from a clean
venv:
```
$ pip install -r requirements.txt "mcp[cli]"
...
Successfully installed ... fastapi-0.115.0 ... mcp-2.1.1 ... starlette-0.38.6 ...
$ python3 -c "from app.main import app; from mcp.server import MCPServer; MCPServer('t')"
app.main import: OK, routes: 22
MCPServer instantiation: OK
```
Full suite green under that same venv. Logged as **D-37** in
`governance/plan/03-errors-and-fixes.md`: "found by re-verifying a prior
claim rather than carrying it forward."

**Fix** (commit `cf84ea8`): `mcp[cli]==2.1.1` added to `requirements.txt`
as a normal pinned dependency. `tests_mcp/test_mcp_server.py` →
`tests/test_mcp_server.py` (now covered by `testpaths` and
`.github/workflows/tests.yml`, which never installed or ran it before).
`scripts/mcp/_test_server.py` → `tests/_mcp_http_test_server.py`
(leading underscore, not pytest-collected — kept as a genuine real-HTTP
test fixture on its own merits, not a workaround). `.venv-mcp/`,
`scripts/mcp/`, `tests_mcp/` deleted. `app/mcp_server.py`'s architecture
(thin HTTP client, no import of `app.main`) is **unchanged** — that
design was always sound on its own merits (it exercises the real
propose/commit boundary over a real socket); only the false
justification for *needing* a separate venv is corrected. 241 passed, 9
skipped (was 236/9 — 5 previously-uncovered MCP tests now run).

### Correction 2 — `SESSION_FLOOR_MODE` default: `shadow` → `off`

The original default (`"shadow"`) meant every `evaluate()` call paid a
DB round-trip (`list_session_actions`) plus a full session-stats
aggregation with **no flag set at all**. The original justification —
matching `CALIBRATION_MODE`'s own "shadow by default" precedent — didn't
actually hold: unlike calibration, this computation isn't free even when
it changes nothing.

**Fix** (commit `e375fe0`): default is now `"off"`, matching S5's own
wording ("behind an env flag defaulting off"). Shadow mode is strictly
opt-in. Four tests that had relied on the old ambient default now
explicitly `monkeypatch.setenv("SESSION_FLOOR_MODE", "shadow")`; two new
tests assert `session_floor` is `None`/absent with no env var set — the
actual out-of-the-box behavior now.

**Latency delta, measured local, 20 calls per group, real Postgres
(Neon) via `SQLAlchemyStore` — not `InMemoryStore`** (an in-memory
comparison was tried first and showed no meaningful delta, because
`InMemoryStore`'s session query is a pure in-process dict scan, not a
real DB round-trip; that's not what this needed to measure). LLM
confidence and embedding providers faked (real OpenAI latency would
dominate and is irrelevant to what this isolates). One warm-up call
outside the measured groups (Neon's serverless cold-start otherwise adds
~7s to the very first connection — confirmed separately, unrelated to
this fix).

```
off      n=20  p50=11281.90ms  p95=13443.11ms  mean=11508.68ms  min=11110.63ms  max=13443.11ms
shadow   n=20  p50=13663.36ms  p95=15241.79ms  mean=13721.15ms  min=13150.85ms  max=15241.79ms
```

**Read this with the caveat stated plainly, not buried**: the `off`
baseline itself (~11.3s p50 per `evaluate()` call) is far higher than a
healthy pooled-Postgres round-trip should ever be — this environment's
network path to Neon was unusually slow during this run (a separate,
one-off connectivity check earlier in the same session also hit a
~7.4s cold-start and a handful of `ConnectionDoesNotExistError` drops
requiring retries). These absolute numbers are **not representative of
a production deployment** (e.g. Railway co-located with Neon would see
something far smaller). What IS a real, repeatable signal from this
same run, isolating the variable actually under test: **shadow mode
added ~2.4s at p50 and ~1.8s at p95 on top of that baseline — a
consistent ~15-20% relative increase**, from one extra DB round-trip
(`list_session_actions`) plus a session-stats aggregation, every single
`evaluate()` call, unconditionally, when the flag was on. That relative
finding is exactly what justified making it opt-in (this fix), even
though the absolute millisecond figures above should not be read as
"what shadow mode costs in production" — only as "shadow mode reliably
costs *something* non-trivial, on top of whatever the DB round-trip
baseline already is, wherever this runs."

### Correction 3 — Feature E's "grounding" test renamed to match what it tests

`test_explain_endpoint_grounds_only_in_the_structured_record` →
`test_explain_receives_only_the_structured_record` (commit `46df893`).
It verifies **input scope** — that `action_type`/`resource`/`params`
never cross the endpoint→provider boundary — using
`_FakeExplanationProvider`, which is not an LLM and has no capacity to
invent anything. It does not, and structurally cannot within this
project's own no-live-LLM-calls testing convention, verify **grounding**
as a model behavior: whether a real model, given the permitted
structured record, actually refrains from inventing facts beyond it. No
test in this suite inspects the real prompt text
`OpenAIExplanationProvider._call()` sends to OpenAI either.

Logged as **L-K** in `governance/plan/03-errors-and-fixes.md`'s LEFT OUT
section (committed alongside D-37): production approach would be a
recorded-response fixture — a real, one-time-captured OpenAI response,
replayed deterministically — built from a deliberately incomplete
structured record, asserting the recorded output invents nothing absent
from it.

### Correction 4 — tier-invariance gate re-run after all three fixes above

Full grid sweep (1408 points: every `Reversibility` × 11
`affected_records` values spanning every band edge × every `Regulatory`
× 8 `llm_confidence` values spanning the 0.5 floor boundary), all flags
at their (now-corrected) defaults — `SESSION_FLOOR_MODE=off`,
`CALIBRATION_MODE=shadow`:
```
clean-v4: 1408 rows | HEAD: 1408 rows
DIFFERENCES: 0
```

## What needs you

- **Nothing is deployed.** Railway/Lambda still run `clean-v4` exactly
  as before this pass, per S2.
- **Merge decision**: the branch is safe to merge as code (see below).
  Feature A no longer needs a separate deployment/venv decision — it's
  one deployable now (Correction 1) — but someone still needs to
  actually run `app/mcp_server.py` as its own process if you want it
  live, pointed at whichever deployment via `GOVERNANCE_API_BASE_URL`.
- **B2's known approximation**: `cumulative_affected_records` /
  `cumulative_irreversible_records` are derived from `data_scope_score`'s
  band lower-bound (a documented undercount), not an exact persisted
  integer — no live-DB migration was made for this shadow-only feature.
  If B3's session floor is ever promoted out of shadow mode, this
  approximation should be revisited first.
- **E's cache is in-process only** — does not survive a restart or a
  second worker process.
- **E's grounding is genuinely untested** (Correction 3/L-K) — this is
  the one place in the whole pass where a test's name overstated its
  own coverage. Worth knowing before treating Feature E's output as more
  verified than it is.

## Is the branch safe to merge, and what to verify after

Safe to merge as code: full suite green (243 passed, 9 pre-existing
skips, 0 failed), the four frozen criterion tests are byte-identical to
their original commit (`git diff a573663 -- tests/test_routing.py` is
empty), every pre-existing file under `app/risk/` is at **zero diff**
against the branch base (`scorer.py`, `floors.py`, `tiers.py`,
`router.py`, `decision.py` — confirmed individually), and the
tier-invariance sweep shows **zero differences** against `clean-v4`
(Correction 4, above) with every flag at its now-corrected default.

After merging, verify:
1. `pytest -q` still green on whatever machine/CI runs it — this now
   includes `tests/test_mcp_server.py`, previously never covered
   anywhere (Correction 1); confirm CI actually picks it up.
2. The two new read-only endpoints (`GET /v1/actions/pending`,
   `POST /v1/precedent/check`) and the new singular
   `GET /v1/review-queue/{id}` don't collide with anything added to
   `master` independently since `hardening-v5`'s tip.
3. If Feature A goes live, confirm `GOVERNANCE_API_BASE_URL` is pointed
   at the correct deployment before distributing the Claude Desktop
   config in the README.

This report is not a claim that the branch is free of every possible
defect — only what was specifically verified above, verified the way
described. The corrections above exist precisely because the *first*
version of this claim wasn't good enough; treat this one the same way —
re-verify before trusting it downstream, especially D-37, which was
wrong with a fully plausible-looking traceback attached.
