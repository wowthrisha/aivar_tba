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
| D-14 | Live gate-check (post-restart demo warm): a read-only, fully-reversible action with zero blast radius landed on CONFIRM instead of AUTONOMOUS whenever the live LLM's self-reported confidence dipped below 0.5 | `low_confidence` (`app/risk/floors.py`) fired on `llm_confidence < 0.5` unconditionally, regardless of `reversibility`. A read carries no consequence if the model is wrong about it, so escalating on confidence alone produced exactly the human-oversight bottleneck the problem statement warns against — every read had a real chance of needing a human for no reason tied to actual risk. | Renamed to `low_confidence_on_mutation`, gated on `is_mutation` (mirrors the reversibility weight of 0.40: consequence, not uncertainty alone, drives oversight). Scope: only this floor's precondition changed — the 0.5 threshold, all four weights, both tier thresholds, every other floor, and the fail-closed direction are untouched; `tests/test_routing.py` has an empty diff, confirmed before the commit. 5 new tests added to `tests/test_floors.py` (read-only immune across the full confidence range 0.0–1.0 in 0.05 steps; single-update and bulk-delete proven still NOT immune, i.e. still escalate, the same way), 2 existing tests updated for the now-intentional behavior change. Full suite: 144 passed, 6 skipped. Commit `a076248`. | Fixed |
| D-21 | Lambda rejected the ECR image on manifest media type | `docker buildx` emits OCI media types and attaches provenance/SBOM attestations by default, producing an image index Lambda cannot accept. Image content, architecture and digest were all correct; the failure was packaging format. | Fixed with `oci-mediatypes=false`, `--provenance=false`, `--sbom=false`. Production approach: assert the manifest media type immediately after push (`infra/aws/deploy-lambda.sh`). | Fixed |
| D-22 | Two console image updates presented as complete but did not apply; the resolved digest and `LastModified` were unchanged | Not surfaced by any error - the update API call reported success both times. | Detected by comparing the resolved digest against the pushed digest, not by any surfaced error. Production approach: assert `LastModified` advanced after every update (`infra/aws/deploy-lambda.sh`). | Fixed |
| D-23 | `aivar-deploy` could create the Lambda function and push to ECR but could not update the function or read its configuration | Least-privilege IAM granted at resource-creation time did not cover subsequent operations (update, introspection). | Verification was completed behaviourally via the Function URL instead of via AWS API introspection. The agent declined to broaden its own permissions. Note: this mirrors the delegation principle the engine itself enforces — an authorisation obtained for one hop does not authorise the next. Production approach: a scoped deployment role covering the full deploy lifecycle, granted once and reviewed. | Not fixed (behavioural workaround) |
| D-26 | `git status --short` showed the `Dockerfile` — the build input for the AWS Lambda deployment — as untracked (`??`) in `~/aivar_tba` | The Dockerfile was never `git add`ed and existed only in a working tree (originally written in the separate `gae-aws`/`aws-deploy` checkout, itself never committed there either — see L-J). A fresh clone could not have rebuilt the deployed image. Found during repo consolidation, not by a failing build, because the local file was always present so the build never actually failed. | Committed the Dockerfile to `master`. Production approach: assert that every file referenced by a deploy script is tracked, as a CI check. | Fixed |
| D-27 | Post-push verification against Railway using freshly-invented resource names (`post-push-readonly`, `post-push-single-update`) showed read escalate to CONFIRM and single-update escalate to FULL_REVIEW, both via `floor_name":"novelty_unprecedented"` — one tier above the documented AUTONOMOUS/CONFIRM baseline, initially reading as a regression from the D-26 Dockerfile commit. | The novelty check (D-14/L-B/L-J territory) correctly escalates any action with no precedent in the audit history, one tier, regardless of its base score. Each response's own `explanation` field showed the base computation was correct before escalation (`"0.12 -> AUTONOMOUS..."`, `"0.42 -> CONFIRM..."`) — this is the novelty feature working as designed, not an app defect. The D-26 commit changed only the Dockerfile and this log; zero `app/*.py` diff. | Not a code fix — a verification-methodology fix. Re-ran the read scenario against both Railway and AWS using the exact seeded resource (`customers/42`, from `demo.sh`/`final-demo-capture.json`, which carries real precedent) and got `floor_name: null`, `tier: AUTONOMOUS` on both, matching baseline exactly. Production approach: smoke/verification scripts must always use the same fixed seeded resource identifiers, never freshly generated ones, or must explicitly treat novelty escalation as expected rather than a diff against a non-seeded baseline. | Fixed (verification methodology) |
| D-28 | The tamper-evident audit record could not reproduce its own derivation — sub-scores lived only in the mutable, non-chained risk_assessments table. Integrity and reproducibility are different properties; the chain proved nothing had been altered but the record could not show how the composite was reached. Found in cross-layer reconciliation, not by a failing test. | `app/main.py`'s `evaluate()` only wrote `tier`/`composite`/`floor_name`/`floors_fired`/`explanation` to the audit payload, never the four sub-scores, `weights_version`, or the deployed code's `git_sha`. | Added `reversibility_score`, `data_scope_score`, `regulatory_score`, `confidence_score`, `weights_version`, and `git_sha` to the audit payload — additive only, existing hash-chained records and chain integrity unaffected. `tests/test_observability.py::test_audit_payload_can_reconstruct_composite_without_risk_assessments_table` proves `sum(WEIGHTS[k] * payload[f"{k}_score"])` equals the payload's own `composite` exactly. Caveat: this holds only as the system is actually configured (`CALIBRATION_MODE=shadow`, confirmed the only mode ever active in this repo/deploy config) — an "enforce"-mode calibration adjustment isn't itself logged, so the reconstruction guarantee would quietly stop holding if enforce mode were ever turned on. Not fixed now (out of scope of what was asked); named here so it isn't a silent gap later. | Fixed |
| D-29 | Live fuzz sweep (final defect sweep, Part 1A) found 4 reproducible crashes: `affected_records: NaN`, `affected_records: Infinity`, a null byte in `params`, and 500-level-deep nested `params` — all returned raw `500 {"detail":"internal server error"}` instead of a clean 4xx. | Traced to two distinct root causes via local reproduction (TestClient + fakes, no live secrets), not assumed: (1) NaN/Infinity — Pydantic already rejects these correctly (`finite_number` validation error), but `ValidationError.errors()` always echoes the raw offending value, and FastAPI's default `RequestValidationError` handler crashes trying to JSON-render it, since Starlette's `JSONResponse` enforces `allow_nan=False` (`ValueError: Out of range float values are not JSON compliant: nan`) — the crash is in the framework's own error-rendering path, not in validation, so no per-field validator alone could have prevented it. (2) 500-deep `params` — Pydantic itself parses this fine; the crash was at response-serialization time echoing `params` back in `ActionResponse` (`pydantic-core`: `ValueError: Circular reference detected (depth exceeded)`), downstream of where a request-time validator runs. | Added a `RequestValidationError` exception handler (`app/main.py`) that sanitizes non-finite floats before rendering the 422 response — general, not per-field, covers current and future numeric fields. Added `app/schemas.py` validators: `affected_records` rejects non-finite floats (defense in depth); `params` and all four top-level string fields reject null bytes/control characters, `params` additionally capped at 20 levels of nesting and 64KB serialized size, string fields capped at 10,000 characters. `tests/test_input_validation.py` (8 new tests) proves each case now 422s cleanly instead of 500ing. | Fixed |

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
- **L-E** AWS: completed post-submission (2026-08-20, ~17:30 IST). The
  ECR repository, `linux/amd64` image, IAM execution role and policy,
  Lambda function (`ps91-t15`), and its Function URL are all live and
  curl-verified — `/livez`, `/readyz`, and a full read-only
  `POST /v1/actions/evaluate` round-trip (`governance/evidence/T-15-curl.txt`).
  Railway (`https://aivartba-production.up.railway.app`) remains the
  deployed environment of record for the original submission; AWS is
  now a second, independently working deployment. AWS App Runner was
  never an option — closed to new customers since 30 Apr 2026 (a
  documented constraint from the start of this project, not a
  late-discovered blocker). It is now accurate to describe AWS as
  deployed in submission materials, dated after the original
  submission.
  **Security note**: verifying the Lambda config via
  `aws lambda get-function` printed its environment variables
  (`DATABASE_URL`, `OPENAI_API_KEY`) in plaintext into a terminal/chat
  transcript — AWS's default behavior for that call, not a repo bug.
  Disclosed immediately; user was asked to rotate both credentials.
  Production follow-up: move these into Secrets Manager / SSM
  Parameter Store instead of plain Lambda env vars.
