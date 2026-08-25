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
| D-13 | Live adversarial sweep (hardening pass): `POST /v1/actions/evaluate` with `affected_records: -5` returns `500 {"detail": "internal server error"}` instead of a `422` validation error | `EvaluateRequest.affected_records` (`app/schemas.py`) is a bare `int` with no `ge=0` constraint, so Pydantic accepts a negative value. It reaches `app/risk/scorer.py::_data_scope_score`, which explicitly `raise ValueError("affected_records must be >= 0")` — an unhandled exception that propagates to the generic `unhandled_exception_handler`. The FAIL-CLEAN handler (D-11's fix) does its job (clean JSON, no stack trace leaked, audit chain unaffected — reverified `valid: true` immediately after via `/v1/audit/verify`), but the request still 500s for what is genuinely invalid input, not a server fault. | Not fixed — found during a read-only Phase 3 adversarial sweep with an explicit "do not fix yet" instruction. Production approach: add `Field(ge=0)` to `affected_records` on `EvaluateRequest` so this is caught at the API boundary as a `422`, matching how `b`/`c` (malformed types / missing fields) already behave correctly. See D-29 (2026-08-21/22) for where this row's own "Production approach" was eventually carried out. | Found, not fixed |
| D-14 | Live gate-check (post-restart demo warm): a read-only, fully-reversible action with zero blast radius landed on CONFIRM instead of AUTONOMOUS whenever the live LLM's self-reported confidence dipped below 0.5 | `low_confidence` (`app/risk/floors.py`) fired on `llm_confidence < 0.5` unconditionally, regardless of `reversibility`. A read carries no consequence if the model is wrong about it, so escalating on confidence alone produced exactly the human-oversight bottleneck the problem statement warns against — every read had a real chance of needing a human for no reason tied to actual risk. | Renamed to `low_confidence_on_mutation`, gated on `is_mutation` (mirrors the reversibility weight of 0.40: consequence, not uncertainty alone, drives oversight). Scope: only this floor's precondition changed — the 0.5 threshold, all four weights, both tier thresholds, every other floor, and the fail-closed direction are untouched; `tests/test_routing.py` has an empty diff, confirmed before the commit. 5 new tests added to `tests/test_floors.py` (read-only immune across the full confidence range 0.0–1.0 in 0.05 steps; single-update and bulk-delete proven still NOT immune, i.e. still escalate, the same way), 2 existing tests updated for the now-intentional behavior change. Full suite: 144 passed, 6 skipped. Commit `a076248`. | Fixed |
| D-21 | Lambda rejected the ECR image on manifest media type | `docker buildx` emits OCI media types and attaches provenance/SBOM attestations by default, producing an image index Lambda cannot accept. Image content, architecture and digest were all correct; the failure was packaging format. | Fixed with `oci-mediatypes=false`, `--provenance=false`, `--sbom=false`. Production approach: assert the manifest media type immediately after push (`infra/aws/deploy-lambda.sh`). | Fixed |
| D-22 | Two console image updates presented as complete but did not apply; the resolved digest and `LastModified` were unchanged | Not surfaced by any error - the update API call reported success both times. | Detected by comparing the resolved digest against the pushed digest, not by any surfaced error. Production approach: assert `LastModified` advanced after every update (`infra/aws/deploy-lambda.sh`). | Fixed |
| D-23 | `aivar-deploy` could create the Lambda function and push to ECR but could not update the function or read its configuration | Least-privilege IAM granted at resource-creation time did not cover subsequent operations (update, introspection). | Verification was completed behaviourally via the Function URL instead of via AWS API introspection. The agent declined to broaden its own permissions. Note: this mirrors the delegation principle the engine itself enforces — an authorisation obtained for one hop does not authorise the next. Production approach: a scoped deployment role covering the full deploy lifecycle, granted once and reviewed. | Not fixed (behavioural workaround) |
| D-26 | `git status --short` showed the `Dockerfile` — the build input for the AWS Lambda deployment — as untracked (`??`) in `~/aivar_tba` | The Dockerfile was never `git add`ed and existed only in a working tree (originally written in the separate `gae-aws`/`aws-deploy` checkout, itself never committed there either — see L-J). A fresh clone could not have rebuilt the deployed image. Found during repo consolidation, not by a failing build, because the local file was always present so the build never actually failed. | Committed the Dockerfile to `master`. Production approach: assert that every file referenced by a deploy script is tracked, as a CI check. | Fixed |
| D-27 | Post-push verification against Railway using freshly-invented resource names (`post-push-readonly`, `post-push-single-update`) showed read escalate to CONFIRM and single-update escalate to FULL_REVIEW, both via `floor_name":"novelty_unprecedented"` — one tier above the documented AUTONOMOUS/CONFIRM baseline, initially reading as a regression from the D-26 Dockerfile commit. | The novelty check (D-14/L-B/L-J territory) correctly escalates any action with no precedent in the audit history, one tier, regardless of its base score. Each response's own `explanation` field showed the base computation was correct before escalation (`"0.12 -> AUTONOMOUS..."`, `"0.42 -> CONFIRM..."`) — this is the novelty feature working as designed, not an app defect. The D-26 commit changed only the Dockerfile and this log; zero `app/*.py` diff. | Not a code fix — a verification-methodology fix. Re-ran the read scenario against both Railway and AWS using the exact seeded resource (`customers/42`, from `demo.sh`/`final-demo-capture.json`, which carries real precedent) and got `floor_name: null`, `tier: AUTONOMOUS` on both, matching baseline exactly. Production approach: smoke/verification scripts must always use the same fixed seeded resource identifiers, never freshly generated ones, or must explicitly treat novelty escalation as expected rather than a diff against a non-seeded baseline. | Fixed (verification methodology) |
| D-28 | The tamper-evident audit record could not reproduce its own derivation — sub-scores lived only in the mutable, non-chained risk_assessments table. Integrity and reproducibility are different properties; the chain proved nothing had been altered but the record could not show how the composite was reached. Found in cross-layer reconciliation, not by a failing test. | `app/main.py`'s `evaluate()` only wrote `tier`/`composite`/`floor_name`/`floors_fired`/`explanation` to the audit payload, never the four sub-scores, `weights_version`, or the deployed code's `git_sha`. | Added `reversibility_score`, `data_scope_score`, `regulatory_score`, `confidence_score`, `weights_version`, and `git_sha` to the audit payload — additive only, existing hash-chained records and chain integrity unaffected. `tests/test_observability.py::test_audit_payload_can_reconstruct_composite_without_risk_assessments_table` proves `sum(WEIGHTS[k] * payload[f"{k}_score"])` equals the payload's own `composite` exactly. Caveat: this holds only as the system is actually configured (`CALIBRATION_MODE=shadow`, confirmed the only mode ever active in this repo/deploy config) — an "enforce"-mode calibration adjustment isn't itself logged, so the reconstruction guarantee would quietly stop holding if enforce mode were ever turned on. Not fixed now (out of scope of what was asked); named here so it isn't a silent gap later. Verified live on both deployments post-deploy (`governance/evidence/final-closeout-clean-v2.txt`). **Caveat resolved (fix pass, 2026-08-22):** `calibration_mode`, `calibration_adjustment`, `base_composite`, `effective_composite` added to the audit payload; `test_audit_payload_reconstructs_composite_under_enforce_mode` proves reconstruction now holds under `enforce` mode too. | Fixed |
| D-29 | Live fuzz sweep (final defect sweep, Part 1A) found 4 reproducible crashes: `affected_records: NaN`, `affected_records: Infinity`, a null byte in `params`, and 500-level-deep nested `params` — all returned raw `500 {"detail":"internal server error"}` instead of a clean 4xx. | Traced to two distinct root causes via local reproduction (TestClient + fakes, no live secrets), not assumed: (1) NaN/Infinity — Pydantic already rejects these correctly (`finite_number` validation error), but `ValidationError.errors()` always echoes the raw offending value, and FastAPI's default `RequestValidationError` handler crashes trying to JSON-render it, since Starlette's `JSONResponse` enforces `allow_nan=False` (`ValueError: Out of range float values are not JSON compliant: nan`) — the crash is in the framework's own error-rendering path, not in validation, so no per-field validator alone could have prevented it. (2) 500-deep `params` — Pydantic itself parses this fine; the crash was at response-serialization time echoing `params` back in `ActionResponse` (`pydantic-core`: `ValueError: Circular reference detected (depth exceeded)`), downstream of where a request-time validator runs. | Added a `RequestValidationError` exception handler (`app/main.py`) that sanitizes non-finite floats before rendering the 422 response — general, not per-field, covers current and future numeric fields. Added `app/schemas.py` validators: `affected_records` rejects non-finite floats (defense in depth); `params` and all four top-level string fields reject null bytes/control characters, `params` additionally capped at 20 levels of nesting and 64KB serialized size, string fields capped at 10,000 characters. `tests/test_input_validation.py` (8 new tests) proves each case now 422s cleanly instead of 500ing. All 4 cases re-verified live on both deployments post-deploy, zero 500s (`governance/evidence/final-closeout-clean-v2.txt`). | Fixed |
| D-30 | Five parallel background forks were dispatched for the final defect sweep, all writing to the same live shared database (evaluate() calls append audit records) concurrently. One fork became unaddressable partway through and, on its next check-in, echoed a redone copy of three OTHER forks' assigned work instead of its own assigned Part 3 — indistinguishable at the time from genuine independent re-verification, since its findings happened to match. | Same shape as D-15 (module-scoped async fixtures colliding across a shared resource under concurrent access) — concurrency across independent workers sharing one mutable resource (here: a live database and, implicitly, task/session state) produces behavior that doesn't fit the single-writer mental model the rest of this system assumes. Not caught by a failing test — caught by the coordinating session noticing the redone-work pattern didn't match what was actually asked of that fork. | Not fixed in code — a process-design lesson. The actual Part 3 (security/adversarial) results were re-requested and obtained correctly afterward. Production approach: parallel forks/agents are safe for read-only analysis (each independently reading code/live state, no shared mutable resource contention); anything that WRITES (here: every `evaluate()` call appends to the shared audit log) should run from a single session/worker, sequentially, so state changes stay attributable and reconciliation numbers stay meaningful — exactly the constraint later honored in this session's own live verification calls (run sequentially, not from parallel forks). | Not fixed (process finding) |
| D-31 | Deployment staleness verification was previously behavioural and indirect — inferring which build was running from response schema shape, a 422-vs-500 difference, and Lambda's LastModified timestamp. Three separate staleness incidents (D-16, D-22, and the post-rotation sync) each required forensic reconstruction. | No single source of truth existed for "what code, with what dependencies, is this deployment actually running right now" — every check was an inference from indirect behavioral evidence. | `GET /v1/version` now reports `git_sha`, `build_time`, and live dependency versions read via `importlib.metadata`, making staleness a single GET on each deployment instead of forensic reconstruction. Related to D-17: `weights_version` pins the weights, `git_sha` pins the code, and the ECR image digest (reported at build/push time, e.g. `sha256:dac517a7...` for this deploy) pins the artifact — every input to a decision is now identified. | Fixed |
| D-32 | While building L-I's `test_all_four_layers_agree` (DB-backed), driving `evaluate()` through `TestClient` with a real `SQLAlchemyStore`/`SQLAlchemyAuditLog` hung indefinitely — no timeout, no error. `pg_stat_activity` showed ZERO active queries during the hang, ruling out a slow query. | Two independent causes, both confirmed by direct reproduction, not assumed: (1) `TestClient` runs the FastAPI app in its own internal event loop, which deadlocks against pytest-asyncio's function-scoped loop that created the async `store`/`audit_log`/`engine` fixtures — same class of issue as D-08, just manifesting as a silent hang instead of a raised error, and not previously hit because no other test in `tests/test_db_store.py` combined `TestClient` with a real engine. (2) After rewriting the test to call `app.main.evaluate()` directly (avoiding cause 1), it still took minutes: `app/calibration.py`'s `calibration_for_action_type()`/`_historical_outcomes()` does one `store.get_action()` round-trip PER historical confirmed/decision audit record — an N+1 pattern that only became slow enough to notice once this session's live testing had grown `audit_records` to 600+ rows. Measured in isolation: 122 seconds for just 17 records. | Cause 1: the new test calls `evaluate()`/`get_action()` directly instead of through `TestClient`, matching every other test in the file. Cause 2: **RESOLVED (clean-v3 closeout, 2026-08-22, `app/calibration.py`/`app/db_store.py`/`app/audit.py`):** `_historical_outcomes()` (the N+1 loop) removed; `compute_calibration_by_action_type()` now calls a new `audit.calibration_outcomes(store)` — on the DB-backed path (`SQLAlchemyAuditLog`) this is a single aggregate SQL query (CTE + `UNION ALL`, `GROUP BY action_type`, joining `audit_records`/`actions`/`approvals`) instead of one round trip per historical record; the in-memory test double (`AuditLog`) keeps a plain Python iteration, which has no I/O cost so was never the problem. Read-only change, no routing impact — `git diff --stat -- app/risk/` empty. Verified bit-identical output before/after against the live shared DB (dict equality asserted programmatically). Timing against the then-current 633 records (629 at the time this row was first written, grew by 4 from this session's own `/v1/version`-adjacent activity): 119.97s -> 8.49s (~14x; the residual ~8.5s is Neon connection setup, confirmed by this repo's own test-fixture docstring in `tests/test_db_store.py`, not query time). New test `tests/test_db_store.py::test_calibration_report_issues_a_bounded_number_of_queries` asserts query COUNT via a SQLAlchemy `before_cursor_execute` listener (bounded at <=3), not timing, against the same already-600+-row live table — would fail loudly if the N+1 pattern ever regressed. Full suite: 183 passed, 0 skipped, 0 failed (run with a real `DATABASE_URL` so DB-backed tests executed too). Commit `059d3fd`. | Fixed (calibration.py N+1); TestClient/loop conflict fixed in the test |
| D-33 | Railway's `GET /v1/version` kept reporting `git_sha=f5b8575` after a real deploy of `27aa4d2` (new image built, healthcheck passed, `GET /v1/calibration` timing independently proved the new code was running) — the endpoint built specifically to detect deployment staleness was itself serving stale data. | `GIT_SHA`/`BUILD_TIME` were configured as STATIC Railway service variables (set once, manually, in a past session) rather than derived per-deploy. The Dockerfile's `ARG GIT_SHA=unknown`/`ENV GIT_SHA=$GIT_SHA` only sets a build-time default; Railway's own runtime variable of the same name overrides it at container start, and nothing was re-setting that variable on each deploy. Would have silently validated every future Railway deploy as current regardless of what actually shipped. Found while updating variables for this deploy, not by a failing check. Same class of apparent-success-that-was-not as D-16/D-22. | Checked whether Railway auto-injects a git-commit variable for this service before assuming a fix, per instruction not to guess: `railway run env` (full runtime environment, not just configured variables) grepped for `RAILWAY_GIT*`/`GIT_*` and for every `RAILWAY_*` key present — no git-metadata variable exists (only `RAILWAY_ENVIRONMENT*`, `RAILWAY_PROJECT*`, `RAILWAY_SERVICE*`, `RAILWAY_*_DOMAIN`/`_URL`). This repo's Railway service builds directly from the root `Dockerfile` (not Nixpacks), and Railway does not inject git metadata for that build path — confirmed by direct inspection, not documented behavior taken on faith. Since no such variable exists to prefer, `app/main.py`'s `/v1/version` is unchanged (nothing to wire it to); the manual update is now a documented, scripted deploy step instead: `infra/railway/deploy-railway.sh` (new) runs `git push` then `railway variables --set GIT_SHA=<full sha> --set BUILD_TIME=<UTC now>` in one place, and `CLAUDE.md`'s Known constraints section states this requirement explicitly so it isn't rediscovered as a surprise on the next Railway deploy. | Found and worked around (documented+scripted manual step); no code fix exists since no Railway-provided git variable to consume |
| D-34 | Step 3A's constructed fuzz sweep (`governance/evidence/fuzz-matrix.py`, 93 cases against both live deployments) found 3 reproducible 500-class crashes: `NaN` nested inside `params`, `Infinity` nested inside `params`, and an unpaired Unicode surrogate in a string field (`resource`) - all confirmed live via Railway tracebacks, not inferred. | `params: dict[str, Any]` in `app/schemas.py::EvaluateRequest` has no finiteness, encoding, or size constraint applied to values nested inside it - unlike `affected_records` (a typed `int` with its own D-29 finiteness validator). NaN/Infinity pass Pydantic cleanly and crash at INSERT (`app/db_store.py:111`) when Postgres's JSON parser rejects the literal `NaN`/`Infinity` tokens (`asyncpg.exceptions.InvalidTextRepresentationError: invalid input syntax for type json`). A lone UTF-16 surrogate is syntactically valid inside a JSON string (`json.loads` decodes it fine) but has no UTF-8 encoding, and nothing in the existing control-character validator checked for that - it crashed asyncpg encoding the VARCHAR parameter (`UnicodeEncodeError: 'utf-8' codec can't encode character '\ud800'... surrogates not allowed`). **Different boundary than D-29**: D-29 fixed the typed top-level `affected_records` field and the request-validation-error *rendering* path (`_json_safe()`, `app/main.py:80-101`); these three are on the *success* path, inside the untyped `params` dict (or an unconstrained top-level string field), never reached by D-29's validators. | Added the UTF-8-encodability check to `_reject_control_chars()` (`app/schemas.py`) - the single function already shared by every string-typed field (`agent_id`/`action_type`/`resource`/`idempotency_key` AND every `params` key/value), so one fix point covers the typed top-level fields (closing the actual `resource` crash reproduced) and the untyped `params` dict together. Added a non-finite-float check to `_validate_params_value()`, recursing through dicts/lists exactly as the existing depth/control-char checks already do. `MAX_PARAMS_DEPTH=20` was already an explicit limit (not parser-tolerance-dependent); re-verified live (4/4 direct retests) that a 21-deep structure correctly 422s - a single transient 502 seen during the batch sweep did not reproduce and was unrelated to the depth check. Coverage: `EvaluateRequest.params` (`POST /v1/actions/evaluate`) is the ONLY endpoint/field accepting a `params` dict as input in this API - confirmed by enumerating every `@app.*` route and every schema field named `params` (`ActionResponse.params` is output-only, echoing already-validated content). 5 new tests in `tests/test_input_validation.py`, one per defect plus a no-regression case (real Unicode/finite floats/reasonable nesting still accepted). Validator-only change: `git diff --stat -- app/risk/` empty; `git diff a573663 -- tests/test_routing.py` empty. Full suite: 195 passed, 0 skipped, 0 failed. **Round 2, found by live re-verification, not assumed complete:** deploying this to Railway and re-running the live fuzz matrix showed the params/NaN/Infinity cases now correctly 422, but all 3 surrogate cases still 500'd - a NEW crash, not the old one. Rejecting the surrogate in the validator worked exactly as intended, but Pydantic's `ValidationError.errors()` echoes the raw offending input value back (the whole `params` value or `resource` string, including any bad dict key), and `app/main.py`'s `_json_safe()` (the D-29 fix for this exact "echoes the raw input" pathology) only handled non-finite floats, not unencodable strings - so `Starlette`'s `JSONResponse.render()` crashed `.encode("utf-8")`-ing the *error response* this time, confirmed via a second live Railway traceback (`starlette/responses.py:187`, same `UnicodeEncodeError`, different call site). Extended `_json_safe()` to also sanitize unencodable strings (both dict keys and values, recursively) via `errors="backslashreplace"` - same intent as the existing float fix, still debuggable, always JSON-safe. New HTTP-level test (`test_unpaired_surrogate_rejected_with_clean_422_not_500`) exercises the full request-to-response path with `raise_server_exceptions=False`, not just schema validation, so a regression here would fail loudly rather than only being caught by live re-verification again. Full suite after round 2: 196 passed, 0 skipped, 0 failed. `git diff a573663 -- tests/test_routing.py` still empty. **Deployed and verified live on both platforms (2026-08-23):** Railway re-run of the full 93-case matrix after round 2 - 0 BLOCKING, 0 unexpected; AWS re-run of the same full matrix after console deploy - 0 BLOCKING, 0 unexpected (see `governance/evidence/final-closeout-clean-v4.txt` for both complete tables). | Fixed |
| D-35 | A Lambda console image deploy presented as complete but did not apply - the third such occurrence in this project (D-22, and two earlier saves this session: `GET /v1/version` still reported `27aa4d2` after the image had been pushed and the console reported success). Detected only because `/v1/version` was checked afterward, not because the console surfaced any failure. | Same shape as D-22: `update-function-code`/console-save reporting success is not proof the function's running image actually changed. `aivar-deploy`'s IAM identity lacks `lambda:GetFunction`/`lambda:GetFunctionConfiguration` (D-23's least-privilege ceiling), so no programmatic post-deploy assertion (`LastModified` advancing, digest match) is possible from this session - the only available check is the application-level `GET /v1/version`, which is exactly what caught this. | Not fixed in code - environmental/IAM, same as D-23. Production approach: a scoped IAM policy granting `GetFunction`/`GetFunctionConfiguration`/`UpdateFunctionCode` on this function's ARN alone (not broader Lambda access), enabling `aws lambda wait function-updated` plus a digest assertion in `infra/aws/deploy-lambda.sh` - closing the same gap D-23 already named, now with a third concrete recurrence as evidence it's worth prioritizing. Workaround (used successfully every time so far): re-check `GET /v1/version` after every console deploy before treating it as live; this is now standard practice in this session's own deploy verification steps, not just documented. | Found, not fixed (environmental/IAM ceiling, workaround in place) |
| D-36 | Secrets exposed seven times: `railway variables` dumps, `aws lambda get-function` metadata, and recursive greps over a directory containing `.env`. Root cause: secrets stored as plaintext environment variables and in a local `.env`, combined with the unsafe command being easier to type than the safe one in every instance. | Containment verified — git history clean, `.env` gitignored and untracked, all credentials rotated. Re-verified independently rather than trusted (this environment's interactive `grep` is aliased to a gitignore-aware `ugrep` wrapper that would silently skip `.env` during a recursive scan and produce a false-clean result; re-checked with `command grep`, bypassing the alias). | Fixed structurally: `scripts/scan-secrets.sh` (filename-only scan wrapper — first version's generic 40-hex-char pattern false-positived on this repo's own git-SHA references in governance docs, caught by actually running it and scoped to code paths only), `scripts/check-var.sh` (scoped variable-check wrapper, PRESENT/ABSENT + 6 chars max, demonstrated on all three platforms), `scripts/pre-commit` (5-pattern block, each demonstrated blocking a fake value with the value never printed — the previous version of this same hook echoed the matched line, a real gap found and fixed here), `app/logging_config.py::RedactSecretsFilter` (log redaction, tested against a real log line), `.github/workflows/secret-scan.yml` (CI diff scan, demonstrated against a pushed scratch branch), and a NEVER-DUMP section in `CLAUDE.md` making the safe form the documented path for all three historical vectors. AWS secrets migration to SSM SecureString **not completed**: `aivar-deploy` has zero `ssm:*`/`iam:*` permissions (`ssm:DescribeParameters` and every `iam:List*Policies` call AccessDenied, confirmed live, not assumed) — stopped per instruction rather than working around it; exact policy JSON and console steps reported to the user instead. Railway retains env vars — no equivalent store — protected by the tooling guardrails only, stated explicitly in the README rather than implying parity with SSM. | Fixed (guardrails); SSM migration blocked on IAM, reported not worked around |

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
  **Re-assessed post-D-24 (final defect sweep, Part 5, 2026-08-21):
  STILL VALID — D-24 fixed the symptom, not the structure.** D-24
  rewrote `evaluate_floors()` to evaluate-all-and-collect and fixed
  `app/main.py`'s novelty step to append rather than overwrite
  `floor_name`, but `route_action()` (`app/risk/router.py`) still
  returns only the base tier; `app/main.py`'s `evaluate()` still
  independently re-derives a possibly-different tier via calibration
  (bypassing `route_action()`) and then escalates it again for novelty.
  Two authorities still exist — D-24 closed the specific
  floor-name-gets-erased consequence this entry originally worried
  about, not the "one authoritative tier-composing function" structural
  gap.
  **RESOLVED (fix pass, 2026-08-22, `app/risk/decision.py`):**
  `compose_final_decision()` now owns composite -> calibration ->
  thresholds -> floors -> novelty -> final tier/floors_fired/explanation
  end-to-end, wrapping `route_action()` (unchanged) rather than
  reimplementing it. `app/main.py`'s `evaluate()` gathers I/O then makes
  ONE call and uses the result verbatim — no post-hoc tier adjustment
  anywhere in `main.py`. Bit-identical refactor, proven by
  `tests/test_decision.py::test_refactor_preserves_every_tier` sweeping
  the full input grid (2,916 combinations) against `route_action()`'s
  own output. `app/risk/router.py`/`floors.py`/`scorer.py`/`tiers.py`
  stayed at zero diff throughout.
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
  **MOSTLY RESOLVED (fix pass, 2026-08-22):** `uncertainty_score` added
  to the response and audit payload (same value, honest name);
  `llm_confidence_raw` added (the actual model self-report, uninverted,
  evaluate()-only — no DB column holds it); `confidence_score` kept as a
  documented deprecated alias.
  `tests/test_scoring.py::test_direction_contract_per_dimension` added,
  parameterised across all 4 composite dimensions — passed immediately,
  confirming the composite math was always directionally correct, only
  the field name lied. **Remaining half, still deferred as this entry
  originally said:** the `risk_assessments.confidence_score` DB column
  itself is NOT renamed — that migration + backfill of a live table is a
  separate, not-yet-scheduled change.
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
  **Checked (fix pass, 2026-08-22):** `SELECT count(*) FROM actions;` ->
  629; `SELECT count(*) FROM actions WHERE params::text ~ '[^\x00-\x7F]';`
  -> 6. Non-zero, so per instruction: reported, not migrated. Inspected
  (read-only) which 6: all `agent_id="fuzz3"`, `resource="customers/42"`,
  `params={"note": "café"}`, timestamped 2026-08-21 13:32-13:35 UTC —
  this session's own live NFC/NFD verification calls from the `clean-v2`
  closeout, not pre-existing historical/production data. Not treated as
  license to migrate anyway; the question of real historical non-ASCII
  params remains formally unanswered beyond these 6 self-generated rows.
  **FULLY RESOLVED (clean-v3 closeout, 2026-08-22):** the above claim was
  independently re-verified by direct inspection, not taken on the prior
  entry's word — re-ran the query live against the same shared DB
  (`total actions: 633`, `non-ascii params count: 6`, same 6, listed by
  `agent_id`/`action_type`/`resource`/`created_at`: all
  `agent_id="fuzz3"`, `action_type="read"`, `resource="customers/42"`,
  `created_at` 2026-08-21 13:32:37–13:35:09 UTC). All 6 confirmed still
  this session's own NFC/NFD test artifacts from the `clean-v2` pass, zero
  genuine historical data. L-H's forward-only limitation is therefore
  MOOT in practice: no historical/production record is affected by the
  NFC-normalization change. L-H is closed with no further action pending.
- **L-I** Cross-layer reconciliation (API / `risk_assessments` / audit
  / CLI) was verified manually during hardening — repeatedly, across
  OD-1, the bonus calibration feature, and this session's live
  captures — not by a standing automated test. Production approach:
  one test asserting pairwise equality across all four layers for a
  single evaluate call.
  **Narrowed (final defect sweep, Part 2B + D-28, 2026-08-21):** the
  specific gap that sweep found (the audit payload couldn't reconstruct
  `composite` at all — no sub-scores, no `weights_version`) is now
  closed and covered by
  `tests/test_observability.py::test_audit_payload_can_reconstruct_composite_without_risk_assessments_table`.
  Still STILL VALID as a general entry: that one test covers composite
  reconstruction, not the full pairwise equality across all four layers
  (`floors_fired`/`precedent`/`calibration` remain deliberately absent
  from the DB-row layer by design — see `app/schemas.py`'s
  `ActionResponse.floors_fired` comment — which a full pairwise-equality
  test would need to account for as expected, not flag as a failure).
  **RESOLVED (fix pass, 2026-08-22):** standing, automated, DB-backed
  `tests/test_db_store.py::test_all_four_layers_agree` — real
  `SQLAlchemyStore`/`SQLAlchemyAuditLog`, one `evaluate()` call, asserts
  pairwise equality of the 4 sub-scores/composite/tier/floor_name/
  `weights_version` across the API JSON, the `risk_assessments` row
  (added `weights_version` read-back, persisted since T-14 but never
  exposed until now), and the audit payload, plus a real
  `cli.render_result()` call for the CLI layer. The documented
  `floors_fired`/`precedent`/`calibration` DB-row exception is asserted
  explicitly, not silently smoothed over. Found while building this
  test, not fixed here (out of scope for this pass): `app/calibration.py`'s
  `calibration_for_action_type()` does one DB round-trip per historical
  audit record (N+1) — measured at 122s for 17 records against this
  session's grown `audit_records` table; the new test forces
  `CALIBRATION_MODE=off` to avoid that path.
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
- D-36: **CORRECTED (2026-08-25, live-verified, not assumed):** local
  `.env` was originally logged here as holding dead pre-rotation
  credentials - checked directly and that was wrong. `DATABASE_URL`:
  every DB-backed pytest run this session (199/199, repeatedly) has
  succeeded against real Neon using exactly this value. `OPENAI_API_KEY`:
  live `GET https://api.openai.com/v1/models` with this key returned
  HTTP 200 (status code only checked, key never printed). **Local `.env`
  holds the current, rotated values for both.**
  What IS still dead, found live, not assumed: GitHub Actions'
  `secrets.DATABASE_URL` - a SEPARATE copy, confirmed stale via the most
  recent `tests` workflow run on `master` (`15b4932`, run `32864124175`):
  `asyncpg.exceptions.InvalidPasswordError: password authentication
  failed for user 'neondb_owner'` (9 failed, 9 errored, 190 passed) -
  this is currently blocking CI on every push/PR. Not fixed here -
  requires the actual rotated value, which this session does not have
  reason to hold or set. Needs updating via GitHub UI (Settings ->
  Secrets and variables -> Actions -> `DATABASE_URL`).
