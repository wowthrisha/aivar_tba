# Action Log (append-only)

Newest entries go at the BOTTOM. Never edit or delete a prior entry — if an
entry was wrong, add a new entry correcting it.

## Entry template

```
### [YYYY-MM-DD HH:MM TZ] [task-id] [actor]
Action:
Result:
Evidence:
```

---

### [2026-08-19 18:40 IST] [T-04] [delivery engineer]
Action: Created repo scaffold — directory structure, CLAUDE.md,
progress-log/, reports/, .env.example, .gitignore, requirements.txt,
pytest.ini, pre-commit secret-scan hook, .claude/hooks PostToolUse pytest
hook. No feature/application code written.
Result: Scaffold in place, pending git init and first commit.
Evidence: See `git log --oneline` and `tree -L 2` output pasted in the T-04
completion message.

### [2026-08-19 18:55 IST] [T-04] [delivery engineer]
Action: Verified pre-commit secret-scan hook by staging a file containing a
synthetic OpenAI-shaped API key assignment and attempting commit.
Result: Commit blocked (exit 1), hook printed the offending line. Test file
removed, unstaged before real commit.
Evidence: `BLOCKED: possible secret in secret_test.txt` / `EXIT CODE: 1`
(raw output above).

### [2026-08-19 21:10 IST] [T-03] [delivery engineer]
Action: Verified the OpenAI configuration in local `.env`. Extracted
`OPENAI_MODEL` and `OPENAI_API_KEY` into shell variables without echoing
either (grep/cut, no `source`, no printing). Ran (1) `GET
https://api.openai.com/v1/models/${OPENAI_MODEL}` to confirm the pinned
model is retrievable for this key, then (2) a minimal `POST
https://api.openai.com/v1/chat/completions` request with
`max_completion_tokens` (no `temperature` — current-gen reasoning-tier
param convention) to confirm a real successful completion.

Note: mid-task, a prior command (`source .env`) failed to parse due to an
unescaped `&` in `DATABASE_URL`'s query string, and my diagnostic grep
excluded only `API_KEY|SECRET|PASSWORD|TOKEN` by key name — missing that
`DATABASE_URL`/`DATABASE_URL_DIRECT` carry a password inline. Both DB
connection strings were printed to the session transcript as a result.
User rotated the Neon password and refreshed `.env` before this task
resumed. Not repeated — this task's commands extracted only
`OPENAI_MODEL`/`OPENAI_API_KEY` individually and never echoed values.

Result: Model confirmed live and functional.
Evidence:
- Model retrieval: `HTTP_STATUS:200`, body `{"id":"gpt-5.6-luna","object":"model","created":1782228658,"owned_by":"system","shutdown_date":null}`
- Minimal completion: `HTTP_STATUS:200`, `"content":"OK"`, `"finish_reason":"stop"`, `"model":"gpt-5.6-luna"` echoed, `usage.total_tokens: 15`. Full raw response pasted in the T-03 completion message to the product owner.
- No secrets included in either response body; `OPENAI_API_KEY` was never printed.

