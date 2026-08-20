# PS-9.1 — Graduated Autonomy Engine

## Traceability

| Success criterion | Proven by | Endpoint | Video |
|---|---|---|---|
| Bulk delete → full review | `test_bulk_delete_routes_to_review` | `POST /v1/actions/evaluate` | 0:00 |
| Single update → confirmation | `test_single_update_routes_to_confirm` | `POST /v1/actions/evaluate` | 0:00 |
| Read-only → autonomous | `test_read_only_routes_autonomous` | `POST /v1/actions/evaluate` | 0:00 |
| Breakdown accurate & readable | `test_audit_breakdown_is_human_readable` | `GET /v1/audit` | 0:00 |

(timestamps filled after T-20; all four tests live in `tests/test_routing.py`, the T-08 criterion tests — READ-ONLY since T-08)

## What this is

A governance service that scores every proposed AI-agent action for risk
and routes it to one of three tiers — **AUTONOMOUS** (auto-executes),
**CONFIRM** (any differently-identified human may approve, 30-minute
approval TTL), or **FULL_REVIEW** (human approval required, 4-hour TTL).
Every routing decision is written to an append-only, hash-chained audit
log with a plain-English explanation of why that tier was chosen.

## 1. Live URLs

- **Railway (production)**: https://aivartba-production.up.railway.app
  — curl-verified against all three tiers plus `/livez` and `/v1/audit`;
  see `progress-log/02-action-log.md` (T-14 entry) and
  `reports/evidence/T-14-curl.txt`.
- **AWS**: not yet deployed (T-15 is a separate, not-yet-started task).

## 2. Quickstart

```bash
git clone <this-repo-url> && cd aivar_tba
pip install -r requirements.txt
cp .env.example .env   # then fill in DATABASE_URL, DATABASE_URL_DIRECT, OPENAI_API_KEY, OPENAI_MODEL
alembic upgrade head
uvicorn app.main:app --reload
```

`DATABASE_URL` is the **pooled** Neon connection string (app runtime);
`DATABASE_URL_DIRECT` is the **direct** one (Alembic migrations only) —
mixing them up produces `relation does not exist` errors. See
`.env.example` for the full variable list.

## 3. Architecture

The state machine (`app/state_machine.py`) is the only mutation path.
The **propose/commit boundary** is where AUTONOMOUS actions execute
immediately, while CONFIRM/FULL_REVIEW actions require a separate human
decision before anything commits:

```mermaid
stateDiagram-v2
    [*] --> PROPOSED
    PROPOSED --> EVALUATED

    state "Propose (risk-scored, nothing committed yet)" as propose {
        EVALUATED --> AUTONOMOUS
        EVALUATED --> CONFIRM
        EVALUATED --> FULL_REVIEW
    }

    state "Commit boundary — human decision required below this line" as boundary {
        CONFIRM --> APPROVED
        FULL_REVIEW --> APPROVED
        CONFIRM --> REJECTED
        FULL_REVIEW --> REJECTED
        CONFIRM --> EXPIRED
        FULL_REVIEW --> EXPIRED
        APPROVED --> EXPIRED
    }

    AUTONOMOUS --> EXECUTED : auto-commit, no human step
    APPROVED --> EXECUTED : human-approved commit

    REJECTED --> [*]
    EXPIRED --> [*]
    EXECUTED --> [*]
```

`POST /v1/actions/evaluate` moves `PROPOSED → EVALUATED → {AUTONOMOUS |
CONFIRM | FULL_REVIEW}`. Only `AUTONOMOUS` actions commit without a
human ever touching them; `CONFIRM`/`FULL_REVIEW` actions sit at
`EVALUATED`'s far side until `POST /v1/actions/{id}/decision` moves them
to `APPROVED` or `REJECTED` — a genuinely separate step, not a formality.

## 4. The risk model

Four dimensions, each normalized to 0.0–1.0, combined as a weighted sum
(`app/risk/scorer.py:24-56`). **The weights are frozen** — see
`CLAUDE.md`.

| Dimension | Weight | Bands |
|---|---|---|
| Reversibility | 0.40 | read 0.0 · update-with-snapshot 0.4 · update-without-snapshot 0.7 · delete/send/pay 1.0 |
| Data scope | 0.30 | 0→0.0 · 1→0.2 · 10→0.4 · 100→0.6 · 1k→0.8 · 10k+→1.0 (log-scaled on affected records) |
| Regulatory | 0.20 | none 0.0 · internal 0.3 · PII/GDPR 0.7 · PHI/SOX 1.0 |
| Confidence | 0.10 | `1.0 − llm_confidence` — low confidence RAISES risk |

