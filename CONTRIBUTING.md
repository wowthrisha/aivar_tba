# Contributing

## Running tests

```bash
pip install -r requirements.txt
pytest -q
```

No live services are required — `tests/` uses fakes/in-memory doubles
for the LLM provider, embedding provider, and database. A handful of
`tests/test_db_store.py` tests do hit a real Postgres instance and are
function-scoped (see `progress-log/03-errors-and-fixes.md`, D-08).

## Definition of Done

Per task, per `CLAUDE.md`'s operating contract:

- Tests pass before the task is marked done; a green build log alone is
  never treated as proof — a pasted command/curl artifact goes in
  `progress-log/02-action-log.md` and/or `reports/evidence/`.
- The four criterion tests in `tests/test_routing.py` are READ-ONLY —
  never edited to make a change pass.
- The FROZEN list in `CLAUDE.md` (risk weights, thresholds, floor
  triggers, fail-closed direction) is never changed without explicit
  approval.
- One task in progress at a time (WIP limit 1).
- A second consecutive failed fix attempt on the same problem stops and
  reports rather than trying a third approach.

## Where things go

- `progress-log/` — task board, append-only action log, defect register.
- `reports/` — gate reports, block reports, raw evidence.
- `app/risk/` — the pure scoring/floor/tier logic; no I/O.
- `app/main.py` — the FastAPI handlers wiring everything together.
