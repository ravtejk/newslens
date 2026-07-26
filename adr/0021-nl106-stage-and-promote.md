# ADR-0021 — NL-106: stage-and-promote (a regenerate never destroys the readable edition)

**Status:** accepted (gate-approved with fixes; QA re-check pending) · 2026-07-26
**Milestone:** NL-106, data-safety class. Origin: NL-103 remediation QA finding
QA-1 (`workspace/products/newslens/research/2026-07-26--nl103-remediation-qa.md`).
**Schema:** migration `0023_briefings_pending.sql` (additive) — principal checkpoint.
**Amends:** ADR-0001 (archive timing — see *Relationship to ADR-0001* below).

## Context

The pipeline wrote today's `briefings` row in **two separately committed stages**:

- `ranking.persist()` — stage `rank`, **early**. On a re-rank it archived the row
  to `briefings_history` and then explicitly set `narrative_text = NULL,
  script_text = NULL, audio_file_path = NULL` on the LIVE row.
- `generate.persist_generation()` — stage `persist`, **last**. The UPDATE that
  writes the new body.

Everything between them — analysis → narrative → editor → script → audio — is the
long tail of a run whose logged times run 14–37 minutes. **For that entire window a
row existed for today while no readable edition did**, and any failure in the tail
left it that way permanently: an edition still listed in the Archive, still opening,
rendering blank. Proven live end to end in the NL-103 remediation QA (probe
`test_G6_realistic_failed_regenerate_end_to_end`).

The shipped three-state failure panel (f4510fa) made the panel *honest* about this
state. It did not stop the state from happening. That was the interim measure; this
is the fix.

The prior mechanism was not careless — it enforced a real law from the M3 gate
review (NOTES item 11): **a narrative written for OLD slots must never live against
NEW slots.** It enforced it by destruction, which is the strictest possible reading
and the most expensive one for the reader.

## Decision

Split the write in two and make the visible swap atomic.

1. **Stage at rank.** `ranking.persist()` gains a third arm, chosen on the same
   `narrative_text` truthiness predicate `persist_generation` already used (so the
   two sites cannot disagree about what "has a body" means):
   - **readable row** → write the new selection to `briefings_pending`
     (`date` PK, `story_slots`, `corroboration_labels`, `token_cost`, `created_at`),
     archive nothing, touch the live row not at all;
   - **bodyless row** → archive + overwrite + NULL, byte-for-byte as before;
   - **no row** → INSERT, byte-for-byte as before.

2. **Mid-run readers prefer the staged selection.** Exactly two sites read
   `story_slots` mid-run — `generate.load_briefing_inputs` and the analysis stage —
   and both consume the staged payload when one exists. The preference is
   opt-in (`prefer_pending`, default False) and exactly one caller opts in: the
   in-flight run, and only when it can promote (`not report.sample`). Backfill and
   the prompt/cost batteries keep reading the edition of record.

3. **Promote at publish.** `persist_generation` does the whole swap in one
   transaction: archive the displaced edition → install staged slots +
   corroboration + the new body → delete the staged row. The promoted ledger is
   built on the **staged** `token_cost` (this edition's rank step), never the live
   row's, which belongs to the edition being archived in the same transaction
   (NL-95: `usd` is real money).

4. **Failure writes nothing.** A dead run leaves its staged row behind; the next
   re-rank overwrites it. There is no rollback path because nothing was destroyed.

5. **Fail closed on both sides of the staging read/write.** A staging WRITE that
   cannot land raises rather than falling back to the destructive path. A staging
   READ degrades to the live row **only** on `no such table` (a pre-0023 database,
   which is positive evidence that nothing was staged) and propagates every other
   `OperationalError` — because that one read decides which write path the promote
   takes, and swallowing an error there silently produces the exact mixture this
   table exists to prevent.

The invariant, stated once: **at every instant the live `briefings` row for a date
is either the coherent pre-run edition or the coherent new one. Never a mixture,
never bodyless because a run is in flight.**

Item 11's law is preserved and strengthened — the old narrative now lives only
against its OWN old slots, until both are replaced together.

## Alternatives rejected

- **"Just don't NULL."** Forbidden by the M3 gate ruling it would violate: old
  narrative against new slots on the live row is the mixture, not the fix.
- **A foreign key from `briefings_pending.date` to `briefings.date`.** Rejected as
  a second way for a promote to fail. The staged row's lifetime is the RUN's, not
  the row's.
- **`ON CONFLICT … DO UPDATE` for the re-stage.** `INSERT OR REPLACE` is exactly
  equivalent here (`date` is the whole primary key, nothing references the table,
  no triggers) and carries no SQLite version floor beyond the one the schema
  already sets.
- **An expiry on staged rows** (gate ruling R1). Silently discarding a staged row
  would introduce a new silent data decision to fix a silent data decision. The row
  is tiny, same-date-scoped (PK = date; cross-date leakage proven unreachable), and
  overwritten by the next re-rank. Instead, consuming a staged row a run did not
  create is **disclosed** in the run's warnings, with the moment it was ranked.
- **Extending staging to first runs.** A failed first run leaves an honest state
  (nothing readable existed, the panel says "empty", truthfully) and self-healing
  on the next run. Out of scope; the no-row arm stays byte-for-byte.
- **Making the server aware of staged rows.** The opposite of the point. The
  reader's world is the live row and only the live row; `server.py` and `webui.py`
  are provably blind to the table, pinned by a suite test.

## Relationship to ADR-0001

ADR-0001 established archive-before-overwrite: *"regenerate a day in place, prior
version archived to history first."* That contract is **unchanged in substance and
amended in timing**:

- **Then:** the history row was written at *rank* time, and carried the old slots
  with the old body because rank was also the moment the body was destroyed.
- **Now:** the history row is written at *publish* time, inside the promote
  transaction, and carries the same old slots with the same old body.

Both properties ADR-0001 cares about are preserved and pinned by the battery:
**exactly one history row per replaced edition**, and **old slots paired WITH old
body in that row**. The append-only triggers on `briefings_history` are untouched.

What changed for an observer: on a *successful* regenerate the history row appears
~30 minutes later than it used to. On a *failed* one it never appears at all — which
is correct, because no edition was replaced.

## Consequences

- A failed or crashed full regenerate is now recoverable by definition: the reader's
  edition is still there, byte-identical, and the failure panel's "The saved edition
  is intact." is true rather than a lie.
- A `--no-refresh` re-run after a failed regenerate **completes** the interrupted
  run (ratified, R1) rather than re-publishing the old edition — the designed
  recovery path, now disclosed in the run's warnings.
- `briefings_pending` may hold a row indefinitely after a dead run. That is by
  design; it is invisible to the reader and overwritten by the next re-rank.
- The item-11 NULL clauses survive only for hand-edited state (a stray script or
  audio path on a bodyless row). Kept as defence in depth, pinned, and documented
  here so nobody later reads them as live protection.
- **Not closed by this ADR:** `analysis_briefs` is keyed `(date, slot)` with no
  story identity, so briefs written mid-run can render beside a story they are not
  about. Pre-existing (f4510fa's hard-crash path already produced a three-way
  mixture); this change removes the blindfold rather than the defect. Tracked as
  T1, four teeth, MEDIUM.
