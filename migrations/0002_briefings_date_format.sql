-- 0002_briefings_date_format.sql — enforce YYYY-MM-DD on briefings.date
-- (milestone 2; reviewer finding 4 from the milestone-1 review, NOTES-M2 item 2)
--
-- Why triggers, not a CHECK: SQLite cannot ADD a CHECK constraint to an
-- existing table — retrofitting one means a full table rebuild (new table,
-- copy, drop, rename) while briefings is referenced by FKs from memory and
-- briefings_history. BEFORE INSERT/UPDATE triggers enforce the identical rule
-- with zero rebuild risk, matching the append-only-trigger precedent in 0001.
-- (adr/0003-m2-ingestion-decisions.md)
--
-- Scope: format only (reviewer's GLOB suggestion) — calendar validity
-- (month 13, day 32) remains pipeline-code responsibility, same as staleness
-- policy: structure enforces shape, code enforces policy.
--
-- Reminder: briefings.date is the PRINCIPAL-LOCAL calendar day the briefing
-- is for; source_items dedupe uses the UTC fetch-day. Different clocks, on
-- purpose — see the ingestion contract in src/newslens/ingest.py.

BEGIN;

CREATE TRIGGER IF NOT EXISTS trg_briefings_date_format_insert
BEFORE INSERT ON briefings
WHEN NEW.date NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
BEGIN
    SELECT RAISE(ABORT, 'briefings.date must be YYYY-MM-DD');
END;

CREATE TRIGGER IF NOT EXISTS trg_briefings_date_format_update
BEFORE UPDATE OF date ON briefings
WHEN NEW.date NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
BEGIN
    SELECT RAISE(ABORT, 'briefings.date must be YYYY-MM-DD');
END;

COMMIT;
