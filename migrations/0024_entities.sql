-- 0024_entities.sql — NL-17 M1: ENTITY IDENTITY. The entities table + the
-- nullable memory.entity_id FK (ENG-M1 cut, 2026-08-02 engineering council
-- :108; shape RATIFIED UNCHANGED by the product council 2026-08-08 §5
-- "Schema consequences for migration 0024", item 1).
--
-- WHAT THIS IS. A followed thread may or may not be ABOUT a durable actor. When
-- the settle names one — Volkswagen, the ECB, the Strait of Hormuz — that actor
-- gets a row HERE, once, and every thread about it points at the same row. That
-- is what makes "one concept = one vocabulary" (NL-17 acceptance (a)) mean
-- something: two threads that name the same company are two threads, one
-- entity, and the M2 XOR doors have a key to enforce against.
--
--   canonical_name  the actor's name as the settle named it, WITHOUT the class
--                   parenthetical ("Volkswagen", never "Volkswagen (company)").
--                   The parenthetical is the KIND, and it lives in its own
--                   column — a name that carries its own class is the 0018
--                   dumb-render problem in a key column.
--   kind            'org' | 'place' | 'person'. A CLOSED vocabulary, enforced by
--                   CHECK, and it is the no-new-vocabulary tripwire for the
--                   entity tier exactly as ALTITUDES is for the rung tier: a
--                   fourth kind cannot enter without a migration. The resolver's
--                   class words (company/agency/organization/person/place) are
--                   mapped to these three by entities.kind_for_class(), which
--                   REFUSES rather than guessing — an unmappable class mints
--                   NOTHING and the thread stays entity_id NULL. Never a
--                   fabricated identity to fill a column.
--   aliases         other surface forms that resolve to this row, "\n"-joined
--                   ("VW\nVolkswagen AG"). A MATCH KEY, never prose and never
--                   rendered: entities.split_aliases/join_aliases own the whole
--                   parse, so no render ever fishes a name out of a structure
--                   (0018/0019's law). Aliases only ever ACCRETE — a surface
--                   form that once resolved here keeps resolving here, so
--                   mint-or-match cannot silently re-point an old name.
--   created_at      UTC ISO-8601, the mint moment.
--
-- NO PROVISIONAL ROWS, EVER (product council 2026-08-08 §5.1; the alternative
-- was considered and REJECTED in council). A row is minted ONLY by a successful
-- settle through the mint-or-match door. Provisional/placeholder rows would
-- manufacture precisely the junk identities the principal's 2026-08-07
-- amendment (i) targets, pollute mint-or-match with names nothing resolved, and
-- create XOR exposure against sources.yaml for no gain.
--
-- memory.entity_id — NULLABLE, AND NULL IS A FIRST-CLASS SEMANTIC (§5.2): it
-- reads "storyline thread, no broader concept (yet)", NOT merely "unmigrated".
-- A thread whose settle found nothing broader is a lawful, complete, terminal
-- state — auto-widenable the moment a later story in the thread resolves an
-- actor, at which point this column fills and the rename sweeps the chrome.
-- entity_id NULL also means steering weight ZERO by definition (NL-17 criterion
-- (c): generic threads stay zero), so no stacking path exists through this
-- column and acceptance (a)/(b) gain no new case.
--
-- ADDITIVE / O(1) (0010/0017/0018/0019/0021 law). Two ALTER TABLE ADD COLUMNs
-- are not needed — one is, and SQLite requires a REFERENCES column added by
-- ALTER TABLE to default to NULL, which is exactly the semantic we want, so the
-- constraint and the ruling agree. ADD COLUMN with a NULL default is a
-- metadata-only change: SQLite rewrites no rows, so this is O(1) at any row
-- count. The new table is a plain CREATE.
-- Re-apply is guarded by the runner's schema_migrations ledger (SQLite has no
-- ADD COLUMN IF NOT EXISTS — 0001's convention). Rollback = stop reading the
-- column and the table; nothing existing references either.
--
-- memory is a MUTABLE table (0006 rebuilt it; add_thread UPDATEs it) — there is
-- no RAISE(ABORT) append-only trigger here to trip, unlike 0004/0009/0020.
-- entities is deliberately NOT append-only: aliases accrete by UPDATE, which is
-- the row telling more truth about itself, not history being rewritten. The
-- append-only surface for this milestone is 0025 (settle outcomes), where the
-- history question actually lives.
--
-- CHECKPOINT: additive, applies on the principal's NEXT SERVER RESTART. Rides
-- the ship checkpoint per the 0018 pattern, in ONE sanction batch with 0025.

BEGIN;

CREATE TABLE IF NOT EXISTS entities (
    id             INTEGER PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('org', 'place', 'person')),
    aliases        TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- ONE row per canonical name, case-insensitively — the mint-or-match door's
-- structural backstop. The door checks first (so a match is a match, not an
-- IntegrityError caught and reinterpreted), but a door is a promise and an index
-- is a guarantee; 0005 set this precedent for memory.topic.
CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_canonical
    ON entities (lower(canonical_name));

ALTER TABLE memory ADD COLUMN entity_id INTEGER REFERENCES entities(id);

-- Threads that point at an entity, for the M2 XOR/no-stacking queries and the
-- backfill's counts. Created AFTER the ALTER — the column has to exist before
-- an index can name it, and executescript runs these statements in order.
-- Partial: NULL entity_id is the common case and indexing it would index the
-- whole table to answer a question nobody asks ("which threads have no entity"
-- is a scan either way).
CREATE INDEX IF NOT EXISTS idx_memory_entity
    ON memory (entity_id) WHERE entity_id IS NOT NULL;

COMMIT;