**Rationale for the ordering** (reversibility outranks data scope,
which outranks regulatory, which outranks confidence): reversibility
gets the largest weight because it bounds
the *worst case* directly — an irreversible action has no undo path
regardless of how many records it touched or how confident the model
was. Data scope is second because blast radius compounds any other
risk. Regulatory sensitivity is weighted lower than either because it's
already independently covered by a hard floor (below) rather than
relying on the weighted sum alone to catch it. Confidence gets the
smallest weight deliberately — a low-confidence LLM call is a *signal*
to escalate, not proof of danger by itself, and it's independently
backstopped by its own floor too. This ordering is original reasoning
written for this README, not sourced from an existing document — worth
reading closely rather than taking as pre-approved.

The confidence dimension is expressed as uncertainty: all four
dimensions run in the same direction, where higher means higher risk.
An LLM confidence of 0.20 contributes an uncertainty of 0.80. The
low-confidence floor operates independently on the raw LLM confidence
(< 0.5 forces CONFIRM), so the continuous score and the safety floor
are two separate mechanisms reading the same signal.

## 5. Override floors — why a pure weighted sum is insufficient

Floors (`app/risk/floors.py`) are checked *after* the weighted score and
can only escalate a tier, never lower one — proven by the escalate-only
sweep (T-07). Real, live example (`reports/evidence/T-14-curl.txt`):

```
REQUEST:  {"action_type":"delete","reversibility":"irreversible",
           "affected_records":500,"regulatory":"none"}
RESPONSE: composite=0.58, tier=FULL_REVIEW,
          explanation="0.58 -> FULL_REVIEW (floor: irreversible action
          affecting 500 records (>= 100))."
```

The weighted composite alone is **0.58** — inside the 0.30–0.65 CONFIRM
band, not FULL_REVIEW. Left to the weighted sum by itself, a genuinely
confident LLM call on a large irreversible action could land at
CONFIRM: any single human could wave it through. The `irreversible_bulk`
floor overrides that unconditionally once `affected_records >= 100` on
an irreversible action, forcing FULL_REVIEW regardless of how the four
weighted dimensions add up. The other three floors do the same for
regulated-data mutations, low LLM confidence, and any unrecoverable
mutation — each closes a specific way the weighted sum alone could
under-escalate.

## 6. Security controls

- **S-1 Payload hash pinning** — `params_hash` computed over
  canonicalized JSON at evaluate time; `confirm`/`execute` must present
  the same hash or get `409 Conflict`. Why: the most common HITL
  production bug is the approval UI showing one thing while the
  arguments mutate before execution — the human approved something
  that didn't happen.
- **S-2 Idempotency keys** — client-supplied on evaluate/execute; a
  replay returns the original result instead of creating a second
  action or executing twice.
- **S-3 Approval TTL** — every approval has an `expires_at` (CONFIRM 30
  min, FULL_REVIEW 4 hours); expired approvals cannot execute.
- **S-5 Hash-chained audit** — every row stores `prev_hash` +
  `entry_hash`; `/v1/audit/verify` walks the chain and reports
  integrity, so tampering is detectable, not merely discouraged.
- **S-6 Separation of duties** — `reviewer_id` must differ from the
  proposing `agent_id`; an agent cannot approve its own action.

One test per control plus a concurrency race test (two concurrent
decisions on the same item, resolved via a conditional UPDATE — zero
rows affected → 409, not a read-then-write) — see `tests/test_security.py`.

## 7. Compliance mapping

**This section states what design features map to external frameworks.
It is not a claim of legal compliance.**

**EU AI Act, Regulation (EU) 2024/1689, Article 14 ("Human oversight")**
— Article 14(4) requires that human overseers be enabled to: (a)
understand the system's capacities/limitations and duly monitor its
operation; (b) remain aware of automation bias; (c) correctly interpret
the system's output; (d) decide not to use it, or to disregard/override/
reverse its output; (e) intervene or stop it. This design maps to
several of those:

- **(a) understand/monitor** — the hash-chained audit log
  (`GET /v1/audit`) and every decision's plain-English, counterfactual
  explanation string.
