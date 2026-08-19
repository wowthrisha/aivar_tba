# Project Charter — PS-9.1 Graduated Autonomy Engine

## Scope

A governance service that scores every proposed AI-agent action for risk
and routes it to one of three tiers — autonomous, confirm, full review.
Every routing decision is written to an append-only audit log with a
plain-English explanation.

## The four never-cuts

1. **Risk weights** are fixed: reversibility 0.40, data scope 0.30,
   regulatory 0.20, confidence 0.10.
2. **Thresholds** are fixed at 0.30 and 0.65.
3. **Override floors** — which exist and what they trigger on — do not
   change without explicit approval.
4. **Fail-closed on LLM failure** — an LLM safety refusal or failure never
   defaults to autonomous; it fails closed toward full review.

See `CLAUDE.md` for the full frozen list and known constraints.

## Gate schedule

Deadline: 20 Aug 2026, 17:00 IST. Target submission: 13:00 IST.

| Gate | Milestone | Target |
|---|---|---|
| G0 | Scaffold complete (T-04) | 2026-08-19 |
| G1 | Criterion tests written (read-only after) | TBD |
| G2 | Risk scorer implemented, tests green | TBD |
| G3 | API + audit log wired end-to-end | TBD |
| G4 | Deployed, curl-verified against public URL | TBD |
| G5 | Final submission | 2026-08-20 13:00 IST |

Gate dates beyond G0 depend on the task list to be provided next
(`progress-log/01-implementation-plan.md`).
