# Block 2 Report — 2026-08-20

Service (API, persistence, security controls, adversarial review). All
four tasks done, Gate G2 passed (see `governance/gates/G2-report.md`).

## Tasks

| ID | Status | Evidence file | Box | Actual |
|---|---|---|---|---|
| T-10 | Done | T-10-pytest.txt, T-10-curl.txt | 1h | ~40m (incl. T-09 fix cycle) |
| T-11 | Done | T-11-verification.txt, T-11-pytest.txt | 1h | ~11m |
| T-12 | Done | T-12-pytest.txt, T-12-full-suite.txt | 1h | ~6m |
| T-13 | Done | T-13-adversarial-review.txt, T-13-fixes-pytest.txt, T-13-fixes-full-suite.txt, T-13-adversarial-cases-rerun.txt | 30m | ~56m |

All paths relative to `governance/evidence/`. "Actual" is commit-timestamp
spacing, AI-paced, not human-equivalent effort.

## DoD verification

| ID | Clause | Result | Evidence |
|---|---|---|---|
| T-10 | curl each endpoint, responses pasted | PASS | T-10-curl.txt: all 9 endpoints, real request/response pairs, real LLM calls (`llm_degraded: false` post-fix) |
| T-11 | alembic current on DIRECT; app on POOLED; \dt shows 4 tables | PASS | T-11-verification.txt: `alembic current` → `473900d668ec (head)`; `\dt`-equivalent shows exactly actions/approvals/audit_records/risk_assessments; live `/readyz` → `db: "ok"` via the real POOLED engine |
| T-12 | one test per control, five named in output | PASS | T-12-pytest.txt: `test_s1_*`, `test_s2_*` (x2), `test_s3_*`, `test_s5_*`, `test_s6_*`, `test_race_*` — 8/8, all named |
| T-13 | findings pasted with my decision on each | PASS | T-13-adversarial-review.txt (4 findings from a fresh session) + conversation record of product-owner decisions on each + T-13-fixes-*.txt (3 approved fixes implemented, tested, verified) |

## Deviations

1. **T-09 defect surfaced mid-T-10**: live curl testing (not the mocked
   pytest suite) found every real evaluate call silently failing closed
   to `llm_degraded: true` — the pinned model rejects `temperature=0`,
   which T-09's spec required and T-09's mocked tests never exercised.
   Stopped per CLAUDE.md's "dependency behaves differently from its
   documentation" trigger, reported with evidence, got explicit approval,
   fixed in a dedicated commit (`4a1823e`) before continuing T-10's own
   verification. Frozen list untouched; only `app/llm.py`'s `temperature`
   argument removed.
2. **T-11 scope boundary**: built the real Postgres schema, migration,
   and POOLED app engine (satisfying the literal DoD), but did NOT
   rewire T-10's 9 business endpoints to use Postgres instead of
   `InMemoryStore` — that's a materially larger change than the DoD
   asks for. Only `/readyz`'s db check uses the real engine. Documented
   as an explicit, named scope decision (not silent) in the action log
   and `03-errors-and-fixes.md`'s LEFT OUT section, flagged for the
   product owner to confirm as a follow-on if wanted.
3. **T-12 caught two defects in my own test code before trusting
   results**: the race test passed 5/5 against the original
   read-then-write `decision` handler purely by GIL-timing luck, not
   correctness — fixed the implementation to a genuine lock-guarded
   conditional update anyway, since the spec mandates the mechanism, not
   just the observed outcome. Separately, an S-5 tamper-detection test
   indexed into the shared `_audit` singleton and silently tampered a
   no-op value from an unrelated earlier test — fixed by testing a fresh,
   isolated `AuditLog` instance.
4. **T-13 changed the frozen override-floors set** — with explicit,
   itemized, pre-implementation approval from the product owner for each
   of the two floor changes (Finding 1's new floor, Finding 2's
   broadened floor), per CLAUDE.md's "never touch the frozen list
   without asking first." Full before/after evidence for both, plus a
   third fix (Finding 4: prompt hardening + schema validation) also
   pre-approved in the same exchange. Finding 3 (boundary brittleness)
   was explicitly NOT fixed, by product-owner decision, and recorded as
   an accepted limitation.

## Defects

| ID | Symptom | Root cause | Fix | Status |
|---|---|---|---|---|
| D-02 | Real evaluate calls always showed `llm_degraded: true` | Pinned model rejects `temperature=0` | Removed the argument (approved) | Fixed |
| D-03 | Race test passed by luck against a read-then-write handler | GIL timing masked a genuine TOCTOU gap | Lock-guarded `conditional_transition`, used regardless of the test already passing | Fixed |
| D-04 | S-5 tamper test passed in isolation, failed in the full suite | Indexed into a session-shared `_audit` singleton; tampered a no-op value | Test now uses an isolated `AuditLog` instance | Fixed |
| D-05 | T-13 fix regression test failed: `0.38 != 0.28` | Test used `llm_confidence=0.0` instead of `1.0`, not matching the reviewed case | Corrected the test input | Fixed |

Full detail for each in `governance/plan/03-errors-and-fixes.md`.

## Cut / deferred

- T-10: `idempotency_key` accepted but only enforced from T-12 onward
  (S-2 built in T-12, not T-10).
- T-10: `GET /v1/actions/{id}` doesn't lazily check approval expiry the
  way `execute` does — a stale GET can show a not-yet-expired APPROVED
  state past its TTL until execute is attempted.
- T-11: the 9 business endpoints still read/write `InMemoryStore`, not
  the real Postgres tables (see Deviation 2 above). Production approach
  named: a `SQLAlchemyStore` behind the same interface.
- T-12/S-3: TTL values are code constants, not runtime-configurable via
  env var, despite S-3's spec saying "both configurable."
- T-13 Finding 3 (boundary brittleness): accepted as a documented
  limitation of the frozen hard-threshold design, per product-owner
  decision — no calibration/smoothing logic added.
- G2 walkthrough: found that a transient LLM failure gets cached and
  "sticks" for that exact `(action_type, resource, params)` key until
  process restart (no cache-entry TTL, no failure/success distinction
  in what gets cached). Confirmed non-regression, fail-closed behavior
  is correct per T-09's spec; flagged as a design question for a future
  task rather than fixed now.

All items above are also recorded in `governance/plan/03-errors-and-fixes.md`'s
LEFT OUT section.

## RAG: **GREEN**

All four tasks done with verified, non-empty evidence; Gate G2 passed;
full suite 105/105 with no regressions at any step; the one genuine
defect found via live testing (not mocks) was stopped-on and fixed with
approval, not silently patched; the adversarial review's approved fixes
are tested-first, verified against the original demonstrated cases, and
T-08's four criterion tests remain byte-for-byte unchanged and green
throughout. Frozen weights/thresholds/fail-closed-direction untouched;
the only frozen-list change (override floors) had explicit, itemized,
pre-implementation approval for each specific rule.

## Next

T-14 (not started — Block 3 not begun, per instruction).
