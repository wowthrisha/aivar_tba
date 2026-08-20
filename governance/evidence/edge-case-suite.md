# Edge-case demo suite — read-only against LIVE (Railway)

Run 2026-08-20, 15:49-15:57 IST (Group A/B/C) and 15:57-16:11 IST (final
re-verification). Target: `https://aivartba-production.up.railway.app`.
All calls used `agent_id: "edge-test-agent"`. No reviewer decisions were
made on action_type read/update/delete (A7's decision call returned 403
before any state transition, so it did not shift calibration counts).

## Group A — instant validation/auth (no LLM call)

| case | request | expected | ACTUAL status | ACTUAL response | match? |
|---|---|---|---|---|---|
| A1 | `POST /v1/actions/evaluate`, `affected_records: -5` | 422 (D-13 fix) | 422 | `{"detail":[{"type":"greater_than_equal","loc":["body","affected_records"],"msg":"Input should be greater than or equal to 0","input":-5,"ctx":{"ge":0}}]}` | yes |
| A2 | `affected_records: "many"` | 422 type error | 422 | `{"detail":[{"type":"int_parsing","loc":["body","affected_records"],"msg":"Input should be a valid integer, unable to parse string as an integer","input":"many"}]}` | yes |
| A3 | missing `action_type` | 422 field required | 422 | `{"detail":[{"type":"missing","loc":["body","action_type"],"msg":"Field required",...}]}` | yes |
| A4 | missing `resource` | 422 | 422 | `{"detail":[{"type":"missing","loc":["body","resource"],"msg":"Field required",...}]}` | yes |
| A5 | `POST /v1/actions/{id}/confirm` with wrong `params_hash` (seeded action `76be674d-a052-4261-b28e-6c27c2a96f79`) | 409 hash mismatch | 409 | `{"detail":"params_hash mismatch"}` | yes |
| A6 | `POST /v1/actions/{id}/execute`, action still in `confirm` state, no approval | 409 not executable | 409 | `{"detail":"action is in state confirm, not executable"}` | yes |
| A7 | `POST /v1/review-queue/{id}/decision`, `reviewer_id == agent_id` ("edge-test-agent") | 403 must differ | 403 | `{"detail":"reviewer_id must differ from the proposing agent_id"}` | yes |
| A8 | `GET /v1/actions/<random-uuid>` | 404 | 404 | `{"detail":"action not found"}` | yes |

**8/8 match. No 500s. No leaked stack traces or internal paths anywhere in Group A.**

Bootstrap call used for A5-A7 (one real evaluate, `resource: "edge/seed-a5a6a7"`, `reversibility: read`, `affected_records: 1`, `regulatory: none`): landed in `confirm` state via the `novelty_unprecedented` floor (composite 0.078), giving a real `action_id`/`params_hash` for A5-A7's precondition checks.

## Group B — full evaluate (real LLM calls, run in parallel)

| case | request | expected | ACTUAL status | composite | tier | floor_name | notes / match? |
|---|---|---|---|---|---|---|---|
| B1 | `action_type: "frobnicate_widgets"` (unknown), update_with_snapshot, 1 record, none | structural completeness low -> two-signal confidence low -> low_confidence floor and/or novelty escalation | 201 | 0.320 | FULL_REVIEW | `novelty_unprecedented` | explanation shows BOTH mechanisms: weighted-then-floor text cites "LLM confidence 0.00 below 0.5" (low_confidence would have given CONFIRM), then novelty escalated further to FULL_REVIEW. floor_name only records the final/outermost floor. **Matches expectation** (both fired; novelty determined the final tier). |
| B2 | `affected_records: 0`, read | data_scope at floor, AUTONOMOUS unless another floor | 201 | 0.016 | CONFIRM | `novelty_unprecedented` | data_scope_score confirmed 0.0 (floor). Tier is CONFIRM, not AUTONOMOUS, because the novelty floor fired - consistent with the expectation's own "unless another floor" caveat. **Match.** |
| B3 | delete/irreversible, `affected_records: 1000000000` | data_scope saturates at 1.0, irreversible_bulk if delete | 201 | 0.702 | FULL_REVIEW | `irreversible_bulk` | data_scope_score = 1.0 exactly. **Exact match.** |
| B4 | `regulatory: phi_sox`, update, 1 record | regulated-mutation floor fires -> FULL_REVIEW | 201 | 0.562 | FULL_REVIEW | `regulated_mutation` | data_scope_score only 0.2 (1 record) yet still FULL_REVIEW - proves regulation outranks volume. **Exact match.** |
| B5 | delete/irreversible, `affected_records: 100` | irreversible_bulk FIRES (boundary >=) | 201 | 0.582 | FULL_REVIEW | `irreversible_bulk` | **Exact match.** |
| B6 | delete/irreversible, `affected_records: 99` | irreversible_bulk does NOT fire; record what routes it instead + composite | 201 | 0.522 | FULL_REVIEW | `novelty_unprecedented` (weighted-floor text shows `unrecoverable_mutation_requires_confirm` would have given CONFIRM) | **Confound, flagged**: irreversible_bulk correctly did NOT fire (as expected), but the novel `resource` string ("edge/b6") also tripped the (real, working-as-designed) novelty floor, escalating CONFIRM->FULL_REVIEW anyway - same final tier as B5 but via a different mechanism. This masks a clean side-by-side boundary demonstration; not a defect, a test-design confound (novel resources trigger novelty escalation regardless of the boundary being tested). |

