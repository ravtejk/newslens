-- 0022_memory_sync_guard.sql — NL-81, the sync resurrection guard.
-- Design contract: workspace/debates/2026-07-25--newslens--engineering-2.md §5.
--
-- THE INCIDENT THIS CLOSES (2026-07-17, on record): sync_memory() is file-wins
-- with no memory of deletion and no recency signal of any kind. A stale
-- memory.md reconstruction re-imported four threads the swept DB didn't have,
-- dismissed the renamed thread the file couldn't see, and rendered that
-- org-caused dismissal as "(dismissed by you …)". Three holes, three additive
-- structures here:
--
--   (a) memory_tombstones — a DB-resident, APPEND-ONLY record of every
--       supported-lane delete/rename, so a deletion survives any file state.
--       Tombstones live in the DB ONLY: the file is the untrusted side (it is
--       hand-editable and it is the artifact that goes stale), and nothing
--       tombstone-shaped is ever rendered into memory.md.
--   (b) sync_state — a monotonic generation counter echoed into the file's
--       header comment on every DB->file render, plus a pairing identity.
--       NOT mtime: the poisoned 07-17 reconstruction was FRESHLY WRITTEN
--       (newest mtime, stale content), so any wall-clock signal passes the
--       exact file that caused the incident. A content generation stamp is the
--       only honest recency signal.
--   (c) memory.dismissed_via — provenance, so "(dismissed by you)" renders ONLY
--       for acts that arrived through a principal verb surface.
--
-- NO EXPIRY on tombstones, ever (both engineering seats, stated agreement):
-- expiry reopens the hole precisely for the oldest, stalest files — the ones
-- most likely to be wrong. Rows are tiny and N threads is tens.
--
-- ALL ADDITIVE. Two new tables + one ADD COLUMN; no existing row is rewritten.
-- Rollback = revert the code: the tables/column go inert and the file stamp is
-- an HTML comment that the pre-0022 parser already skips (memory.parse_file's
-- comment arm), so a rolled-back build reads a stamped file unchanged.
--
-- CHECKPOINT: schema change + a visible-copy change (legacy dismissals lose the
-- "by you" claim we cannot prove). Applies on the principal's next restart per
-- the 0018-0021 sanction pattern — flagged loudly in the build report, never
-- silently.

BEGIN;