- **(c) correctly interpret output** — the same explanation strings are
  specifically designed to be human-readable (T-06a, T-08's fourth
  criterion test), not raw scores.
- **(d) decide not to use / override / reverse** — the
  `POST /v1/actions/{id}/decision` endpoint (approve/reject) for every
  CONFIRM/FULL_REVIEW action, with S-6 preventing an agent from
  rubber-stamping its own action.
- **(e) intervene / stop** — approval TTLs (S-3) bound how long an
  unactioned decision can sit, and REJECTED is terminal.

**OWASP Top 10 for Agentic Applications (2026)** (OWASP GenAI Security
Project) — several `ASI` categories map to specific controls here:

- **ASI03 Identity and Privilege Abuse** → S-6 separation of duties.
- **ASI09 Human-Agent Trust Exploitation** → the LLM confidence cache
  only ever stores genuine successes, never degraded/failed results
  (Issue 2 fix), and the system fails closed on LLM failure rather than
  defaulting to a trust-inducing "autonomous" outcome.
- **ASI10 Rogue Agents** → override floors apply independently of any
  single LLM call, and the hash-chained audit log makes deviation from
  expected behavior detectable after the fact.
- **ASI01 Agent Goal Hijack** → `app/llm.py`'s prompt explicitly frames
  `action_type`/`resource`/`params` as untrusted data, not instructions
  (T-13 Finding 4a) — this reduces, not eliminates, prompt-injection
  risk; the floors are the actual backstop, not the prompt wording.

## 8. Project management

- `progress-log/01-implementation-plan.md` — task board and status.
- `progress-log/02-action-log.md` — append-only record of what was done
  and why, per task.
- `progress-log/03-errors-and-fixes.md` — defect register plus the
  `LEFT OUT` section (scope explicitly cut or deferred).
- `reports/00-project-charter.md` — scope, frozen list, gate schedule.
- `reports/evidence/` — raw command/curl/pytest output backing every
  DoD claim in the action log.

## 9. Known limitations and next steps

(from `progress-log/03-errors-and-fixes.md`'s `LEFT OUT` section, verbatim)

- No application logic in T-04 (scaffold only, per task spec).
- `requirements.txt` dependency versions left unpinned — pinning exact
  versions now would be guessing; to be finalized when feature code lands
  and real compatibility constraints are known.
- T-08: `router.route_action`'s explanation, when a floor fires, states the
  floor's own reason (e.g. "irreversible action affecting 500 records (>=
  100)") but does not compute a floor-specific counterfactual ("would have
  been CONFIRM if affected_records < 100"). Production approach: extend
  each floor's reason with its own avoidance condition once a concrete
  need for it appears (e.g. in T-18's README worked example) — not needed
  to satisfy T-08's literal assertions (composite + tier + triggering
  reason present in the string).
- T-10: idempotency_key is accepted and stored on evaluate/execute
  requests but not yet enforced (no replay-returns-original-result
  behavior). S-2's dedicated enforcement + test is T-12's job.
- T-10: approval expiry (S-3) is enforced lazily — checked at execute
  time, not by a background sweep — since no scheduler exists. An
  approval past its expires_at is only actually marked EXPIRED when
  something reads/executes it. Acceptable for this system's shape (no
  external party needs to observe "expired" before then), but worth
  naming: `GET /v1/actions/{id}` does NOT currently trigger the same
  lazy-expiry check that `execute` does, so a stale GET can show a
  not-yet-expired APPROVED state past its TTL until execute is attempted.
  Production approach: apply `_check_expiry` in the GET handler too, or
  add a scheduled sweep — deferred since T-10's own DoD doesn't require
  it and no endpoint's correctness depends on GET reflecting expiry
  eagerly.
- T-10: `/v1/audit` filtering/pagination is basic (action_id, event_type,
  limit/offset) — no cursor-based pagination or filtering by date range.
  Sufficient for T-10's DoD ("filterable, paginated"); can be extended if
  a later task needs more.
- ~~T-11: the 9 business endpoints read/write InMemoryStore, not
  Postgres~~ — RESOLVED by the pre-T-14 Issue 1 fix. All 9 endpoints now
  use `SQLAlchemyStore`/`SQLAlchemyAuditLog` at runtime, verified by a
  direct Postgres query showing real persisted rows and a live
  multi-connection concurrency proof. See the "Issue 1 fix" action-log
  entry.
