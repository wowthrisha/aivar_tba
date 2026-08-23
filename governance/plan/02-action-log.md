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
Evidence: governance/evidence/T-06-pytest.txt (`24 passed in 0.02s`).

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
Evidence: governance/evidence/T-06a-pytest.txt (`26 passed in 0.02s`). Sample
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
Evidence: governance/evidence/T-07-pytest.txt (`35 passed in 0.03s`).

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
Evidence: governance/evidence/T-07a-pytest.txt (`47 passed in 0.04s`).

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
Evidence: governance/evidence/T-08-pytest.txt (full suite, `51 passed in
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
Evidence: governance/evidence/T-09-pytest.txt (`56 passed in 0.32s`).

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
Evidence: governance/evidence/T-09a-pytest.txt (`62 passed in 0.29s`).

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
Evidence: governance/evidence/T-09-fix-live-verification.txt; D-02 logged in
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
Evidence: governance/evidence/T-10-pytest.txt (`80 passed`),
governance/evidence/T-10-curl.txt (full request/response pairs for all 9
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
Evidence: governance/evidence/T-11-verification.txt (alembic current,
alembic history, table list, index list, live /readyz curl),
governance/evidence/T-11-pytest.txt (`80 passed`).

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
Evidence: governance/evidence/T-12-pytest.txt (dedicated security tests),
governance/evidence/T-12-full-suite.txt (`88 passed`).

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
Full findings: governance/evidence/T-13-adversarial-review.txt.

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
Evidence: governance/evidence/T-13-adversarial-review.txt (original
findings), governance/evidence/T-13-fixes-pytest.txt (17/17 regression
tests), governance/evidence/T-13-fixes-full-suite.txt (`105 passed`),
governance/evidence/T-13-adversarial-cases-rerun.txt (before/after on the
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
Evidence: governance/evidence/G2-state-machine-curl.txt,
governance/gates/G2-report.md, governance/blocks/block-2-report.md.
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
Evidence: governance/evidence/issue2-llm-cache-fix-pytest.txt,
governance/evidence/issue2-full-suite.txt (`106 passed`).

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
Evidence: governance/evidence/issue1-db-store-pytest.txt (6/6 real-DB
tests), governance/evidence/issue1-full-suite.txt (`106 passed, 6
skipped`), governance/evidence/issue1-db-backed-curl.txt (full walkthrough,
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

Evidence: governance/evidence/T-14-curl.txt (all 5 checks, final run).

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

**D-11** (see `governance/plan/03-errors-and-fixes.md`): while capturing
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

DoD verification (raw evidence in `governance/evidence/T-16-raw-demo.txt`):
  - JSON log line with request_id: PASS on both the success path
    (`GET /livez`) and, after the D-11 fix, the forced-error path.
  - Forced error returns clean JSON: PASS - `RuntimeError` forced via a
    broken `get_store` dependency override returns `HTTP 500`,
    `Content-Type: application/json`, body
    `{"detail":"internal server error"}`, parses cleanly with
    `json.loads`.

Evidence: governance/evidence/T-16-pytest.txt (3/3 + full suite 109
passed/6 skipped), governance/evidence/T-16-raw-demo.txt (literal JSON log
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
    `governance/evidence/T-14-curl.txt`) instead of the prompt's
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

**D-12** (see `governance/plan/03-errors-and-fixes.md`): found while
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

Verification (all raw output in `governance/evidence/T-18-verification.txt`):
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

Evidence: governance/evidence/T-18-verification.txt.

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

Evidence: governance/evidence/T-17-cli-session.txt.

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

Evidence: governance/evidence/T-18a-verification.txt.

### [2026-08-20 03:46 IST] [G3] [autonomous overnight batch - delivery engineer]

Report-only gate check, no code/config changes. Gate condition (task
board/prompt pack): "Live URL + green suite + README". Noted a naming
collision: `governance/charter.md`'s own Gate-schedule table
also has a row labeled "G3" with a different, already-satisfied
condition ("API + audit log wired end-to-end") - evaluated the task-
board/prompt-pack G3 per this batch's explicit instruction, not the
charter's.

All three conditions freshly re-verified (not just cited from earlier
evidence): live `/livez` check (200), latest GitHub Actions run
confirmed at the exact current HEAD commit (`430fab6`, 115/115 passed),
README's 12 sections + 4-row traceability table confirmed present.

**G3: PASS.** Full report: `governance/gates/G3-report.md`. T-15 (AWS)
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

Evidence: governance/evidence/T-19-concurrency.txt.

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
`governance/video-script.md`, since no video-script file/convention
existed yet in this project.

Zero code changed - `git diff --name-only` shows only `README.md`;
`app/risk/` and `tests/test_routing.py` (T-08) confirmed zero-diff.

Evidence: governance/evidence/Item0-verification.txt.

### [2026-08-20 10:09 IST] [Feature A - novelty add-on] [autonomous 60-min batch - delivery engineer] [branch: feature/novelty-addons]

Implemented `GET /v1/oversight/reviewers` per the exact Feature A prompt
in `PS-9-1-Novelty-Addons-v1.0.md` (lines 76-115).

**Design decision, documented not silent**: the spec says "aggregated
over the approvals table", but `approvals` only ever holds
`decision="approve"` rows - `app/main.py`'s `decision()` handler never
calls `store.set_approval()` for a reject. Aggregating strictly over
`approvals` alone would make `approval_rate` trivially 1.0 for any
reviewer who ever approved anything, silently hiding every reject -
exactly the kind of silent approximation E-6 forbids. Used the audit
log's `event_type="decision"` records instead (already written for
every decision, approve or reject, by the same handler) as the single
source for `decisions_total`/`approval_rate`/latency, cross-joined
against each action's `created_at` (proposal time) and current `state`
(for reversal_rate). No schema change, no migration, no new dependency
- `app/oversight.py`'s module docstring documents this choice in full.

**Files**: new `app/oversight.py` (pure aggregation, no I/O - matches
`app/risk/scorer.py`'s pattern), new `OversightResponse`/`ReviewerMetrics`
Pydantic models (kept with the logic module, matching
`RiskAssessmentResult`'s precedent rather than `app/schemas.py`), new
`GET /v1/oversight/reviewers` route in `app/main.py`, new
`tests/test_oversight.py`.

**Real finding from reading the code, not assumed**: `app/state_machine.py`'s
`VALID_TRANSITIONS` shows `APPROVED -> {EXECUTED, EXPIRED}` only - an
approved action can never reach `REJECTED` afterward (structurally
unreachable). So `reversal_rate`'s "REJECTED or EXPIRED" wording is
correct as written (defensive for a state the current state machine
happens not to produce), but only EXPIRED can genuinely occur today -
noted in case this surfaces oddly in review.

**Empty-reviewer semantics**: 0 decisions -> `approval_rate`,
`median_decision_latency`, `p90_decision_latency` = `null` (genuinely
undefined, 0/0) but `reversal_rate` = `0.0` (the spec's own explicit
fallback for that one metric, not null) and `automation_bias_flag` =
`false` (well-defined regardless, since `decisions_total >= 5` fails).

Tests: the three required
(`test_oversight_metrics_computes_approval_rate`,
`test_automation_bias_flag_fires_on_rubber_stamping` - seeded exactly
6 decisions/6 approvals/2s latency per the spec, flag confirmed True -,
`test_oversight_metrics_empty_reviewer_returns_nulls_not_zeros`) plus 4
additional edge cases (sub-5-decisions no-flag, slow-review no-flag,
reversal-rate counting, reversal-rate-zero-with-no-approvals). 7/7 pass.
Full suite: 116 passed, 6 skipped (unchanged real-DB skip count).
`tests/test_routing.py` (T-08) re-verified zero-diff against `a573663`.
`app/risk/` untouched (empty `git diff --stat`). Frozen weights/
thresholds re-grepped and confirmed unchanged.

Endpoint run locally (TestClient, not yet deployed anywhere) against
representative seeded data: reviewer "careful-carla" (3 decisions, 2
approve/1 reject, latency ~1-2ms since scripted) correctly shows
`approval_rate=0.67`, `automation_bias_flag=false`; reviewer
"rubber-stamp-rick" (6 rapid approvals) correctly shows
`approval_rate=1.0`, `automation_bias_flag=true` - the rubber-stamping
signature fires as designed. `review_queue_depth=1` for one pending
action, `oldest_pending_age_seconds` correctly populated.

No defects found this task - first-attempt implementation, all tests
green on first run.

Evidence: governance/evidence/FeatureA-verification.txt.

### [2026-08-20 12:31 IST] [Feature B - novelty add-on] [autonomous pre-video batch - delivery engineer] [branch: feature/novelty-addons]

Implemented precedent retrieval + novelty escalation per the exact
Feature B prompt in `PS-9-1-Novelty-Addons-v1.0.md` (lines 142-192).
Tests-first: wrote and ran `tests/test_novelty.py`'s 4 required tests
against the pure logic before any DB/endpoint wiring existed.

**Architecture, deliberately isolated from app/risk/**: the novelty
floor is implemented in a new `app/embeddings.py`, applied by
`app/main.py`'s `evaluate()` handler as a step AFTER
`route_action()` returns - `app/risk/floors.py`/`router.py`/`scorer.py`/
`tiers.py` are all byte-identical (confirmed, `git diff --stat --
app/risk/` empty). T-08 (`tests/test_routing.py`) untouched and
re-verified zero-diff. This means `route_action()`'s signature never
changed, so nothing about how T-08 calls it could ever be affected,
regardless of what the novelty layer does.

**Migration**: `migrations/versions/5517c3ad655b_add_embedding_to_actions.py`
- single `op.add_column('actions', ..., nullable=True)`, correct
`downgrade()`. Verified additive-only (no drops, no type changes)
BEFORE applying. Applied to the real Neon DB (the same one Railway/
master reads) via `alembic upgrade head` - safe because it's a new
nullable column no other branch's code reads or writes; re-ran the 6
real-DB tests afterward to confirm existing persistence paths are
unaffected (6/6 pass, ~144s). `numpy==2.2.6` pinned explicitly in
`requirements.txt` (was only a transitive dependency locally - same gap
class as T-14's `greenlet` finding, D-caught before it could repeat).

**Design decisions**:
- `precedent` added as an optional field on the existing
  `ActionResponse` (default `None`), populated only by `evaluate()` -
  not persisted, so every other endpoint reusing that model stays null.
- Existing hermetic test fixtures (`tests/test_api.py`,
  `test_adversarial_fixes.py`, `test_security.py`) needed a
  `get_embedding_provider` override added, or they'd try to construct a
  real `AsyncOpenAI` client during hermetic runs. Added a
  `_FakeEmbeddingProvider` to each, matching the existing per-file fake
  pattern. Not a business-logic change - required for the new
  dependency to not break existing tests.
- Escalation explanation is appended to the existing routing
  explanation (`"{original} Escalated to {tier} (novel action: ...)."`)
  rather than replacing it, so the original weighted/floor reasoning
  stays visible alongside the novelty escalation - both are true and
  both matter for an audit.

Tests: 4 required
(`test_precedent_returns_similar_actions_with_outcomes`,
`test_novelty_floor_escalates_unprecedented_action`,
`test_novelty_floor_inactive_below_20_prior_actions`,
`test_escalate_only_invariant_holds_with_novelty_floor` - the last one
sweeps `Reversibility x affected_records x Regulatory x confidence x`
4 novelty-similarity/prior-count cases, proving the SAME escalate-only
guarantee tests/test_floors.py::test_escalate_only_sweep proves for the
original floors, but for this new layer stacked on top). 4/4 pass.
Full suite: 120 passed, 6 skipped (hermetic) + 6/6 real-DB tests passed
separately. T-08 zero-diff. Frozen weights/thresholds re-grepped,
unchanged.

**End-to-end demo** (TestClient, controllable fake embeddings, not
real OpenAI calls): seeded 25 executed (genuinely terminal) actions in
one embedding cluster, then evaluated a read-only action with an
orthogonal embedding - correctly escalated `AUTONOMOUS -> CONFIRM`
with explanation `"... Escalated to CONFIRM (novel action: no close
precedent in 25 prior actions)."` and `precedent.matches` showing 3
same-cluster matches at `similarity: 0.0`. A control action still
inside the familiar cluster correctly stayed AUTONOMOUS
(`similarity: 1.0` on its 3 matches). One real bug caught in my own
demo script during this process (not the implementation): the first
draft never called `/execute` on the seeded actions, so they sat at
`state=autonomous` - not itself terminal - and 0 candidates were found;
fixed the demo, not the code, since "have embeddings AND a terminal
outcome" is exactly what the spec asks for.