-- (a) TOMBSTONES ------------------------------------------------------------
--
--   topic_key     lower(topic) at event time — the sync's match key, so the
--                 import check is one indexed lookup on the same key it
--                 matches file lines by (memory.py's topic.casefold()).
--   topic         the name at event time, kept for forensic legibility and for
--                 the refusal copy (which quotes the name the file uses).
--   thread_id     a PLAIN INTEGER, deliberately NOT a REFERENCES memory(id) —
--                 the 0020 precedent: this log OUTLIVES the row. delete_thread
--                 is a hard DELETE (ADR-0010), and foreign_keys is ON per
--                 connection (db.connect), so a real FK would either abort the
--                 delete or cascade the tombstone away — i.e. destroy the one
--                 record whose whole job is to survive the delete.
--   kind          'delete' — the supported-lane hard delete of a thread.
--                 'rename' — a thread's topic key changed (move_follow_altitude
--                            and any future rename verb). Blocks re-INSERT of
--                            the OLD name only; there is deliberately no
--                            note-mapping across renames in v1 (banked cut:
--                            once a recency gate exists, the "stale file
--                            dismisses the renamed row" case is unreachable).
--                 'lift'   — the EXPLICIT resurrection lane (`newslens memory
--                            add`, and the UI create/follow door that shares
--                            add_thread). A lift SUPERSEDES; nothing is ever
--                            updated or removed.
--   successor_key rename only: lower(new topic). NULL otherwise. Chains
--                 (a->b->c) resolve iteratively with a visited-set cycle guard
--                 so the disclosure can name the thread's CURRENT name.
--   actor         which surface caused it. 'principal' = a CLI/UI verb; 'org' =
--                 org-lane maintenance; 'sync' exists in the vocabulary but the
--                 sync never writes a tombstone in v1 (it only reads them).
--
-- LATEST ROW WINS per topic_key: the import check reads the highest id for the
-- key and blocks on 'delete'/'rename', proceeds on 'lift' or no row at all.
CREATE TABLE IF NOT EXISTS memory_tombstones (
    id            INTEGER PRIMARY KEY,
    topic_key     TEXT NOT NULL,
    topic         TEXT NOT NULL,
    thread_id     INTEGER,
    kind          TEXT NOT NULL CHECK (kind IN ('delete', 'rename', 'lift')),
    successor_key TEXT,
    actor         TEXT NOT NULL CHECK (actor IN ('principal', 'org', 'sync')),
    created_at    TEXT NOT NULL
                  DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- (topic_key, id) so "latest row for this key" is an index-only backward scan.
CREATE INDEX IF NOT EXISTS idx_memory_tombstones_key
    ON memory_tombstones (topic_key, id);

-- APPEND-ONLY, enforced STRUCTURALLY (the 0004/0009/0020 RAISE(ABORT) pair, not
-- convention): a deletion record that a later write can erase is the same hole
-- one layer down.
CREATE TRIGGER IF NOT EXISTS trg_memory_tombstones_no_update
BEFORE UPDATE ON memory_tombstones
BEGIN
    SELECT RAISE(ABORT, 'memory_tombstones is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_memory_tombstones_no_delete
BEFORE DELETE ON memory_tombstones
BEGIN
    SELECT RAISE(ABORT, 'memory_tombstones is append-only');
END;

-- (b) SYNC STATE — generation counter + pairing identity ---------------------
--
--   sync_generation  incremented on EVERY DB->file render and echoed into the
--                    file header. Import precondition: file gen == DB gen. A
--                    reconstruction, a restored backup, or a copy from
--                    elsewhere carries an old gen (or none) and is refused.
--                    Principal hand-edits don't touch the stamp, so the normal
--                    edit loop passes untouched.
--   sync_identity    a random pairing id seeded ONCE, here. Stage 0 gives each
--                    profile its own DB and its own memory.md, so the new
--                    failure mode profiles introduce is CROSS-PAIRING (profile
--                    A's file synced into profile B's DB, via an env-var slip
--                    through the NEWSLENS_MEMORY_FILE/NEWSLENS_DATA_DIR seams
--                    or a copied file). Import refuses on identity mismatch,
--                    naming both sides. This field costs one line now;
--                    retrofitting it later recreates the unstamped-legacy
--                    ambiguity once per profile, in the wild.
--   profile_slug     NULL for the founding single-profile install; set at
--                    Stage-0 profile creation. No rename of the principal's
--                    world required.
--
-- Single row, pinned by CHECK (id = 1). INSERT OR IGNORE so a re-apply (the
-- runner's ledger already guards this, belt and braces) never re-seeds the
-- identity — a changed identity would orphan the live file.
CREATE TABLE IF NOT EXISTS sync_state (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    sync_generation INTEGER NOT NULL DEFAULT 0,
    sync_identity   TEXT NOT NULL,
    profile_slug    TEXT
);

INSERT OR IGNORE INTO sync_state (id, sync_generation, sync_identity)
VALUES (1, 0, lower(hex(randomblob(16))));

-- (c) DISMISSAL PROVENANCE --------------------------------------------------
--
-- "by you" may be claimed ONLY when the act arrived through a principal verb
-- surface. Anything the sync INFERS is labeled by its mechanism, never by an
-- actor it cannot prove.
--
--   'principal'  a CLI/UI dismissal verb   -> "(dismissed by you <date>)"
--   'file_sync'  the sync inferred it from the file (a deleted line, a bare
--                line moved under Inactive) -> "(removed from your memory.md
--                <date>)" — states the mechanism, claims no actor. The file can
--                be written by anyone, including the org: that IS the incident.
--   'org'        org-lane maintenance (reserved; nothing writes it in v1)
--   NULL         legacy rows, pre-0022 -> "(dismissed <date>)", neutral. We
--                never backfill agency we cannot prove.
--
-- ALTER TABLE ADD COLUMN with a CHECK and no NOT NULL is an O(1) metadata-only
-- change (SQLite backfills no rows); existing rows read NULL. Verified on this
-- machine's sqlite 3.43.2 against a populated table: the add succeeds, existing
-- rows read NULL, and a bogus value aborts with CHECK-constraint error 19.
ALTER TABLE memory ADD COLUMN dismissed_via TEXT
    CHECK (dismissed_via IS NULL
           OR dismissed_via IN ('principal', 'file_sync', 'org'));

COMMIT;
