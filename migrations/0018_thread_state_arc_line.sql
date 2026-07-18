-- 0018_thread_state_arc_line.sql — the arc-line contract v1 (Content council
-- 2026-07-18, workspace/debates/2026-07-18--newslens--content.md). The deep
-- view's continuity line becomes a SEPARATELY-AUTHORED field of the state
-- rewrite instead of render-time-derived from the analyst's arc object — the
-- tense-splice defect (state-summary text reused as arc prose, principal's
-- 2026-07-17 served-version review item 2) dies by construction: prior state
-- and today's deltas are simultaneously in hand only in the memory pass, so the
-- arc line is a byproduct of exactly that comparison (Q3 ruling).
--
-- WHY A COLUMN ON thread_state, not a squat in diff_json (the crux; the 0017
-- house precedent — a distinct genre gets its own honest home, never an
-- overloaded existing structure):
--   1. arc_line is AUTHORED, CONTRACTED, VALIDATED prose (anchor date, then-leg
--      past tense, delta-leg named change, ≤35 words, banned lexicon, overlap
--      tripwire vs the state text). diff_json is the MECHANICAL sentence-set
--      diff (write law (c)); burying a first-class validated field inside it
--      would make the render read contracted prose out of a "diff" blob —
--      exactly the semantic drift 0017's header refused when it kept baselines
--      off thread_deltas.
--   2. The render stays DUMB (Q3: the deep view is a record surface). It reads
--      one column verbatim under the contract's render condition, never parses
--      a JSON structure to fish prose out.
--
-- LIFECYCLE — rides thread_state's existing versioned append-only, newest-wins
-- model (0010). A new state row per edition that moved the thread carries THIS
-- edition's arc line; the empty string '' is CONTRACT ABSENCE (render condition
-- §B: a day-one thread — no prior edition-cited entry — authors no arc line, and
-- neither does a validator-rejected line after its one informed retry). Absence
-- renders nothing; it is never filler and never a stale prior edition's line.
--
-- APPEND-ONLY-SAFE. ALTER TABLE ADD COLUMN is a schema change, not a row
-- UPDATE/DELETE, so thread_state's RAISE(ABORT) append-only trigger pair (0010)
-- never fires. The constant DEFAULT '' makes it an O(1) metadata-only change
-- (SQLite backfills no rows); existing state rows read '' — correct, they
-- predate the contract and carry no arc line. Additive only (0010/0017 law):
-- rollback = stop reading the column; nothing to undo.

BEGIN;

ALTER TABLE thread_state ADD COLUMN arc_line TEXT NOT NULL DEFAULT '';

COMMIT;
