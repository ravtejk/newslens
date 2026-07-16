-- 0017_thread_baselines.sql — NL-77 the thread cold-start backgrounder
-- (principal charge 2026-07-17; Executive Brief workspace/briefs/
-- 2026-07-17--newslens--cold-start.md). The "entry-zero" baseline genre: when a
-- thread is newly followed (or first opened with an empty ledger) NewsLens
-- writes a one-shot "How we got here" backgrounder — synthesized from external
-- background the product never itself covered, so it can NEVER license a
-- repetition-word continuity claim (migration 0014's `external-synthesis`
-- class).
--
-- WHY ITS OWN TABLE, not a thread_deltas row (the crux; 0003/0012/0014 house
-- precedent):
--   1. thread_deltas.verdict is CHECK'd IN ('advances','reverses') (0010, "M1
--      gate F1: defense-in-depth"). A baseline is neither — it is the founding
--      floor, not an edition delta. Widening that CHECK means recreating the
--      trust-critical append-only ledger table (drop/rebuild past its
--      RAISE(ABORT) triggers) — exactly the carve-out 0014's header ruled
--      disqualifying.
--   2. A baseline has NO briefing edition. Its cite currency is "(baseline,
--      Jul 14)" — NOT an edition date. Stored as a thread_delta it would sort
--      into every edition-keyed read: the Today arc's "then" leg (violating the
--      anti-obligation invariant — day-one arcs must stay dead), the deep-view
--      "story so far" timeline, and any HSR numerator. A side table keeps
--      entry-zero OFF all of them STRUCTURALLY — the invariants hold by
--      construction, not by a filter that a future read site could forget.
--   3. A baseline is external-synthesis by definition; the grade is fixed on the
--      row (CHECK below), not looked up in thread_delta_provenance (which keys on
--      a delta_id this genre never mints). 0014's external-synthesis class still
--      applies to "any delta inheriting baseline diction" (a later milestone),
--      so the grade is not dead.
--
-- LIFECYCLE — versioned append-only, newest-wins (the thread_state model, not a
-- UNIQUE key): the §F explicit-action lane writes a 'pending' row the instant a
-- thread is followed / first opened (intent captured, $0, no fabrication); the
-- generator later writes a 'ready' row (backgrounder + a seeded standing state)
-- or a 'failed' row (refusal is honest — the gap stays recorded, never filled
-- with invented material). A retry after a failure is a NEW row. A UNIQUE key on
-- thread_id would forbid that retry; append-only + newest-wins is the house law
-- for a versioned record (0010 thread_state) and the trust-honest choice here.
--
-- state_seed — the day-one standing state ("where this stands") the generator
-- distills alongside the backgrounder. It lives on THIS row, not in thread_state,
-- for two reasons: (a) thread_state.state_text is validated against LEDGER dates
-- (validate_state hard-rejects a cite to no ledger entry), and a baseline has no
-- ledger — its only cite currency is "(baseline, <date>)"; (b) keeping the seed
-- here means the external-synthesis grade travels with it, and the render falls
-- back to it only until a real (record-established) thread_state exists.

BEGIN;

CREATE TABLE IF NOT EXISTS thread_baselines (
    id            INTEGER PRIMARY KEY,
    thread_id     INTEGER NOT NULL REFERENCES memory(id),
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

CREATE INDEX IF NOT EXISTS idx_thread_baselines_thread
    ON thread_baselines (thread_id, id);

-- Append-only, the ledger's law (0010/0004 precedent): a baseline row is a dated
-- fact; a correction or a retry is a NEW row (newest wins), never a rewrite.
CREATE TRIGGER IF NOT EXISTS trg_thread_baselines_no_update
BEFORE UPDATE ON thread_baselines
BEGIN
    SELECT RAISE(ABORT, 'thread_baselines is append-only (versioned; a new baseline is a new row, never an edit)');
END;

CREATE TRIGGER IF NOT EXISTS trg_thread_baselines_no_delete
BEFORE DELETE ON thread_baselines
BEGIN
    SELECT RAISE(ABORT, 'thread_baselines is append-only (a baseline is a dated fact, never deleted)');
END;

COMMIT;