- ~~G2: transient LLM failures cached indefinitely~~ — RESOLVED by the
  pre-T-14 Issue 2 fix. `OpenAIConfidenceProvider` now only caches
  `degraded=False` results. See the "Issue 2 fix" action-log entry.
- Pre-T-14 Issue 1 fix: `audit_records` has no DB-level enforcement of
  "APPEND-ONLY, never UPDATE, never DELETE" — that's currently true only
  because application code never issues those statements. A Postgres
  trigger or `REVOKE UPDATE, DELETE` grant would enforce it at the DB
  level. Explicitly deferred as a separate, out-of-scope hardening step
  per the approved plan (not part of "swap the store").
- Pre-T-14 Issue 1 fix: `test_db_store.py`'s real-DB tests run
  function-scoped (fresh engine/connection pool per test, ~20s each,
  ~2 minutes for the file) due to a pytest-asyncio event-loop-scoping
  incompatibility with module-scoped async fixtures (D-08). Production
  approach if this becomes a CI time problem: pin an explicit
  `asyncio_default_fixture_loop_scope` (module or session) in
  `pytest.ini` and re-test module-scoped fixtures against that config,
  or accept the per-test cost as this project already has.
- T-13 Finding 3 (boundary brittleness), ACCEPTED as a documented
  limitation per product owner decision — not fixed, frozen thresholds
  unchanged, no calibration logic added. A fresh-session adversarial
  review found the composite score is brittle near both frozen
  thresholds: a 0.006 change in `llm_confidence` (0.505→0.499) flips
  CONFIRM↔FULL_REVIEW at 0.65; a 0.01 change flips CONFIRM↔AUTONOMOUS at
  0.30 (see reports/evidence/T-13-adversarial-review.txt for the exact
  inputs). Rationale: the two thresholds (0.30, 0.65) are FROZEN — the
  product owner has to defend each on camera and chose not to add
  calibration/smoothing logic under deadline pressure. This is a known,
  accepted characteristic of any hard-threshold system, not a bug.
  Existing boundary tests (tests/test_tiers.py, T-07a) are kept exactly
  as-is; they already prove the boundary behaves per the frozen,
  approved semantics — brittleness near the line is a property of having
  a line at all, not of the semantics being wrong.
- T-12/S-3: `CONFIRM_TTL`/`FULL_REVIEW_TTL` are module-level Python
  constants (`app/main.py`), not runtime-configurable (e.g. via env var).
  T-12's spec says "both configurable" — the values themselves (30 min /
  4 h) are correct and enforced, but changing them currently requires a
  code edit + redeploy, not a config change. Production approach: move
  to env vars (`CONFIRM_TTL_MINUTES`, `FULL_REVIEW_TTL_HOURS`) with the
  same defaults, read once at startup.

## 10. DMAIC

No detailed spec for this section exists in the prompt pack — only the
task-board's one-line entry names it (T-18a). This maps
Define-Measure-Analyze-Improve-Control onto how this project's risk
model and reliability were actually built, using real project
artifacts — it isn't a new process being introduced.

- **Define** — the frozen risk model: four weighted dimensions
  (reversibility 0.40, data scope 0.30, regulatory 0.20, confidence
  0.10), two frozen thresholds (0.30, 0.65), and T-08's four criterion
  tests as the literal acceptance definition of "done."
- **Measure** — `tests/test_scoring.py` (dimension bands),
  `tests/test_tiers.py` (boundary-band sweep, T-07a),
  `tests/test_floors.py` (escalate-only invariant, T-07), and live curl
  verification against the deployed URL (`reports/evidence/`) — a green
  build log alone is never treated as proof.
- **Analyze** — a fresh-session adversarial review (T-13,
  `reports/evidence/T-13-adversarial-review.txt`) found and classified
  real findings, including boundary brittleness (Finding 3) and
  prompt-injection surface (Finding 4a). The defect register
  (`progress-log/03-errors-and-fixes.md`, D-01–D-12) records every other
  defect's root cause the same way.
- **Improve** — fixes approved only where they didn't touch the frozen
  list: broadening the `regulated_mutation` floor to cover `PII_GDPR`
  (T-13 Finding 2), adding `unrecoverable_mutation_requires_confirm`
  (T-13 Finding 1), fixing the LLM-cache-on-failure bug (Issue 2 fix),
  and D-11/D-12.
