-- 0005_memory_topic_unique.sql — one row per thread topic (milestone 4)
--
-- memory.md sync matches file lines to rows by topic name
-- (case-insensitive). Without uniqueness, a duplicate topic would make the
-- sync ambiguous — which row does the principal's edit apply to? Enforced
-- structurally, like every other invariant the sync depends on.
--
-- Deliberately NOT here: the taxonomy's 14 seed threads. Seeds are principal
-- data, not schema — they land via memory.seed bootstrap on the first sync
-- (only when both the table and memory.md are empty), so a migration replay
-- can never resurrect threads the principal dismissed, and schema tests keep
-- an empty table. (ADR-0005.)

BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_topic_unique
    ON memory (lower(topic));

COMMIT;