No defects in the implementation itself this task - first-attempt
green on all 4 required tests and the full suite.

Elapsed: ~12 minutes (started ~12:19 IST after switching from the
blocked AWS task, well under the 50-minute box).

Evidence: governance/evidence/FeatureB-verification.txt.

### [2026-08-20 13:08 IST] [Merge to master + redeploy] [delivery engineer]

Product owner explicitly overrode the standing "never merge to master"
rule for this session, with a full written rationale (RAG credit for
proven work already on `feature/novelty-addons`; `submission-v1` as an
explicit, already-tagged rollback point; avoiding a rebuild of a lesser
version under time pressure) and a strict, gated, stop-on-red execution
order. Followed that order exactly; no step was skipped or reordered.

1. **Full suite on master (post-merge)**: `git merge --no-ff
   feature/novelty-addons` (commit `632ffbf`) - clean, no conflicts.
   120 passed, 6 skipped, 0 failed.
2. **T-08 zero-diff**: `git diff a573663 -- tests/test_routing.py` -
   empty.
3. **Frozen values + app/risk/ zero-diff**: weights/thresholds
   re-grepped unchanged; `git diff --stat 8551be1 632ffbf -- app/risk/`
   - empty (the merge touched nothing in the risk engine, confirming
   Feature B's architecture choice held).
4. **Deploy**: `git push origin master` (`8551be1..632ffbf`) triggered
   Railway's GitHub auto-deploy directly - no separate `railway up`
   needed. New deployment `10c59093`, Online, `/livez` -> 200.
5. **Seeded the live deployment**: 24 actions across the three
   criterion categories (8 bulk deletes with mixed approve/reject
   outcomes, 8 single updates confirmed+executed - CONFIRM-tier actions
   have no reject path via the current API, noted honestly rather than
   worked around -, 8 read-only auto-executed), independently
   re-verified via a fresh `/v1/audit` count (24 seed-agent `evaluated`
   records, 8 per tier) and via the new `/v1/oversight/reviewers`
   endpoint itself (`seed-reviewer`: 8 decisions, 0.5 approval rate,
   matching the seeded approve/reject alternation).
6. **Gate check, all three criterion actions on the live URL** - none
   escalated by the novelty floor, comfortable margin on every one:
     - bulk delete -> FULL_REVIEW, composite 0.68, max_similarity 0.9427
     - single update -> CONFIRM, composite 0.44, max_similarity 0.9187
     - read-only -> AUTONOMOUS, composite 0.07, max_similarity 0.9055
   The seeding worked on the first attempt - no reseed or threshold
   adjustment was needed.
7. **Deliberate novelty firing**: a `pay` action with a resource/params
   pattern unlike anything seeded (`max_similarity=0.2957`, well under
   0.75, with 24 >= 20 prior embedded actions) correctly escalated
   CONFIRM -> FULL_REVIEW. Verified via the actual persisted
   `/v1/audit` record (not just the evaluate response):
   `floor_name: "novelty_unprecedented"`, explanation showing both the
   original floor reason and the escalation.
8. **`/v1/audit`, `/v1/audit/verify`, `/v1/oversight/reviewers`**: all
   200. `/v1/audit/verify` -> `valid: true, records_checked: 303` - hash
   chain intact across the entire session's history, unaffected by the
   merge/deploy/seed/gate-check work.
9. **Tagged and pushed `submission-v2`** ("Item 0 + Feature A + Feature
   B") on the merge commit.
10. Updated `governance/charter.md` with a new "Submission
    tags" section recording both tags and the rollback command.

No step in this sequence hit red - every gate passed on the first
attempt, including the seeding step the plan itself flagged as the one
real risk. Rollback path if ever needed: `git reset --hard
submission-v1`.

Evidence: this action-log entry (raw command output pasted directly in
conversation at each step, not summarized after the fact).

### [2026-08-20 13:44 IST] [OD-1] [delivery engineer]
Action: CLI output redesign per `PS-9-1-Output-Design-v1.0.md` item 1,
presentation-only, on master. Found during exploration that the four
per-dimension risk scores (`reversibility`/`data_scope`/`regulatory`/
`confidence`) and `floor_name` are computed in `app/risk/` and persisted
to `risk_assessments`, but were never returned by `ActionResponse` -
`app/store.py`'s own docstring flagged this as a known gap. This
conflicted with the task's hard-constraint list ("do not modify
app/main.py") vs its FACTOR-DATA RULE (which anticipates exactly this
case and asks for an additive-only fix). Stopped and asked the user via
AskUserQuestion rather than guess; approved to proceed with an
additive-only edit.
Changes: `app/schemas.py` (+5 optional `ActionResponse` fields, nothing
existing touched), `app/store.py`/`app/db_store.py` (populate + read
back those 5 values, already computed/passed in, previously dropped),
`app/main.py` (single touch: `_to_action_response()` now passes the 5
new fields through - no other line changed), `requirements.txt` (pinned
`rich==14.2.0`, present locally only as a transitive dependency - same
class of gap as `numpy`/`greenlet`, documented inline), `cli.py`
(rewrote the rendering path only - verdict banner colored by tier, WHY
block when a floor fired, a score axis using the real `THRESHOLD_CONFIRM`/
`THRESHOLD_FULL_REVIEW` constants, per-factor bars using the real
`WEIGHTS` dict, IF... counterfactual when the API provides one, tier-
dependent NEXT line). Arg parsing, HTTP calls, and the confirm/full-review
control flow in `main()` are unchanged.
Tests added: `tests/test_cli.py` (`test_cli_renders_all_three_tiers_without_error`,
`test_cli_output_contains_composite_and_tier`, built from the real
`score_action`/`route_action` outputs, not hand-picked numbers) and
`tests/test_api.py::test_evaluate_response_includes_factor_scores_matching_persisted_assessment`
(proves the 5 new response fields equal what `score_action`/`route_action`
actually compute for the request, and that `GET` round-trips the same
persisted values).
Result: `pytest tests/test_cli.py tests/test_api.py -q` green; full suite
125 passed, 6 skipped (pre-existing skips, unrelated to this change), 0
failures. Safety checks before commit: `git diff a573663 --
tests/test_routing.py` empty; `app/risk/` and `app/llm.py` diff empty;
weights still 0.40/0.30/0.20/0.10, thresholds still 0.30/0.65
(unedited). Ran the CLI against the then-current live URL for a
read-only case first - genuinely surfaced a FULL_REVIEW-tier, composite
0.16 result (low-confidence floor + Feature B's novelty-escalation
floor both fired), which the redesigned banner rendered correctly
without me mistaking it for a bug (verified against raw `GET
/v1/actions/{id}` JSON before trusting the render - E-1). The FACTORS
block was legitimately empty against that pre-deploy backend, since the
old schema doesn't carry the new fields yet - correct degrade-clean
behavior, not a defect.
Evidence: `governance/evidence/OD-1-full-suite.txt`,
`governance/evidence/OD-1-test-routing-diff.txt` (empty file = proof),
`governance/evidence/OD-1-raw-autonomous-case.json`,
`governance/evidence/OD-1-cli-autonomous.txt`.

### [2026-08-20 13:50 IST] [OD-1] [delivery engineer]
Action: Committed (`fddaec7`) and pushed to `origin/master`, which
triggered Railway's connected auto-deploy. `/livez` never distinguishes
versions (no I/O by design, T-15), so verified the new fields were
actually live by POSTing a real evaluate call rather than trusting the
healthcheck alone. Then ran all three canonical scenarios against the
live URL, capturing raw API JSON and the CLI rendering from the exact
same call each time (not two separate calls, since the live LLM call
makes composite non-deterministic across calls) - one per tier:
read-only -> AUTONOMOUS, single update -> CONFIRM (novelty-escalated
from AUTONOMOUS, Feature B), bulk irreversible delete -> FULL_REVIEW
(irreversible_bulk floor).
Result: Numerical reconciliation exact (not just within tolerance) on
all three: for every one of the 4 factors on all 3 actions,
displayed_contribution == raw_score * WEIGHTS[key] to full float
precision, and sum(contributions) == API composite exactly (diff
0.000000 in all 3 cases). WHY block correctly shown only when
floor_name is non-null, using the real persisted explanation string
verbatim (no parsing/derivation). NO_COLOR=1 run against the
FULL_REVIEW case verified byte-for-byte free of ANSI escape codes
(checked programmatically, not by eye) and remained fully readable.
Full suite re-run green after all live calls (125 passed, 6 skipped,
same as before deploy - the deploy didn't regress anything).
Evidence: `governance/evidence/OD-1-raw-{autonomous,confirm,full-review}-case.json`,
`governance/evidence/OD-1-cli-{autonomous,confirm,full-review}.txt`,
`governance/evidence/OD-1-cli-no-color.txt`,
`governance/evidence/OD-1-numerical-reconciliation.txt`.
Commit: `fddaec7`. `git log --oneline -1 origin/master` == `fddaec7`
after push (HEAD == origin/master).

### [2026-08-20 14:43 IST] [BONUS-CALIBRATION] [delivery engineer]
Action: Implemented the adaptive-calibration bonus, shadow mode only by
default, 40-minute box. New `app/calibration.py`: derives clean vs
modified/rejected confirmation counts per action_type from EXISTING
audit ("confirmed"/"decision" events) + approval records - no new
table, no migration, no writes. Pure formula
`adjustment = clamp((0.5 - clean_ratio) * 0.20, -0.10, +0.10)`,
recomputed from history every call, zero below 5 samples. `CALIBRATION_MODE`
env var (off/shadow/enforce, unknown value -> off, unset -> shadow, enforce
never enabled by default anywhere in this repo).
Wiring: `app/schemas.py` (+`CalibrationInfo`, `CalibrationActionTypeStats`,
`CalibrationResponse`, +`calibration` field on `ActionResponse`, all
additive). `app/main.py`: imports `evaluate_floors`/`final_tier`/
`tier_for_composite` from `app/risk/` (NOT modified, only called a
second time with a calibration-adjusted composite in enforce mode) and
inserts a calibration step in `evaluate()` between `route_action()` and
the novelty-escalation block, per the required order (base composite ->
calibration -> tier -> floors -> novelty). New read-only `GET
/v1/calibration`. Necessarily modified `evaluate()`'s body and
`_to_action_response()` to do this - flagging this explicitly since one
of the final-verification bullets says "no existing endpoint handler
changed": no OTHER endpoint (confirm/decision/execute/review-queue/
audit/oversight/get_action) was touched at all, and evaluate()'s
observable behaviour in off/shadow mode (the default) is proven
byte-identical to pre-feature baseline by the regression tests below -
but the source of evaluate() itself did change, which is the only way
to implement the required calibration interception point.
Safety: floors are evaluated on raw inputs (reversibility/
affected_records/regulatory/llm_confidence), never on composite, and
`final_tier()` is a `max()` over the weighted tier and the floor tier -
so calibration structurally cannot suppress a floor, proven by test
(max -0.10 adjustment against an irreversible-bulk floor: still
FULL_REVIEW). CLI (`cli.py`): a CALIBRATION line, shown only when
adjustment != 0, labelled "modification/rejection" rather than
"rejection" since the underlying bucket doesn't distinguish the two -
avoided overclaiming specificity the data doesn't have.
Tests: `tests/test_calibration.py`, 13 tests - pure-formula (min
evidence, negative/positive adjustment, clamp), fail-soft on a
raising audit (module-level and full API-level), off-mode omits the
field, the critical regression test (all 3 canonical scenarios,
parametrized, baseline obtained from a same-run CALIBRATION_MODE=off
call - not assumed from memory - and asserted byte-identical under
shadow), shadow-mode-with-real-history-still-untouched, enforce-mode
floor-suppression-impossible, and the GET /v1/calibration endpoint.
Result: full suite 138 passed (125 prior + 13 new), 6 skipped
(pre-existing), 0 failed. All local, no live/deployed calls - per the
task's "do NOT push yet."
Final verification (A-I, all local via TestClient, no deploy):
A) full suite green (above). B) `git diff a573663 -- tests/test_routing.py`
empty. C) `git diff --stat -- app/risk/` empty. D) no migration; weights
0.40/0.30/0.20/0.10 and thresholds 0.30/0.65 unedited; only
evaluate()/_to_action_response() changed in app/main.py (see caveat
above), every other endpoint untouched. E/G) shadow mode vs a
same-run off-mode baseline: composite/tier/floor_name identical on all
3 canonical scenarios, calibration.applied=False on all 3. F) off mode
omits `calibration` entirely. H) a 4th action_type ("send") seeded with
6 clean confirmations produced a real -0.10 adjustment in shadow mode
(still applied=False). I) GET /v1/calibration returns per-action_type
counts/adjustment/sample_size/has_min_evidence + current mode.
Evidence: `governance/evidence/bonus-calibration/A-full-suite.txt`,
`B-D-safety-checks.txt`, `EFGHI-verification.txt`,
`EFGHI-verification.json`.
Not pushed, not deployed, per explicit instruction.

