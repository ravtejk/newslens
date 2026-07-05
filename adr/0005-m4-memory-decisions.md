# ADR 0005 — Milestone-4 memory + continuity decisions

**Date:** 2026-07-04 · **Status:** accepted · **Milestone:** 4
**Contract:** spec §B memory mechanics + §E-M4; taxonomy contract §§B/D/F;
principal confirmation 2026-07-04 ("live threads with lifespans").

## Decisions

1. **memory.md sync semantics: FILE WINS, loudly.** The file is read as
   source of truth at generation time (spec §B, literal). Line present/absent
   and section placement define status; note text after " — " is the note.
   A DELETED line means DISMISSAL (a principal action with an audit row kept
   — stated in the file's own header, never a surprise). After every sync the
   file is regenerated in canonical form. An unparseable or unreadable file
   is a LOUD failure (MemorySyncError -> RankingError, BUG-6-logged):
   silently ignoring hand edits is the one unforgivable failure for a
   transparency surface. The file is left untouched for the principal to fix
   (or delete, to regenerate from the DB).
2. **Seeds are code-bootstrap, not migration data.** Migration 0005 adds only
   the case-insensitive UNIQUE(topic) index the sync's line<->row matching
   requires (escalation-flagged). The taxonomy's 14 threads seed via
   `memory.seed_if_first_run` ONLY when the table is empty AND memory.md is
   absent — so a migration replay can never resurrect dismissed threads, and
   QA's schema tests keep an empty table. Note: the dispatch said "14 threads
   + the 5 borderline twins"; the taxonomy's own tally (§C) counts the 5
   borderline items INSIDE the 14 — 14 rows seeded, the 5 twins carrying a
   rename-when-acute note. Reported, not silently reconciled.
3. **Staleness clock = max(created_at, last-referenced briefing's
   generated_at), 14 days.** Principal note edits deliberately do NOT reset
   the clock — referenced-ness is about briefings citing the thread, not
   about editing it. Transitions are surfaced in memory.md's Stale section
   AND the run report. Revival is a principal action (file move / `memory
   add` on a stale topic); stale threads never match silently because only
   ACTIVE threads enter the prompt.
4. **Context cap:** active threads, ORDER BY last_referenced_briefing_id
   (never-referenced last, newest first), LIMIT 15 — one implementation in
   `memory.active_context`; ranking delegates.
5. **Continuity for M5 = derived, not duplicated.**
   `memory.prior_briefing_context` builds the 2-3-sentence-per-slot summary
   deterministically from the prior briefing's story_slots at read time —
   storing a second copy of slot data would be a sync bug waiting to happen.
   Bounded by construction (5 slots, 1500 chars). M5 consumes it directly.
6. **Tags table deferred again** (NOTES item 8): memory sync is this
   milestone's risk budget; a second file<->table sync surface (interests)
   in the same change doubles it for zero M5 dependency. sources.yaml stays
   the tags source of truth; `tag add/drop/list` land when real use demands
   ergonomics the file can't give.
7. **Item 11 fixed here** (persist was open anyway): re-rank NULLs
   narrative_text/script_text/audio_file_path on slot overwrite — a
   narrative describing OLD slots must never survive onto new ones; the
   history archive keeps the old state.
8. **personal_score stays max-not-sum** (recorded per item 12): a cluster
   matching three topic tags is not three times more personal than one —
   max() measures "how strongly does this touch what you follow" without
   letting tag-count game the score; the followed boost is the one additive
   term, and the 1.0 cap bounds everything.
9. **CLI verbs are file-equivalent:** every memory verb syncs file->DB first
   (hand edits are never clobbered unseen), applies, and resyncs — the file
   and table can't drift. `memory add` on a dismissed/stale topic revives it
   (same as the file move).

## Alternatives rejected

- Seeding via migration INSERTs (replay could resurrect dismissals; breaks
  QA's empty-table schema fixtures).
- Silent-skip on memory.md parse errors (transparency surface; loud or
  nothing).
- Deletion = "remove row" (loses audit; spec keeps dismissed rows excluded
  but present).
- Storing prior-briefing summaries in a new column/table (duplication; the
  slots already hold the data).

## Superseded in part — 2026-07-04, lifecycle v2

§§1/3 status vocabulary and the Stale/Dismissed file sections are superseded
by ADR-0006 (principal amendment: three states, auto-revival, Active/Inactive
file). File-wins sync semantics, seeding rules, context cap, continuity
derivation, and the tags deferral all stand unchanged.
