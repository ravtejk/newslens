-- 0025_follow_settle_events.sql — NL-17 M1: THE SETTLE OUTCOME LOG.
-- Product council ruling 2026-08-08 §5 item 3: "Settle outcomes are append-only
-- EVENTS — settled_entity / settled_none / settle_failed — never memory-column
-- flips."
--
-- WHY A NEW TABLE AND NOT A NEW `kind` ON 0020. 0020's follow_altitude_events
-- carries CHECK (kind IN ('commit','correct')) and a RAISE(ABORT) trigger pair.
-- Widening that CHECK in SQLite means rebuilding the table — and rebuilding an
-- append-only forensic log is the one operation append-only exists to forbid.
-- (The dispatch anticipated this: "if the settle-outcome events genuinely need
-- their own migration, 0025 joins the same sanction batch.") The separation is
-- also honest on the merits: 0020 answers Axel's medium-confidence question
-- (did the reader correct what we auto-picked?); this answers a different one
-- (what did the settle find, and may it try again?).
--
-- RENUMBER NOTE for M2: the ENG council reserved 0025 for `vocabulary_moves`.
-- That ledger becomes 0026 — migrations apply in lexicographic filename order
-- and nothing in M2 has landed, so this costs a number and nothing else.
--
--   outcome     'settled_entity' — the settle named an actor AND mint-or-match
--                                  produced a row; entity_id below is that row.
--               'settled_none'   — the settle RAN and AFFIRMATIVELY FOUND
--                                  NOTHING BROADER: a storyline (a broader story
--                                  is not an actor, and the kind vocabulary has
--                                  no seat for one), or an actor whose class does
--                                  not map to org/place/person. THE TERMINAL
--                                  STATE OF CASE (a): lawful, complete, and
--                                  auto-widenable forever — a later story that
--                                  resolves an actor settles onto this thread
--                                  and the rename sweeps the chrome.
--               'settled_low'    — the settle RAN and came back UNCONFIDENT,
--                                  holding NAMED CANDIDATES it would not stand
--                                  behind. The SURFACE is identical to
--                                  'settled_none' — the thread stays at seed,
--                                  nothing is attached, the state layer is
--                                  silent — but the FACT is not, and the split
--                                  is load-bearing twice over:
--
--                                  (1) DENOMINATOR HYGIENE. Kass's terminal-none
--                                  falsifier is evaluated at the principal's
--                                  checkpoint on the count of threads whose
--                                  settle looked and found NOTHING. A thread that
--                                  found two plausible things and could not
--                                  choose between them is not one of those, and
--                                  folding it in would inflate the exact number
--                                  his dissent is measured against.
--                                  (2) THE CANDIDATES ARE THIS ROW'S POINT — see
--                                  the `detail` note below.
--
--                                  Supplemental ruling 2026-08-08 (product
--                                  RECONVENE §R.3). AMENDED BY BIRTH: 0025 is
--                                  uncommitted and unapplied, so this widens a
--                                  CHECK that has never guarded a live row. That
--                                  is exactly why it lands here rather than as an
--                                  0026 — the append-only reasoning this file is
--                                  built on forbids rebuilding a LIVE table, and
--                                  a table that has never existed anywhere is not
--                                  one.
--               'settle_failed'  — the settle could not run to an answer: the
--                                  resolver raised, the lane was unavailable,
--                                  the transport died, the cap refused it
--                                  (detail names which). Case (b): the reader
--                                  sees NOTHING, and exactly ONE re-settle is
--                                  permitted (see `attempt`).
--   attempt     1 for the settle that runs at the tap; 2 for the ONE permitted
--               background re-settle. THE RETRY BOUND READS THIS LOG
--               (memory.settle_allowed): one trailing failure earns one retry,
--               a second consecutive failure ends it. Structural, queryable,
--               and auditable after the fact — a bound enforced only by a
--               client's good manners is not a bound.
--   entity_id   the minted/matched entity on 'settled_entity'; NULL otherwise.
--               A PLAIN INTEGER, deliberately NOT a REFERENCES entities(id) —
--               same reasoning as 0020's thread_id: this log OUTLIVES what it
--               describes, and an append-only table cannot participate in a
--               cascade anyway.
--   thread_id   the memory row this outcome is about. Also a plain INTEGER, and
--               for 0020's exact reason: foreign_keys is ON per-connection
--               (db.connect), so a hard FK would abort a legitimate
--               `thread/delete` of a thread that carries events.
--   topic       the thread's name at event time (forensic legibility only).
--   detail      TWO SHAPES, and the difference is the supplemental ruling.
--
--               For every kind EXCEPT 'settled_low': MACHINE diagnostics —
--               'coverage', 'storyline', 'unmapped-kind: fund', str(exc). NEVER
--               reader copy: those landings render NOTHING on screen, and this
--               column is why that silence is still accountable. No surface
--               reads it; the backfill and the operator do.
--
--               For 'settled_low' ONLY: a JSON object carrying the resolver's
--               NAMED CANDIDATES —
--                   {"reason": "low-confidence",
--                    "primary_entity": "Volkswagen",
--                    "candidates": [{"altitude": "entity",
--                                    "disclosure": "Volkswagen (company)"},
--                                   {"altitude": "storyline",
--                                    "disclosure": "Volkswagen job cuts"}]}
--               and the management surfaces DO read it, to render the ratified
--               "Instead: <Name (kind)> · <Storyline name>" row (v11 ruling ②'s
--               own carve-out: Instead: lives on deep view + Following, banned
--               on today cards). The reconciling law, verbatim from the ruling:
--               THE SETTLE NEVER ANNOUNCES; MANAGEMENT SURFACES MAY AFFORD.
--
--               ON THE 0018/0019 DUMB-RENDER LAW, stated rather than skirted:
--               that law bans a render PARSING A STRUCTURE TO FISH PROSE OUT.
--               These are not prose. They are the same controlled two-part
--               compact-qualifier names the `disclosure` and `alt_label` COLUMNS
--               already hold and the render already splits
--               (follow_altitude.split_qualifier — "a formatting split of a
--               two-part name, NOT prose-parsing"). The JSON is parsed in ONE
--               place (memory.settle_candidates) which hands the render a plain
--               list; no renderer ever touches this column. A list is the honest
--               shape here because the thing is genuinely a LIST — columns would
--               have meant candidate_1_*, candidate_2_* and a cap nobody ruled.
--   occurred_at UTC ISO-8601.
--
-- APPEND-ONLY, enforced STRUCTURALLY (the 0004/0009/0020 RAISE(ABORT) pair, not
-- convention). Additive, new table — rollback = stop reading it.
--
-- CHECKPOINT: additive, applies on the principal's NEXT SERVER RESTART. ONE
-- sanction batch with 0024 (0018 pattern).

BEGIN;

CREATE TABLE IF NOT EXISTS follow_settle_events (
    id          INTEGER PRIMARY KEY,
    thread_id   INTEGER,          -- join key only (see header) — NOT a hard FK
    topic       TEXT NOT NULL,
    outcome     TEXT NOT NULL CHECK (outcome IN
                    ('settled_entity', 'settled_none', 'settled_low',
                     'settle_failed')),
    attempt     INTEGER NOT NULL DEFAULT 1,
    entity_id   INTEGER,          -- the minted/matched row on settled_entity
    detail      TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- The retry-bound query's index: "any row for this thread at attempt >= 2?"
CREATE INDEX IF NOT EXISTS idx_follow_settle_events_thread
    ON follow_settle_events (thread_id, attempt);

CREATE TRIGGER IF NOT EXISTS trg_follow_settle_events_no_update
BEFORE UPDATE ON follow_settle_events
BEGIN
    SELECT RAISE(ABORT, 'follow_settle_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_follow_settle_events_no_delete
BEFORE DELETE ON follow_settle_events
BEGIN
    SELECT RAISE(ABORT, 'follow_settle_events is append-only');
END;

COMMIT;
