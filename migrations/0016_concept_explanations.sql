-- 0016_concept_explanations.sql — the explained-once registry (substrate ruling
-- C, the collect-now list; principal-approved migration class, 2026-07-16).
--
-- SCHEMA ONLY. NL-77's cold-start backgrounder will WRITE it — recording that a
-- concept/term was FIRST explained in a given edition, so a later edition can
-- say "as we explained when this began" instead of re-explaining it. No writer
-- ships in this batch; the table stands ready.
--
-- Append-only + UNIQUE(concept): a concept is explained ONCE, and the registry
-- holds the FIRST explanation (NL-77 writes with INSERT OR IGNORE — a later
-- re-explanation is a no-op, not a second row). Normalization of `concept` is
-- NL-77's job; this table stores it verbatim.
--
-- concept                 — the concept/term explained (verbatim).
-- first_explained_edition — the edition (YYYY-MM-DD) that first explained it.
-- brief_id                — the analysis brief that carried the explanation, when
--                           there is one (NULLable).

BEGIN;

CREATE TABLE IF NOT EXISTS concept_explanations (
    id                      INTEGER PRIMARY KEY,
    concept                 TEXT NOT NULL UNIQUE,
    first_explained_edition TEXT NOT NULL,
    brief_id                INTEGER REFERENCES analysis_briefs(id),
    created_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_concept_explanations_edition
    ON concept_explanations (first_explained_edition);

CREATE TRIGGER IF NOT EXISTS trg_concept_explanations_no_update
BEFORE UPDATE ON concept_explanations
BEGIN
    SELECT RAISE(ABORT, 'concept_explanations is append-only (the first explanation is a fact; never rewritten)');
END;

CREATE TRIGGER IF NOT EXISTS trg_concept_explanations_no_delete
BEFORE DELETE ON concept_explanations
BEGIN
    SELECT RAISE(ABORT, 'concept_explanations is append-only (the first explanation is a fact; never deleted)');
END;

COMMIT;
