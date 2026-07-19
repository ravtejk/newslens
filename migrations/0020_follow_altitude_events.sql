-- 0020_follow_altitude_events.sql — Axel's medium-confidence instrument
-- (NL-17-M1b build-contract; design round 2026-07-18, adjudication + preserved
-- dissent). Append-only event log; ONE operator-facing count, no reader surface.
--
-- THE INSTRUMENT (verbatim from the ruling): "count medium-confidence auto-
-- commits corrected within a day (a correction = the reader changing that
-- follow's altitude within 24h)." The pre-registered FLIP (≥1-in-5 live →
-- medium goes picker-first) is a HUMAN decision later; this table's only job is
-- to make the count observable. It is NOT a reader surface and NOT §F read-
-- logging — a follow tap is an EXPLICIT signal (a lawful write), and only
-- explicit follow acts land here.
--
--   kind        'commit'  — a picker follow was auto-committed (source='auto')
--                           or reader-picked (source='pick') or landed narrow on
--                           resolver failure (source='degrade').
--               'correct' — the reader CHANGED that follow's altitude (switched
--                           rung) or unfollowed it. The correction the flip
--                           watches for.
--   thread_id   the memory row the event is about (the join key commit<->correct;
--                stable across a switch — the follow MOVES, never copies). A
--                PLAIN INTEGER, deliberately NOT a REFERENCES memory(id): the
--                forensic log OUTLIVES the thread. Delete is a SOFT delete of
--                TRACKING (ADR-0010) — it must not be blocked by, nor cascade
--                into, the instrument (which is append-only and cannot cascade
--                anyway). foreign_keys is ON per-connection (db.connect), so a
--                hard FK would abort a legitimate `thread/delete` of a followed
--                thread that carries events.
--   topic       the follow's name at event time (forensic legibility; the
--                thread_id is the join).
--   altitude / confidence / source  the committed pick's shape, copied onto the
--                event so the count is a pure query over THIS log (no back-join
--                to a mutable memory row that a later switch would have changed).
--   occurred_at UTC ISO-8601; the 24h window is julianday(correct)-julianday(
--                commit) <= 1.0.
--
-- APPEND-ONLY, enforced STRUCTURALLY (the 0004/0009 RAISE(ABORT) pair, not
-- convention): an instrument whose history can be rewritten proves nothing.
-- Additive, new table — rollback = stop reading it; nothing else references it.

BEGIN;

CREATE TABLE IF NOT EXISTS follow_altitude_events (
    id          INTEGER PRIMARY KEY,
    thread_id   INTEGER,          -- join key only (see header) — NOT a hard FK
    topic       TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('commit', 'correct')),
    altitude    TEXT NOT NULL DEFAULT '',
    confidence  TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_follow_altitude_events_thread
    ON follow_altitude_events (thread_id, kind);

CREATE TRIGGER IF NOT EXISTS trg_follow_altitude_events_no_update
BEFORE UPDATE ON follow_altitude_events
BEGIN
    SELECT RAISE(ABORT, 'follow_altitude_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_follow_altitude_events_no_delete
BEFORE DELETE ON follow_altitude_events
BEGIN
    SELECT RAISE(ABORT, 'follow_altitude_events is append-only');
END;

COMMIT;