### [2026-08-19 21:40 IST] [T-05] [delivery engineer]
Action: Hello-world deploy to Railway. Created `app/main.py` (FastAPI,
single `GET /livez` route, no I/O), `Procfile`
(`web: uvicorn app.main:app --host 0.0.0.0 --port $PORT` — confirmed via
docs subagent, not guessed), and `railway.json`
(`{"deploy":{"healthcheckPath":"/livez"}}` — field name/nesting confirmed
via docs subagent). Re-authenticated Railway CLI (prior token was
invalid), linked to existing project `zippy-expression` / service
`aivar_tba` (already connected to `wowthrisha/aivar_tba` on GitHub, prior
status `Failed` since no app existed yet). Deployed via `railway up --ci`
(image built `linux/amd64` per Railpack's manifest annotation, matching
CLAUDE.md's known constraint). Generated public domain via `railway
domain`.
Result: Live HTTPS URL confirmed serving `/livez` with 200 and no I/O.
Evidence:
- `curl -s -w "HTTP_STATUS:%{http_code}" https://aivartba-production.up.railway.app/livez`
  → body `{"status":"ok"}`, `HTTP_STATUS:200`.
- Deploy log: Railpack detected Python + "Found web command in Procfile";
  image descriptor annotated `"architecture":"amd64","os":"linux"`;
  "Deploy complete".
- No secrets in response body or deploy log.

### [2026-08-19 22:05 IST] [T-06] [delivery engineer]
Action: Wrote `tests/test_scoring.py` first (confirmed red: `ModuleNotFoundError:
No module named 'app.risk.scorer'`), then implemented `app/risk/scorer.py`
— pure Python (Enum + Pydantic v2 only, no FastAPI/DB import), four
dimensions banded per spec, WEIGHTS dict + WEIGHTS_VERSION="v1" constant,
`RiskAssessmentResult` Pydantic model (composite, four sub-scores, weights,
version). `data_scope` implemented as step bands (lower-bound thresholds)
since the spec gave anchor points, not a continuous formula — documented as
a comment in scorer.py. Commit SHA: (see next commit).
Result: 24/24 tests pass, weights verified frozen (0.40/0.30/0.20/0.10).
Evidence: reports/evidence/T-06-pytest.txt (`24 passed in 0.02s`).

### [2026-08-19 22:20 IST] [T-06a] [delivery engineer]
Action: Added counterfactual explanation to `RiskAssessmentResult`.
Dependency note: this required a tier-from-composite function that no
prior task ID owns — introduced `app/risk/tiers.py` (Tier IntEnum,
THRESHOLD_CONFIRM=0.30, THRESHOLD_FULL_REVIEW=0.65, `tier_for_composite`)
using the exact boundary semantics you approved for T-07a
(composite<0.30→AUTONOMOUS, 0.30<=composite<0.65→CONFIRM,
composite>=0.65→FULL_REVIEW), since it's the same frozen thresholds and
T-07/floors.py will need it too ("final_tier = max(weighted_tier,
highest_floor_tier)"). Design decision: counterfactual sweep covers
reversibility, data_scope, regulatory (all have discrete named bands) but
NOT confidence (continuous, 1.0 - llm_confidence, no discrete band) —
documented as a comment in scorer.py. Wrote the two new tests in
tests/test_scoring.py FIRST (confirmed red: `ModuleNotFoundError: No
module named 'app.risk.tiers'`), then implemented.
Result: 26/26 tests pass (24 from T-06 unchanged + 2 new). No regressions.
Evidence: reports/evidence/T-06a-pytest.txt (`26 passed in 0.02s`). Sample
real output: `0.72 -> FULL_REVIEW. Would have been CONFIRM if
reversibility were update_without_snapshot instead of irreversible.`

### [2026-08-19 22:35 IST] [T-07] [delivery engineer]
Action: Wrote tests/test_floors.py first (confirmed red:
`ModuleNotFoundError: No module named 'app.risk.floors'`), then
implemented app/risk/floors.py. Three FROZEN floors: irreversible+100
records -> FULL_REVIEW, PHI_SOX+mutation -> FULL_REVIEW,
llm_confidence<0.5 -> CONFIRM (`is_mutation` derived as
`reversibility != READ`, documented in-code). `final_tier =
max(weighted_tier, floor.tier)` — escalate-only is structurally guaranteed
by max() (floor.tier defaults to the lowest tier, AUTONOMOUS, when no
floor fires) plus an explicit `assert result >= weighted_tier` for
self-documentation, not just a comment.
Result: 9/9 floors tests pass, including a sweep test over all 4x9x4x9=1296
combinations of (reversibility, affected_records, regulatory,
llm_confidence) asserting final_tier never falls below weighted_tier. Full
suite 35/35, no regressions.
Evidence: reports/evidence/T-07-pytest.txt (`35 passed in 0.03s`).

### [2026-08-19 22:45 IST] [T-07a] [delivery engineer]
Action: No task-specific prompt exists for T-07a in the Prompt Pack
(flagged and confirmed earlier this session) — used only the approved
interpretation and the task-board DoD. Wrote tests/test_tiers.py testing
`app/risk/tiers.tier_for_composite` (built during T-06a) against the
approved boundary semantics: composite<0.30->AUTONOMOUS,
0.30<=composite<0.65->CONFIRM, composite>=0.65->FULL_REVIEW. Parametrized
test at each threshold +/- 0.04 (5 points per threshold) plus a
monotonicity sweep (80 steps across each 0.08-wide band) asserting tier
never regresses as composite rises. No implementation change was needed —
tiers.py already matched the approved semantics from T-06a; this task
formalizes the dedicated boundary-precision coverage the DoD asks for.
Result: 12/12 new tests pass. Full suite 47/47, no regressions.
Evidence: reports/evidence/T-07a-pytest.txt (`47 passed in 0.04s`).

### [2026-08-19 23:00 IST] [T-08] [delivery engineer]
Action: THE FOUR CRITERION TESTS — tests/test_routing.py, now READ-ONLY.
As with T-06a's tiers.py, no task ID owns the scorer+floors integration
these tests need — built `app/risk/router.py` (`route_action`: runs
score_action + evaluate_floors + final_tier, and if a floor fired, uses
the floor's own reason as the explanation's triggering reason instead of
the weighted-only counterfactual). Wrote the test file first (confirmed
red: `ModuleNotFoundError: No module named 'app.risk.router'`), then
implemented router.py, then the four tests passed without further changes
on first run (hand-verified the arithmetic before writing: bulk delete
composite 0.585 weighted-CONFIRM but floor escalates to FULL_REVIEW;
single update composite 0.35 CONFIRM via weighted score alone, no floor;
read-only composite 0.125 AUTONOMOUS).
LEFT OUT: the router's explanation, when a floor fires, states the floor's
reason but does not compute a floor-specific "would have been X if Y"
counterfactual (e.g. "if affected_records < 100") — T-08's literal
assertions (composite + tier + triggering reason present) don't require
it, and building genuine per-floor counterfactual logic was out of this
task's scope. Logged to 03-errors-and-fixes.md.
Result: THE FOUR CRITERION TESTS pass. GATE G1 met: `pytest
tests/test_routing.py -vv` → 4/4 passed, NO FastAPI, NO database imported
anywhere in app/risk/. Full suite 51/51, no regressions.
Evidence: reports/evidence/T-08-pytest.txt (full suite, `51 passed in
0.04s`). Named results:
  tests/test_routing.py::test_bulk_delete_routes_to_review PASSED
  tests/test_routing.py::test_single_update_routes_to_confirm PASSED
  tests/test_routing.py::test_read_only_routes_autonomous PASSED
  tests/test_routing.py::test_audit_breakdown_is_human_readable PASSED

### [2026-08-19 23:20 IST] [T-09] [delivery engineer]
Action: Before writing any code, introspected the ACTUAL installed
`openai` SDK (local: 2.49.0) rather than trusting docs/memory (E-3) —
confirmed via source inspection: `client.chat.completions.parse()` with a
Pydantic `response_format` auto-sets `strict=True` and
`additionalProperties=False` (openai/lib/_pydantic.py,
lib/_parsing/_completions.py:286); `message.refusal: str|None` and
`message.parsed` are real fields on `ParsedChatCompletionMessage`;
refusal short-circuits parsing (parsed stays None); a field_validator on
the response_format model fires during the SDK's own parse and raises
`pydantic.ValidationError` out of `.parse()` itself; `APITimeoutError`
and finish-reason errors are all `openai.OpenAIError` subclasses.
Flagged and fixed a real risk: local openai==2.49.0 but Railway's T-05
deploy resolved openai==3.3.0 (requirements.txt was unpinned) — pinned to
`openai==2.49.0` in requirements.txt with a comment explaining why, so
the verified behavior is what actually ships.
Wrote tests/test_llm.py first (confirmed red:
`ModuleNotFoundError: No module named 'app.llm'`), implemented
`app/llm.py` (OpenAIConfidenceProvider, ConfidenceSchema with a
range-check field_validator, cache keyed on sha256 of canonical JSON of
(action_type, resource, params), fail-closed on timeout/refusal/API
error/out-of-range with degraded=True).
One test-authoring bug (not implementation): `UnboundLocalError` in
test_out_of_range_value_is_rejected — Python deletes an `except ... as
name` binding on block exit; fixed by capturing into a pre-declared
variable. Logged as D-01 in 03-errors-and-fixes.md.
Result: 5/5 new tests pass (happy path, refusal terminal+not retried,
timeout fails closed, out-of-range rejected, cache hit). Full suite
56/56, no regressions.
Evidence: reports/evidence/T-09-pytest.txt (`56 passed in 0.32s`).

### [2026-08-19 23:30 IST] [T-09a] [delivery engineer]
Action: Design decision, documented rather than silently done: did NOT
modify T-09's `OpenAIConfidenceProvider.get_confidence` to apply the
two-signal minimum internally, since its existing committed tests
(test_llm.py) use `params={"id": 42}` fixtures that would fail a real
action-catalogue structural check, breaking already-green tests. Instead
built a separate, additive pure module `app/risk/confidence.py`:
`ACTION_CATALOGUE` (minimal action_type -> required-params registry, to
be extended in later tasks), `structural_completeness(action_type,
params)` (1.0 only if action_type known AND all required params present
— this doubles as the "did the schema validate first-pass" check per the
DoD, since no per-field type schema was specified to validate against),
and `two_signal_confidence(self_reported, structural) = min(...)`. Wrote
tests/test_confidence.py first (confirmed red: `ModuleNotFoundError: No
module named 'app.risk.confidence'`), then implemented.
Result: 6/6 new tests pass, including the required "high self-report + low
completeness -> low confidence" case (0.95 self-reported, missing
required param -> structural=0.0 -> combined=0.0). Full suite 62/62, no
regressions.
Evidence: reports/evidence/T-09a-pytest.txt (`62 passed in 0.29s`).