- **Control** — the FROZEN list (`CLAUDE.md`) locks weights, thresholds,
  which floors exist, and the fail-closed direction against silent
  change; T-08's four criterion tests are READ-ONLY; every fix in this
  project re-verified T-08 stays at zero diff before being accepted.

## 11. Versioning

- **Risk model versioning** — every risk assessment records
  `weights_version` (`app/risk/scorer.py:56`, currently `"v1"`)
  alongside the four dimension scores and composite, so a historical
  audit stays reproducible even after the weights themselves change in
  a future version — the explanation attached to a past decision won't
  silently drift.
- **API versioning** — every endpoint is under a `/v1/` prefix
  (`/v1/actions/evaluate`, `/v1/audit`, `/v1/review-queue`, etc.); a
  breaking request/response-shape change would ship under `/v2/`, not
  mutate `/v1/` in place.
- **Change history** — standard git history, plus the append-only
  `progress-log/02-action-log.md` and
  `progress-log/03-errors-and-fixes.md` as a human-readable record of
  *why* each change happened, not just *what* changed.

## 12. Fuzzy rejection

No detailed spec for this section exists in the prompt pack either —
same situation as DMAIC above. This documents the system's current,
deliberate state; no code changed to write it.

This system currently does **not** implement fuzzy or graduated
rejection. Every routing decision is a hard classification into exactly
one of AUTONOMOUS / CONFIRM / FULL_REVIEW, using two frozen, hard-edged
thresholds (0.30, 0.65) — there is no partial-confidence middle state
and no "ask again" outcome.

This is a documented, deliberate limitation, not an oversight: a
fresh-session adversarial review (T-13 Finding 3) found the composite
score is brittle right at both threshold boundaries — a 0.006 change in
`llm_confidence` can flip CONFIRM↔FULL_REVIEW, and a 0.01 change can
flip CONFIRM↔AUTONOMOUS
(`reports/evidence/T-13-adversarial-review.txt`). A fuzzy-rejection
scheme — e.g. treating scores within some margin of a threshold as
their own "needs a second opinion" state, or blending tiers by
confidence — would reduce that brittleness, but was deliberately not
built: the two thresholds are frozen, and the product owner chose not
to add calibration/smoothing logic under deadline pressure (see §9
above). Any future fuzzy-rejection design would need explicit sign-off
before touching the frozen thresholds it would necessarily interact
with.

## 13. Theoretical grounding

This engine implements a **cost-sensitive deferral policy**. The
formalism dates to Chow's (1970) *classification with a reject
option*, was generalised by El-Yaniv and Wiener as *selective
classification* — a predictor paired with a selection function that
decides whether to act or abstain — and extended by Madras et al.
(2018) into *learning to defer*, where abstention routes an instance to
an external decision-maker rather than simply withholding output.

The mapping is direct. The **predictor** is the agent's proposed
action. The **rejector** is the risk router. **Abstention** is not a
null output but an escalation to a named human tier. The three-tier
structure corresponds to the scalable-oversight view of a principal as
a tiered system whose composite competence exceeds any single
component's.

**Design decision: the rejector is deterministic and rule-based, not
learned.** This is deliberate, for three reasons.

1. **Cold start.** Learned deferral requires human decisions for every
   instance in a training set. At deployment there is no such history —
   the system must be correct on day one.
2. **Explainability.** Success criterion 4 requires a human-readable
   breakdown. A learned rejector produces a score, not a reason. A
   black-box rejector inside a system whose purpose is human oversight
   is self-defeating.
3. **Auditability.** Deterministic weights carry a version. Any
   historical decision can be recomputed exactly. A learned rejector's
   decisions are reproducible only if the model checkpoint is also
   versioned and retained.

**Known limitation, precisely stated.** Madras et al. (2018) show that
confidence-based deferral is suboptimal because it ignores the
downstream human's performance: at high model uncertainty the human may
be no more accurate than the model, so it can be preferable to defer
*other*, lower-uncertainty instances where the human genuinely
outperforms. This engine defers on action risk, not on modelled human
competence. The reviewer oversight metrics endpoint (below) is the
first step toward closing that gap — measuring the human is the
prerequisite for ever routing to them optimally.

A second acknowledged gap: the deferral literature largely ignores
**capacity management**. A review queue is a finite resource. This
engine records queue depth and decision latency but does not yet
allocate against a capacity budget.
