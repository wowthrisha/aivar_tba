# Overnight Hardening Pass — clean-v5

Branch: `feature/hardening-v5` (pushed, not merged). Base: `clean-v4` (`ff706ee3c0ed4da7af933604df0cc5b8642e0b5a`).
Rules followed: branch-only (S1), no deploys (S2), no migrations (S3), frozen weights/thresholds/floor-triggers/fail-closed-direction/`tests/test_routing.py` never touched persistently (S4), `scripts/scan-secrets.sh`/`scripts/check-var.sh` used for anything secret-adjacent (S6), sequential only (S7), two-attempt rule (S8, not triggered — nothing failed twice), no force-push/history-rewrite/tag-deletion (S9).

## 1. Phase 1 — register claim vs. actual

Read-only. None of these were trusted from the register without independent evidence.

| ID | Register claim | Actual | Evidence |
|---|---|---|---|
| L-B | `compose_final_decision()` is the sole tier authority, no post-hoc adjustment | **Confirmed shipped** | `app/main.py:371` calls it once; `app/main.py:382` assigns `final_tier = decision.tier` directly, used verbatim at lines 406/418 — no reassignment anywhere in between |
| L-G | `uncertainty_score`/`llm_confidence_raw` in API response; DB column still `confidence_score` | **Confirmed shipped, remaining half correctly still deferred** | `app/schemas.py:199,201,206`; `app/db_models.py:51` (`confidence_score`, unrenamed) + `:57` (`uncertainty_score`, additive) |
| L-I | Standing 4-layer reconciliation test exists | **Confirmed shipped** | `tests/test_db_store.py::test_all_four_layers_agree` (line 227) |
| D-32 | Single-aggregate calibration query at HEAD | **Confirmed shipped** | `app/db_store.py:354` `calibration_outcomes()`; the old `_historical_outcomes()` N+1 loop no longer exists anywhere |
| D-33 | `GIT_SHA` derived or manually set — for both platforms | **Manually set on both, confirmed not inferred** | Railway: `infra/railway/deploy-railway.sh:23,27` (`railway variables --set`); Lambda: `infra/aws/deploy-lambda.sh:40,59` (Docker `--build-arg`) |
| 2B | TTL env vars shipped or still constants | **Confirmed shipped** | `app/main.py:143,149-150` (`_ttl_from_env`, `CONFIRM_TTL_MINUTES`/`FULL_REVIEW_TTL_HOURS`) |
| 2C | Floor counterfactuals shipped or absent | **Confirmed shipped** | `app/risk/floors.py:102,112,121,131` — all 4 floors |

**Full suite (Phase 1)**: `199 passed in 191.76s`, 0 failed, 0 skipped. `git diff a573663 -- tests/test_routing.py`: empty.

## 2. Phase 2 — test quality

### 2.1 Classification (199 tests, full read of all 18 files)

- **BEHAVIOURAL**: 192
- **INTERNAL**: 2 — `tests/test_security.py::test_ttl_env_var_overrides_default`, `test_ttl_absent_env_var_yields_current_default`. Both test the private `_ttl_from_env()` helper and module-level constants directly; neither exercises the real `/confirm`/`/decision` → `expires_at` flow. A mutation breaking the wiring between `CONFIRM_TTL`/`FULL_REVIEW_TTL` and where they're actually used would not be caught by these two.
- **WEAK**: 5 — `tests/test_api.py::test_execute_requires_approval_hash_match` (literally `pass`, zero assertions); `tests/test_cli.py::test_cli_renders_all_three_tiers_without_error` (only checks non-empty output); `tests/test_floors.py`'s four 2C counterfactual tests (`test_irreversible_bulk_reason_contains_counterfactual` etc. — only check a keyword substring is present in `result.reason`, not that the counterfactual claim is actually true).

Two borderline cases resolved as BEHAVIOURAL, not INTERNAL, with reasoning: `test_llm.py`'s `parse.assert_awaited_once()`/`await_count` assertions (call-count is the only observable way to test a caching contract at the SDK boundary) and `test_db_store.py::test_calibration_report_issues_a_bounded_number_of_queries`'s query-count assertion (query count *is* the D-32 contract being verified). `test_adversarial_fixes.py::test_finding4a_prompt_delimits_untrusted_content` asserts on literal prompt string content — also kept BEHAVIOURAL since the fix *is* the prompt text; the contract has no other observable form.

### 2.2 Mutation check — 9/9 CAUGHT

Every mutation: stated in advance, applied, full suite run, reverted via `git checkout --`, `git diff --stat` confirmed empty before the next one.

