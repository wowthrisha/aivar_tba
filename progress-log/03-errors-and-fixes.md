# Defect Register

| ID | Symptom | Root cause | Fix | Status |
|---|---|---|---|---|
| D-01 | `tests/test_llm.py::test_out_of_range_value_is_rejected` failed with `UnboundLocalError` | Python deletes an `except ... as name` binding when the except block exits, so `real_validation_error` was referenced after being auto-deleted | Captured the exception into a variable declared before the try/except instead | Fixed |
| D-02 | Every real `/v1/actions/evaluate` call showed `llm_degraded: true` in the audit log during T-10's live curl verification; confidence always fell back to 0.0, spuriously tripping the T-07 `low_confidence` floor on low-risk actions | `app/llm.py`'s `.parse()` call passed `temperature=0` per T-09's literal spec. The live pinned model (`gpt-5.6-luna`) rejects that value: `Error code: 400 - "Unsupported value: 'temperature' does not support 0 with this model. Only the default (1) value is supported."` T-09's mocked tests never caught this since they mock the client and never exercise the real `temperature` argument. Present since T-09's original commit — not introduced by T-10. | Removed the `temperature=0` argument (approved by product owner) so the SDK uses the model's required default. Root cause and fix confirmed via a real live call: `confidence: 0.82, degraded: False, reason: None`. | Fixed |

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
