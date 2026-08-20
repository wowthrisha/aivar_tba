# Defect Register

| ID | Symptom | Root cause | Fix | Status |
|---|---|---|---|---|
| D-01 | `tests/test_llm.py::test_out_of_range_value_is_rejected` failed with `UnboundLocalError` | Python deletes an `except ... as name` binding when the except block exits, so `real_validation_error` was referenced after being auto-deleted | Captured the exception into a variable declared before the try/except instead | Fixed |
| D-02 | Every real `/v1/actions/evaluate` call showed `llm_degraded: true` in the audit log during T-10's live curl verification; confidence always fell back to 0.0, spuriously tripping the T-07 `low_confidence` floor on low-risk actions | `app/llm.py`'s `.parse()` call passed `temperature=0` per T-09's literal spec. The live pinned model (`gpt-5.6-luna`) rejects that value: `Error code: 400 - "Unsupported value: 'temperature' does not support 0 with this model. Only the default (1) value is supported."` T-09's mocked tests never caught this since they mock the client and never exercise the real `temperature` argument. Present since T-09's original commit — not introduced by T-10. | Removed the `temperature=0` argument (approved by product owner) so the SDK uses the model's required default. Root cause and fix confirmed via a real live call: `confidence: 0.82, degraded: False, reason: None`. | Fixed |
| D-03 | T-12's race test (`test_race_concurrent_decisions_resolve_via_conditional_update`) passed 5/5 runs against T-10's original `decision` handler, which read `record.state`, checked it, then mutated it as two separate steps — a genuine read-then-write, not a conditional update | Python's GIL plus the specific fast, non-yielding code path in that handler made the race window narrow enough that this synthetic two-thread test never happened to land in it. A passing test is not proof of a correct implementation when the spec mandates a specific mechanism (conditional UPDATE), not just an observed outcome. | Added `InMemoryStore.conditional_transition` (lock-guarded atomic check-and-set) and switched confirm/decision/execute to use it instead of read-then-write, regardless of the test already passing. | Fixed |
| D-04 | `test_s5_tampered_middle_record_is_detected` passed in isolation but failed when the full suite ran together (`valid=True` when `False` was expected) | The test indexed into the app's shared `_audit` module-level singleton, which accumulates audit records across the entire pytest session (every test that calls evaluate/confirm/decision/execute appends to it). `records[1]` picked up an unrelated record from a different, earlier test whose `tier` value already equaled what the test was "tampering" it to — a no-op mutation that happened to leave the hash chain intact. | Rewrote the test to construct and tamper a fresh, isolated `AuditLog()` instance directly, rather than reaching into the shared app singleton. | Fixed |
| D-05 | `test_finding1_update_without_snapshot_no_longer_autonomous` (T-13 fix regression test) failed: `assert 0.38 == 0.28 ± 2.8e-07` | Test called `score_action(..., llm_confidence=0.0)` but the comment and expected value (`composite=0.28`) were computed for the review's actual case, `llm_confidence=1.0` — a copy/transcription mismatch between the two calls in the same test (the second call, to `evaluate_floors`, correctly used `1.0`). | Corrected the `score_action` call to use `llm_confidence=1.0`, matching the review's original input. | Fixed |
| D-06 | Every real `/v1/actions/evaluate` call showed `llm_degraded: true` again during the G2 walkthrough, even though D-02 (the temperature bug) was already fixed | `OpenAIConfidenceProvider`'s cache stored the failed/degraded result from the process's very first (cold-start) live call and replayed it on every subsequent identical-key call, indefinitely — the exact issue later fixed as "Issue 2" in this same session. | Traced via a direct provider call (bypassing the cache) confirming the SDK itself worked (`confidence: 0.86`); confirmed non-regression with a fresh cache key. Formally fixed later the same session — see the Issue 2 fix entry below. | Fixed |
| D-07 | Pre-T-14 Issue 1 fix: after swapping to dependency-injected `get_store`, three test files' evaluate-then-follow-up tests (e.g. evaluate then `GET /v1/actions/{id}`) failed with spurious 404s | Fixture used `app.dependency_overrides[get_store] = lambda: InMemoryStore()` — FastAPI calls the override on every request, so each lambda invocation built a BRAND NEW empty store; the action written by `evaluate()` never existed in the store the follow-up request received. | Fixed by constructing the store/audit_log ONCE per test and capturing it in the lambda's closure (`store = InMemoryStore(); ... lambda: store`), matching the pattern `_FakeProvider`/`_FakeEngine` already used correctly. | Fixed |
| D-08 | New `tests/test_db_store.py` hung (2-minute timeout) with module-scoped async engine fixtures, then failed with `RuntimeError: ... attached to a different loop` / `Event loop is closed` | This pytest-asyncio version's default event-loop handling creates a new event loop per test function; a module-scoped async fixture (the engine) gets created in one loop and later tests try to use its connection pool from a different loop. | Reverted to function-scoped fixtures — real per-test connection-setup cost against live Neon (~20s/test) accepted rather than fighting the framework's default loop scoping under time pressure. | Fixed (worked around) |
| D-09 | Latent (never shipped): `_check_expiry`'s original design mutated `record.state = ActionState.EXPIRED` directly on the local Python object | Correct for `InMemoryStore` (whose `get_action()` returns a live reference into its internal dict) but silently wrong for `SQLAlchemyStore` (whose `get_action()` reconstructs a fresh `ActionRecord` from a DB query every call) — the mutation would vanish with the object, never reaching Postgres. Found during design review before writing `db_store.py`'s callers, not from a failing test. | Routed expiry through `conditional_transition` (the same real DB update every other transition already uses) instead of local mutation. | Fixed |
| D-10 | Latent (never shipped): `SQLAlchemyAuditLog.append()`'s "read the last record by created_at, then write the next" is a genuine TOCTOU race under concurrent writers | `audit_records` has no auto-incrementing sequence column (only `created_at`, an unordered timestamp) to serialize on, unlike `actions.state`. Two concurrent appends could both read the same "last" row and both compute the same `prev_hash`, forking the hash chain. Found during design review, not from a failing test — no concurrent-audit-write test was run before this was caught. | Serialized all `append()` calls with a Postgres advisory transaction lock (`pg_advisory_xact_lock`), released automatically at transaction end. | Fixed |
| D-11 | T-16's own raw verification evidence showed the forced-error JSON log line with `"request_id": null`, even though the success-path log line correctly carried a real UUID | `request_id_middleware` (`app/main.py`) set the `request_id` contextvar before `call_next(request)` and reset it in a `finally` block. When `call_next` raised, that `finally` ran and reset the contextvar to `None` BEFORE the exception reached `@app.exception_handler(Exception)`, so the error log line was written after the value was already cleared. Found while capturing T-16's own raw evidence, not from a failing test — the two original tests only asserted the success-path log line and the error response body separately, neither of which exercised the error *log* line's request_id. | Stored `request_id` on `request.state` (set before `call_next`, unaffected by the middleware's `finally`/reset) in addition to the contextvar; `unhandled_exception_handler` now re-sets the contextvar from `request.state.request_id` before logging. Added `test_forced_error_log_line_contains_request_id` (`tests/test_observability.py`) asserting the ERROR log line itself carries a non-null `request_id`. Fixed on the first attempt. | Fixed |
| D-12 | `README.md`'s risk-model weight-ordering sentence rendered as a broken blockquote on GitHub, splitting one sentence into a paragraph + an unintended quote block | A wrapped markdown line started with `>` — the source read `"...(reversibility > data scope > regulatory\n> confidence): reversibility gets..."`, and GitHub's renderer reads any line starting with `>` as a blockquote marker, regardless of intent. Found by fetching GitHub's own rendered HTML (`gh api repos/.../readme -H "Accept: application/vnd.github.html"`) during T-18's required render-proof step, not from reading the raw markdown source — the source alone gave no indication of the bug. | Reworded to `"reversibility outranks data scope, which outranks regulatory, which outranks confidence"` so no line starts with `>`; re-verified via the same render API (`grep -c '<blockquote>'` → 0) and grepped the whole file for any other `^>` line (none found). Fixed on the first attempt. | Fixed |
| D-13 | Live adversarial sweep (hardening pass): `POST /v1/actions/evaluate` with `affected_records: -5` returns `500 {"detail": "internal server error"}` instead of a `422` validation error | `EvaluateRequest.affected_records` (`app/schemas.py`) is a bare `int` with no `ge=0` constraint, so Pydantic accepts a negative value. It reaches `app/risk/scorer.py::_data_scope_score`, which explicitly `raise ValueError("affected_records must be >= 0")` — an unhandled exception that propagates to the generic `unhandled_exception_handler`. The FAIL-CLEAN handler (D-11's fix) does its job (clean JSON, no stack trace leaked, audit chain unaffected — reverified `valid: true` immediately after via `/v1/audit/verify`), but the request still 500s for what is genuinely invalid input, not a server fault. | Not fixed — found during a read-only Phase 3 adversarial sweep with an explicit "do not fix yet" instruction. Production approach: add `Field(ge=0)` to `affected_records` on `EvaluateRequest` so this is caught at the API boundary as a `422`, matching how `b`/`c` (malformed types / missing fields) already behave correctly. | Found, not fixed |

## LEFT OUT

Scope explicitly cut or deferred, and why. Per contract E-6/E-7 — report
what was not done, never substitute silently.

- **L-A** Re-evaluation is not reproducible across restarts. Audit
  records ARE reproducible (all inputs, `weights_version`, and
  `llm_model` are persisted; composite recomputes exactly — verified
  live during the hardening pass, 5/5 recent rows matched
  `WEIGHTS[k] * score[k]` summed to the stored `composite` bit-for-bit).
  Re-evaluation calls a live model at default temperature (D-02: the
  pinned model rejects `temperature=0`), so composites can vary ~0.03
  between processes on identical inputs. Tiers stay stable because
  floors are deterministic (computed from raw inputs, never from
  composite). Deliberate, not an oversight: an audit must replay
  exactly what happened; a fresh evaluation should reflect current
  model evidence, not a frozen historical one. Production approach: an
  explicit `/replay` endpoint that scores from persisted inputs only
  (no live LLM call), distinct from `/evaluate`.
- **L-B** Two tier decision points. `route_action()` (`app/risk/`)
  returns the BASE tier; the enforced tier is composed in
  `app/main.py`'s `evaluate()` (calibration -> floors -> novelty). The
  audit record correctly reflects the ENFORCED tier and reason
  (verified live throughout this session, including the bonus
  calibration and OD-1 work), but the risk module is no longer the
  sole authority on the tier that actually governs an action. Found in
  self-review, not from a failing test. Production approach: a single
  `compose_final_decision()` owning base -> calibration -> thresholds
  -> floors -> novelty end-to-end, so one function is authoritative.
  Not refactored under deadline: the current path is fully tested
  (routing, calibration, novelty each independently proven, plus the
  critical regression test that all three canonical scenarios are
  byte-identical under calibration=shadow), and a rushed restructure
  this close to submission would risk that already-verified behaviour
  for a structural cleanliness gain, not a correctness one.
- **L-C** Adaptive calibration ships in SHADOW mode: computed, audited,
  not applied. This is a dark-launch validation stage, not a permanent
  state. The mechanism is implemented and bounded (±0.10, minimum 5
  decisions, floors always win — proven by
  `tests/test_calibration.py::test_enforce_mode_cannot_suppress_full_review_floor`
  and reverified live: a bulk-delete floor case held FULL_REVIEW under
  a full -0.10 calibration test in enforce mode); it does not yet
  influence production routing (`CALIBRATION_MODE` unset on Railway ->
  defaults to `shadow`; `enforce` is never set anywhere in this repo or
  its deploy config). Promotion criterion: sustained agreement between
  the shadow adjustment and actual reviewer outcomes over a meaningful
  sample, not just passing tests.
- **L-D** Calibration input is observational, not ground truth.
  Reviewer behaviour may itself be biased — the automation-bias metrics
  (`app/oversight.py`, see L-F) exist precisely to surface that
  possibility. Calibration should stay advisory until its inputs are
  themselves statistically validated as trustworthy signal, not just
  until the formula is unit-tested.
- **L-E** AWS: reporting actual state honestly, not aspirationally. An
  ECR repository and a `linux/amd64` image were built; an IAM execution
  role and policy were created. The Lambda function and its Function
  URL were NOT completed within the available window. Railway
  (`https://aivartba-production.up.railway.app`) is the deployed
  environment of record for this entire submission. AWS App Runner was
  never an option — closed to new customers since 30 Apr 2026 (a
  documented constraint from the start of this project, not a
  late-discovered blocker). Do not describe AWS as deployed anywhere in
  submission materials; it is scripted and partially provisioned, not
  live.
- **L-F** Reviewer metrics report `decisions_total` alongside every
  rate, so a small sample cannot be misread as an extreme signal.
  Confirmed both in code (`ReviewerMetrics.decisions_total: int`,
  `app/oversight.py:44`, populated on every code path including the
  zero-decisions branch) and live: `GET /v1/oversight/reviewers`
  returned `decisions_total` for every reviewer in the response
  (`reviewer-9: 4`, `seed-reviewer: 8`, `pre-record-reviewer: 1`, ...)
  during this hardening pass. The claim holds.
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
- Post-Feature-B, route_action() returns the BASE tier; the enforced
  tier is composed in app/main.py after the novelty check. The audit
  record correctly reflects the enforced tier and reason (verified),
  but the risk module is no longer the single source of truth for the
  tier. Found in self-review, not from a failing test. Production
  approach: compose the novelty signal inside app/risk/ so
  route_action() returns the enforced tier, restoring one authority.
