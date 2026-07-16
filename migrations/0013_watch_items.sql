-- 0013_watch_items.sql — NL-75: the expiry register (Content council
-- 2026-07-16, the Forward-Claim Rules item 2; the accountability loop).
--
-- CHECKPOINT FLAG (implementer -> principal/gate): this is a THIRD schema
-- migration. The principal's "moat-strategy rulings" (2026-07-16, C) approved
-- exactly TWO — the consumption view-events (0011) and supersession (0012).
-- The Content spec says the expiry register "may ride the migration below,"
-- and persistence genuinely requires a table: the writer's watch_for prose is
-- NOT durably persisted anywhere today (briefings holds narrative_text +
-- story_slots only; the structured stories live solely in the thinned
-- generation_log). A derived read model over that log would be fragile. This
-- migration is therefore surfaced as a new checkpoint, not slipped in.
--
-- A watch-for becomes a LEDGER-ADJACENT OBJECT: (observable, due-date when
-- parseable, status). At the next edition an expired watch-for must CONVERT to
-- exactly one of RESOLVED / UNANSWERED / SUPERSEDED — never re-shipped, never
-- silently dropped (exemplar C: the silence is content).
--
-- Append-only, ledger philosophy: the 'open' promise is a row; a conversion is
-- a NEW row that `converts` the open one (status resolved|unanswered|
-- superseded). "Is this expired item still unconverted?" is a read: an open
-- row past its due-date with no conversion row pointing at it. Nothing is ever
-- rewritten — so a re-generation of the same edition dedups on
-- (briefing_id, slot, kind) rather than UPDATE-ing.

BEGIN;

CREATE TABLE IF NOT EXISTS watch_items (
    id            INTEGER PRIMARY KEY,
    thread_id     INTEGER REFERENCES memory(id),      -- NULL for a non-thread story's watch-for
    briefing_id   INTEGER REFERENCES briefings(id),
    slot          INTEGER,
    edition_date  TEXT NOT NULL,                       -- the edition that raised OR converted this item
    kind          TEXT NOT NULL DEFAULT 'open'
                  CHECK (kind IN ('open', 'resolved', 'unanswered', 'superseded')),
    observable    TEXT NOT NULL,                       -- the watch-for promise (open) / the conversion note
    due_date      TEXT,                                -- parsed due-date YYYY-MM-DD, when the observable named one
    converts      INTEGER REFERENCES watch_items(id),  -- a conversion row points at the open item it closes
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_watch_items_thread
    ON watch_items (thread_id, edition_date);
CREATE INDEX IF NOT EXISTS idx_watch_items_open
    ON watch_items (kind, due_date);
CREATE INDEX IF NOT EXISTS idx_watch_items_converts
    ON watch_items (converts);

CREATE TRIGGER IF NOT EXISTS trg_watch_items_no_update
BEFORE UPDATE ON watch_items
BEGIN
    SELECT RAISE(ABORT, 'watch_items is append-only (a promise and its conversion are dated facts; corrections are new rows)');
END;

CREATE TRIGGER IF NOT EXISTS trg_watch_items_no_delete
BEFORE DELETE ON watch_items
BEGIN
    SELECT RAISE(ABORT, 'watch_items is append-only (a promise and its conversion are dated facts; never deleted)');
END;

COMMIT;