### [2026-08-20 00:20 IST] [T-09 fix, discovered during T-10] [delivery engineer]
Action: T-10's live curl verification (POST /v1/actions/evaluate against
the real API) showed `llm_degraded: true` on every call. Stopped Block 2
execution and reported per CLAUDE.md's "a dependency behaves differently
from its documentation" trigger, rather than silently patching. Product
owner approved a minimal fix.
Root cause (confirmed via direct provider call, not guessed): `app/llm.py`
passed `temperature=0` to `client.chat.completions.parse()` per T-09's
literal spec ("temperature 0"). The live pinned model `gpt-5.6-luna`
rejects it: `Error code: 400 - "Unsupported value: 'temperature' does not
support 0 with this model. Only the default (1) value is supported."`
This has been present since T-09's original commit (f612c9f) — T-09's
mocked tests never exercised the real `temperature` argument, so it was
never caught until real API calls happened in T-10.
Fix: removed the `temperature=0` line entirely (app/llm.py:122) so the
SDK uses the model's required default. Nothing else changed — frozen
weights/thresholds/floors, fail-closed direction, T-08 tests, refusal
handling, timeout behavior, structured-output validation, and two-signal
confidence logic all untouched.
Result: tests/test_llm.py 5/5 pass (unchanged — mocks never asserted on
`temperature`). Full suite 80/80. Direct live call:
`confidence: 0.82, degraded: False, reason: None`. Audit record for a
real evaluate call now shows `"llm_degraded": false`.
Evidence: reports/evidence/T-09-fix-live-verification.txt; D-02 logged in
03-errors-and-fixes.md.

### [2026-08-20 00:30 IST] [T-10] [delivery engineer]
Action: Built the FastAPI service + state machine. New modules:
`app/state_machine.py` (ActionState enum, VALID_TRANSITIONS map,
`transition()`), `app/audit.py` (append-only hash-chained AuditLog),
`app/store.py` (InMemoryStore — T-11 will back this with SQLAlchemy
behind the same shape), `app/schemas.py` (request/response models),
extended `app/main.py` with all 8 new routes plus a
`get_confidence_provider` FastAPI dependency (real OpenAIConfidenceProvider
at runtime, overridden with a fake in tests so no test hits the live API).
Also added `health_check()` to `ConfidenceProvider`'s ABC (verified via
`AsyncOpenAI.models.retrieve`, an async method — checked via introspection
before using it) so `/readyz`'s LLM leg is real and mockable.
Design decisions made and documented, not silently assumed:
  - `/readyz` checks LLM reachability for real (cheap `models.retrieve`
    call) but reports `db: "not_configured (T-11 pending)"` rather than
    faking a DB check — there is no DB yet.
  - Two distinct human-gate endpoints: `/actions/{id}/confirm`
    (self-service, CONFIRM tier, only checks params_hash) vs
    `/review-queue/{id}/decision` (reviewer-gated, FULL_REVIEW tier,
    reviewer_id required and rejected with 403 if it equals agent_id —
    S-6 groundwork, though T-12 owns the dedicated named test).
  - EvaluateRequest takes reversibility/affected_records/regulatory as
    explicit caller-supplied fields rather than deriving them from
    action_type, since no such mapping was specified anywhere.
  - REJECTED/EXPIRED/EXECUTED are terminal states directly; the spec
    diagram's "TERMINAL" target isn't a separate enum value since no
    endpoint transitions further out of them.
Wrote tests/test_api.py first (confirmed red:
`ImportError: cannot import name 'get_confidence_provider'`), then
implemented. 18/18 new tests pass on first run against the design.

BLOCKER found and fixed mid-task: live curl testing (not the mocked
pytest suite) surfaced that every real evaluate call had
`llm_degraded: true` — traced to T-09's `temperature=0` being rejected by
the live pinned model. Stopped, reported per the "dependency behaves
differently from its documentation" trigger, got approval, fixed in a
separate commit (4a1823e) — see the entry above. Did not proceed with
T-10's live verification until that fix was confirmed.

