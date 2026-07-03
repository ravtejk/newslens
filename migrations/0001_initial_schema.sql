-- 0001_initial_schema.sql — NewsLens core schema (milestone 1)
--
-- Spec: workspace/debates/2026-07-02--newslens--engineering.md §B.
-- Decision record: adr/0001-schema-three-tables-plus-history.md
--
-- Migration convention (binding on all future migrations):
--   * Every migration must be safe to re-apply (IF NOT EXISTS everywhere).
--     The runner records applied files in schema_migrations, but that record
--     is not atomic with the script itself, so re-application must be harmless.
--   * Each migration carries its own BEGIN/COMMIT; the runner executes it in
--     autocommit mode.
--
-- Data conventions:
--   * All *_at timestamps are UTC ISO-8601 text (e.g. 2026-07-02T14:00:00.000Z).
--   * briefings.date is the principal-local calendar day the briefing is FOR
--     (YYYY-MM-DD), supplied by the pipeline — deliberately NOT defaulted here.
--   * JSON columns are TEXT with json_valid() CHECKs: a malformed LLM output
--     that survives app-level validation still cannot land in the database
--     (ENGINEERING.md: structured outputs are validated; no silent garbage).

BEGIN;

-- ---------------------------------------------------------------------------
-- source_items — raw pulled content (Tier 1 RSS + Tier 2 Sonar discovery).
-- source_type is deliberately a closed CHECK list: adding a new source kind
-- (e.g. 'gnews' if the Sonar fallback ever triggers) is a visible migration
-- and a principal checkpoint, never a silent string.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source_items (
    id                    INTEGER PRIMARY KEY,
    source_type           TEXT    NOT NULL CHECK (source_type IN ('rss', 'sonar')),
    outlet                TEXT    NOT NULL,
    url                   TEXT    NOT NULL,
    title                 TEXT    NOT NULL,
    published_at          TEXT,            -- ISO-8601 when the feed provides it; feeds may omit
    fetched_at            TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    raw_excerpt           TEXT,
    wire_syndication_flag INTEGER NOT NULL DEFAULT 0 CHECK (wire_syndication_flag IN (0, 1))
);

-- Idempotency anchor for ingestion (milestone 2): re-running "today" upserts
-- instead of duplicating. One row per (url, fetch-day) — not per url — so a
-- briefing always references the snapshot it was actually built from, and a
-- later re-fetch of the same URL on a later day cannot mutate day-1 references
-- out from under day-1's briefing (faithfulness by construction).
CREATE UNIQUE INDEX IF NOT EXISTS idx_source_items_url_fetch_day
    ON source_items (url, date(fetched_at));

CREATE INDEX IF NOT EXISTS idx_source_items_fetched_at
    ON source_items (fetched_at);

-- ---------------------------------------------------------------------------
-- briefings — one row per calendar day (generated output).
-- UNIQUE(date) is the idempotent re-run anchor: regenerating a day is an
-- update-in-place, with the superseded version preserved in briefings_history
-- first (enforced by pipeline code from milestone 5 on).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS briefings (
    id                   INTEGER PRIMARY KEY,
    date                 TEXT NOT NULL UNIQUE,          -- YYYY-MM-DD, principal-local
    story_slots          TEXT NOT NULL DEFAULT '[]'
                         CHECK (json_valid(story_slots)),        -- 1–5 stories, each referencing source_items ids
    corroboration_labels TEXT NOT NULL DEFAULT '[]'
                         CHECK (json_valid(corroboration_labels)),
    narrative_text       TEXT,
    script_text          TEXT,                          -- spoken-delivery adaptation of narrative_text
    audio_file_path      TEXT,
    token_cost           TEXT
                         CHECK (token_cost IS NULL OR json_valid(token_cost)), -- JSON: per-step tokens + estimated USD
    generated_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- ---------------------------------------------------------------------------
-- memory — durable tracked-topics state ("what NewsLens is tracking for you").
-- Separate table from briefing history on purpose: prunable and queryable on
-- its own (spec §B staleness policy: active → stale after 14 days unreferenced,
-- surfaced not silently dropped; only principal-dismissed rows leave context;
-- prompt pulls active rows only, capped at N=15 most recently referenced).
-- Staleness transitions are pipeline logic (milestone 4), not schema triggers —
-- "14 days" and "N=15" are tunable policy, not structure.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory (
    id                          INTEGER PRIMARY KEY,
    topic                       TEXT NOT NULL,
    status                      TEXT NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active', 'stale', 'dismissed')),
    last_referenced_briefing_id INTEGER REFERENCES briefings(id),
    principal_note              TEXT,   -- the principal's own words; hand-editable via memory.md sync (milestone 4)
    created_at                  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at                  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_memory_status_updated
    ON memory (status, updated_at);

-- ---------------------------------------------------------------------------
-- briefings_history — append-only log of superseded briefing versions.
-- Exists so an idempotent re-run never destroys yesterday's (or this
-- morning's) output: if day 8's regeneration is worse, the prior version is
-- still here untouched to diff against. Append-only is enforced structurally,
-- not by convention: UPDATE and DELETE abort via triggers.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS briefings_history (
    id                   INTEGER PRIMARY KEY,
    briefing_id          INTEGER NOT NULL REFERENCES briefings(id),
    date                 TEXT NOT NULL,
    story_slots          TEXT,
    corroboration_labels TEXT,
    narrative_text       TEXT,
    script_text          TEXT,
    audio_file_path      TEXT,
    token_cost           TEXT,
    generated_at         TEXT,    -- when the archived version was originally generated
    archived_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_briefings_history_briefing
    ON briefings_history (briefing_id);

CREATE TRIGGER IF NOT EXISTS trg_briefings_history_no_update
BEFORE UPDATE ON briefings_history
BEGIN
    SELECT RAISE(ABORT, 'briefings_history is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_briefings_history_no_delete
BEFORE DELETE ON briefings_history
BEGIN
    SELECT RAISE(ABORT, 'briefings_history is append-only');
END;

COMMIT;
