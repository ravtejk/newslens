-- 0027_thread_baselines_delete_cascade.sql — THE DELETE TRIANGLE, DISSOLVED.
-- (Principal ruling 2026-08-24, DECISIONS "[2026-08-24] THE SLATE RULED" item 3:
-- "Option A — recreate the baselines FK with ON DELETE CASCADE." Diagnosis:
-- workspace/products/newslens/research/2026-08-14--nl77r-build.md §STOP.)
--
-- WHAT WAS BROKEN. Three shipped rules could not all hold at once:
--   1. ADR-0010 §4 — a memory row is HARD-deleted. Delete is the product's only
--      irreversible verb, and it removes the row, not a flag.
--   2. 0017's `trg_thread_baselines_no_delete` — a baseline row is NEVER deleted
--      (append-only; a baseline is a dated fact).
--   3. 0017's FK — `thread_id NOT NULL REFERENCES memory(id)` with NO cascade,
--      and `db.connect()` sets `PRAGMA foreign_keys = ON` on every connection.
-- Consequence, live in his tree since 2026-07-16: any thread that ever received
-- a baseline row — a `pending` intent counts — became PERMANENTLY undeletable.
-- `memory.delete_thread` raised `sqlite3.IntegrityError: FOREIGN KEY constraint
-- failed` from inside its own `with con:` block; the tombstone rolled back with
-- it, so the verb failed loudly and left nothing behind. ADR-0013's own deferred
-- list predicted this ("thread renames/deletes land BEFORE baselines"); the junk
-- sweep never landed and this is the collision it named.
--
-- THE RULING'S SEMANTICS, stated plainly because it is a real loss: deleting a
-- thread now deletes its baseline rows with it. An entry-zero backgrounder is
-- the founding floor OF A THREAD; once the thread it founds is gone, the floor
-- is not a readable dated fact any more, it is an orphan pointing at nothing.
-- Rule 1 (the reader's irreversible verb) outranks rule 2 (the ledger's
-- append-only law) at exactly this one seam, and nowhere else.
--
-- ============================================================================
-- THE REEF, AND WHY THE FK CHANGE ALONE WOULD HAVE BEEN INERT
-- ============================================================================
-- In SQLite, a BEFORE DELETE trigger FIRES on rows removed by an ON DELETE
-- CASCADE action. Measured on this machine (sqlite 3.43.2 — the venv engine
-- that applies migrations and runs the suite; first probed on the homebrew
-- python's 3.53.3, identical behaviour, receipt corrected at gate) before writing a
-- line of this file, both with `PRAGMA recursive_triggers` off (the default)
-- and on: a parent DELETE against a cascading child whose BEFORE DELETE trigger
-- RAISE(ABORT)s comes back `IntegrityError`, cascade or not. So 0017's
-- never-delete trigger would have caught the very cascade this migration exists
-- to create, and the ruling would have shipped as a no-op.
--
-- Therefore the trigger is recreated, GUARDED — and the guard is the whole
-- design decision in this file:
--
--     WHEN EXISTS (SELECT 1 FROM memory WHERE id = OLD.thread_id)
--
-- By the time a cascade fires the child's BEFORE DELETE trigger, SQLite has
-- ALREADY removed the parent row (measured with a probe trigger that counted
-- `memory` rows from inside the cascade: 0). A DIRECT `DELETE FROM
-- thread_baselines` is the opposite case — the thread it belongs to is still
-- standing. So "is the thread still there?" is not a heuristic about intent, it
-- is the structural signature of which of the two deletes is happening, and the
-- trigger reads it rather than trusting a caller.
--
-- WHAT THIS DOES NOT DO, so no one reads it wider than it is: a direct delete of
-- a baseline row belonging to a LIVE thread still ABORTs, exactly as before —
-- the append-only law is untouched everywhere except in the arms of the thread
-- delete the principal ruled on. This is deliberately NARROWER than the rejected
-- Option B (relax the trigger for the delete path), which would have made any
-- baseline row deletable by any caller willing to say it was deleting a thread.
-- The UPDATE trigger is recreated verbatim, unguarded: a baseline is still never
-- edited, and nothing about the ruling touches rewriting.
--
-- RESIDUAL, NAMED: a caller that opens the database WITHOUT `PRAGMA
-- foreign_keys = ON` can delete a `memory` row with no cascade, orphaning its
-- baselines — and the guard would then permit those orphans to be deleted
-- directly, since their thread really is gone. Every src path goes through
-- `db.connect()`, which sets the pragma (that is why it exists), so this is
-- reachable only from a hand-opened sqlite3 shell. Two truths, split at gate
-- (QA F-4): orphan CREATION is the FK's pre-existing exposure, unchanged here;
-- orphan DELETABILITY is new with this guard (0017's unconditional trigger
-- refused it on every connection) — intended, as the completion of the ruled
-- cascade: an orphan is debris of a lawless delete, and 0027 itself refuses to
-- carry orphans forward.
--
-- ============================================================================
-- WHY A REBUILD
-- ============================================================================
-- SQLite cannot add ON DELETE CASCADE to an existing foreign key in place —
-- there is no ALTER TABLE form for it. The table is rebuilt: 0006's memory_v2
-- and 0011's consumption_events_v2 are the house precedent, and this is the
-- smaller case of the two (18 rows in his tree at authoring time; nothing FK-
-- references thread_baselines, so no other table's references need repointing).
-- Column definitions, defaults, CHECKs and the index are copied from 0017
-- VERBATIM. The FK's `ON DELETE CASCADE` is the ONLY substantive change.
--
-- Triggers are dropped FIRST, before the table (0026's unwind note: "a DROP
-- TABLE with its own triggers still attached is the shape that has bitten this
-- house before"), then recreated on the rebuilt table after the rename.
--
-- A BASELINE ROW WHOSE THREAD IS ALREADY MISSING WOULD MAKE THIS MIGRATION FAIL
-- LOUDLY, and that is correct: FKs are ON during the copy, so an orphan cannot
-- be silently carried across or silently dropped. Measured against his real
-- database read-only at authoring time: 0 orphans, 18 baseline rows.
--
-- RE-APPLY BEHAVIOR (honest, matching 0006/0011's disclosure): one BEGIN/COMMIT,
-- so a mid-script failure rolls back whole. In the documented lost-record crash
-- gap (script ran, `schema_migrations` insert did not), a second run finds
-- `thread_baselines` already cascading, copies its rows into a fresh empty _v2
-- with identical ids, and renames back — same rows, same ids, same schema. The
-- re-apply is a no-op in content.
--
-- UNWIND, ON RECORD AND IN ORDER. This one is NOT free — read the cost first.
-- Reverting restores the triangle: every baselined thread becomes undeletable
-- again. Run it only to get back to pre-0027 bytes:
--
--     DROP TRIGGER IF EXISTS trg_thread_baselines_no_delete;
--     DROP TRIGGER IF EXISTS trg_thread_baselines_no_update;
--     CREATE TABLE thread_baselines_v0 AS SELECT * FROM thread_baselines;
--     -- (then recreate 0017's table DDL verbatim, copy the rows back, and
--     --  recreate 0017's two UNGUARDED triggers + the index)
--     DELETE FROM schema_migrations WHERE filename =
--       '0027_thread_baselines_delete_cascade.sql';
--
-- SCOPE BOUND — MEASURED, AND WIDER THAN THE RECORD SAID. thread_baselines is
-- ONE of FIVE tables whose FK points at `memory(id)` with no cascade. The other
-- four block `delete_thread` identically, and every one was proven to raise the
-- same IntegrityError on untouched 7d878a7 bytes:
--     thread_deltas   (0010)   thread_state    (0010)
--     watch_items     (0013)   thread_closures (0015)
-- This migration fixes the baselines leg ONLY — the leg the principal ruled on.
-- Census of his 37 threads at authoring time (read-only): 12 have no child rows
-- and delete today; 15 are blocked by a baseline ALONE and become deletable with
-- this migration; 10 are blocked by one of the other four (3 of them ALSO hold a
-- baseline and stay blocked after this). The remaining four legs are a separate
-- ruling, not a silent widening taken here.
--
-- CHECKPOINT: schema migration — the principal's call. It reaches his database
-- through normal product operation (`newslens migrate` / the next server start,
-- the 0018/0024/0026 pattern), NEVER by an agent running it against data/. The
-- insurance-backup convention (data/newslens.db.pre-0027, the 0011-0013
-- precedent) is his to take before the first apply.

BEGIN;

-- 1. The old triggers come off first — the guarded delete trigger replaces one
--    of them below, and a DROP TABLE with its triggers still attached is the
--    shape 0026's unwind note warns about.
DROP TRIGGER IF EXISTS trg_thread_baselines_no_update;
DROP TRIGGER IF EXISTS trg_thread_baselines_no_delete;

-- 2. 0017's table, VERBATIM, with one change: ON DELETE CASCADE on the FK.
CREATE TABLE IF NOT EXISTS thread_baselines_v2 (
    id            INTEGER PRIMARY KEY,
    thread_id     INTEGER NOT NULL REFERENCES memory(id) ON DELETE CASCADE,
    as_of_date    TEXT NOT NULL,                 -- the baseline cite date; currency is "(baseline, <as_of>)"
    status        TEXT NOT NULL
                  CHECK (status IN ('pending', 'ready', 'failed')),
    backgrounder  TEXT NOT NULL DEFAULT '',      -- the "How we got here" prose (empty until ready)
    state_seed    TEXT NOT NULL DEFAULT '',      -- the seeded day-one standing state (empty until ready)
    cites_json    TEXT NOT NULL DEFAULT '[]',    -- external research keys the synthesis leaned on
    provenance    TEXT NOT NULL DEFAULT 'external-synthesis'
                  CHECK (provenance = 'external-synthesis'),  -- the genre's fixed 0014 class
    reason        TEXT NOT NULL DEFAULT '',      -- basis / failure note (the honest failed state)
    model         TEXT NOT NULL DEFAULT '',
    cost_usd      REAL NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- 3. Every row, ids preserved — `latest_baseline`'s newest-wins ordering is
--    `ORDER BY id DESC`, so a renumbering here would silently reorder history.
INSERT INTO thread_baselines_v2
    (id, thread_id, as_of_date, status, backgrounder, state_seed, cites_json,
     provenance, reason, model, cost_usd, created_at)
SELECT
     id, thread_id, as_of_date, status, backgrounder, state_seed, cites_json,
     provenance, reason, model, cost_usd, created_at
FROM thread_baselines;

DROP TABLE thread_baselines;
ALTER TABLE thread_baselines_v2 RENAME TO thread_baselines;

CREATE INDEX IF NOT EXISTS idx_thread_baselines_thread
    ON thread_baselines (thread_id, id);

-- 4. Append-only, unchanged: a correction or a retry is a NEW row, never a
--    rewrite. 0017's text verbatim.
CREATE TRIGGER IF NOT EXISTS trg_thread_baselines_no_update
BEFORE UPDATE ON thread_baselines
BEGIN
    SELECT RAISE(ABORT, 'thread_baselines is append-only (versioned; a new baseline is a new row, never an edit)');
END;

-- 5. THE GUARDED DELETE TRIGGER. Still forbids a direct delete — the thread is
--    still standing when one of those fires. Silent for a cascade — the thread
--    row is already gone by then, which is the only way this WHEN goes false.
CREATE TRIGGER IF NOT EXISTS trg_thread_baselines_no_delete
BEFORE DELETE ON thread_baselines
WHEN EXISTS (SELECT 1 FROM memory WHERE id = OLD.thread_id)
BEGIN
    SELECT RAISE(ABORT, 'thread_baselines is append-only (a baseline is a dated fact; it is removed only with the thread it founds, by ON DELETE CASCADE)');
END;

COMMIT;
