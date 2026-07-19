-- 0019_memory_follow_altitude.sql — the follow-altitude picker's persisted
-- disclosure (NL-17-M1b, mockup-v9 PASSED-AS-AMENDED 2026-07-18).
--
-- A follow created through the picker now carries its ALTITUDE and the words
-- that disclose it (Kass's law: the altitude is visible everywhere the follow
-- surfaces). These five columns are the RENDER's source of truth — the deck
-- verb's steady form ("● Following — Volkswagen (company)") and the Following
-- row's qualifier read them VERBATIM. Separate columns, not a JSON blob, for
-- exactly the 0018 reason: the render stays dumb (reads one column under a
-- render condition; never parses a structure to fish prose out).
--
--   altitude         'entity' | 'storyline' | 'narrow'. 'narrow' is the
--                    just-this-story follow (the reader's pick or the resolver-
--                    failure landing); '' is an UNMIGRATED follow (a pre-M1b
--                    thread — renders BARE, the honest v1 mix, until NL-17
--                    proper backfills it; nothing is fabricated here).
--   primary_entity   the actor the resolver named (match anchor); '' when bare.
--   disclosure       the COMPACT qualifier-grammar name shown after "Following"
--                    ("Volkswagen (company)" / "Volkswagen job cuts" /
--                    "Redemption Gates (fund-withdrawal story)"). '' when bare.
--   alt_label        the OTHER rung named in words — the standing one-tap switch
--                    offer on the committed surface (mutation law: stored, so a
--                    re-expand never re-consults the resolver).
--   altitude_source  'auto' | 'pick' | 'degrade' — provenance the render needs:
--                    a DEGRADE narrow keeps the quiet upgrade line ("choose it
--                    anytime"); a PICK narrow does not (the reader chose it).
--
-- WHY COLUMNS ON memory (the follow's home table), additive: a follow IS a
-- memory row (add_thread). The altitude is an attribute OF that follow, one-to-
-- one, mutated only by the reader's tap (the follow MOVES, never copies — the
-- switch UPDATEs these in place). No new table, no join for the row's own
-- disclosure.
--
-- APPEND-ONLY-SAFE / ADDITIVE (0010/0017/0018 law). memory is a MUTABLE table
-- (0006 rebuilt it; add_thread UPDATEs it) — no RAISE(ABORT) append-only trigger
-- to trip. ALTER TABLE ADD COLUMN with a constant DEFAULT '' is an O(1)
-- metadata-only change (SQLite backfills no rows); the 19 existing threads read
-- '' — correct, they predate the picker and carry no altitude. Re-apply is
-- harmless (ADD COLUMN IF NOT EXISTS is unavailable in SQLite, so the runner's
-- schema_migrations ledger is the idempotency guard, per 0001's convention).
-- Rollback = stop reading the columns; nothing to undo.

BEGIN;

ALTER TABLE memory ADD COLUMN altitude        TEXT NOT NULL DEFAULT '';
ALTER TABLE memory ADD COLUMN primary_entity  TEXT NOT NULL DEFAULT '';
ALTER TABLE memory ADD COLUMN disclosure      TEXT NOT NULL DEFAULT '';
ALTER TABLE memory ADD COLUMN alt_label       TEXT NOT NULL DEFAULT '';
ALTER TABLE memory ADD COLUMN altitude_source TEXT NOT NULL DEFAULT '';

COMMIT;
