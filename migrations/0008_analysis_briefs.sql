-- 0008_analysis_briefs.sql — the Analyst's artifact (M9 milestone 2)
--
-- Pre-approved by the principal 2026-07-06 (DECISIONS.md M9 rulings, item 3).
-- One row per (date, slot) analysis attempt; append-only like generation_log
-- — regenerations add rows, readers take the newest (id DESC). Rejected
-- briefs are persisted too (status='rejected') for forensics: the deep view
-- and the writer read ONLY status='valid' rows. The brief itself is a fixed
-- JSON shape (contract §5.1 sections as data); citations resolve to the
-- run's retrieval manifest embedded in the JSON header.

BEGIN;

CREATE TABLE IF NOT EXISTS analysis_briefs (
    id            INTEGER PRIMARY KEY,
    date          TEXT NOT NULL,      -- briefing date (YYYY-MM-DD)
    slot          INTEGER NOT NULL,   -- 1-based slot in that day's edition
    tier          TEXT NOT NULL CHECK (tier IN ('full', 'medium')),
    status        TEXT NOT NULL CHECK (status IN ('valid', 'rejected')),
    brief_json    TEXT NOT NULL CHECK (json_valid(brief_json)),
    reject_reason TEXT,
    model         TEXT NOT NULL,
    cost_usd      REAL,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_analysis_briefs_date_slot
    ON analysis_briefs (date, slot, id);

COMMIT;
