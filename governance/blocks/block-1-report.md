# Block 1 Report — 2026-08-19

Risk engine. All seven tasks done, Gate G1 passed (see
`governance/gates/G1-report.md`).

## Tasks

| ID | Status | Evidence file | Box | Actual |
|---|---|---|---|---|
| T-06 | Done | governance/evidence/T-06-pytest.txt | 2h | ~3m (commit-to-commit) |
| T-06a | Done | governance/evidence/T-06a-pytest.txt | 15m | ~2m |
| T-07 | Done | governance/evidence/T-07-pytest.txt | 45m | ~2m |
| T-07a | Done | governance/evidence/T-07a-pytest.txt | 10m | ~1m |
| T-08 | Done | governance/evidence/T-08-pytest.txt | 45m | ~2m |
| T-09 | Done | governance/evidence/T-09-pytest.txt | 45m | ~5m |
| T-09a | Done | governance/evidence/T-09a-pytest.txt | 20m | ~2m |

"Actual" is commit-timestamp spacing (git log), not human-equivalent
effort — AI-paced sequential execution, not a claim the boxes are wrong
for a human engineer.

## DoD verification

| ID | Clause | Result | Evidence |
|---|---|---|---|
| T-06 | `pytest tests/test_scoring.py` green | PASS | T-06-pytest.txt: `24 passed` |
| T-06a | test asserting "would have been X if Y" | PASS | T-06a-pytest.txt: `26 passed`; sample string in action log: `0.72 -> FULL_REVIEW. Would have been CONFIRM if reversibility were update_without_snapshot instead of irreversible.` |
| T-07 | test asserting escalate-only: no input lowers a tier | PASS | T-07-pytest.txt: `35 passed`, incl. 1296-combination sweep |
| T-07a | test at threshold +/- 0.04 escalating | PASS | T-07a-pytest.txt: `47 passed`, 12 boundary/monotonicity tests |
| T-08 | `pytest tests/test_routing.py`: 4 passed | PASS | T-08-pytest.txt + G1-report.md: all 4 named PASSED, no FastAPI/DB |
| T-09 | test with mocked refusal proving terminal, not retried | PASS | T-09-pytest.txt: `56 passed`; `test_refusal_is_terminal_and_not_retried` asserts `parse.assert_awaited_once()` |
| T-09a | test: high self-report + low completeness = low | PASS | T-09a-pytest.txt: `62 passed`; `test_high_self_report_low_completeness_yields_low_confidence` (0.95 self-report -> 0.0 combined) |

## Deviations

1. **T-06a** needed a composite-to-tier function that no task ID owned.
   Introduced `app/risk/tiers.py` using the boundary semantics you
   approved before Block 1 started (>= escalates at both frozen
   thresholds), since T-07's floors.py needed the same thing
   (`final_tier = max(weighted_tier, floor_tier)`).
2. **T-07a** has no task-specific prompt in the Prompt Pack (confirmed and
   flagged before execution began). Used only your approved interpretation
   and the task-board DoD; no prompt was invented.
3. **T-08** needed a scorer+floors integration layer, also unowned by any
   task ID. Introduced `app/risk/router.py` (`route_action`).
4. **T-09**: found `openai` was unpinned in `requirements.txt` and had
   resolved to different versions locally (2.49.0, what was verified
   against) vs. on Railway's T-05 deploy (3.3.0). Pinned to `2.49.0` so
   the SDK behavior actually verified (refusal field, parsed field,
   strict/additionalProperties auto-set, exception hierarchy) is what
   ships, rather than an unverified newer surface.
5. **T-09a**: deliberately did NOT modify T-09's `OpenAIConfidenceProvider`
   to apply the two-signal minimum internally — its committed tests use
   param fixtures that would fail a real structural-completeness check,
   which would have broken already-green T-09 tests. Built a separate,
   additive `app/risk/confidence.py` instead.

## Defects

| ID | Symptom | Root cause | Fix | Status |
|---|---|---|---|---|
| D-01 | `test_out_of_range_value_is_rejected` failed with `UnboundLocalError` | Python deletes an `except ... as name` binding when the except block exits | Captured the exception into a variable declared before the try/except | Fixed |

## Cut / deferred

- `router.route_action`'s explanation, when a floor fires, states the
  floor's own reason but does not compute a floor-specific "would have
  been X if Y" counterfactual (e.g. "if affected_records < 100"). T-08's
  literal assertions don't require it. Production approach: extend each
  floor's reason with its own avoidance condition once a concrete need
  appears (e.g. the README worked example).
- T-06a's counterfactual sweep excludes the `confidence` dimension
  (continuous, no discrete band, unlike reversibility/data_scope/
  regulatory). Documented in code.
- T-09a's `ACTION_CATALOGUE` is deliberately minimal (5 action types:
  read, update, delete, send, pay). Production approach: extend as real
  action types are registered in later tasks.

## RAG: **GREEN**

All seven tasks done with verified, non-empty evidence files; Gate G1
passed; full suite 62/62 with no regressions introduced at any step;
frozen list (weights, thresholds, floor conditions, fail-closed direction)
untouched throughout.

## Next

T-10 (not started — Block 2 not begun, per instruction).
