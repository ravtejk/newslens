-- 0009_analysis_append_only_and_retrieval.sql (M9-M2 fix loop 1)
--
-- Part 1 — BUG14: 0008's comment claimed "append-only like generation_log"
-- but nothing enforced it; a forensic 'rejected' row could be UPDATEd into
-- a servable 'valid' brief. Trigger pair per the 0004/ranking_runs
-- precedent (BUG-5 class). This follow-up migration is the path onto the
-- LIVE already-applied DB — fresh installs get 0008 then 0009.
--
-- Part 2 — receipts stay inspectable (Editor's observability flag, CoS-
-- routed): retrieved material (fetched full text, Sonar snippets, excerpts,
-- prior-briefing extracts) persists WITH the brief, keyed to its row, so
-- hand-traces never depend on re-fetching pages that can change or rot.
-- Storage math: ~15-40KB per brief, 2-3 briefs/day ≈ 1-3 MB/month — trivial
-- at one-reader scale.

BEGIN;

CREATE TRIGGER IF NOT EXISTS trg_analysis_briefs_no_update
BEFORE UPDATE ON analysis_briefs
BEGIN
    SELECT RAISE(ABORT, 'analysis_briefs is append-only (forensic record; regenerations add rows)');
END;

CREATE TRIGGER IF NOT EXISTS trg_analysis_briefs_no_delete
BEFORE DELETE ON analysis_briefs
BEGIN
    SELECT RAISE(ABORT, 'analysis_briefs is append-only (forensic record; regenerations add rows)');
END;

CREATE TABLE IF NOT EXISTS analysis_retrieval (
    id           INTEGER PRIMARY KEY,
    brief_id     INTEGER NOT NULL REFERENCES analysis_briefs(id),
    key          TEXT NOT NULL,       -- S#/C#/R#/P# as offered to the model
    kind         TEXT NOT NULL,
    outlet       TEXT NOT NULL DEFAULT '',
    title        TEXT NOT NULL DEFAULT '',
    url          TEXT NOT NULL DEFAULT '',
    retrieved_at TEXT NOT NULL DEFAULT '',
    text         TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_analysis_retrieval_brief
    ON analysis_retrieval (brief_id, key);

CREATE TRIGGER IF NOT EXISTS trg_analysis_retrieval_no_update
BEFORE UPDATE ON analysis_retrieval
BEGIN
    SELECT RAISE(ABORT, 'analysis_retrieval is append-only (the receipts behind a brief)');
END;

CREATE TRIGGER IF NOT EXISTS trg_analysis_retrieval_no_delete
BEFORE DELETE ON analysis_retrieval
BEGIN
    SELECT RAISE(ABORT, 'analysis_retrieval is append-only (the receipts behind a brief)');
END;

COMMIT;
