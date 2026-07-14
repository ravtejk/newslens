-- 0010_thread_memory.sql — NL-63 M1: the memory core (the moat build)
--
-- Two DB-only, per-thread records. NEITHER enters memory.md's two-way sync
-- surface (engineering ruling 2026-07-10: state/ledger are DB-only; memory.md
-- stays the thread-name/note surface it is). Transparency comes from RENDERING
-- these in the UI, never from hand-editable files.
--
-- thread_deltas — the append-only delta LEDGER ("how we got here"). One entry
-- per thread per edition that MOVED the thread (advances|reverses only;
-- merely-matches writes nothing — Rhys's delta-gate kills theater at the root).
-- Each entry is a two-clause SIGNIFICANCE delta (Uma's product rule): what
-- happened + what it changed about the story. External cites (S/R/C keys +
-- P#) are stored ON the delta so history stays anchored outside our own prose
-- (Rook's self-reference loop mitigation). No backfill from model memory,
-- ever (Sten's law; the no-backfill refusal is the whole trust case).
--
-- thread_state — the standing STATE ("where this stands"), VERSIONED and
-- append-only (Rook: never UPDATE-in-place; a state that silently rewrites
-- its own past is the forensic anti-pattern the rest of this codebase
-- refuses). Newest row per thread wins; a failed rewrite writes NO row and the
-- prior state renders stale-but-honest (Content write law (d)). Each row
-- carries its diff vs the prior state (write law (c), diff-logged).
--
-- Append-only is enforced STRUCTURALLY with the RAISE(ABORT) trigger pair per
-- the 0004/ranking_runs + 0009/analysis_briefs precedent (BUG-5 class) — not
-- by convention. Rollback = stop reading the tables; nothing rebuilds, nothing
-- to undo (Onna: additive migration only).

BEGIN;

CREATE TABLE IF NOT EXISTS thread_deltas (
    id            INTEGER PRIMARY KEY,
    thread_id     INTEGER NOT NULL REFERENCES memory(id),
    briefing_id   INTEGER REFERENCES briefings(id),
    brief_id      INTEGER REFERENCES analysis_briefs(id),
    edition_date  TEXT NOT NULL,                 -- the dated cite (YYYY-MM-DD)
    slot          INTEGER,                       -- writing slot (M1 gate F2; NULL on seeds)
    verdict       TEXT NOT NULL
                  CHECK (verdict IN ('advances','reverses')),  -- M1 gate F1: defense-in-depth
    what_happened TEXT NOT NULL,                 -- clause 1: the event
    significance  TEXT NOT NULL,                 -- clause 2: what it changed about the story
    cites_json    TEXT NOT NULL DEFAULT '[]',    -- external S/R/C keys + P# carried by the arc
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_thread_deltas_thread
    ON thread_deltas (thread_id, edition_date);

CREATE TRIGGER IF NOT EXISTS trg_thread_deltas_no_update
BEFORE UPDATE ON thread_deltas
BEGIN
    SELECT RAISE(ABORT, 'thread_deltas is append-only (the ledger; corrections are new dated entries, never rewrites)');
END;

CREATE TRIGGER IF NOT EXISTS trg_thread_deltas_no_delete
BEFORE DELETE ON thread_deltas
BEGIN
    SELECT RAISE(ABORT, 'thread_deltas is append-only (the ledger; corrections are new dated entries, never rewrites)');
END;

CREATE TABLE IF NOT EXISTS thread_state (
    id            INTEGER PRIMARY KEY,
    thread_id     INTEGER NOT NULL REFERENCES memory(id),
    briefing_id   INTEGER REFERENCES briefings(id),
    as_of_date    TEXT NOT NULL,                 -- the edition that produced this state (YYYY-MM-DD)
    state_text    TEXT NOT NULL,
    cites_json    TEXT NOT NULL DEFAULT '[]',    -- the dated-edition keys the state's sentences carry
    diff_json     TEXT NOT NULL DEFAULT '{}',    -- diff vs the prior state (write law: diff-logged)
    model         TEXT NOT NULL DEFAULT '',
    cost_usd      REAL NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_thread_state_thread
    ON thread_state (thread_id, id);

CREATE TRIGGER IF NOT EXISTS trg_thread_state_no_update
BEFORE UPDATE ON thread_state
BEGIN
    SELECT RAISE(ABORT, 'thread_state is append-only (versioned; a rewrite adds a row, never edits one — anti-photocopier)');
END;

CREATE TRIGGER IF NOT EXISTS trg_thread_state_no_delete
BEFORE DELETE ON thread_state
BEGIN
    SELECT RAISE(ABORT, 'thread_state is append-only (versioned; a rewrite adds a row, never edits one — anti-photocopier)');
END;

COMMIT;