- **L-F** Reviewer metrics report `decisions_total` alongside every
  rate, so a small sample cannot be misread as an extreme signal.
  Confirmed both in code (`ReviewerMetrics.decisions_total: int`,
  `app/oversight.py:44`, populated on every code path including the
  zero-decisions branch) and live: `GET /v1/oversight/reviewers`
  returned `decisions_total` for every reviewer in the response
  (`reviewer-9: 4`, `seed-reviewer: 8`, `pre-record-reviewer: 1`, ...)
  during this hardening pass. The claim holds.
- **L-G** Semantic duplication of the confidence signal. `floors.py`
  uses raw `llm_confidence` (< 0.5 forces CONFIRM); `scorer.py` and the
  `confidence_score` column hold `1 - llm_confidence`, an uncertainty.
  Both correct, and the CLI now labels the displayed value
  "uncertainty" (this session's earlier step), but one signal exists
  under one name in two orientations. A future edit reaching for
  "confidence" would silently get the inverse. Found by semantic audit,
  not a failing test — every value assertion passes. Production
  approach: rename to `uncertainty_score`, persist raw `llm_confidence`
  separately, add a per-dimension direction-contract test. Deferred:
  needs a migration and a redeploy of a verified system.
- ~~**L-H** `params_hash` is not Unicode-normalised. Key order,
  whitespace, and nesting canonicalise correctly; NFC and NFD forms of
  one string hash differently because `json.dumps` does not normalise.
  FAILS CLOSED — a mismatch returns 409 and cannot authorise anything;
  the impact is rejecting a legitimate confirmation, not admitting an
  illegitimate one.~~ — RESOLVED (final defect sweep fix pass,
  2026-08-21): `app/store.py::canonical_params_hash` now normalizes to
  NFC before hashing — the only hash-computation site in the codebase
  (`app/db_store.py` imports it, doesn't redefine it), so this covers
  both the compute path and, since `confirm`/`execute` compare via plain
  string equality against the stored value rather than recomputing a
  hash, the "compare path" too. Required also switching `json.dumps` to
  `ensure_ascii=False`: with the default `ensure_ascii=True`, non-ASCII
  characters are escaped to `\uXXXX` sequences *before* normalization
  ever runs, so NFC and NFD forms were already flattened into two
  different (but both now all-ASCII, hence normalization-is-a-no-op)
  escape sequences — confirmed this was the reason the first attempt at
  this fix still failed its own test. `tests/test_security.py::test_s1_hash_is_unicode_normalized_nfc_and_nfd_produce_same_hash`
  proves NFC and NFD forms of the same logical string now hash
  identically. Caveat, not silently omitted: `ensure_ascii=False` changes
  the hash for *any* params containing non-ASCII characters, not only
  NFC/NFD edge cases — not verified whether any historical persisted
  record contains non-ASCII params content that would now hash
  differently; not reconciling retroactively, per the same reasoning the
  original entry gave for deferring this fix.
- **L-I** Cross-layer reconciliation (API / `risk_assessments` / audit
  / CLI) was verified manually during hardening — repeatedly, across
  OD-1, the bonus calibration feature, and this session's live
  captures — not by a standing automated test. Production approach:
  one test asserting pairwise equality across all four layers for a
  single evaluate call.
- **L-J** Audit provenance. A late semantic audit reported shipped
  features as absent; it had run in the `gae-aws` clone on
  `aws-deploy`, a pre-merge tree. Correct for that tree, wrong for the
  system actually running on Railway. Every gate check in this log
  records its branch and commit; this entry records why that matters —
  a finding is only as good as the tree it was run against.
- No application logic in T-04 (scaffold only, per task spec).
- ~~`requirements.txt` dependency versions left unpinned — pinning exact
  versions now would be guessing; to be finalized when feature code lands
  and real compatibility constraints are known.~~ — RESOLVED (Phase 2
  consolidation, commit e702701): pydantic, fastapi, uvicorn,
  python-dotenv, pytest, pytest-asyncio pinned to `pip show`'s actual
  installed versions. Every line in requirements.txt now carries `==`.
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
  0.30 (see governance/evidence/T-13-adversarial-review.txt for the exact
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
