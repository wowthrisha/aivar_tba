# Implementation Plan — Task Board

Status reflects only what is documented with evidence in
`progress-log/02-action-log.md` for that exact task ID — not inferred from
incidental state. Rows with no such log entry are `Not started`.

## BLOCK 0 — Foundation

| ID | Task | Definition of Done (evidence that closes it) | Box | Status |
|---|---|---|---|---|
| T-01 | Accounts + billing alarm | screenshot of USD 5 alarm ACTIVE | 30m | Not started |
| T-02 | Neon DB | both strings in .env, labelled POOLED and DIRECT | 20m | Not started |
| T-03 | OpenAI key + pinned model | pasted API response; model string in CLAUDE.md | 10m | Done |
| T-04 | Scaffold | git log + tree output pasted | 40m | Done |
| T-05 | Hello-world deploy | curl <url>/livez returns 200 | 20m | Done |

>>> **GATE G0**: a live HTTPS URL exists

## BLOCK 1 — Risk engine

| ID | Task | Definition of Done (evidence that closes it) | Box | Status |
|---|---|---|---|---|
| T-06 | scorer.py, four dimensions | pytest tests/test_scoring.py green | 2h | Done |
| T-06a | Counterfactual explanation | test asserting "would have been X if Y" | 15m | Done |
| T-07 | floors.py | test asserting escalate-only: no input lowers a tier | 45m | Done |
| T-07a | Boundary escalation band | test at threshold +/- 0.04 escalating | 10m | Done |
| T-08 | FOUR CRITERION TESTS | pytest tests/test_routing.py: 4 passed | 45m | Done |
| T-09 | llm.py adapter | test with mocked refusal proving terminal, not retried | 45m | Done |
| T-09a | Two-signal confidence | test: high self-report + low completeness = low | 20m | Done |

>>> **GATE G1**: four criterion tests green, NO framework, NO database —
    **PASS** (see reports/gates/G1-report.md, reports/blocks/block-1-report.md)

## BLOCK 2 — Service

| ID | Task | Definition of Done (evidence that closes it) | Box | Status |
|---|---|---|---|---|
| T-10 | API + state machine | curl each endpoint, responses pasted | 1h | Done |
| T-11 | Persistence | alembic current on DIRECT; app on POOLED; \dt shows 4 tables | 1h | Done |
| T-12 | Security S-1,2,3,5,6 | one test per control, five named in output | 1h | Done |
| T-13 | Adversarial review (fresh session) | findings pasted with my decision on each | 30m | Not started |

>>> **GATE G2**: full suite green locally, state machine proven by curl

## BLOCK 3 — Deploy and polish

| ID | Task | Definition of Done (evidence that closes it) | Box | Status |
|---|---|---|---|---|
| T-14 | Railway deploy + CI | curl live URL exercising all three tiers; green Actions | 1h | Not started |
| T-15 | AWS deploy — HARD 2h BOX | curl an AWS HTTPS URL | 2h | Not started |
| T-16 | Observability | JSON log line with request_id; forced error returns clean JSON | 45m | Not started |
| T-17 | CLI | pasted session showing risk table + confirm prompt | 45m | Not started |
| T-18 | README + traceability table | table rendering on GitHub, 4 rows populated | 45m | Not started |
| T-18a | DMAIC + versioning + fuzzy-rejection sections | present in README | 15m | Not started |

>>> **GATE G3** (Wed 21:00): SCOPE FREEZE. Live URL + green suite + README

## BLOCK 4 — Evidence and submission

| ID | Task | Definition of Done (evidence that closes it) | Box | Status |
|---|---|---|---|---|
| T-19 | Concurrency proof | 50 requests, 0 failures, p95, matching audit rows | 45m | Not started |
| T-20 | Record video | file exists, 5-7 min. CODE FREEZE at 09:00 | 2h | Not started |
| T-21 | Upload + verify sharing | link opened in an INCOGNITO window | 45m | Not started |
| T-22 | Final polish | repo public, every README link clicked | 45m | Not started |
| T-23 | Submit + retrospective | Form confirmation; retrospective appended | 45m | Not started |

>>> **GATE G4**: submitted. **GATE G5**: buffer closes, 4 hours spare

## STRETCH — only if G3 passes early, in this order

| ID | Task | Box | Status |
|---|---|---|---|
| T-B1 | Precedent retrieval (protect this first) | 90m | Not started |
| T-B2 | Reviewer bias metrics | 45m | Not started |
| T-B3 | Shadow mode flag | 20m | Not started |
| T-B4 | Adaptive calibration (stated bonus) | 60m | Not started |

## NEVER CUT

the four criterion tests · one live deployment · the human-readable audit
breakdown · the video with verified sharing

## CUT ORDER under pressure

stretch items, then CLI, then concurrency proof, then AWS deploy (Railway
already satisfies deployed-not-localhost).
