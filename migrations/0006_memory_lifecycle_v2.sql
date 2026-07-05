-- 0006_memory_lifecycle_v2.sql — three-state memory lifecycle (principal
-- amendment, finalized 2026-07-04; ADR-0006)
--
-- States collapse to active | dormant | dismissed_user: under auto-revival,
-- "stale" and "auto-dismissed" behave identically, so they merge. One
-- automatic transition each way (14d-unreferenced -> dormant; earned-slot
-- match -> active). dismissed_user never auto-revives.
--
-- Why a TABLE REBUILD (unlike 0002/0004's triggers): the 0001 CHECK
-- physically rejects the new status values — triggers can't widen a CHECK.
-- Rebuild is safe here because nothing FK-references memory (it only points
-- outward at briefings). New column status_changed_at makes the mandated
-- memory.md annotations ("dormant since <date>" / "dismissed by you <date>")
-- truthful — updated_at moves on note edits, transition dates must not.
--
-- Re-apply behavior (honest — corrected at the M4 gate): statuses map via a
-- pass-through CASE and re-application is SAFE, but it is NOT identical: a
-- replay re-seeds status_changed_at from updated_at, so transition dates can
-- drift forward if rows were touched since (demoed 07-01 -> 07-03). Accepted
-- residual: replay only occurs in the documented record-loss crash gap, and
-- pure SQL cannot preserve a column that exists on only one side of the
-- v1/v2 divide.

BEGIN;

CREATE TABLE IF NOT EXISTS memory_v2 (
    id                          INTEGER PRIMARY KEY,
    topic                       TEXT NOT NULL,
    status                      TEXT NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active', 'dormant', 'dismissed_user')),
    last_referenced_briefing_id INTEGER REFERENCES briefings(id),
    principal_note              TEXT,
    status_changed_at           TEXT,   -- when status last transitioned (annotation dates)
    created_at                  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at                  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

INSERT INTO memory_v2 (id, topic, status, last_referenced_briefing_id,
                       principal_note, status_changed_at, created_at, updated_at)
SELECT id, topic,
       CASE status
           WHEN 'stale' THEN 'dormant'
           WHEN 'dismissed' THEN 'dismissed_user'
           ELSE status
       END,
       last_referenced_briefing_id, principal_note, updated_at, created_at, updated_at
FROM memory;

DROP TABLE memory;
ALTER TABLE memory_v2 RENAME TO memory;

CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_topic_unique ON memory (lower(topic));
CREATE INDEX IF NOT EXISTS idx_memory_status_updated ON memory (status, updated_at);

COMMIT;
