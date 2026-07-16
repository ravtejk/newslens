-- 0015_thread_closures.sql — the closure register (substrate ruling C, the
-- collect-now list; principal-approved migration class, 2026-07-16).
--
-- A thread can reach a natural end (the story is over). `memory close <topic>
-- --reason` is the EXPLICIT-ACTION lane (taxonomy §F: explicit actions only,
-- nothing inferred from reading behavior) that records it. SCHEMA + the write
-- verb ship in this batch; the closure FEATURE (rendering the dated line on the
-- thread page, halting further deltas) is a vision-item backlog row that reads
-- this table later — nothing else writes it yet.
--
-- Append-only, the house ledger law (0010/0013/0014 precedent): a closure is a
-- dated fact, corrected only by a new fact, never rewritten or deleted. The
-- write verb refuses a SECOND closure on a thread (one closure per thread — a
-- re-close is a data smell, named not silently duplicated); the append-only
-- triggers are the structural backstop.
--
-- thread_id    — the thread this closure ends.
-- reason       — the operator's note (why it closed).
-- edition_date — the edition context the closure is dated to (renders as a
--                dated line on the thread page when the feature ships).
-- closed_at    — wall-clock of the explicit action.

BEGIN;

CREATE TABLE IF NOT EXISTS thread_closures (
    id            INTEGER PRIMARY KEY,
    thread_id     INTEGER NOT NULL REFERENCES memory(id),
    reason        TEXT NOT NULL DEFAULT '',
    edition_date  TEXT NOT NULL,
    closed_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- SCHEMA-QA-1: structurally ONE closure per thread — the append-only
-- triggers forbid DELETE, so a raced duplicate would be PERMANENT. The
-- close_thread SELECT stays as the friendly named refusal; this is the
-- backstop.
CREATE UNIQUE INDEX IF NOT EXISTS uq_thread_closures_one_per_thread
    ON thread_closures (thread_id);

CREATE TRIGGER IF NOT EXISTS trg_thread_closures_no_update
BEFORE UPDATE ON thread_closures
BEGIN
    SELECT RAISE(ABORT, 'thread_closures is append-only (a closure is a dated fact, never rewritten)');
END;

CREATE TRIGGER IF NOT EXISTS trg_thread_closures_no_delete
BEFORE DELETE ON thread_closures
BEGIN
    SELECT RAISE(ABORT, 'thread_closures is append-only (a closure is a dated fact, never deleted)');
END;

COMMIT;
