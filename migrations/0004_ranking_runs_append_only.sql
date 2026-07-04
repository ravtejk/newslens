-- 0004_ranking_runs_append_only.sql — enforce what 0003 declared (BUG-5, M3 QA)
--
-- ADR-0004 §6 calls ranking_runs append-only: the day-14 override
-- recalibration is only trustworthy if history cannot be rewritten. 0003
-- shipped the table without the abort-trigger pair 0001 gave
-- briefings_history — convention, not structure. This migration adds exactly
-- that pair.
--
-- Why a NEW migration instead of amending 0003 (ADR-0004 fix-loop amendment):
-- 0003 is already applied to the real database on this machine, which now
-- carries ingested history feeding the recency window — amending 0003 in
-- place would enforce nothing there without a destructive reset. A follow-on
-- migration applies cleanly everywhere, and it adds no new semantics: it
-- enforces exactly what the principal already approved 0003 to be.

BEGIN;

CREATE TRIGGER IF NOT EXISTS trg_ranking_runs_no_update
BEFORE UPDATE ON ranking_runs
BEGIN
    SELECT RAISE(ABORT, 'ranking_runs is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_ranking_runs_no_delete
BEFORE DELETE ON ranking_runs
BEGIN
    SELECT RAISE(ABORT, 'ranking_runs is append-only');
END;

COMMIT;
