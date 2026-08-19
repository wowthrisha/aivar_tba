# PS-9.1 — Graduated Autonomy Engine

Governance service: scores every proposed AI-agent action for risk, routes
it to autonomous / confirm / full-review, and writes an append-only,
plain-English-explained audit log for every decision.

Deadline: 20 Aug 2026, 17:00 IST. Target submission 13:00.

## Operating contract

- **E-1** Never claim, always show. Paste raw command/curl output, not summaries.
- **E-2** Never speculate about unread code. Cite `file:line` for behavior claims.
- **E-3** Never invent an API/parameter. Check docs (subagent) or say "I need to check."
- **E-4** Proof of deployment = curl against the public URL, not a green build log.
- **E-5** "I don't know" is valid. Guessing is not.
- **E-6** No silent scope changes. If a task can't be done as specified, stop and say so.
- **E-7** Report partial completion honestly (e.g. "3 of 4 tests pass, X fails with Y").
- **L-1** Plan mode before any multi-file change. Propose, then wait.
- **L-2** Tests before implementation. Never edit a criterion test to pass.
- **L-3** Run the verification command after every change; show output.
- **L-4** One task in progress at a time (WIP limit 1).
- **L-5** Two-attempt rule: a fix failing twice → stop and report, no third try.
- **L-6** Append to the action log before committing.
- **L-7** Use a subagent for documentation reading; return a summary only.
- **L-8** Never touch the FROZEN LIST without asking first.
- **L-9** Never commit or print secrets.
- **L-10** State the task ID in every response.

**Stop and ask if:** a change touches the frozen list; a test needs editing
to pass; a second consecutive fix attempt has failed; a dependency behaves
differently than documented; a task can't be completed as written; a deploy
has rolled back twice; anything not on the plan is about to be committed.

## FROZEN LIST — do not change without explicit approval

- Risk weights: reversibility **0.40**, data scope **0.30**, regulatory **0.20**, confidence **0.10**
- Thresholds: **0.30** and **0.65**
- Which override floors exist and what they trigger on
- Fail-closed direction on LLM failure
- The four criterion tests, once written — READ-ONLY

## Known constraints

- AWS App Runner is CLOSED to new customers since 30 Apr 2026. Use ECS
  Express Mode or Lambda + Function URL instead.
- Platform health-check timeout ≈2s. `/livez` does NO I/O. Deep checks go
  in `/readyz`. Pointing the platform at `/readyz` rolls the deploy back.
- Postgres has TWO connection strings: app uses **POOLED**, Alembic uses
  **DIRECT**. Mixing them up causes "relation does not exist".
- asyncpg through a transaction-mode pooler needs `statement_cache_size=0`.
- OpenAI structured outputs: `strict=true`, every field in `required`,
  `additionalProperties=false`, model string **pinned**, not aliased.
- An LLM safety refusal is TERMINAL — never retry it. Fail closed.
- Build container images with `--platform=linux/amd64`.

## Commands

```
pytest -q                        # run tests
uvicorn app.main:app --reload    # run locally (once app exists)
alembic upgrade head              # run migrations (uses DATABASE_URL_DIRECT)
```

## Env vars

| Var | Purpose |
|---|---|
| `DATABASE_URL` | App runtime DB connection — POOLED (transaction mode) |
| `DATABASE_URL_DIRECT` | Alembic migrations only — DIRECT connection |
| `OPENAI_API_KEY` | LLM calls for risk assessment |
| `OPENAI_MODEL` | Pinned model string, not an alias. Live-verified 2026-08-19 (T-03): `gpt-5.6-luna` — confirmed via `GET /v1/models/{model}` (200) and a minimal `/v1/chat/completions` call (200, `finish_reason: stop`) |
| `ENV` | Deployment environment (dev/staging/prod) |
| `LOG_LEVEL` | Logging verbosity |