### [2026-08-20 17:47 IST] [T-15] [post-submission]

Action: Verified AWS Lambda + Function URL deploy (T-15), previously
"Not started" at submission time. User reported it as done; found the
resources already live in the account (`aws sts get-caller-identity`
confirmed account 870591755696) rather than deploying anything new this
session — function `ps91-t15`, ECR repo `ps91-t15`, IAM role
`ps91-t15-lambda-execution-role`, Function URL created 2026-08-20
09:31 UTC (~15:01 IST), well before this verification.

Verified live, not just "State: Active": `GET /livez` -> 200
`{"status":"ok"}`; `GET /readyz` -> 200
`{"status":"ok","checks":{"llm":"ok","db":"ok"}}`; then the full
read-only scenario via `cli.py` (same inputs as `demo.sh read`) against
the Function URL -> AUTONOMOUS, composite 0.098 - confirms the actual
governance logic works end-to-end on Lambda, not only the health
endpoints. Evidence: `governance/evidence/T-15-curl.txt`.

**Security incident (mid-task)**: `aws lambda get-function
--function-name ps91-t15` was run to inspect the function's config and
returned its environment variables in plaintext as part of the normal
response - `DATABASE_URL` (with the Neon Postgres password) and
`OPENAI_API_KEY` were both printed into the terminal/chat transcript.
This is AWS's default behavior for that API call, not a script or repo
bug, but it's a real exposure regardless. Disclosed to the user
immediately in the same turn; user was asked to rotate both the OpenAI
key and the Neon DB password. No further `get-function` calls were made
for the rest of this task - `get-function-url-config` and curl were
used instead, neither of which return secrets.

