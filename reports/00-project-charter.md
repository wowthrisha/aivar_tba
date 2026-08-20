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
| G4 | Deployed, curl-verified against public URL | 2026-08-20 |
| G5 | Final submission | 2026-08-20 13:00 IST |

Gate dates beyond G0 depend on the task list to be provided next
(`progress-log/01-implementation-plan.md`).

## Live deployment (G4, T-14)

- **URL**: https://aivartba-production.up.railway.app
- **Platform**: Railway (ECS Express Mode successor product; App Runner
  is closed to new customers, per the known constraints)
- Curl-verified against all three routing tiers plus `/livez` and
  `/v1/audit` — see `progress-log/02-action-log.md` (T-14 entry) and
  `reports/evidence/T-14-curl.txt` for full raw evidence.
- CI: GitHub Actions runs the full test suite on every push —
  https://github.com/wowthrisha/aivar_tba/actions/runs/32303917004

## Submission tags

- **`submission-v1`** — pre-novelty-addons snapshot (commit `8551be1`),
  frozen as a rollback point before merging experimental work.
- **`submission-v2`** — current submission: `submission-v1` + the three
  novelty add-ons (Item 0 theoretical reframing, Feature A reviewer
  oversight metrics, Feature B precedent retrieval + novelty escalation),
  merged into `master` and redeployed to the same live URL above. Gate
  check (all three original criterion actions still route correctly,
  none escalated by the new novelty floor) and a deliberate novelty
  firing are both verified against this live deployment — see
  `progress-log/02-action-log.md` for the full record.
- Rollback path if needed: `git reset --hard submission-v1`.
