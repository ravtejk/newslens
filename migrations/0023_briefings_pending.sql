-- 0023_briefings_pending.sql — NL-106, stage-and-promote for regenerate.
--
-- THE INCIDENT THIS CLOSES (proven live in the NL-103 remediation QA, finding
-- QA-1): a full regenerate DESTROYS the readable edition ~30 minutes before its
-- replacement exists. `ranking.persist()` archived the old row and NULLed
-- narrative_text/script_text/audio_file_path on the LIVE row at rank time
-- (ranking.py, the M3-gate NULL) while the replacement body only lands in
-- `generate.persist_generation()` at the very END of the run. Everything
-- between — analysis, narrative, editor, script, audio — is the long tail. A
-- failure anywhere in it strands a listed-but-blank edition in the Archive: the
-- reader opens their edition and it is gone, with no way back.
--
-- THE INVARIANT THIS TABLE ESTABLISHES: at every instant, the live `briefings`
-- row for a date is either the coherent pre-run edition or the coherent new one
-- — never a mixture, never bodyless because a run is in flight. A failed or
-- crashed full regenerate leaves the previous edition exactly as it was:
-- readable, listed, byte-identical.
--
-- HOW: the re-rank of a date that ALREADY has a readable edition no longer
-- touches the live row at all. Its new selection is STAGED here, and
-- persist_generation PROMOTES it in one transaction with the new body (archive
-- the displaced edition -> install new slots + new body -> delete the staged
-- row). A dead run simply leaves a staged row behind; the next re-rank writes
-- over it. Nothing to roll back, because nothing was destroyed.
--
--   date                  the edition's date — PRIMARY KEY, so a date has at
--                         most ONE staged selection and a re-rank replaces it.
--                         Deliberately NOT a REFERENCES briefings(date): the
--                         staging row's lifetime is the RUN's, not the row's,
--                         and an FK would be a second way for a promote to fail.
--   story_slots           the new selection, same JSON shape as briefings.
--   corroboration_labels  its corroboration payload, same shape.
--   token_cost            the RANK step's ledger for this staged selection. It
--                         rides here rather than on the live row so the old
--                         edition's spend can never pollute the new edition's
--                         ledger (NL-95: `usd` is real money) and so the new
--                         rank step is not lost when the promote folds in the
--                         generation steps.
--   created_at            when the selection was staged (the rank stage's `now`).
--
-- ALL ADDITIVE. One new table; `briefings` is untouched, no existing row is
-- rewritten, and NO SERVER/UI CODE EVER READS THIS TABLE — the reader's world
-- is the live row and only the live row, which is the isolation that makes the
-- invariant hold by construction. Rollback = revert the code: the table goes
-- inert and any staged row is dead weight a pre-0023 build never looks at.
--
-- CHECKPOINT: schema change. Applies on the principal's next restart per the
-- 0018-0022 sanction pattern — flagged in the build report, never silently.

BEGIN;

CREATE TABLE IF NOT EXISTS briefings_pending (
    date                 TEXT PRIMARY KEY,
    story_slots          TEXT NOT NULL,
    corroboration_labels TEXT,
    token_cost           TEXT,
    created_at           TEXT NOT NULL
);

COMMIT;