Synced documentation to match verified reality (this was previously
inconsistent - the repo said "AWS is not deployed" while AWS was
actually live): `README.md` (Live URLs section + L-E),
`governance/plan/03-errors-and-fixes.md` (L-E), `governance/plan/01-implementation-plan.md`
(T-15 status Not started -> Done). Not yet committed - append-to-log-
before-commit (L-6) done first, per this project's own operating
contract.

### [2026-08-21 00:05 IST] [D-26/D-27] [assistant] [L-6 catch-up]
Action: The prior two commits (`475122c`, `6eeff52`) were pushed without
an entry here first, missing this project's own L-6. Recording
retroactively before any new work starts.
`475122c`: found `Dockerfile` (the AWS Lambda build input) untracked in
`~/aivar_tba` - `git status --short` showed `??`, meaning a fresh clone
could not have rebuilt the deployed image. Recovered it from the
`gae-aws`/`aws-deploy` checkout (deleted earlier the same session after
its only uncommitted file, this Dockerfile, was copied out first) and
committed it here. Logged as D-26 in `03-errors-and-fixes.md`.
`6eeff52`: ran the user's 4-axis staleness verification (code/
dependencies/credentials/shared-state) against Railway and AWS.
F1-F3 fingerprints passed on both; F4 (`floors_fired`) absent on both,
expected since it did not exist yet (this is what D-24, below, ships).
Axis 2 (dependencies) could not be verified - no version endpoint
existed at the time. Axis 4 (shared state) reconciled exactly
(505 -> 506, +1 accounted for by one call made between checks). Also
logged D-27: a follow-up verification pass using freshly-invented
resource names showed read/update scenarios escalate one tier via
`novelty_unprecedented` - initially looked like a regression, was
actually the novelty floor working as designed on unprecedented
resource names; re-verified correctly with seeded names instead.
Result: Both commits' content stands as pushed; this entry is the
missing log record for them, not a code change.
Evidence: `governance/plan/03-errors-and-fixes.md` (D-26, D-27),
`governance/evidence/staleness-verification.txt`.

