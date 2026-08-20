# governance/

The process record for PS-9.1: what was planned, what was done, what
broke, and the raw artifacts proving each claim.

- **`plan/`** — the task board, the append-only action log, and the
  defect register. See [`plan/01-implementation-plan.md`](plan/01-implementation-plan.md),
  [`plan/02-action-log.md`](plan/02-action-log.md),
  [`plan/03-errors-and-fixes.md`](plan/03-errors-and-fixes.md).
- **`charter.md`** — scope, the frozen list, and the gate schedule.
- **`gates/`** — one report per gate (G0–G5), each checking the gate's
  own pass condition against pasted evidence.
- **`blocks/`** — one report per block of the task board, summarizing
  every task closed in that block.
- **`evidence/`** — the raw command/curl/pytest output every DONE claim
  in `plan/` points back to. `evidence/archive/` holds superseded
  captures kept for narrative continuity, not current proof.
- **`video-script.md`** — the submission video script.

## Method

Work runs as a **Kanban board with WIP limit 1** — one task in progress
at a time, tracked in `plan/01-implementation-plan.md`. Every task has a
**Definition of Done** stated as a piece of evidence to produce, not a
description of finished work — a task is DONE only once that evidence
exists and is pasted into `plan/02-action-log.md` or a file under
`evidence/`. A green build log alone is never treated as proof.

Progress gates through **six checkpoints, G0–G5**, each with its own
pass condition in `charter.md` and its own report in `gates/`. A gate
does not pass on narrative — it passes when every piece of evidence it
requires is present and re-checked, not just cited from memory.

`plan/02-action-log.md` is append-only: entries are never edited or
deleted, only corrected by a later entry. `plan/03-errors-and-fixes.md`
tracks defects (`D-nn`) and explicitly deferred scope (`LEFT OUT`), both
kept honest by the same evidence discipline — a defect is only marked
Fixed once re-verified, not once a fix is written.
