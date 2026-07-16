-- 0014_thread_delta_provenance.sql — NL-69 the provenance bound (Executive
-- Brief decision A; engineering council 2026-07-17, Ruling 1). The deadline-
-- class fix for HSR baseline §5.1(2): the 07-14 backfill wrote "reinstated a
-- naval blockade" into thread_deltas 5–6 (what_happened) as SOURCE-ECHO diction
-- the record could not support. Strict before_date protected the antecedent
-- validator only while those rows were same-day; from 2026-07-17 they PREDATE
-- every new edition, so the date bound alone now VALIDATES the poison — a
-- future "reinstated (per our Jul 14 coverage)" reads as a true hit. This
-- migration installs the mark that keeps a source-echo row from ever licensing
-- the word it echoed, no matter how old it gets.
--
-- Shape (Ruling 1, and the 0012/0003 house precedent): an APPEND-ONLY SIDE
-- TABLE, not a `provenance` column on thread_deltas. Three load-bearing
-- reasons, identical to 0012's:
--   1. 0003 set the precedent ("Why a table and not a column: ALTER TABLE ADD
--      COLUMN cannot ...") — a side table is how a fact is added to an existing
--      row without a rebuild.
--   2. thread_deltas is the trust-critical append-only LEDGER (0010's
--      RAISE(ABORT) UPDATE/DELETE triggers). The poisoned rows 5–6 could never
--      TAKE a column value without a carve-out in those triggers (disqualifying,
--      per the council). A side table leaves 0010's triggers UNTOUCHED.
--   3. A mark is a fact that happened once — INSERT-only, fully idempotent and
--      re-apply-safe (CREATE ... IF NOT EXISTS), no ADD COLUMN hazard.
--
-- ABSENCE OF A ROW = 'record-established' (the read layer's default — the
-- honest grade for an organically-written delta; see memory_core.
-- effective_provenance). The four CHECK'd values are surfaced on every ledger
-- dict as `provenance` (LEFT JOIN in ledger_for_thread), but only ONE read site
-- acts on it: has_predating_antecedent drops the NON-LICENSING grades
-- ('source-echo', 'external-synthesis') from licensing. Everything else (state
-- regen, timelines, writer context) still sees the row — the mark bounds
-- LICENSING, not the row's existence (Ruling 1: 0014 stops new validation harm;
-- NL-73 supersession cures the poisoned TEXT, separately).
--
-- delta_id    — the delta this mark grades (UNIQUE: a delta is marked at most
--               once; a re-mark is refused in the write path AND by the PK).
-- provenance  — source-echo      : merely echoed edition-day source diction;
--                                   NEVER licenses a repetition-word antecedent.
--               record-established: an organically-written, record-grade delta
--                                   (also the absence-of-row default).
--               reader-explicit   : grounded in a reader's own explicit input
--                                   (reserved for the voice/reader-provenance
--                                   domain; not written by this milestone).
--               external-synthesis: content derived from externally-researched
--                                   material the product never covered (the
--                                   thread-baseline "entry-zero" genre, and any
--                                   delta inheriting baseline diction). Like
--                                   source-echo, it does NOT license a bare
--                                   repetition word — baseline-derived diction
--                                   is licensed only by dated-anchored prose (a
--                                   writer-side rule that lands later; this
--                                   validator just refuses the bare form).
-- reason      — the human/basis note (the supervised marking command fills it).
-- marked_at   — when the mark was written.

BEGIN;

CREATE TABLE IF NOT EXISTS thread_delta_provenance (
    delta_id    INTEGER PRIMARY KEY REFERENCES thread_deltas(id),
    provenance  TEXT NOT NULL
                CHECK (provenance IN ('source-echo', 'record-established',
                                      'reader-explicit', 'external-synthesis')),
    reason      TEXT NOT NULL DEFAULT '',
    marked_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_provenance_provenance
    ON thread_delta_provenance (provenance);

-- Append-only, the same law as the ledger it annotates: a provenance mark is a
-- dated fact, corrected only by a new fact (a supersession), never rewritten or
-- deleted.
CREATE TRIGGER IF NOT EXISTS trg_provenance_no_update
BEFORE UPDATE ON thread_delta_provenance
BEGIN
    SELECT RAISE(ABORT, 'thread_delta_provenance is append-only (a provenance mark is a dated fact, never rewritten)');
END;

CREATE TRIGGER IF NOT EXISTS trg_provenance_no_delete
BEFORE DELETE ON thread_delta_provenance
BEGIN
    SELECT RAISE(ABORT, 'thread_delta_provenance is append-only (a provenance mark is a dated fact, never deleted)');
END;

COMMIT;
