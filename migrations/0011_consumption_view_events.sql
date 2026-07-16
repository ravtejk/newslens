-- 0011_consumption_view_events.sql — NL-75: per-surface engagement instrument
-- (Data council 2026-07-16 §5/§6; principal checkpoint C(i), APPROVED
-- "moat-strategy rulings" 2026-07-16).
--
-- Adds two consumption KINDS — 'thread_view' and 'deep_view' — plus a TARGET
-- (which thread topic / deep-view story anchor was opened) and a REFERRER
-- (the surface the open came FROM: today | following | archive). This answers
-- Sol's "did memory pull him in": a thread_view referred from 'today' is the
-- moat working; from 'archive' is browsing. No dwell beacons (Mara's cut) —
-- one migration, ~$0 runtime; the live phase inherits the instrument.
--
-- Why a REBUILD (not triggers, unlike 0002/0004): 0007's `kind` CHECK
-- physically rejects the two new values, and SQLite cannot widen a CHECK in
-- place. The 0006 memory_v2 rebuild is the precedent. This is safe because
-- consumption_events is NOT append-only (reads log every view; the day-30
-- metric dedups) and nothing FK-references it.
--
-- Re-apply behavior (honest, matching 0006's disclosure): each run is one
-- BEGIN/COMMIT, so a mid-script failure rolls back whole. A re-apply in the
-- documented lost-record crash gap copies the existing rows into a fresh _v2
-- and renames back — read/listen rows are preserved exactly. The copy carries
-- only the four original columns; because thread_view/deep_view emission is
-- NOT wired in this milestone (Data: instrument-first), there are zero view
-- rows to lose at apply time — the residual is purely theoretical.

BEGIN;

CREATE TABLE IF NOT EXISTS consumption_events_v2 (
    id          INTEGER PRIMARY KEY,
    date        TEXT NOT NULL,            -- the briefing date consumed
    kind        TEXT NOT NULL
                CHECK (kind IN ('read', 'listen', 'thread_view', 'deep_view')),
    target      TEXT,                     -- thread topic / deep-view anchor (NULL for read/listen)
    referrer    TEXT                      -- origin surface (NULL for read/listen)
                CHECK (referrer IS NULL OR referrer IN ('today', 'following', 'archive')),
    occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

INSERT INTO consumption_events_v2 (id, date, kind, occurred_at)
    SELECT id, date, kind, occurred_at FROM consumption_events;

DROP TABLE consumption_events;
ALTER TABLE consumption_events_v2 RENAME TO consumption_events;

CREATE INDEX IF NOT EXISTS idx_consumption_date_kind
    ON consumption_events (date, kind);
CREATE INDEX IF NOT EXISTS idx_consumption_occurred
    ON consumption_events (occurred_at);
CREATE INDEX IF NOT EXISTS idx_consumption_kind_referrer
    ON consumption_events (kind, referrer);

COMMIT;
