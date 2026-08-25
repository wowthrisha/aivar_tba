# FEATURE B1 — empirical fragmentation-gap probe (live Railway)

Read-only. Against `https://aivartba-production.up.railway.app`.

## Round 1 — confounded (distinct fresh resource names)

5 sequential `POST /v1/actions/evaluate` calls, agent_id
`session-risk-probe`, each a single-record irreversible delete on a
distinct never-before-seen resource (`probe-resource-1` .. `probe-5`).
All 5 returned `tier: FULL_REVIEW`, `floor_name:
unrecoverable_mutation_requires_confirm`,
`floors_fired: [unrecoverable_mutation_requires_confirm,
novelty_unprecedented, low_confidence_on_mutation]` (calls 6/7 pulled
for full detail). Explanation text: `"... Escalated to FULL_REVIEW
(novel action: no close precedent in 34 prior actions)."`

**Confound identified**: this is `novelty_unprecedented` — an
EXISTING, unrelated escalation mechanism (`app/embeddings.py`) firing
because each resource name is itself unprecedented, independently of
any session accumulation. Matches the project's own documented D-27
lesson (fresh resource names in a verification probe trigger novelty
escalation and confound the result). Round 1 alone could not distinguish
"the engine is accumulating risk across calls" from "the engine is
independently flagging each call as novel."

## Round 2 — unconfounded (identical resource repeated)

5 sequential calls, agent_id `session-risk-probe2`, IDENTICAL request
every time (`resource: "customers"`, `params: {"resource_id": 999}`,
same reversibility/affected_records/regulatory):

| call | tier | composite | floor_name | floors_fired |
|---|---|---|---|---|
| 1 | FULL_REVIEW | 0.462 | unrecoverable_mutation_requires_confirm | [unrecoverable_mutation_requires_confirm, novelty_unprecedented] |
| 2 | FULL_REVIEW | 0.462 | unrecoverable_mutation_requires_confirm | (same) |
| 3 | FULL_REVIEW | 0.462 | unrecoverable_mutation_requires_confirm | (same) |
| 4 | FULL_REVIEW | 0.462 | unrecoverable_mutation_requires_confirm | (same) |
| 5 | FULL_REVIEW | 0.462 | unrecoverable_mutation_requires_confirm | (same) |

**Byte-identical tier, composite, and floors on every call.** `novelty_unprecedented`
still fires on every repeat (the candidate pool for precedent is
TERMINAL actions only — `executed`/`rejected`/`expired` — and none of
these bare `evaluate()` calls ever reach a terminal state, so the prior-count
denominator never grows from the probe itself). This is a clean control:
same input, five times, zero drift in either direction.

**Across all 12 live calls in this probe (rounds 1+2), `irreversible_bulk`
never once appears in `floors_fired`.** Only the per-action, single-record
floor (`unrecoverable_mutation_requires_confirm`) or the unrelated novelty
floor ever fire.

## Conclusion

**The fragmentation gap is real.** A sequence of single-record
irreversible deletes — 5 here, and by construction any number — never
triggers `irreversible_bulk` regardless of volume, because every action
is scored in total isolation from every other action by the same agent.
Round 2 additionally shows there is no accumulating *anything* (not
composite, not tier, not floors) across identical repeated calls against
the live system as it exists today (`clean-v4`/`hardening-v5`, pre-this-branch).

This empirically confirms B1's premise. Per B1's own instruction
("If the gap does NOT exist, say so and skip B2-B4"), B2-B4 proceed:
`GET /v1/sessions/{agent_id}` (read model) and the shadow-mode session
floor in `app/risk/session_floor.py`, gated by `SESSION_FLOOR_MODE`
(default `shadow`, never applied to a real tier).
