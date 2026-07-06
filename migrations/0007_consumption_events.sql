-- 0007_consumption_events.sql — the day-30 falsifier's data (milestone 7)
--
-- Spec §F semantics: consumption events are PRINCIPAL-INVOKED access,
-- structurally distinct from generation. Server-side capture (M7 UI):
-- a briefing page-view = 'read'; an episode play = 'listen'. Joined to
-- data/generation_log.jsonl by date.
--
-- Dedup ruling (ADR-0010): 'read' logs every view (raw truth — the metric
-- dedups); 'listen' logs at most one row per (date, occurred-day) because
-- an <audio> element issues bursts of Range requests per play. The day-30
-- metric is trailing-two-week UNPROMPTED OPEN DAYS: SELECT COUNT(DISTINCT
-- date(occurred_at)) ... WHERE occurred_at >= now-14d — flood-immune by
-- construction either way.

BEGIN;

CREATE TABLE IF NOT EXISTS consumption_events (
    id          INTEGER PRIMARY KEY,
    date        TEXT NOT NULL,            -- the briefing date consumed
    kind        TEXT NOT NULL CHECK (kind IN ('read', 'listen')),
    occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_consumption_date_kind
    ON consumption_events (date, kind);
CREATE INDEX IF NOT EXISTS idx_consumption_occurred
    ON consumption_events (occurred_at);

COMMIT;