Full live curl walkthrough (post-fix) exercised all 9 endpoints for real
against a local uvicorn instance (not Railway — T-05's live deployment is
untouched, per instruction; redeploy is T-14's job): /livez, /readyz,
evaluate (bulk delete -> FULL_REVIEW via floor, single update -> CONFIRM
via weighted score, read -> AUTONOMOUS), GET action (found + 404),
confirm (hash mismatch 409 + success), review-queue, decision (missing
reviewer_id 422, self-review 403, approve, reject), execute (hash
mismatch 409, AUTONOMOUS direct execute, APPROVED execute, blocked on
REJECTED 409, blocked on replay-after-executed 409), audit list, audit
verify. Every real evaluate call showed `llm_degraded: false` and a
genuine self-reported confidence (e.g. 0.82, 0.148 composite on a clean
read). Evaluate never executed in the same request (state stayed
autonomous/confirm/full_review, never executed, until a separate execute
call).
Result: 18/18 new tests pass, full suite 80/80. Live curl walkthrough:
all 9 endpoints proven, hash chain valid across 10 real audit records
(`{"valid":true,"records_checked":10,"first_invalid_id":null}`).
Evidence: reports/evidence/T-10-pytest.txt (`80 passed`),
reports/evidence/T-10-curl.txt (full request/response pairs for all 9
endpoints).

### [2026-08-20 01:10 IST] [T-11] [delivery engineer]
Action: Real SQLAlchemy + Alembic persistence against the live Neon DB.
Before writing code: verified (not guessed) three things that would have
silently broken otherwise — (1) `asyncpg.connect()`'s real signature has
`ssl`, not `sslmode`, and no `channel_binding` at all, so the libpq-style
query params already in `.env`'s URLs cannot be passed through as-is; (2)
SQLAlchemy's asyncpg dialect exposes the known-constraint's
"statement_cache_size=0" as its OWN DBAPI query param
`prepared_statement_cache_size`, confirmed by reading
`sqlalchemy/dialects/postgresql/asyncpg.py`, not the raw asyncpg kwarg
name; (3) confirmed both by actually connecting to the live DIRECT and
POOLED Neon endpoints with the derived connection recipe before writing
any application code around it.
Built: `app/db.py` (`make_app_engine` — POOLED, pool_size=5,
max_overflow=5, pool_pre_ping=True, pool_recycle=1800,
prepared_statement_cache_size=0, ssl=require), `app/db_models.py` (four
ORM tables, exact columns per spec, three named indexes each matching a
real query), `alembic init -t async` (Alembic's own official async
template, not hand-rolled) with `migrations/env.py` reading
DATABASE_URL_DIRECT from the environment only (never alembic.ini, no
secret committed). Autogenerated and ran the migration against the real
DB.
SCOPE DECISION (documented, not silent): T-11's literal DoD is "alembic
current on DIRECT; app on POOLED; \dt shows 4 tables" — infrastructure
proof, not "rewire all 9 T-10 endpoints to Postgres." Given the size of a
full store rewrite (every T-10 handler, full re-verification via curl,
new test-isolation strategy) against a 1h box, I built the real
schema/migration/POOLED-engine and wired ONLY `/readyz`'s db check to the
real engine (closing T-10's documented `"not_configured (T-11 pending)"`
gap honestly). The 9 business endpoints still use T-10's InMemoryStore.
If you want the full store swap now, say so and I'll treat it as an
explicit follow-on rather than assume it.
Updated tests/test_api.py's readyz test + fixtures to override the new
`get_app_engine` dependency with a fake connection object, so pytest
never touches Postgres (hermetic, fast, no live credentials needed to run
the suite).
Pinned asyncpg==0.30.0, sqlalchemy==2.0.36, alembic==1.14.0 in
requirements.txt — same version-drift risk pattern as T-09's openai fix;
the dialect internals just verified are version-specific.
Result: migration ran clean first attempt. `alembic current` ->
`473900d668ec (head)`. Real `\dt`-equivalent query shows exactly
actions/approvals/audit_records/risk_assessments (+ alembic's own
bookkeeping table). All 3 named indexes confirmed present. Live
`/readyz` -> `{"status":"ok","checks":{"llm":"ok","db":"ok"}}`. Full
suite 80/80, no regressions.
Evidence: reports/evidence/T-11-verification.txt (alembic current,
alembic history, table list, index list, live /readyz curl),
reports/evidence/T-11-pytest.txt (`80 passed`).

### [2026-08-20 01:40 IST] [T-12] [delivery engineer]
Action: Five named security controls plus the race test. Assessed what
T-10 already covered before building: S-1 (hash pinning) and S-6
(separation of duties) had working groundwork from T-10 but no dedicated
named test; S-2 (idempotency), S-5's tamper-detection proof, and the
race-safe conditional update were genuinely new.
Wrote tests/test_security.py first; confirmed red for S-2 specifically
(`AssertionError: assert 'be7e...' == '3fa1...'` on replay — different
action IDs, proving idempotency wasn't yet enforced) while S-1/S-3/S-5/
S-6/race passed immediately against existing T-10 behavior.
IMPORTANT CAUGHT ISSUE: the race test passed 5/5 runs against the OLD
read-then-write `decision` handler before any fix — that is NOT proof of
correctness, only that this run didn't hit the window. The spec
explicitly requires a conditional UPDATE, not merely a passing test, so
I implemented the real fix regardless of the test already passing by
luck: added `InMemoryStore.conditional_transition` (lock-guarded
check-and-set) and switched confirm/decision/execute to use it instead
of read-then-write.
Implemented S-2: `InMemoryStore.get_or_create_action` makes the
idempotency check-then-create atomic (one lock acquisition) so a replayed
evaluate call returns the original action, not a second one; execute
checks `was_executed_with_key` before doing anything else and returns the
original terminal result without re-running the transition or appending
a second audit record.
Found and fixed a real bug in my OWN test (not the implementation):
`test_s5_tampered_middle_record_is_detected` originally indexed into the
app's shared `_audit` singleton, which accumulates records across the
WHOLE pytest session (every other test's evaluate/confirm/decision/
execute calls append to it too) — `records[1]` picked up an unrelated
record from an earlier test whose `tier` already equaled the value I was
"tampering" it to, making the tamper a silent no-op and the test falsely
green in isolation, then failing once the full suite ran together and
polluted `_audit` further. Fixed by testing a fresh, isolated `AuditLog()`
instance directly instead of the shared app singleton.
Result: 8/8 new tests pass (S-1, S-1, S-2, S-2, S-3, S-5, S-6, race).
Full suite 88/88, stable across 3 repeat runs (checked given the race
test's inherent flakiness risk). No frozen-list changes; T-08's
tests/test_routing.py untouched.
Evidence: reports/evidence/T-12-pytest.txt (dedicated security tests),
reports/evidence/T-12-full-suite.txt (`88 passed`).

### [2026-08-20 02:00 IST] [T-13] [delivery engineer]
Action: Adversarial review (a genuinely fresh, no-prior-context Claude
Code session — not a fork of this session — read app/risk/scorer.py,
floors.py, tiers.py, changed nothing). Report only:
  Finding 1: IRREVERSIBLE (1 record, NONE, 0.99 confidence) -> composite
    0.461 -> CONFIRM, not FULL_REVIEW. UPDATE_WITHOUT_SNAPSHOT (0 records,
    NONE, 1.0 confidence) -> composite 0.28 -> AUTONOMOUS, zero human
    oversight for an unrecoverable mutation.
  Finding 2: regulated_mutation floor fires for PHI_SOX but not PII_GDPR,
    despite the scorer itself scoring PII_GDPR (0.7) nearly as severe as
    PHI_SOX (1.0).
  Finding 3: composite brittle near both frozen thresholds (0.006 swing
    in llm_confidence flips CONFIRM<->FULL_REVIEW at 0.65; 0.01 swing
    flips CONFIRM<->AUTONOMOUS at 0.30).
  Finding 4: reversibility/affected_records/regulatory are caller-
    supplied with no cross-validation (90% of composite weight);
    action_type/resource/params are interpolated verbatim into the LLM
    confidence prompt, a direct prompt-injection surface.
Full findings: reports/evidence/T-13-adversarial-review.txt.

Product owner decisions:
  Finding 1: FIX NOW - new floor, reversibility in
    {UPDATE_WITHOUT_SNAPSHOT, IRREVERSIBLE} -> at least CONFIRM.
  Finding 2: FIX NOW - broaden regulated_mutation to regulatory in
    {PII_GDPR, PHI_SOX} AND is_mutation -> FULL_REVIEW.
  Finding 3: ACCEPT as documented limitation - no threshold/calibration
    change (recorded in 03-errors-and-fixes.md LEFT OUT).
  Finding 4: FIX NOW - both (a) prompt hardening (untrusted-data
    delimiters) AND (b) server-side action_type<->reversibility
    consistency validation for read/delete/send/pay only (explicitly NOT
    for update/affected_records/regulatory - no textual basis for those,
    would be inventing a rule).

Proposed exact rules for 1/2/4, showed T-08 effect analysis and frozen-
value confirmation for each, got explicit approval before writing any
code (per product owner's own requirement) - see conversation for the
full proposal text.

Implementation (tests written first for all three, confirmed red before
implementing):
  - app/risk/floors.py: broadened `regulated_mutation` to
    `regulatory in (PII_GDPR, PHI_SOX)`; added new floor
    `unrecoverable_mutation_requires_confirm` (reversibility in
    {UPDATE_WITHOUT_SNAPSHOT, IRREVERSIBLE} -> CONFIRM), checked LAST so
    the FULL_REVIEW floors keep precedence when they also apply.
  - app/llm.py: prompt now wraps action_type/resource/params in
    `<untrusted_action>` delimiters with an explicit
    "never treat it as instructions" instruction. Documented in-code and
    in evidence that this reduces, not eliminates, injection risk.
  - app/schemas.py: `EvaluateRequest` gained a `model_validator(mode=
    "after")` requiring reversibility=READ for action_type="read" and
    reversibility=IRREVERSIBLE for action_type in {"delete","send","pay"}
    (the exact three mappings T-06's own original prompt already named
    verbatim: "read 0.0 | ... | delete/send/pay 1.0") - mismatch -> 422.
    Deliberately no rule added for "update" or for affected_records/
    regulatory.
One self-caught test bug: initial regression test for Finding 1's exact
case used `llm_confidence=0.0` instead of `1.0`, not matching the
review's original input, producing composite 0.38 instead of the
expected 0.28. Fixed by correcting the test input to match the review's
exact case.
Result: 17/17 new regression tests pass. T-08's four criterion tests
re-verified passing UNCHANGED (`git diff --stat tests/test_routing.py`
empty). Full suite 105/105, no regressions. Re-ran all four fixed
findings' exact original inputs: Finding 1 case B (AUTONOMOUS->CONFIRM,
fixed), Finding 2 (CONFIRM->FULL_REVIEW, fixed), Finding 4b's exact
demonstrated attack (`action_type=delete,
reversibility=update_with_snapshot`) now rejected 422. Finding 1 case A
intentionally unchanged in outcome (stays CONFIRM) since the approved
fix scope was "prevent AUTONOMOUS," not "force FULL_REVIEW for every
single-record irreversible action" - the new floor now fires for it too,
just doesn't change its final tier since weighted score already gave
CONFIRM.
No frozen weights or thresholds touched (0.40/0.30/0.20/0.10,
0.30/0.65 unchanged) - confirmed by diff review before each fix.
Evidence: reports/evidence/T-13-adversarial-review.txt (original
findings), reports/evidence/T-13-fixes-pytest.txt (17/17 regression
tests), reports/evidence/T-13-fixes-full-suite.txt (`105 passed`),
reports/evidence/T-13-adversarial-cases-rerun.txt (before/after on the
exact original cases).
T-13 CLOSED — all approved fixes implemented and verified.

### [2026-08-20 00:15 IST] [G2] [delivery engineer]
Action: Gate G2 check. Verified evidence files present/non-empty (all
11), re-ran full suite clean, then ran a fresh live end-to-end state
machine walkthrough via a local uvicorn instance running the current
code (post all T-13 fixes) — all four terminal paths (AUTONOMOUS-direct,
CONFIRM->APPROVED, FULL_REVIEW->APPROVED incl. S-6 self-review block,
FULL_REVIEW->REJECTED->blocked-execute) plus a final `/v1/audit/verify`
across the whole session.
Investigated and resolved (as an explanation, not a fix) an apparent
regression during the walkthrough: the first live evaluate call showed
`llm_degraded: true` again. Traced it: NOT a repeat of D-02 (temperature
issue is fixed, confirmed via a direct provider call returning
confidence=0.86) - it was the provider's own cache (per T-09's original
spec: cache by (action_type, resource, params)) storing a transient
first-call failure and replaying it on identical retries. Confirmed
non-regression with a fresh resource identifier, which got a genuine
successful call immediately. Flagged as a design observation (should
failed results be cached?) for a future task, not fixed now - outside
G2's scope.
Result: Gate G2 PASS. Full suite 105/105. State machine proven live by
curl across all terminal paths, hash chain valid across 15 real records.
Evidence: reports/evidence/G2-state-machine-curl.txt,
reports/gates/G2-report.md, reports/blocks/block-2-report.md.
BLOCK 2 COMPLETE. Not starting Block 3.

### [2026-08-20 01:15 IST] [Issue 2 fix] [delivery engineer]
Action: Pre-T-14 production-readiness fix, plan-mode approved with 4
open design decisions resolved by the product owner (real Neon dev DB
for new DB tests, atomic multi-table writes, populate both LLM metadata
columns, don't-cache-failures over TTL-based expiry).
Fixed the documented G2 finding: `OpenAIConfidenceProvider`'s cache
(`app/llm.py`) cached failed/degraded results identically to successes,
with no TTL, in a process-lifetime singleton — a transient failure could
"stick" forever. Wrote `test_degraded_result_is_not_cached` first
(confirmed red: second call replayed the cached failure instead of
retrying), then changed `get_confidence` to only write to `self._cache`
when `result.degraded is False` (two-line change, `app/llm.py`).
Result: 6/6 tests in test_llm.py pass. Full suite 106/106. T-08's four
criterion tests re-verified unchanged (`tests/test_routing.py` zero
diff). No frozen weights/thresholds/floors touched - this file isn't
imported by any risk-engine module.
Evidence: reports/evidence/issue2-llm-cache-fix-pytest.txt,
reports/evidence/issue2-full-suite.txt (`106 passed`).

### [2026-08-20 02:00 IST] [Issue 1 fix] [delivery engineer]
Action: Pre-T-14 persistence fix, plan-mode approved with 4 open design
decisions resolved by the product owner (real Neon dev DB for new tests,
atomic actions+risk_assessments writes, populate both llm_model and
llm_latency_ms, defer audit_records DB-level append-only enforcement as
a separate follow-on).
Built `app/db_store.py` (`SQLAlchemyStore`, `SQLAlchemyAuditLog`) behind
the exact method surface `InMemoryStore`/`AuditLog` already exposed (now
`async def` throughout, for interface uniformity with the DB-backed
versions). Wired `app/main.py`'s 9 business endpoints via new
`get_store()`/`get_audit_log()` FastAPI dependencies (mirroring the
existing `get_confidence_provider`/`get_app_engine` pattern) - the
module-level `_store`/`_audit` globals are gone.

Schema gap found and fixed (flagged in the approved plan): T-11's
original `actions` table had no `state` column at all. New migration
`c5326268c7fa` (`add state to actions`) applied against the real DIRECT
connection - table was confirmed empty (0 rows) before applying a NOT
NULL column with no default.

Two additional correctness issues found and fixed DURING this work, not
in the original plan - flagging both explicitly:
  1. `_check_expiry`'s original logic mutated `record.state` directly,
     which only works when `get_action()` returns a live reference to
     the same object still held in memory (true for InMemoryStore, NOT
     true for SQLAlchemyStore, which reconstructs a fresh ActionRecord
     from the DB on every call). Fixed by routing expiry through
     `conditional_transition` (the same real DB update every other
     transition uses) instead of a local mutation that would have
     silently failed to persist.
  2. `SQLAlchemyAuditLog.append()`'s "read the last record, write the
     next" is a genuine TOCTOU race under concurrent writers - unlike
     `actions.state`, `audit_records` has no auto-incrementing sequence
     to serialize on (only `created_at`, an unordered timestamp), so two
     concurrent appends could both read the same prev_hash and fork the
     chain. Fixed with a Postgres advisory transaction lock
     (`pg_advisory_xact_lock`) serializing all appends - same "genuine
     atomic operation, not read-then-write" standard T-12 already
     established for `conditional_transition`, applied here since it's
     equally a correctness requirement, not an optional hardening step.

Design choices per the approved decisions: `evaluate()` now calls
`score_action()` separately (in addition to `route_action()`, which
already calls it internally) to get the four sub-scores for
`risk_assessments` - a deliberate small duplicate computation rather
than modifying `app/risk/router.py`/`RoutingResult`, keeping every
risk-engine file completely untouched by this fix as promised.
Evaluate-idempotency reuses the existing `actions.idempotency_key`
column; execute-idempotency has no dedicated column - `was_executed_with_key`
queries `audit_records.payload` (JSONB) for a matching `executed` event,
so `execute()` now includes `idempotency_key` in that event's payload
when present. `ApprovalRecord` gained a required `approved_params_hash`
field (was missing, but `approvals.approved_params_hash` is NOT NULL in
T-11's schema) - both call sites in `main.py` updated.

Testing: wrote `tests/test_db_store.py` (6 tests) against the REAL Neon
dev DB (approved strategy - unique per-test action_id, explicit teardown
deletes). Hit and fixed a real pytest-asyncio incompatibility: module-
scoped async fixtures failed with "attached to a different loop" /
"Event loop is closed" against this pytest-asyncio version's default
per-function event loop - reverted to function-scoped fixtures (real
per-test connection-setup cost against live Neon, ~20s/test, accepted).
Added `pytestmark = pytest.mark.skipif("DATABASE_URL" not in os.environ,
...)` so the normal full-suite run skips these cleanly rather than
erroring when no live DB credentials are present.
Updated `tests/test_api.py`, `tests/test_security.py`,
`tests/test_adversarial_fixes.py` fixtures to override
`get_store`/`get_audit_log` with fresh `InMemoryStore()`/`AuditLog()`
instances CAPTURED ONCE PER TEST (not constructed fresh per request -
first attempt used `lambda: InMemoryStore()`, which silently gave every
request within a test a different empty store, breaking every
multi-request test with spurious 404s; fixed by capturing one instance
per test, exactly like `_FakeProvider`/`_FakeEngine` already do).
`tests/test_security.py`'s S-3/S-5 tests updated to `async def` +
`await`, since `AuditLog`'s methods are now async too.

Result: 6/6 new DB-backed tests pass against real Postgres. 106/106
existing tests pass unchanged (in-memory-backed, hermetic), 6 skipped
cleanly without DATABASE_URL. T-08's four criterion tests re-verified
unchanged (zero diff on tests/test_routing.py). Live curl walkthrough
against the DB-backed server proved all three tiers end-to-end
(AUTONOMOUS/CONFIRM/FULL_REVIEW incl. S-6 block), confirmed via a DIRECT
Postgres query that rows genuinely persisted (state, tier, composite,
llm_model, llm_latency_ms=6418 from a real LLM call). Hash chain valid
across 10 real audit records. Live concurrency proof: two genuinely
concurrent HTTP requests (not Python threading within one process) to
the same decision endpoint, run twice, both times exactly one 200 / one
409 - the real `UPDATE ... WHERE state = :expected` holds under actual
multi-connection concurrency, which the in-memory lock could never prove.
No frozen weights/thresholds/floors touched; app/risk/*.py never
imported by anything this fix changed.
Evidence: reports/evidence/issue1-db-store-pytest.txt (6/6 real-DB
tests), reports/evidence/issue1-full-suite.txt (`106 passed, 6
skipped`), reports/evidence/issue1-db-backed-curl.txt (full walkthrough,
direct-DB-query proof, and the concurrency proof, both runs).

### [2026-08-20 03:06 IST] [T-14] [delivery engineer]

Deployed the full application (T-13 + the pre-T-14 Postgres/LLM-cache
fixes) to Railway, replacing the T-05 hello-world-only deploy, and
proved all three routing tiers against the live public URL.

**Security incident (mid-task)**: a redaction command
(`railway variable list | sed ...`) failed to redact Railway's
box-drawing table output, briefly exposing `DATABASE_URL`,
`DATABASE_URL_DIRECT`, and `OPENAI_API_KEY` in the transcript. Disclosed
immediately; user rotated both the Neon DB credentials and the OpenAI
key. All variable inspection from that point on used
`railway variable --json` parsed through Python for names only, never
raw output.

**Deploy blockers, found and fixed in sequence**:
1. `greenlet` missing in Railway's clean container build (`ValueError:
   the greenlet library is required...`) - it's an optional SQLAlchemy
   async-engine dependency, present locally only via another package's
   transitive dependency. Fixed: pinned `greenlet==3.3.2` in
   requirements.txt.
2. `DATABASE_URL_DIRECT` `InvalidPasswordError` against Neon, twice in a
   row (once before, once after a credential re-copy) - stopped per the
   two-attempt rule rather than guessing a third time.
3. "No start command detected" - read-only diagnosis found
   `origin/master` had never received a push this entire session
   (stuck 19 commits behind, at T-04's pre-Procfile scaffold), while
   all deploys had gone through `railway up`'s local-directory upload,
   which bypasses git entirely. Railway's GitHub-connected build source
   was building from the stale remote. Fixed: `git push origin master`.
4. `railway.json` gained `preDeployCommand: "alembic upgrade head"`
   (docs.railway.com/reference/config-as-code) so migrations run
   against DIRECT before the container starts, satisfying that DoD
   clause explicitly rather than relying on app-startup side effects.

**First successful deploy** then passed checks 1-3 of the required curl
proofs but check 4 (read-only) returned CONFIRM, not AUTONOMOUS.
Misdiagnosed initially as a timeout: `TIMEOUT_SECONDS` in `app/llm.py`
was raised 3.0s -> 10.0s (commit 982ae48) on the theory that the
model's ~2.3-2.9s local latency left no margin. Real observed
`llm_latency_ms` values from Railway (98-929ms) disproved this before
it shipped - too fast to be a timeout. `railway run` (executes locally
with Railway's real injected env vars, no deploy) reproduced the actual
failure directly: `openai.AuthenticationError`, HTTP 401 - the
`OPENAI_API_KEY` value stored on Railway was stale, never updated after
the earlier rotation. Reverted the timeout change in full (commit
81e44c5); confirmed `TIMEOUT_SECONDS` back at the original T-09 value
of 3.0, all 112 tests green, T-08's four criterion tests byte-identical
to commit a573663. User manually re-set the correct `OPENAI_API_KEY` on
Railway; verified via `railway run` before redeploying.

**Second discrepancy after the key fix**: the live LLM was now working,
so it returned genuine high confidence (~0.93-0.98) instead of failing
closed - and the T-14 curl walkthrough's "single update" example
(`reversibility: update_with_snapshot`) computed a real composite of
0.227, genuinely AUTONOMOUS, not CONFIRM. Root-cause analysis (read-only,
no code touched) found the actual mismatch: T-08's own criterion test
(`tests/test_routing.py::test_single_update_routes_to_confirm`, frozen
since T-08) uses `Reversibility.UPDATE_WITHOUT_SNAPSHOT`, not
`UPDATE_WITH_SNAPSHOT` - the T-14 curl example had drifted from T-08's
own canonical input. Fixed by changing only the curl example to match
T-08 exactly (`update_without_snapshot`, 1 record, regulatory=none),
which now reliably routes to CONFIRM via the frozen
`unrecoverable_mutation_requires_confirm` floor regardless of live LLM
confidence variance. No changes to scorer.py, tiers.py, floors.py,
weights, thresholds, or T-08 tests.

**Final deploy and verification** (deployment `b6fd821c`, commit
`2f2794a`): all 5 required curl checks pass against
https://aivartba-production.up.railway.app with genuine (non-degraded)
LLM calls confirmed via `/v1/audit` (`llm_degraded: false` on all
three evaluate calls):
  1. `GET /livez` -> 200
  2. bulk delete (irreversible, 500 records) -> FULL_REVIEW (floor:
     irreversible_bulk)
  3. single update (update_without_snapshot, 1 record, none) -> CONFIRM
     (floor: unrecoverable_mutation_requires_confirm)
  4. read-only (50 records) -> AUTONOMOUS (composite 0.13, no floor)
  5. `GET /v1/audit` -> all three records present, hash-chained,
     human-readable explanations

Added `.github/workflows/tests.yml` (pytest on push, with a
`DATABASE_URL` repo secret so the 6 real-DB tests run in CI instead of
skipping). Green run: 112/112 passed in 11.11s -
https://github.com/wowthrisha/aivar_tba/actions/runs/32303917004

No secret values were printed at any point in this task; every Railway/
GitHub variable inspection used names-only output, and every log/error
message was grepped for secret-shaped patterns before display.

Evidence: reports/evidence/T-14-curl.txt (all 5 checks, final run).

### [2026-08-20 03:22 IST] [T-16] [delivery engineer]

Implemented Observability. No detailed `## T-16` task-prompt section
exists in `PS-9-1-Prompt-Pack-v1.0.md` (confirmed by grep, unlike T-06
through T-15/T-18) - used the one-line task-board entry as the complete
spec, per product owner confirmation: "JSON log line with request_id;
forced error returns clean JSON."

Added `app/logging_config.py` (JSON log formatter + a `request_id`
contextvar, stdlib `logging`/`json` only, no new dependency) and wired
it into `app/main.py`: a `request_id_middleware` generating a UUID per
request, and `@app.exception_handler(Exception)` returning
`{"detail": "internal server error"}` (reusing the app's existing
error-body convention) instead of Starlette's default plain-text 500
for genuinely unhandled exceptions.

**D-11** (see `progress-log/03-errors-and-fixes.md`): while capturing
T-16's own raw evidence (not from a failing test), the forced-error
JSON log line showed `"request_id": null` — the middleware's
`finally: request_id_var.reset(token)` ran before the exception reached
the handler, clearing the contextvar first. Fixed by also storing
`request_id` on `request.state` (survives that unwind) and having the
exception handler re-set the contextvar from there before logging.
Added a regression test asserting the ERROR log line itself carries a
non-null `request_id`. Fixed on the first attempt.

Result: `tests/test_observability.py` 3/3 pass. Full suite 109/109
passed, 6 skipped (unchanged real-DB skip count). `tests/test_routing.py`
(T-08, frozen) re-verified zero-diff against commit `a573663`. No
`app/risk/*.py` file touched - frozen weights/thresholds/floors/
fail-closed direction unaffected.

DoD verification (raw evidence in `reports/evidence/T-16-raw-demo.txt`):
  - JSON log line with request_id: PASS on both the success path
    (`GET /livez`) and, after the D-11 fix, the forced-error path.
  - Forced error returns clean JSON: PASS - `RuntimeError` forced via a
    broken `get_store` dependency override returns `HTTP 500`,
    `Content-Type: application/json`, body
    `{"detail":"internal server error"}`, parses cleanly with
    `json.loads`.

Evidence: reports/evidence/T-16-pytest.txt (3/3 + full suite 109
passed/6 skipped), reports/evidence/T-16-raw-demo.txt (literal JSON log
lines and forced-error response, before/after the D-11 fix).

### [2026-08-20 03:37 IST] [T-18] [delivery engineer]

Wrote `README.md`. Unlike T-16, T-18 has a full detailed section in
`PS-9-1-Prompt-Pack-v1.0.md:634-665` (the referenced "(2)" file still
doesn't exist) - used that as the exact spec: traceability table at the
very top, then live URLs, quickstart, architecture, risk model,
override-floors worked example, security controls, compliance mapping,
project-management links, known limitations. T-18a (DMAIC/versioning/
fuzzy-rejection) is explicitly separate and out of scope.

Content sourcing, cross-checked against the prompt pack and existing
docs before writing (full point-by-point audit given to the product
owner, who reviewed and approved before this was committed):
  - Traceability table (4 rows), risk model weights/bands, and security
    control descriptions: directly required, sourced from the prompt
    pack / CLAUDE.md's frozen list.
  - Architecture Mermaid diagram: built directly from
    `app/state_machine.py`'s real `VALID_TRANSITIONS`, not invented.
  - Override-floors worked example: used REAL evidence (composite 0.58,
    `reports/evidence/T-14-curl.txt`) instead of the prompt's
    illustrative "0.69" - no live call was forced to hit that exact
    number, and the "would have been CONFIRM" reasoning is written
    prose, not a literal system-generated string (a known gap already
    logged in the LEFT OUT section before this task).
  - Weight-ordering rationale: original writing - no existing rationale
    exists anywhere in the project's prior docs.
  - Compliance mapping: researched via a documentation-reading subagent
    (L-7) rather than drafted from memory. This corrected two
    imprecise phrases in the prompt pack itself - "Article 14's three
    oversight obligations" (Article 14(4) actually has five lettered
    sub-obligations, (a)-(e)) and "OWASP ASI risks" (the actual project
    name is "OWASP Top 10 for Agentic Applications (2026)"; ASI is the
    category-ID prefix inside it, not the project's name). Sourced from
    secondary mirrors (artificialintelligenceact.eu, genai.owasp.org,
    cross-checked against independent summaries) - the subagent could
    not get a clean fetch of the primary eur-lex.europa.eu text or the
    primary OWASP PDF, so this is flagged as researched-but-not-
    primary-source-verified, not treated as beyond question.
  - S-3's security-control line deliberately omits "both configurable"
    (present in the prompt pack's own S-3 text) because
    `CONFIRM_TTL`/`FULL_REVIEW_TTL` are hardcoded Python constants, not
    actually runtime-configurable - already documented in this file's
    own LEFT OUT section, which is quoted verbatim later in the same
    README. Including "configurable" would have contradicted the
    project's own recorded limitation within one document.

**D-12** (see `progress-log/03-errors-and-fixes.md`): found while
fetching GitHub's own rendered HTML as the required render-proof step
(`gh api repos/.../readme -H "Accept: application/vnd.github.html"`),
not from reading the raw markdown - a wrapped line in the weight-
rationale sentence started with `>` (from "reversibility > data scope >
regulatory\n> confidence"), which GitHub's renderer read as a
blockquote, splitting one sentence into a paragraph plus an unintended
quote block. Fixed by rewording to avoid any line starting with `>`;
re-verified via the same render API (0 `<blockquote>` tags) and grepped
the whole file for any other `^>` line (none found). Fixed on the first
attempt.

Verification (all raw output in `reports/evidence/T-18-verification.txt`):
  - All 4 traceability-table test names confirmed present in
    `tests/test_routing.py` (T-08, frozen - re-verified zero-diff
    against commit `a573663`).
  - Both traceability-table endpoints confirmed present in
    `app/main.py`.
  - Traceability table confirmed positioned before the project
    description (first content block after the H1 title).
  - No placeholder video timestamps presented as final evidence - all
    `0:00` with the prompt's own "(timestamps filled after T-20)" note.
  - Secret-shaped pattern scan: 0 matches.
  - GitHub rendering fetched directly via the API (not just previewed
    locally) both before and after the D-12 fix - the actual proof this
    task's DoD ("table rendering on GitHub") requires.

Evidence: reports/evidence/T-18-verification.txt.

### [2026-08-20 03:41 IST] [T-17] [autonomous overnight batch - delivery engineer]

Wrote `cli.py`. Used the task-board's one-line entry as the complete
spec (product owner explicit instruction): "CLI | pasted session
showing risk table + confirm prompt" - no additional CLI features
invented.

stdlib-only (argparse/json/urllib), no new dependency. Talks to the
real deployed API over HTTP rather than reimplementing risk-engine
logic, so a session against it exercises the actual live system.
`POST /v1/actions/{id}/confirm` is literally named "confirm" in
`app/main.py:240` - mapped the DoD's "confirm prompt" directly onto
that real endpoint rather than inventing a broader decision UI. When a
FULL_REVIEW-tier action is evaluated, the CLI explains that a *separate*
reviewer_id is required (S-6) and does not attempt that step itself,
since this is a single-user session.

Verification: ran a real session against the live Railway URL
(`https://aivartba-production.up.railway.app`) using the canonical T-08
single-update scenario (`update_without_snapshot`, 1 record,
`regulatory=none`) - risk table printed (composite 0.38, tier CONFIRM,
floor `unrecoverable_mutation_requires_confirm`), confirm prompt
answered "y", action moved to `approved`. Full raw terminal output
saved, grepped for secret-shaped patterns first (0 matches).

No frozen weights/thresholds/floors/fail-closed direction touched -
`cli.py` is a pure HTTP client, imports nothing from `app.risk.*`.
`tests/test_routing.py` (T-08) not touched.

Evidence: reports/evidence/T-17-cli-session.txt.

### [2026-08-20 03:44 IST] [T-18a] [autonomous overnight batch - delivery engineer]

Added three README sections (`## 10. DMAIC`, `## 11. Versioning`,
`## 12. Fuzzy rejection`). Used the task-board's one-line entry as the
complete spec (product owner explicit instruction): "DMAIC + versioning
+ fuzzy-rejection sections | present in README" - confirmed via grep
that, same as T-16 and T-18a's own DoD, none of the three has a
detailed section anywhere else in the prompt pack.

Content grounded in real, already-established project artifacts, not
invented process or features:
  - DMAIC maps Define-Measure-Analyze-Improve-Control onto this
    project's actual history (T-08 criterion tests as Define, the test
    suites + live curl evidence as Measure, T-13's adversarial review +
    the D-01-D-12 defect register as Analyze, the approved T-13/Issue-2/
    D-11/D-12 fixes as Improve, the FROZEN list + T-08's read-only
    status as Control).
  - Versioning covers `weights_version` (`app/risk/scorer.py:56`), the
    `/v1/` API prefix, and the action-log/defect-register as change
    history - no new versioning mechanism added.
  - Fuzzy rejection documents that this system deliberately does NOT
    implement graduated/fuzzy rejection - citing T-13 Finding 3's
    already-accepted boundary-brittleness limitation - rather than
    introducing new logic. No frozen thresholds touched.

Learned from D-12 (T-18): checked for the same `^>` line-start
blockquote hazard before pushing this time, and verified via GitHub's
actual render API (not just local preview) after pushing - both times
clean (0 `<blockquote>` tags, all three headers render as real `<h2>`
elements).

No code changed. `tests/test_routing.py` (T-08) untouched. No frozen
weights/thresholds/floors/fail-closed direction modified.

Evidence: reports/evidence/T-18a-verification.txt.

### [2026-08-20 03:46 IST] [G3] [autonomous overnight batch - delivery engineer]

Report-only gate check, no code/config changes. Gate condition (task
board/prompt pack): "Live URL + green suite + README". Noted a naming
collision: `reports/00-project-charter.md`'s own Gate-schedule table
also has a row labeled "G3" with a different, already-satisfied
condition ("API + audit log wired end-to-end") - evaluated the task-
board/prompt-pack G3 per this batch's explicit instruction, not the
charter's.

All three conditions freshly re-verified (not just cited from earlier
evidence): live `/livez` check (200), latest GitHub Actions run
confirmed at the exact current HEAD commit (`430fab6`, 115/115 passed),
README's 12 sections + 4-row traceability table confirmed present.

**G3: PASS.** Full report: `reports/gates/G3-report.md`. T-15 (AWS)
remains Not started - out of scope for this batch, not required by
G3's own condition. Continuing to T-19 per the batch's instruction.

### [2026-08-20 03:50 IST] [T-19] [autonomous overnight batch - delivery engineer]

Ran a genuine concurrency load test against the live Railway URL. Used
the task-board's one-line entry as the complete spec: "50 requests, 0
failures, p95, matching audit rows" - no different workload or
acceptance condition invented.

Workload: 50 genuinely concurrent `POST /v1/actions/evaluate` calls
(`asyncio.gather` + `httpx.AsyncClient`, not sequential, not threading
within one process - matching the concurrency-proof discipline already
established in the pre-T-14 Issue 1 fix) against a read-only action, so
the load exercises the real deployed system end-to-end: real LLM call,
real Postgres writes, real hash-chained audit append.

**Attempt 1** (client timeout 30s): 2/50 requests hit the client's own
timeout. Root-caused via read-only diagnosis, not a blind retry:
`app/db_store.py:243`'s `pg_advisory_xact_lock` (D-10's intentional fix
to prevent the hash chain forking under concurrent writers) serializes
every audit write, so 50 concurrent evaluate calls queue for that lock
one at a time - total wall-clock time scales with N. My test client's
30s cutoff was tighter than the server's actual completion time for the
last few queued requests; this is a test-harness limitation, not a
server-side failure.

**Attempt 2** (client timeout raised to 90s - test-harness change only,
no application code touched): 0/50 failures. p50=19.2s, **p95=29.4s**,
p99=30.3s, max=30.3s. All 50 returned HTTP 201 with a unique `action_id`.

**Audit-row reconciliation**: fetched `/v1/audit?limit=500` (the
endpoint defaults to `limit=50`, `app/main.py:386` - the first
reconciliation attempt without an explicit limit was silently truncated
and corrected before drawing any conclusion, not reported as a false
finding). Total audit records after both attempts: 128 = baseline 28 +
attempt 1's 50 + attempt 2's 50 exactly - confirming attempt 1's 2
"failed" requests DID complete server-side despite the client timing
out. All 50 of attempt 2's `action_id`s are present in `/v1/audit` as
`evaluated` records, 0 missing - **matching audit rows confirmed 1:1**.

No frozen weights/thresholds/floors/fail-closed direction touched; no
application code changed - this was a read-only load-generation script
plus diagnosis of already-existing, already-approved (D-10) behavior.
`tests/test_routing.py` (T-08) not touched.

**Honest note on latency**: p95 ≈ 29.4s under 50-way concurrency is
high, driven by the intentional audit-write serialization (D-10). The
DoD ("50 requests, 0 failures, p95, matching audit rows") only requires
recording p95, not a specific bound - reporting this transparently
rather than characterizing it as fast.

Evidence: reports/evidence/T-19-concurrency.txt.

### [2026-08-20 10:02 IST] [Item 0 - novelty add-on] [autonomous 60-min batch - delivery engineer] [branch: feature/novelty-addons]

Added the "Theoretical grounding" README section (`## 13.`) and a video
script line, per `PS-9-1-Novelty-Addons-v1.0.md` ("ITEM 0 - The
reframing"). The user's message referenced this file with a "(1)"
suffix that does not exist - found the real, unsuffixed file
(`PS-9-1-Novelty-Addons-v1.0.md`, repo root) via a broad filesystem
search before treating any content as authoritative, same discipline
applied earlier this session to the "(2)"-suffixed prompt pack.

Content pasted matches the source's "Paste into README, new section:
'Theoretical grounding'" block (lines 40-52) exactly, converted from
the source's own blockquote-as-paste-target convention to plain README
prose - not literal `>` blockquote markup, to avoid reintroducing D-12's
line-wrap blockquote bug (checked: 0 lines start with `>` after the
edit). The video line (lines 56-60) went into a new
`reports/video-script.md`, since no video-script file/convention
existed yet in this project.

Zero code changed - `git diff --name-only` shows only `README.md`;
`app/risk/` and `tests/test_routing.py` (T-08) confirmed zero-diff.

Evidence: reports/evidence/Item0-verification.txt.