### [2026-08-21 00:40 IST] [D-24 / version endpoint] [assistant]
Action: Two explicitly-approved, explicitly-scoped changes.
(1) `GET /v1/version` (`app/main.py`, `app/schemas.py`): read-only, no
auth/DB/LLM. Reports `git_sha`/`build_time` from env vars ("unknown" if
absent - Railway has no auto-injected git-SHA var on this service,
confirmed by reading `railway variables`, so this is not guessed) and
`python_version`/`key_dependencies` (pydantic/fastapi/openai/sqlalchemy)
read live via `importlib.metadata`, never from requirements.txt. Closes
Axis 2 of the D-26/D-27 staleness check permanently.
(2) D-24: `evaluate_floors()` (`app/risk/floors.py`) rewritten from a
first-match/return chain to evaluate-all-and-collect. Zero change to any
trigger condition, threshold, or produced tier - only which matched
floor gets NAMED (`FLOOR_PRIORITY`, an explicit product-decision order)
and that every match is now recorded (new `floors_fired` field, additive
on `FloorResult`/`RoutingResult`/`ActionResponse`, not persisted to the
DB - same pattern as the existing `precedent` field). Separately fixed
`app/main.py`'s novelty step, which previously overwrote
`final_floor_name = "novelty_unprecedented"` unconditionally, erasing
whatever floor had actually fired; it now appends to `floors_fired` and
re-derives `floor_name` by priority. Also fixed `infra/aws/deploy-lambda.sh`'s
`BUILD_DIR` default (`../gae-aws` - a sibling clone this session deleted
after D-26 moved the Dockerfile into this repo) to `.`, and added
`GIT_SHA`/`BUILD_TIME` `ARG`/`ENV` to `Dockerfile` plus `--build-arg`
passthrough in the same script, so the AWS image gets real values baked
in at build time without needing any Lambda IAM write permission (D-23).
Result: `pytest` — 152 passed, 6 skipped (144 prior + 8 new: 5 in
`tests/test_floors.py`, 1 in `tests/test_novelty.py`, 2 in new
`tests/test_version.py`), 0 failed. `git diff a573663 -- tests/test_routing.py`
empty (T-08 frozen criterion tests untouched). New tests confirmed to
FAIL against pre-refactor code before the implementation was written
(L-2).
Evidence: raw `pytest` output and `git diff` output pasted in this
session's transcript.

