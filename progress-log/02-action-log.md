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
