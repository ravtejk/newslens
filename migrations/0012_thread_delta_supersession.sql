-- 0012_thread_delta_supersession.sql — NL-75: machine-readable supersession
-- (Rook's gate, engineering council 2026-07-16; principal checkpoint C(ii),
-- APPROVED "moat-strategy rulings" 2026-07-16).
--
-- Problem (Rook): migration 0010's trigger says "corrections are new dated
-- entries, never rewrites," but there is no machine-readable link from a wrong
-- delta to the entry that corrects it — so a factually wrong delta re-enters
-- EVERY future state regeneration forever. This milestone wires the READ side
-- (a superseded delta is excluded from state regeneration and rendered
-- struck/annotated in timelines); the full repair rung — detecting a wrong
-- delta, writing the correction, and regenerating affected states — stays
-- NL-73.
--
-- Shape decision (implementer, flagged for the reviewer): the supersession
-- link is an APPEND-ONLY SIDE TABLE, not an in-place `superseded_by` column on
-- thread_deltas. Rationale, three reasons, each load-bearing:
--   1. 0003 set the precedent explicitly ("Why a table and not a column: ALTER
--      TABLE ADD COLUMN cannot ...") — a side table is the house pattern for
--      adding a fact to an existing row without a rebuild.
--   2. thread_deltas is the trust-critical append-only LEDGER. An in-place
--      column forces either a rebuild (Rook: "a rebuild re-opens every
--      write-law invariant") or a carve-out in its RAISE(ABORT) UPDATE trigger.
--      A side table leaves 0010's append-only triggers UNTOUCHED and verified.
--   3. A side table is INSERT-only (a supersession is a fact that happened once)
--      and thus fully idempotent and re-apply-safe (CREATE ... IF NOT EXISTS),
--      with no ADD COLUMN duplicate-column hazard.
-- The read layer surfaces the value as `superseded_by` on each delta dict, so
-- the read-side contract ("machine-readable; excluded from state regen; struck
-- in timelines") is met exactly.
--
-- delta_id      — the delta that was superseded (UNIQUE: a delta is superseded
--                 at most once; the newest correction wins if the repair rung
--                 ever chains, enforced in the write path, NL-73).
-- superseded_by — the delta that supersedes it (the later, corrected entry).

BEGIN;

CREATE TABLE IF NOT EXISTS thread_delta_supersessions (
    delta_id      INTEGER PRIMARY KEY REFERENCES thread_deltas(id),
    superseded_by INTEGER NOT NULL REFERENCES thread_deltas(id),
    reason        TEXT NOT NULL DEFAULT '',   -- why (the correction's basis) — NL-73 fills it
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (delta_id <> superseded_by)          -- a delta cannot supersede itself
);

CREATE INDEX IF NOT EXISTS idx_supersession_superseded_by
    ON thread_delta_supersessions (superseded_by);

-- Append-only, same law as the ledger it annotates: a supersession is a dated
-- fact, corrected only by a new fact, never rewritten or deleted.
CREATE TRIGGER IF NOT EXISTS trg_supersession_no_update
BEFORE UPDATE ON thread_delta_supersessions
BEGIN
    SELECT RAISE(ABORT, 'thread_delta_supersessions is append-only (a supersession is a dated fact, never rewritten)');
END;

CREATE TRIGGER IF NOT EXISTS trg_supersession_no_delete
BEFORE DELETE ON thread_delta_supersessions
BEGIN
    SELECT RAISE(ABORT, 'thread_delta_supersessions is append-only (a supersession is a dated fact, never deleted)');
END;

COMMIT;