**B5 vs B6 boundary, side by side:**
| | affected_records | composite | floor_name | tier |
|---|---|---|---|---|
| B5 | 100 | 0.582 | `irreversible_bulk` | FULL_REVIEW |
| B6 | 99 | 0.522 | `novelty_unprecedented` (would have been `unrecoverable_mutation_requires_confirm` / CONFIRM without the novelty confound) | FULL_REVIEW |

No 500s in Group B.

## Group C — system endpoints

| case | request | expected | ACTUAL | match? |
|---|---|---|---|---|
| C1 | `GET /livez` | 200, no I/O | 200, `{"status":"ok"}` | yes |
| C2 | `GET /readyz` | db and llm status | 200, `{"status":"ok","checks":{"llm":"ok","db":"ok"}}` | yes |
| C3 | `GET /v1/audit/verify` (run after A+B) | valid:true, records_checked count | 200, `{"valid":true,"records_checked":434,"first_invalid_id":null}` | yes - **chain survived the validation-error storm** |
| C4 | `GET /v1/oversight/reviewers` | `decisions_total` alongside every rate | 200, all 3 reviewers show `decisions_total`, `approval_rate`, `median_decision_latency`, `p90_decision_latency`, `reversal_rate`, `automation_bias_flag` | yes |
| C5 | `GET /v1/calibration` | per action_type, `mode: shadow` | 200, `{"mode":"shadow","action_types":[{"action_type":"update",...},{"action_type":"delete",...}]}` | yes |

(C4/C5 each needed one retry after an initial curl timeout - both succeeded on retry with no error.)

## FINAL — mandatory re-verification (ran `./demo.sh` once each, per the original instruction)

| scenario | expected tier/composite/floor | **ACTUAL** | match? |
|---|---|---|---|
| read-only (`customers/42`) | AUTONOMOUS, 0.102, floor `None` | **CONFIRM**, composite **0.117**, floor **`low_confidence`** (LLM confidence 0.43 < 0.5) | **NO — tier changed** |
| single update (`orders/9981`) | CONFIRM, 0.382, floor `unrecoverable_mutation_requires_confirm` | CONFIRM, composite **0.440**, floor **`low_confidence`** (LLM confidence 0.00 < 0.5) | **NO — composite + floor changed, tier happens to match** |
| bulk delete (`customers`, 5000) | FULL_REVIEW, 0.642, floor `irreversible_bulk` | FULL_REVIEW, composite **0.737**, floor `irreversible_bulk` | **NO — composite changed, tier + floor match** |

Raw FACTORS for all three:
```
read-only:      reversibility 0.00  data_scope 0.20  regulatory 0.00  uncertainty 0.57  composite 0.117
single update:  reversibility 0.70  data_scope 0.20  regulatory 0.00  uncertainty 1.00  composite 0.440
bulk delete:    reversibility 1.00  data_scope 0.80  regulatory 0.00  uncertainty 0.97  composite 0.737
```

### Diagnosis

In every case only the `uncertainty` factor (`1 - llm_confidence`, weight 0.10) moved from its previously-recorded value - `reversibility`/`data_scope`/`regulatory` all match exactly what the fixed scenario inputs produce. Back-solving the original read-only number (0.102): `uncertainty` was previously ≈0.42 (`llm_confidence` ≈0.58, just above the 0.5 floor). This run it was 0.57 (`llm_confidence` 0.43) - just enough to cross the floor and flip AUTONOMOUS -> CONFIRM.

This is **not attributed to the edge-case suite disturbing shared state**: the three canonical actions use resources (`customers/42`, `orders/9981`, `customers` bulk-delete) entirely distinct from every `edge/*` resource exercised in Groups A/B, and none of their floors this run were `novelty_unprecedented` or calibration-related (the only floors involved were `low_confidence` and `irreversible_bulk`, both driven purely by the live LLM confidence call and the fixed threshold/weight inputs). The most plausible explanation is that the live, non-deterministic OpenAI self-reported confidence for these specific canonical prompts sits close enough to the 0.5 floor boundary that ordinary call-to-call variance can flip a read-only, zero-blast-radius action across a floor meant for consequential mutations.

**Root cause identified and fixed** (see `governance/plan/03-errors-and-fixes.md`, D-14): the `low_confidence` floor fired on ALL actions regardless of reversibility, including fully-reversible reads. Fixed by gating it on `is_mutation` (`app/risk/floors.py`) - see the code change and STEP 3 re-verification (`governance/evidence/final-demo-capture.json`) in this same commit range for the deterministic re-verification across the full confidence range.