| Mutation | Tests failed | Verdict |
|---|---|---|
| `scorer.py`: invert `REVERSIBILITY_BAND` (READ=1.0…IRREVERSIBLE=0.0) | 17, incl. frozen `test_routing.py::test_read_only_routes_autonomous` | **CAUGHT** |
| `scorer.py`: swap `reversibility`/`confidence` weights | 6, incl. `test_weights_are_frozen` | **CAUGHT** |
| `floors.py`: `final_tier()` `max`→`min` (escalate→de-escalate) | 15, incl. frozen `test_routing.py::test_bulk_delete_routes_to_review` | **CAUGHT** |
| `floors.py`: reverse `FLOOR_PRIORITY` | 9, incl. frozen `test_routing.py::test_audit_breakdown_is_human_readable` | **CAUGHT** |
| `tiers.py`: `>=`→`>` at `THRESHOLD_FULL_REVIEW` | 1 (`test_boundary_band_tier[0.65-2]`) | **CAUGHT** — narrow: exactly one parametrized point catches it |
| `llm.py`: timeout → `confidence=1.0, degraded=False` (fail-open) | 2 (`test_llm.py` only) | **CAUGHT** — narrow: only unit tests mocking the SDK catch it; no integration test verifies the downstream routing effect (that a timeout should still trip `low_confidence_on_mutation`) |
| `embeddings.py`: `novelty_floor_should_escalate` `<`→`>` | 2 (`test_novelty.py`) | **CAUGHT** — note: `test_escalate_only_invariant_holds_with_novelty_floor` did *not* catch it; that invariant only checks "never de-escalates," not "escalates in the right direction" |
| `audit.py`: `AuditLog.append()` always `prev_hash = GENESIS_HASH` | 1 (`test_s5_tampered_middle_record_is_detected`) | **CAUGHT** — narrow: single direct hit |
| `app/schemas.py`: remove the non-finite-float check in `_validate_params_value` | 2 (D-34's own regression tests) | **CAUGHT** |

**Result: 0 NOT CAUGHT.** No new test was required by the instructions (only NOT CAUGHT mutations require one). The core risk-scoring/routing/audit/validation logic has genuine, not-manufactured, test coverage — including via the frozen criterion tests themselves for 3 of the 4 highest-impact mutations. Three mutations (tiers.py, llm.py, audit.py) were caught by only 1-2 tests each — not broad redundancy, but real, direct coverage; flagged as narrower than the others rather than glossed over.

Mutation runs used in-memory tests only (no `DATABASE_URL` set, for speed) — this does not weaken any of the 9 results since all 9 mutated functions are exercised by in-memory-backed tests; the DB-backed suite would only add redundant coverage here, not unique catches.

### 2.3 Skipped tests

Exactly one skip source: `tests/test_db_store.py`'s module-level `skipif("DATABASE_URL" not in os.environ)`, covering all 9 tests in that file. Documented, approved architectural decision (file's own docstring: "approved test-DB strategy: no separate test DB/rollback infra"); **justified**. Real, worth-naming tradeoff: running `pytest -q` without `DATABASE_URL` silently loses L-I's reconciliation test, D-32's query-count regression test, and 2A's DB-column test — exactly what happened during this pass's own mutation-testing runs (by design, for speed).

## 3. Phase 3 — version reporting

**3.1**: Re-verified fresh (not assumed from a past session — 4th independent check across this project) that Railway exposes no `RAILWAY_GIT_COMMIT_SHA` (`railway run env`, full runtime environment; exhaustive `RAILWAY_*` key list unchanged). Added `_git_sha_and_source()` (`app/main.py`) so if that variable is ever added, it wins automatically (`source="derived"`); until then, distinguishes the two current manual mechanisms — `"manual"` (Railway, detected via any `RAILWAY_*` var present) vs. `"build-arg"` (Lambda/Docker, none present).

**3.2**: `infra/aws/deploy-lambda.sh` now re-reads `git rev-parse HEAD` immediately before the docker build and fails loudly if it drifted from the `GIT_SHA` captured earlier in the script — catches a concurrent commit or a stale shared `BUILD_DIR` checkout silently baking a wrong SHA into the image. Syntax-checked (`bash -n`), not executed (S2: no deploys).

**3.3**: `/v1/version` gains `git_sha_source` (`"derived"`/`"manual"`/`"build-arg"`/`"unknown"`). 4 new tests, one per value.

**Full suite (Phase 3)**: `203 passed in 190.19s`, 0 failed, 0 skipped. `git diff a573663 -- tests/test_routing.py`: empty. `app/risk/`: untouched.

## 4. Phase 4 — small closures

