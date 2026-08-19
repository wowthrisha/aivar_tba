# Gate G2 Report — 2026-08-20

Gate condition (task board): **full suite green locally, state machine proven by curl**

## 1. Every task in Block 2

| ID | Status | Evidence file | Box | Actual (commit span) |
|---|---|---|---|---|
| T-10 | Done | reports/evidence/T-10-pytest.txt, T-10-curl.txt | 1h | ~40m (incl. mid-task T-09 fix) |
| (fix) | Done | reports/evidence/T-09-fix-live-verification.txt | — | — |
| T-11 | Done | reports/evidence/T-11-verification.txt, T-11-pytest.txt | 1h | ~11m |
| T-12 | Done | reports/evidence/T-12-pytest.txt, T-12-full-suite.txt | 1h | ~6m |
| T-13 | Done | reports/evidence/T-13-adversarial-review.txt, T-13-fixes-pytest.txt, T-13-fixes-full-suite.txt, T-13-adversarial-cases-rerun.txt | 30m | ~56m (incl. review round-trip + 3 approved fixes) |

## 2. Evidence file check

All evidence files verified present and non-empty immediately before this
report (see command output pasted in the T-13 close-out and reproduced
here):

```
OK   reports/evidence/T-10-pytest.txt (96 lines)
OK   reports/evidence/T-10-curl.txt (238 lines)
OK   reports/evidence/T-09-fix-live-verification.txt (29 lines)
OK   reports/evidence/T-11-verification.txt (23 lines)
OK   reports/evidence/T-11-pytest.txt (96 lines)
OK   reports/evidence/T-12-pytest.txt (23 lines)
OK   reports/evidence/T-12-full-suite.txt (104 lines)
OK   reports/evidence/T-13-adversarial-review.txt (129 lines)
OK   reports/evidence/T-13-fixes-pytest.txt (32 lines)
OK   reports/evidence/T-13-fixes-full-suite.txt (121 lines)
OK   reports/evidence/T-13-adversarial-cases-rerun.txt (52 lines)
```

No task marked DONE has a missing or empty evidence file.

## 3. Gate pass condition — PASS

**Full suite green locally:**
```
============================= 105 passed in 0.85s ==============================
```
(clean run, post all Block 2 work including the three T-13 fixes)

**State machine proven by curl** — full live walkthrough against a local
uvicorn instance (`app/main.py`, current code including all T-13 fixes),
saved in full to `reports/evidence/G2-state-machine-curl.txt`:

- `PROPOSED -> EVALUATED -> AUTONOMOUS -> EXECUTED` (read action): evaluate
  201 (`state: autonomous`), execute 200 (`state: executed`).
- `PROPOSED -> EVALUATED -> CONFIRM -> APPROVED -> EXECUTED` (update, low
  LLM confidence triggering the `low_confidence` floor): evaluate 201,
  confirm 200 (`state: approved`), execute 200 (`state: executed`).
- `PROPOSED -> EVALUATED -> FULL_REVIEW -> APPROVED -> EXECUTED` (bulk
  delete, `irreversible_bulk` floor): evaluate 201, self-review correctly
  blocked 403 (S-6), decision by a different reviewer 200
  (`state: approved`), execute 200 (`state: executed`).
- `PROPOSED -> EVALUATED -> FULL_REVIEW -> REJECTED` (terminal, blocked
  from executing): decision reject 200, execute attempt 409.
- `GET /v1/audit/verify` at the end of the whole session:
  `{"valid":true,"records_checked":15,"first_invalid_id":null}` — hash
  chain intact across every real state transition made during this
  walkthrough.

One non-regression finding surfaced and investigated during this
walkthrough, documented in the evidence file: the first live evaluate
call hit a transient LLM failure (cold start) which the provider's
per-key cache correctly stored and replayed on identical retries (fail-
closed working as designed — T-09's own spec caches by
`(action_type, resource, params)` with no mention of excluding failures).
Confirmed non-regression: a fresh resource identifier got a genuine
successful call immediately. Flagged as a design observation for a
future task (should failed results be cached, or should there be a
short TTL/negative-cache-bypass?) — not fixed under G2, since it's
outside T-10-T-13's scope and the underlying fail-closed behavior is
correct per spec.

**PASS.**

## 4. Elapsed time vs planned box

Sum of planned boxes: 1h + 1h + 1h + 30m = **3h 30m**.
Actual wall-clock span (Gate G1 pass to Gate G2 evidence complete,
commit timestamps): **22:05:27 -> 23:59:14 = ~1h 54m**, including the
mid-T-10 T-09 temperature-bug stop/report/fix/re-verify cycle and the
full T-13 adversarial-review round-trip (fresh-session review, product-
owner decision exchange, three approved fixes with their own
regression-test-first cycle). AI-paced execution, not a claim about
human-equivalent effort.

## 5. RAG status: **GREEN**

All four Block 2 tasks done with verified, non-empty evidence; one
genuine defect found via live testing (T-09's `temperature=0`
incompatibility) was stopped-on, reported, approved, and fixed rather
than silently patched or ignored; T-13's adversarial review found four
real findings, three fixed (with tests-first, T-08 unaffected) and one
explicitly accepted as a documented limitation per product-owner
decision; Gate G2 condition met; full suite green; frozen list
(weights, thresholds, fail-closed direction) untouched throughout —
only the override-floors set changed, and only with explicit,
per-rule, per-finding approval before each change.

## 6. Cut order

Not applicable — GREEN, nothing to cut.
