# Defect Register

| ID | Symptom | Root cause | Fix | Status |
|---|---|---|---|---|
| D-01 | `tests/test_llm.py::test_out_of_range_value_is_rejected` failed with `UnboundLocalError` | Python deletes an `except ... as name` binding when the except block exits, so `real_validation_error` was referenced after being auto-deleted | Captured the exception into a variable declared before the try/except instead | Fixed |
| D-02 | Every real `/v1/actions/evaluate` call showed `llm_degraded: true` in the audit log during T-10's live curl verification; confidence always fell back to 0.0, spuriously tripping the T-07 `low_confidence` floor on low-risk actions | `app/llm.py`'s `.parse()` call passed `temperature=0` per T-09's literal spec. The live pinned model (`gpt-5.6-luna`) rejects that value: `Error code: 400 - "Unsupported value: 'temperature' does not support 0 with this model. Only the default (1) value is supported."` T-09's mocked tests never caught this since they mock the client and never exercise the real `temperature` argument. Present since T-09's original commit — not introduced by T-10. | Removed the `temperature=0` argument (approved by product owner) so the SDK uses the model's required default. Root cause and fix confirmed via a real live call: `confidence: 0.82, degraded: False, reason: None`. | Fixed |
| D-03 | T-12's race test (`test_race_concurrent_decisions_resolve_via_conditional_update`) passed 5/5 runs against T-10's original `decision` handler, which read `record.state`, checked it, then mutated it as two separate steps — a genuine read-then-write, not a conditional update | Python's GIL plus the specific fast, non-yielding code path in that handler made the race window narrow enough that this synthetic two-thread test never happened to land in it. A passing test is not proof of a correct implementation when the spec mandates a specific mechanism (conditional UPDATE), not just an observed outcome. | Added `InMemoryStore.conditional_transition` (lock-guarded atomic check-and-set) and switched confirm/decision/execute to use it instead of read-then-write, regardless of the test already passing. | Fixed |
| D-04 | `test_s5_tampered_middle_record_is_detected` passed in isolation but failed when the full suite ran together (`valid=True` when `False` was expected) | The test indexed into the app's shared `_audit` module-level singleton, which accumulates audit records across the entire pytest session (every test that calls evaluate/confirm/decision/execute appends to it). `records[1]` picked up an unrelated record from a different, earlier test whose `tier` value already equaled what the test was "tampering" it to — a no-op mutation that happened to leave the hash chain intact. | Rewrote the test to construct and tamper a fresh, isolated `AuditLog()` instance directly, rather than reaching into the shared app singleton. | Fixed |
| D-05 | `test_finding1_update_without_snapshot_no_longer_autonomous` (T-13 fix regression test) failed: `assert 0.38 == 0.28 ± 2.8e-07` | Test called `score_action(..., llm_confidence=0.0)` but the comment and expected value (`composite=0.28`) were computed for the review's actual case, `llm_confidence=1.0` — a copy/transcription mismatch between the two calls in the same test (the second call, to `evaluate_floors`, correctly used `1.0`). | Corrected the `score_action` call to use `llm_confidence=1.0`, matching the review's original input. | Fixed |

## LEFT OUT

Scope explicitly cut or deferred, and why. Per contract E-6/E-7 — report
what was not done, never substitute silently.

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
- T-11: the 9 T-10 business endpoints (evaluate, confirm, decision,
  execute, audit list/verify, review-queue) still read/write
  `InMemoryStore`/`AuditLog`, not the new Postgres tables. T-11's DoD
  ("alembic current on DIRECT; app on POOLED; \dt shows 4 tables") is
  infrastructure proof and does not literally require the swap; a full
  store rewrite would mean re-touching and re-verifying all 9 already-
  proven T-10 endpoints, which is a materially larger task than the 1h
  box. Only `/readyz`'s db check uses the real POOLED engine. Production
  approach: implement a `SQLAlchemyStore` behind the same interface as
  `InMemoryStore` (the interface was already kept swap-ready for this)
  and switch `app/main.py`'s `_store`/`_audit` module globals over to it
  — flagged for explicit confirmation before doing it, since it's a
  meaningfully larger change than T-11's literal DoD asks for.
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