**No new work required.** Phase 1 already found 2B, 2C, and L-I fully shipped on `clean-v4` (this branch's base) — confirmed identical on this branch. 4.3's specific ask (agreement including "counterfactual") is already satisfied in substance: `test_all_four_layers_agree`'s existing 3-way `explanation` equality (`api == payload == db_row`) covers it, since 2C's counterfactual sentences are embedded in `explanation`, not a separate field.

## 5. Phase 5 — verify, do not deploy

- **5.1**: `203 passed in 185.89s`, 0 failed, 0 skipped (real `DATABASE_URL`).
- **5.2**: `git diff a573663 -- tests/test_routing.py` — empty.
- **5.3 (tier-invariance gate)**: `git diff clean-v4..HEAD -- app/risk/` — **empty** (byte-identical, the strongest possible guarantee for these pure functions). The only other diff touching `app/main.py` is 100% confined to the `/v1/version` endpoint (added `_git_sha_and_source()`, wired one new response field) — zero touch on `evaluate()` or any tier-computation path. Every tier/`floor_name`/`floors_fired` is therefore necessarily identical to `clean-v4` by construction, not by a runtime sweep that could theoretically miss something.
- **5.4**: `feature/hardening-v5` pushed to origin. `master` untouched (`eb13d36`, same as before this pass started). `clean-v4` tag untouched (`ff706ee3c0ed4da7af933604df0cc5b8642e0b5a`, verified via `git rev-list -n1 clean-v4` — an annotated tag's own `git rev-parse` output is the tag *object* hash, not the commit; verified with the correct command after an initial false alarm on my own part, caught and corrected before reporting).
- **5.5**: this file.

## What shipped on the branch, per phase

- **Phase 1**: nothing (read-only verification).
- **Phase 2**: nothing (mutation tests all reverted; no NOT CAUGHT findings requiring a new test).
- **Phase 3**: `_git_sha_and_source()` + `git_sha_source` field (`app/main.py`, `app/schemas.py`), `deploy-lambda.sh` drift assertion, 4 new tests (`tests/test_version.py`). One commit (`4ab1b41`).
- **Phase 4**: nothing (already shipped on `clean-v4`).
- **Phase 5**: this report.

## What was attempted and abandoned under S5/S8

Nothing. No fix failed twice; no full-suite run went red at any point (every mutation's failures were the *intended*, verified-then-reverted signal, not an unintended break).

## Anything requiring the user

1. **Merge decision**: this branch is ready to review/merge (see below) — not merged automatically, since only branch-push was authorized.
2. **Deploy**: `git_sha_source` is new response content — once merged, both platforms need redeploying (Railway auto-deploy + `deploy-railway.sh`; Lambda console deploy) and re-verifying, same pattern as every prior closeout, before any future `clean-v*` tag should move past `ff706ee`.
3. **No IAM grants, no GitHub secrets, no console actions were needed for this pass** — everything here was branch-local.

## Is the branch safe to merge, and what must be verified after

**Safe to merge as code**: full suite green (203/203), frozen criterion tests and `app/risk/` untouched (verified both by empty git diff and by the exhaustive mutation/grid tests themselves passing), no secrets touched, no destructive git operations.

**Must be verified after merging and deploying** (not done here, per S2):
- `/v1/version` on both platforms reports `git_sha_source` correctly once redeployed — expect `"manual"` on Railway (has `RAILWAY_*` vars, no `RAILWAY_GIT_COMMIT_SHA`) and `"build-arg"` on Lambda (no `RAILWAY_*` vars) — this is an inference from the code logic, not yet observed against a live process, since nothing was deployed this pass.
- `deploy-lambda.sh`'s new drift assertion has never actually run against a real build — syntax-checked only. Low risk (it's a read-only `git rev-parse` comparison before the build, matching the pattern of D-21/D-22's existing assertions in the same script), but genuinely unexercised.

## Final verdict

**Verified**: all 7 register claims checked in Phase 1 hold (L-B, L-G, L-I, D-32, D-33, 2B, 2C); 9/9 mutation checks caught by the existing suite, with the 3 narrow ones (tiers.py, llm.py, audit.py) named explicitly rather than left implied as robust; the one skip is documented and justified; `git_sha_source` implemented, tested, and reasoned through for both platforms without guessing; the deploy-lambda.sh drift assertion is syntactically sound; the tier-invariance gate holds by construction (byte-identical `app/risk/`).

**Open by decision**: 2 INTERNAL tests (TTL wiring not exercised end-to-end) and 5 WEAK tests (1 empty, 1 non-empty-only, 4 substring-only counterfactual checks) — named specifically above, not fixed, since Phase 2 was scoped as "read, classify, mutate, report," not "rewrite the suite." The DB-test skip-when-`DATABASE_URL`-absent tradeoff stands as previously approved.

**Not exhaustively checked**: the `git_sha_source` field's actual behavior on a live, deployed process (code-reasoned, not live-observed — no deploy occurred); the `llm.py` fail-open mutation's downstream *routing* effect specifically (caught at the unit level, not integration level — a real, if narrow, gap in what "caught" means for that one case); whether other, un-enumerated mutations beyond the 9 specified would also be caught (only the 9 named were tested — this is not a claim of general mutation-testing coverage across the whole codebase).

This is not "error free." It is what was actually checked, stated as such.