### [2026-08-21 18:10 IST] [D-28 / D-29 / L-H] [assistant]
Action: Fix pass approved after the 5-part parallel final-defect-sweep
audit (fuzz sweep, test-quality review, semantic/cross-layer, security/
adversarial, deployment parity). Three items, BLOCKING first, explicitly
scoped to not touch `app/risk/`, routing logic, or `tests/test_routing.py`.
(1) D-29: 4 confirmed live 500s (NaN/Infinity in `affected_records`, a
null byte in `params`, 500-deep-nested `params`) - investigated locally
before implementing rather than assuming the suggested fix mechanism
would work, and found it wouldn't: Pydantic already rejects non-finite
floats correctly, but FastAPI's default `RequestValidationError` handler
crashes trying to render the resulting error (it always echoes the raw
invalid value, and Starlette's `JSONResponse` enforces `allow_nan=False`).
Fixed with a custom `RequestValidationError` handler (`app/main.py`) that
sanitizes non-finite floats before rendering, plus `app/schemas.py`
validators (finite-number check, null-byte/control-char rejection,
64KB/20-level params bounds, 10,000-char string cap) applied uniformly
across every string/numeric field, not just the three that crashed.
(2) L-H: `canonical_params_hash` (`app/store.py`) now NFC-normalizes
before hashing - first attempt silently failed its own test because
`json.dumps`'s default `ensure_ascii=True` escapes non-ASCII chars to
`\uXXXX` sequences before normalization ever sees them, flattening NFC/NFD
forms into two different but equally-ASCII (hence normalization-is-a-
no-op) strings; fixed by also switching to `ensure_ascii=False`.
(3) D-28: audit payload now additionally carries the four sub-scores,
`weights_version`, and `git_sha`, so a hash-chained record can
reconstruct its own composite without reading the mutable
`risk_assessments` table - caveat noted in the register: only holds
under `CALIBRATION_MODE=shadow` (confirmed the only mode ever active
here); an enforce-mode adjustment isn't itself logged.
Result: `pytest` - 164 passed, 6 skipped, 0 failed (152 prior + 12 new: 10
in `tests/test_input_validation.py` - the 6 rejection cases plus 2
allow-list sanity checks for tab/newline/CR and reasonable nesting depth,
and 2 more from the field-enumeration; 1 in `tests/test_security.py`
(NFC/NFD); 1 in `tests/test_observability.py` (D-28)). `git diff a573663
-- tests/test_routing.py` empty.
`git diff --stat -- app/risk/` empty. New tests confirmed to fail against
pre-fix code before implementation, per L-2.
Evidence: raw `pytest`/`git diff` output and the local crash-reproduction
tracebacks (NaN/Infinity and deep-nesting) pasted in this session's
transcript.

### [2026-08-21 19:15 IST] [clean-v2 closeout] [assistant]
Action: Final closeout after the fix pass (`3047533`) was deployed to
both Railway and AWS (user confirmed via `GET /v1/version` on both
matching before this closeout began). Ran, live, both deployments: the
full parity table (3 canonical scenarios x 4 runs each, seeded resources
only per D-27), the fingerprint table (F1-F4, `readyz`, `/v1/version`,
`/v1/audit/verify`), all 4 D-29 fuzz cases (NaN/Infinity/null-byte/
deep-nesting - all now 422 on both), and L-H's NFC/NFD `params_hash`
fix (identical hash on both deployments, confirmed after retrying two
Railway calls that initially timed out client-side - not counted as
evidence until the retry actually returned data).
Logged D-30 (five parallel forks writing to one shared live database;
one became unaddressable and echoed redone copies of other forks'
assigned work instead of its own - same shape as D-15, concurrency
across independent workers sharing a mutable resource) and D-31
(GET /v1/version closes the staleness-verification gap that D-16/D-22/
the post-rotation sync each had to forensically reconstruct). Re-assessed
L-B (STILL VALID - D-24 fixed the symptom, not the two-tier-decision-
point structure) and narrowed L-I (the specific audit-payload gap it
flagged is now closed by D-28's test; the general "no full pairwise
cross-layer test" gap remains).
Result: both deployments identical at every checkpoint (git_sha,
records_checked, tier/floor_name/floors_fired on every scenario across
all 4 runs). Audit chain valid on both throughout, growing in lockstep
(513 -> 621 -> 671 -> 677), never diverging.
Evidence: `governance/evidence/final-closeout-clean-v2.txt` (parity
table, fingerprint table, fuzz/unicode verification raw output, all
four layers).

### [2026-08-22 09:30 IST] [Fix pass, L-B] [assistant]
Action: New `app/risk/decision.py::compose_final_decision()` - the sole
tier-composing function (composite -> calibration -> thresholds ->
floors -> novelty -> final tier/floors_fired/explanation). Wraps
`route_action()` (unchanged signature/behavior) rather than
reimplementing it. `app/main.py`'s `evaluate()` rewritten to gather I/O
(calibration stats, embedding/precedent) then make ONE call and use its
result verbatim - no post-hoc tier adjustment anywhere in `evaluate()`
now. `app/risk/router.py`/`floors.py`/`scorer.py`/`tiers.py` untouched.
Result: THE GATE (`tests/test_decision.py::test_refactor_preserves_every_tier`)
swept the full `itertools.product(Reversibility, affected_records_values,
Regulatory, confidence_values)` grid (2,916 combinations) comparing
`route_action()`'s tier/floor_name/floors_fired against
`compose_final_decision()`'s (off mode, no novelty) - identical on every
combination, confirmed before this commit. Full suite: 167 passed (164
prior + 3 new), 6 skipped, 0 failed. `git diff --stat -- app/risk/router.py
app/risk/floors.py app/risk/scorer.py app/risk/tiers.py` empty.
`git diff a573663 -- tests/test_routing.py` empty.
Evidence: raw `pytest` output and the gate sweep pasted in this
session's transcript.

### [2026-08-22 09:45 IST] [Fix pass, L-G] [assistant]
Action: `ActionResponse`/audit payload gain `uncertainty_score` (same
value as `confidence_score`, honestly named) and `llm_confidence_raw`
(the actual model self-report, uninverted, evaluate()-only per the same
pattern as `precedent`). `confidence_score` kept as a documented
deprecated alias - DB column not renamed this pass (separate
migration+backfill). Direction-contract test
(`tests/test_scoring.py::test_direction_contract_per_dimension`,
parameterised over all 4 composite dimensions) added as a standing
regression guard - passed immediately, confirming the underlying math
was always directionally correct; only the field NAME lied.
Result: `pytest` - 174 passed (167 prior + 7 new), 6 skipped, 0 failed.
`git diff --stat -- app/risk/` empty (schemas.py/main.py only).
Evidence: raw `pytest` output pasted in this session's transcript.

### [2026-08-22 10:20 IST] [Fix pass, L-I] [assistant]
Action: `weights_version` (persisted since T-14, never read back) now
flows `risk_assessments.weights_version` -> `ActionRecord.weights_version`
(`app/store.py`, `app/db_store.py`'s `_hydrate()`/`save_risk_assessment()`)
-> `ActionResponse.weights_version` (`app/schemas.py`, `app/main.py`).
Additive, no migration (column already existed). New DB-backed
`tests/test_db_store.py::test_all_four_layers_agree` (L-I) asserts
pairwise equality of the 4 sub-scores/composite/tier/floor_name/
weights_version across API JSON, the `risk_assessments` row, and the
audit payload, plus a real `cli.render_result()` call against the actual
API JSON for the CLI layer.
**Blocker hit and resolved during this fix, reported here rather than
silently worked around**: the test originally drove `evaluate()` via
`TestClient`, which hung indefinitely - confirmed via `pg_stat_activity`
showing ZERO active queries during the hang, ruling out a slow query and
pointing to a loop conflict between `TestClient`'s internal event loop
and pytest-asyncio's function-scoped loop (same class of issue as D-08).
Rewrote the test to call `app.main.evaluate()`/`get_action()` directly
(matching every other test in this file), which surfaced a SECOND,
independent, genuine performance defect: `app/calibration.py`'s
`calibration_for_action_type()`/`_historical_outcomes()` does one
`store.get_action()` round-trip PER historical confirmed/decision audit
record (N+1) - measured live at 122s for just 17 records against this
session's now-600+-row `audit_records` table. **Not fixed** -
`app/calibration.py` is out of scope for this fix pass (not one of
L-B/L-G/L-I or the two closeout gaps); the test instead forces
`CALIBRATION_MODE=off` via `monkeypatch`, which avoids the path entirely
without needing calibration behavior for what L-I actually tests.
Flagging this N+1 pattern to the user as a new finding, not silently
fixing it.
Result: `pytest tests/test_db_store.py` (real `DATABASE_URL`) - 7 passed
in 171.40s. Full local suite: 174 passed, 7 skipped (up from 6 - the new
test also skips without `DATABASE_URL`), 0 failed. `git diff --stat --
app/risk/` empty.
Evidence: raw `pytest` output, the `pg_stat_activity` diagnostic query,
and the isolated 122s `calibration_for_action_type` timing pasted in
this session's transcript.

### [2026-08-22 10:35 IST] [Fix pass, closeout gap 1] [assistant]
Action: audit payload (`app/main.py`'s `evaluate()`) gains
`calibration_mode`, `calibration_adjustment`, `base_composite`,
`effective_composite` - additive. Closes the caveat D-28's own entry
named: composite reconstruction from audit fields alone previously only
held under `CALIBRATION_MODE=shadow` (adjustment wasn't logged);
now holds under `enforce` too.
Result: new test `test_audit_payload_reconstructs_composite_under_enforce_mode`
(in-memory fakes, `CALIBRATION_MODE=enforce`, 6 seeded clean
confirmations for one action_type so the adjustment is genuinely
non-zero) confirms `sum(WEIGHTS[k]*payload[f"{k}_score"]) + payload["calibration_adjustment"] == payload["composite"]`
exactly. Full suite: 175 passed (174 prior + 1 new), 7 skipped, 0 failed.
`git diff --stat -- app/risk/` empty.
Evidence: raw `pytest` output pasted in this session's transcript.

### [2026-08-22 10:45 IST] [Fix pass, register update] [assistant]
Action: Register updated per the approved plan's item (h). L-B, L-G, L-I
marked resolved in-place (L-G noting the `risk_assessments.confidence_score`
DB column as the explicitly deferred remaining half; L-I noting the
calibration N+1 finding hit while building its test). D-28's caveat
marked resolved. D-13's row left completely unedited except one
appended cross-reference sentence to D-29 (its own "Production
approach" was eventually carried out there) — per instruction, not
rewriting the historical claim. New D-32 logged for the
`calibration.py` N+1 pattern (found, not fixed — out of scope for this
pass) and the `TestClient`/event-loop conflict (fixed, in the test).
L-H's entry updated with Fix 5's live count: 6 of 629 actions have
non-ASCII params, all this session's own NFC/NFD test artifacts, not
historical data — non-zero, so per instruction reported and not
migrated.
Result: docs-only change, no code touched, no test run needed beyond
the prior commits' own verification.
Evidence: `governance/plan/03-errors-and-fixes.md` diff.

### [2026-08-22 14:35 IST] [clean-v3 closeout, 3 items] [assistant]
Action: Lambda deploy confirmed by the user; independently re-verified
via `GET /v1/version` on both deployments (git_sha `f5b8575` on both) —
E-4, not taken on the user's word alone. Then, in order:
(1) Fix 5/L-H: re-ran the non-ASCII query live rather than accepting
the register's prior claim — `total actions: 633`, `non-ascii params
count: 6`, same 6 rows (`agent_id="fuzz3"`, `resource="customers/42"`,
`created_at` 2026-08-21 13:32-13:35 UTC), confirmed by fresh inspection
to be this session's own NFC/NFD test artifacts, zero genuine historical
data. L-H marked fully resolved (forward-only limitation moot in
practice).
(2) D-32: fixed the `calibration.py` N+1 (one named `calibration_report()`
in the instruction — no such function exists; the actual site is
`compute_calibration_by_action_type()`/the old `_historical_outcomes()`,
backing `GET /v1/calibration`, exactly matching the register's own D-32
description — flagged per E-3 rather than silently assumed, then
proceeded on that identification). Replaced with
`audit.calibration_outcomes(store)`: a single aggregate SQL query (CTE +
`UNION ALL`, `GROUP BY action_type`) on the DB-backed path, a plain
iteration on the in-memory test double. Snapshotted output before
touching code, asserted bit-identical after (dict equality), timing
119.97s -> 8.49s against the live 633-row table. New
`tests/test_db_store.py::test_calibration_report_issues_a_bounded_number_of_queries`
asserts query count (<=3) via a SQLAlchemy event listener, not timing.
Full suite: 183 passed, 0 skipped, 0 failed (189.26s, real `DATABASE_URL`
so DB-backed tests ran). `git diff --stat -- app/risk/ tests/test_routing.py`
empty throughout. Commit `059d3fd` — NOT deployed (read-only,
no-routing-impact change, verified locally against the live shared DB;
clean-v3 tags the already-deployed, already-parity-verified commit, not
this one).
(3) Final parity: 24 live calls (3 seeded scenarios x 4 runs x 2
deployments, sequential per D-30's lesson against shared-DB writes) —
tiers match on every scenario, floor_name stable across all 4 runs per
scenario/deployment. Fingerprint table (F1-F4, `readyz`, `/v1/version`,
`/v1/audit/verify`) PASS on both, `records_checked` identical (703) on
both. `uncertainty_score`/`llm_confidence_raw` (L-G) confirmed present
in all 24 responses on both deployments. NFC/NFD `params_hash` parity
(L-H) re-confirmed live on both deployments, matching the clean-v2
closeout's own hash exactly (content-derived, expected).
Result: L-H closed, D-32 fixed (locally verified, not deployed), parity
confirmed live on both deployments for every item this instruction
named, including its L-G/L-H addition.
Evidence: `governance/evidence/final-closeout-clean-v3.txt` (all raw
curl/query output); `governance/plan/03-errors-and-fixes.md` diff
(L-H final resolution, D-32 row updated to Fixed).

### [2026-08-23 10:20 IST] [Final resolution pass, Step 1] [assistant]
Action: Full suite green (183 passed, 0 skipped, 0 failed, 178.26s) run
before pushing. Pushed `059d3fd`/`27aa4d2` and tag `clean-v3` to
`origin/master`, triggering Railway's connected auto-deploy - confirmed
via `railway logs --build` (image digest `sha256:689d2a15...`,
healthcheck succeeded) and `GET /v1/calibration` timing (1.958s,
independent proof the D-32 fix is actually running, not just that a SHA
string changed). Built and pushed the Lambda image
(`GIT_SHA=27aa4d2`/`BUILD_TIME=2026-08-23T04:48:42Z` build-args, digest
`sha256:6d7675a2...`, manifest media type confirmed
`application/vnd.docker.distribution.manifest.v2+json`) but did NOT call
`update-function-code` - user deploys via console, per instruction.
User confirmed Lambda deploy and `/v1/version` match on both platforms;
independently re-verified via direct curl to both URLs before proceeding
(E-4).
Found D-33 while doing this (`GIT_SHA`/`BUILD_TIME` were static Railway
variables, not derived per-deploy - `/v1/version` kept reporting the OLD
commit despite a real, healthy, functionally-verified new deploy).
Checked whether Railway exposes a git-commit variable before assuming a
fix - `railway run env`, full runtime environment, grepped for every
`RAILWAY_*`/`GIT_*` key: none exists for this service (root-Dockerfile
build, not Nixpacks). Logged as D-33. Fixed the immediate instance via
`railway variables --set` (confirmed live: `/v1/version` now reports
`27aa4d2ab0d6e3e2910c490a54e5af7c6d1ff615`). Since no Railway-provided
variable exists to wire `/v1/version` to, the durable fix is process, not
code: new `infra/railway/deploy-railway.sh` scripts the required
`railway variables --set GIT_SHA=... BUILD_TIME=...` step, and
`CLAUDE.md`'s Known constraints section documents the requirement so the
next Railway deploy doesn't rediscover this the same way.
Result: both deployments verified on `27aa4d2` (raw `/v1/version` output
pasted in this session); D-33 logged with the "checked, doesn't exist"
finding stated explicitly, not guessed; process gap closed via a script
+ CLAUDE.md, not a code change (there was no code to change).
Evidence: this session's transcript (raw curl/`railway`/`aws ecr`
output); `governance/plan/03-errors-and-fixes.md` diff (new D-33 row).
