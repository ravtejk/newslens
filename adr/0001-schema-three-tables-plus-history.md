# ADR 0001 — Ship the full core schema (3 tables + append-only history) in milestone 1

**Date:** 2026-07-02 · **Status:** accepted · **Milestone:** 1

## Context

Spec §B (engineering debate 2026-07-02) fixes three tables — `source_items`,
`briefings`, `memory` — as the separation-of-concerns floor (Ada's position,
upheld against Pax's two-table compromise in adjudication). The spec places the
`briefings_history` append-only log in milestone 7 (scheduling/idempotency),
but two binding ENGINEERING.md rules pull it earlier:

- *"Scheduled jobs are idempotent... safe to re-run for the same day without
  duplicates"* — the overwrite-preserving re-run behavior binds from the first
  moment a briefing row can be overwritten, which is **milestone 5** (first
  generation), not milestone 7.
- Schema migrations are **escalation triggers**. One reviewed migration at the
  scaffold stage beats a second escalation mid-build.

## Decision

Migration `0001_initial_schema.sql` ships all four tables now:

1. The three spec §B tables, fields exactly as spec'd, plus CHECK constraints:
   `source_type IN ('rss','sonar')` (adding a source kind, e.g. a GNews
   fallback, is deliberately a visible migration + principal checkpoint, never
   a silent string); `json_valid()` on all JSON columns (DB-level backstop for
   the structured-output validation rule — malformed LLM output cannot land
   even if app validation regresses); `status IN ('active','stale','dismissed')`.
2. `briefings_history`, **append-only enforced by triggers** (UPDATE/DELETE
   abort). Onna's diff-ability requirement — "day 7's briefing still sitting
   there untouched" — held structurally, not by convention.
3. `UNIQUE (url, date(fetched_at))` on `source_items`: one row per
   (url, fetch-day). Same-day re-ingestion upserts (idempotency); a *later-day*
   re-fetch gets a new row, so an old briefing's story slots keep referencing
   the exact snapshot they were built from (faithfulness by construction).
4. `UNIQUE (briefings.date)`: the idempotent re-run anchor — regenerate a day
   in place, prior version archived to history first (pipeline code, M5).

Timestamps: UTC ISO-8601 TEXT. `briefings.date` is the principal-local
calendar day, supplied by pipeline code — deliberately not defaulted in SQL,
so a run started at 00:05 or a backfill can't silently mislabel its day.
`token_cost` is a JSON column (per-step tokens + estimated USD), because the
spec's pipeline logs cost *per step*.

Staleness ("14 days", "N=15 active rows in context") stays in pipeline code
(M4), not in schema — those are tunable policy numbers, not structure.

## Alternatives rejected

- **Two tables, memory as JSON state** (Pax's opening) — already rejected in
  the spec's own adjudication; can't prune memory or answer "what is NewsLens
  tracking" cleanly.
- **`briefings_history` at M7 as spec'd** — leaves M5/M6 briefings
  overwritable with no preserved prior version, and costs a second
  schema-migration escalation.
- **`consumption_events` table now** — genuinely M7 scope (falsifier
  instrumentation); no rule pulls it earlier. Not built ahead.
- **WAL journal mode now** — concurrency (cron writer + CLI reader) starts at
  M7; flipping journal mode is a trivial one-statement change then. Default
  rollback journal until a milestone needs otherwise.

## Consequences

- Milestones 2–5 code against a stable, reviewed schema; no mid-build DDL.
- Migration convention set for all future files: idempotent (`IF NOT EXISTS`),
  own `BEGIN/COMMIT`, because the runner's applied-record insert is not atomic
  with the script.
- QA can verify append-only behavior directly (UPDATE/DELETE on
  `briefings_history` must abort).
