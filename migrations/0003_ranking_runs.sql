-- 0003_ranking_runs.sql — ranking/override instrumentation log (milestone 3)
--
-- Why a table and not a briefings column: (a) ALTER TABLE ADD COLUMN cannot
-- be made safe to re-apply, and this repo's migration convention (0001 header)
-- requires re-apply safety; (b) the urgency-override contract
-- (workspace/debates/2026-07-03--newslens--product.md §E) wants fire/no-fire
-- rates OVER TIME for the day-14 recalibration — an append-per-run log is the
-- right shape, where a last-write column on briefings would lose history on
-- every idempotent re-rank.
--
-- meta JSON carries, per run: override {pool_size, fired, threshold, score,
-- reason, slot}, weights in force, model, prompt file, item/cluster counts.
-- token_usage JSON carries the LLM call's real tokens + estimated USD.

BEGIN;

CREATE TABLE IF NOT EXISTS ranking_runs (
    id          INTEGER PRIMARY KEY,
    date        TEXT NOT NULL,              -- the briefings.date this run ranked for
    ran_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    meta        TEXT NOT NULL CHECK (json_valid(meta)),
    token_usage TEXT CHECK (token_usage IS NULL OR json_valid(token_usage))
);

CREATE INDEX IF NOT EXISTS idx_ranking_runs_date ON ranking_runs (date, ran_at);

COMMIT;
