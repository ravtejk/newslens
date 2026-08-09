-- 0026_vocabulary_moves.sql — NL-17 M2: THE VOCABULARY-MOVE LEDGER.
--
-- NUMBERING NOTE, because the record says otherwise. The 2026-08-02
-- engineering council (:109) reserved slot 0025 for this table. That slot was
-- SPENT by 0025_follow_settle_events.sql during M1 (see its own header's
-- renumber note). This is 0026. Nothing about the design changed — only the
-- number the record predicted.
--
-- WHAT THIS IS. One concept must live in exactly one vocabulary (NL-17
-- acceptance (a)). "Federal Reserve" is a sources.yaml TAG today and an
-- ENTITY after the M3 approval batch; the move between those two vocabularies
-- is the event this table records, once, forever.
--
-- WHY THE LEDGER IS THE MECHANISM AND NOT THE PAPERWORK (engineering R2.1,
-- Remy's weight-atomic law — the correction the product council forced on the
-- original M2/M3 ordering). Atomicity across a SQLite database and a
-- hand-editable YAML file cannot be a literal transaction: two stores, no
-- shared commit. So the invariant is moved to where a transaction DOES exist.
-- THE ROW IS THE ATOMIC SWITCH: ranking derives BOTH effects from this one row
-- — the concept's TAG contribution is suppressed AND its ENTITY becomes
-- steer-eligible — in a single derivation (steering.SteeringState.derive). No
-- instant of "moved but still tag-scored" or "tag off but not yet steered" is
-- reachable at rank time, even while sources.yaml still carries the tag line.
-- The YAML edit is then approval-gated cosmetics, not the switch.
--
-- APPEND-ONLY, AND THAT DECIDES THE REVERSAL SHAPE. 0004's trigger pair is the
-- house pattern for a ledger whose history must not be rewritable, and it is
-- applied here verbatim. Which means the "reversed marker" the charter names
-- CANNOT be a mutable column — marking a row reversed would be an UPDATE and
-- the trigger refuses it. So a reversal is A NEW ROW that names the row it
-- undoes (`reversal_of`), which is what an append-only ledger's reversal has
-- always been. "Is this move live?" is DERIVED, in one place
-- (steering.live_moves): a move is live iff no later row reverses it. The
-- reversal script the M3 batch carries writes that row; it never edits this
-- one.
--
--   concept      the concept's name in the vocabulary it is LEAVING — for a
--                tag->entity move, the exact sources.yaml interest string.
--                Matched case-insensitively against cluster matched_tags at
--                rank time; stored in the spelling the source file uses so a
--                human reading the ledger sees the line they blessed.
--   from_vocab   'tag' | 'entity' — CLOSED, by CHECK. The same
--                no-new-vocabulary tripwire 0024 put on `kind`.
--   to_vocab     'tag' | 'entity', and never equal to from_vocab.
--   entity_id    the entities row this concept IS, after the move. REQUIRED
--                when to_vocab='entity' (CHECK below) — a move into the entity
--                vocabulary with no entity to point at is exactly the
--                half-applied state the weight-atomic law exists to forbid,
--                and it would derive as "tag suppressed, nothing steerable":
--                zero weight in both vocabularies, which is the NL-14
--                starvation this program was chartered to cure. NULL is legal
--                only on the way BACK (to_vocab='tag').
--   blessed_at   UTC ISO-8601. When the principal approved the move.
--   blessed_by   who blessed it. Free text, but the only lawful value in
--                practice is the principal — moves are his call, item by item,
--                in the M3 approval diff (product R2 adjudication item 4).
--   reversal_of  the id of the move this row undoes, or NULL. A reversal row
--                is an ordinary move in the other direction PLUS this pointer.
--   note         the provenance sentence — which approval batch, which diff.
--
-- CONDITION-SHAPED CONCEPTS ARE NOT ENTITIES AND DO NOT BELONG HERE (Rook's
-- classification, engineering :146 region): "Recession Risk" and its siblings
-- stay tag-vocabulary, storyline-altitude, zero entity weight. They are a
-- LEGAL COEXISTENCE, not a pending move, so the absence of a row for them is
-- the correct record — never a backlog.
--
-- NO ROWS ARE WRITTEN BY M2. The machinery this table feeds is built dark; the
-- one-shot move script, the proposed sources.yaml diff, and the blessed rows
-- are the M3 approval batch. At zero rows the derivation is provably inert and
-- rank output is byte-identical to pre-0026 HEAD, pinned by THREE tests in
-- tests/test_nl17_m2_steering.py (named exactly, fix loop 1 / QA F-1 — the
-- earlier text cited a `test_dark_run_is_byte_identical_to_head` that does not
-- exist):
--     test_dark_scoring_is_byte_identical_to_head
--     test_dark_selection_is_byte_identical_on_a_would_steer_fixture
--     test_dark_apply_looks_is_pure_pass_through_above_the_cluster_cap
--
-- ADDITIVE / O(1). One CREATE TABLE, two indexes, two triggers. No existing
-- table is read or rewritten. Re-apply is guarded by the runner's
-- schema_migrations ledger plus IF NOT EXISTS everywhere (0001's convention).
--
-- UNWIND, ON RECORD AND IN ORDER (M1 precedent; fix loop 1 / QA F-8). "Stop
-- reading the table" is the operational rollback and stays true — the code
-- reads this table in exactly one place (steering.live_moves) and derives inert
-- when it is empty or gone. But an unwind that is only described cannot be
-- executed under pressure, so here it is, in dependency order — TRIGGERS first
-- (a DROP TABLE with its own triggers still attached is the shape that has bitten
-- this house before), then INDEXES, then the TABLE, then the ledger row that
-- would otherwise make the runner believe 0026 is still applied:
--
--     DROP TRIGGER IF EXISTS trg_vocabulary_moves_no_update;
--     DROP TRIGGER IF EXISTS trg_vocabulary_moves_no_delete;
--     DROP INDEX   IF EXISTS idx_vocabulary_moves_reversal;
--     DROP INDEX   IF EXISTS idx_vocabulary_moves_concept;
--     DROP TABLE   IF EXISTS vocabulary_moves;
--     DELETE FROM schema_migrations WHERE filename = '0026_vocabulary_moves.sql';
--
-- READ THE COST BEFORE RUNNING IT: at zero rows this is lossless and there is
-- nothing to preserve. Once the M3 approval batch writes blessed moves, the
-- DROP DESTROYS AN APPEND-ONLY LEDGER of the principal's own decisions, which
-- no other table records — dump the rows first, or the unwind is a data loss
-- wearing a rollback's name.
--
-- CHECKPOINT: schema migration — the principal's call, applied on his NEXT
-- SERVER RESTART, per the 0018/0024 pattern.

BEGIN;

CREATE TABLE IF NOT EXISTS vocabulary_moves (
    id          INTEGER PRIMARY KEY,
    concept     TEXT NOT NULL,
    from_vocab  TEXT NOT NULL CHECK (from_vocab IN ('tag', 'entity')),
    to_vocab    TEXT NOT NULL CHECK (to_vocab IN ('tag', 'entity')),
    entity_id   INTEGER REFERENCES entities(id),
    blessed_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    blessed_by  TEXT NOT NULL,
    reversal_of INTEGER REFERENCES vocabulary_moves(id),
    note        TEXT NOT NULL DEFAULT '',
    CHECK (from_vocab <> to_vocab),
    CHECK (to_vocab <> 'entity' OR entity_id IS NOT NULL),
    CHECK (length(trim(concept)) > 0)
);

-- The derivation's two hot reads: "which moves are live" walks reversal_of,
-- and the XOR scan asks "has this concept ever moved" by name.
CREATE INDEX IF NOT EXISTS idx_vocabulary_moves_reversal
    ON vocabulary_moves (reversal_of) WHERE reversal_of IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_vocabulary_moves_concept
    ON vocabulary_moves (concept);

-- 0004's pair, verbatim in shape: history that decides scoring must not be
-- rewritable, or every audit that reads it is reading a story instead of a
-- record. INSERT stays lawful; UPDATE and DELETE abort.
CREATE TRIGGER IF NOT EXISTS trg_vocabulary_moves_no_update
BEFORE UPDATE ON vocabulary_moves
BEGIN
    SELECT RAISE(ABORT, 'vocabulary_moves is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_vocabulary_moves_no_delete
BEFORE DELETE ON vocabulary_moves
BEGIN
    SELECT RAISE(ABORT, 'vocabulary_moves is append-only');
END;

COMMIT;
