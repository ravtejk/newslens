-- 0021_memory_follow_origin.sql — the origin-story bridge for the follow-
-- altitude picker (NL-17-M1b FIX LOOP 1, QA NO-GO 2026-07-18 FIX-1).
--
-- THE BUG THIS CLOSES: a HIGH/MED auto-commit stores the follow under the
-- RESOLVER's name ("Volkswagen"), not the headline the reader tapped
-- ("Volkswagen plans significant job cuts"). Recognition on the origin card
-- matched only story_title/headline ∈ active_topics, so after a reload the card
-- rendered RESTING — and a second tap ran a second PAID resolve and created a
-- DIVERGENT second active follow (QA: rows "…job cuts" + "Volkswagen" for one
-- story). This column records WHICH story a picker follow was born from, so the
-- render recognizes its altitude-renamed follow and a tap becomes the steady-
-- state expand (never a new resolve). No render-time LLM call — a column read.
--
--   origin_story   the reader-tapped story's canonical topic (story_title, else
--                  the headline) at follow-creation time — the STABLE identity
--                  the origin card is re-matched on across reload AND across a
--                  regenerate where the same story recurs (survives a headline
--                  drift while story_title holds). '' for a follow not born from
--                  a story card (a manually-added thread, an unmigrated row) —
--                  those keep name-only recognition, nothing fabricated.
--
-- Set ONCE, at creation, and never rewritten by a switch (the follow MOVES
-- rungs but keeps its birthplace). memory is the follow's home table (0019's
-- reasoning); this is one more attribute OF that follow, one-to-one.
--
-- APPEND-ONLY-SAFE / ADDITIVE (0010/0017/0018/0019 law). memory is MUTABLE
-- (0006 rebuilt it; add_thread UPDATEs it) — no RAISE(ABORT) append-only trigger
-- to trip. ALTER TABLE ADD COLUMN with a constant DEFAULT '' is an O(1)
-- metadata-only change (SQLite backfills no rows); existing threads read '' —
-- correct, they predate the origin bridge. Re-apply guarded by the runner's
-- schema_migrations ledger (ADD COLUMN IF NOT EXISTS is unavailable in SQLite).
-- Rollback = stop reading the column; nothing to undo.
--
-- CHECKPOINT: additive, applies on the principal's next server restart. Joins
-- the 0019/0020 checkpoint batch (SANCTION RIDES THE SHIP CHECKPOINT, 0018
-- pattern) — flagged loudly in the fix-loop report.

BEGIN;

ALTER TABLE memory ADD COLUMN origin_story TEXT NOT NULL DEFAULT '';

COMMIT;
