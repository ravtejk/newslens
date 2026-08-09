"""NL-17 M1 item 8 — the PROPOSED entity backfill list.

WHAT HAS TO BE TRUE ABOUT THIS INSTRUMENT, in order of how much damage getting
it wrong would do:

  1. IT CANNOT APPLY ANYTHING HE HAS NOT BLESSED. His memory, his bless (ENG-M1
     cut; product council §5.4). M1 held this as "no code path, and no flag,
     writes to the record" — and M3 NARROWED it by ruling, not by drift:
     DECISIONS [2026-08-08] item 4 blessed the list and routed application to a
     build item, so `--apply` now exists and writes exactly the blessed items.
     What is pinned today, structurally AND behaviourally: the classification
     core still cannot write; the write verbs live in `apply_proposal` alone;
     the default invocation still opens the record read-only; and an entity item
     the bless does not name is refused BY NAME rather than carried along.
  2. IT COSTS NOTHING AND CALLS NOTHING. Classification re-reads answers the
     resolver already gave and stored. A model call here would spend his money
     to re-derive facts we hold, and would make the list unreproducible.
  3. IT WORKS ON A PRE-0024 RECORD. This proposal is the INPUT to the checkpoint
     that sanctions 0024, and 0024 applies on his next server restart — so at
     the moment it is most needed, entity_id does not exist yet. (The first
     draft of this instrument assumed the column and refused on his live record.
     Caught here.)
  4. THE COUNT THE RULING TURNS ON IS HONEST. Kass's ratification demand is
     about TERMINAL-NONE — threads whose settle RAN and found nothing. An
     unmigrated thread never ran one. Reporting them together would inflate the
     exact number his dissent is measured against, in the direction that
     flips it.

Offline by construction: autouse sandbox (conftest), no network, no key, $0.
"""

from __future__ import annotations

import inspect
import json
import re
import sqlite3

import pytest

from newslens import backfill, db, memory, paths


def _py_code(src: str) -> str:
    """Python source with comments and docstrings stripped AND whitespace
    collapsed (FL.6's rule; the twin of `_js_code`). One stripper, shared with
    test_nl17_m1_fixloop1_instead rather than a second copy.

    Whitespace goes because the tokenizer re-emits `db.connect_readonly` as
    `db . connect_readonly`, so a substring check against source-shaped needles
    would silently never match — a pin that passes because it can never find
    anything is worse than no pin."""
    from test_nl17_m1_fixloop1_instead import _py_code as strip
    import textwrap
    return "".join(strip(textwrap.dedent(src)).split())


@pytest.fixture()
def con():
    db.migrate(db_path=paths.DB_PATH)
    c = db.connect(paths.DB_PATH)
    yield c
    c.close()


def _mixed(con):
    """One of each class, so every bucket and sub-bucket is exercised."""
    memory.add_thread_at_altitude(
        con, "Federal Reserve", altitude="entity",
        disclosure="Federal Reserve (agency)", primary_entity="Fed",
        source="auto")
    memory.add_thread_at_altitude(
        con, "Redemption Gates", altitude="entity",
        disclosure="Redemption Gates (fund-withdrawal story)",
        primary_entity="Redemption Gates", source="auto")
    memory.add_thread_at_altitude(
        con, "July jobs report", altitude="storyline",
        disclosure="July jobs report", source="auto")
    memory.add_thread_at_altitude(
        con, "Some headline the reader tapped", altitude="narrow", source="seed")
    memory.add_thread(con, "Strait of Hormuz")          # unmigrated


# ===========================================================================
# 1 — IT CANNOT APPLY ANYTHING EXCEPT WHAT HE BLESSED
# ===========================================================================

def test_the_classification_core_has_no_write_path_to_the_record():
    """M1's blanket "no write path anywhere" pin, NARROWED BY RULING — not
    weakened by drift. DECISIONS [2026-08-08] item 4 blessed the list and routed
    application to a build item; M3 builds it. The module now HAS a write path,
    on purpose, so the honest invariant is the one that still protects him: THE
    CLASSIFICATION CORE CANNOT WRITE. `build_proposal` + `classify_thread` are
    what every propose-only invocation runs, and they stay structurally
    incapable of touching the record.

    (Kept structural for M1's reason: "we remembered not to" is not a property.)
    """
    # QA F-12: read COMMENT-STRIPPED source (see _py_code).
    core = _py_code(inspect.getsource(backfill.build_proposal)
                    + inspect.getsource(backfill.classify_thread)
                    + inspect.getsource(backfill._print_report))
    for verb in ("INSERTINTOmemory", "UPDATEmemory", "DELETEFROMmemory",
                 "INSERTINTOentities", "UPDATEentities", "mint_or_match",
                 "db.connect("):
        assert verb not in core, verb


def test_every_write_verb_in_the_module_lives_in_apply_proposal():
    """The other half of the narrowing: the verbs did not scatter. A future diff
    that writes to the record from anywhere but the one sanctioned function
    fails here — the property M1's blanket assertion was really buying."""
    whole = _py_code(inspect.getsource(backfill))
    apply_src = _py_code(inspect.getsource(backfill.apply_proposal))
    for verb in ("UPDATEmemorySETentity_id", "mint_or_match"):
        assert verb in apply_src, f"{verb} missing from apply_proposal"
        assert whole.count(verb) == apply_src.count(verb), (
            f"{verb} appears outside apply_proposal")
    # Record-destroying verbs exist nowhere in the module, apply included.
    for verb in ("DELETEFROMmemory", "DELETEFROMentities", "DROPTABLE",
                 "UPDATEmemorySETtopic", "UPDATEmemorySETstatus"):
        assert verb not in whole, verb


def test_the_record_is_opened_read_only_and_a_write_would_fail(con, monkeypatch):
    """BORN RED. The structural pin above says no write is written; this says
    the handle could not perform one anyway. Two independent guarantees, because
    this is his memory."""
    _mixed(con)
    con.close()
    ro = db.connect_readonly(paths.DB_PATH)
    try:
        proposal = backfill.build_proposal(ro)
        assert proposal["threads_total"] == 5
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("UPDATE memory SET topic = 'x'")
    finally:
        ro.close()


def test_the_proposal_leaves_the_record_byte_identical(con):
    """BORN RED. The end-to-end version of the same promise: build the proposal
    over a real record and prove every row is exactly as it was."""
    _mixed(con)
    before = [tuple(r) for r in con.execute(
        "SELECT id, topic, status, altitude, disclosure, entity_id"
        " FROM memory ORDER BY id")]
    backfill.build_proposal(con)
    after = [tuple(r) for r in con.execute(
        "SELECT id, topic, status, altitude, disclosure, entity_id"
        " FROM memory ORDER BY id")]
    assert after == before
    assert con.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0


# ===========================================================================
# 2 — IT COSTS NOTHING
# ===========================================================================

def test_classification_is_deterministic_and_calls_no_model(con, monkeypatch):
    """BORN RED. Two runs, byte-identical output — which is what makes the list
    reviewable at all: a proposal that changes between the reading and the
    blessing is not a proposal. Enforced by construction (no resolver import is
    ever called), and checked by making any call an error."""
    _mixed(con)
    from newslens import follow_altitude

    def _boom(*a, **kw):
        raise AssertionError("the backfill must never call the resolver")
    monkeypatch.setattr(follow_altitude, "resolve_altitude", _boom)
    monkeypatch.setattr(follow_altitude, "resolve_cost_gate", _boom)

    a = json.dumps(backfill.build_proposal(con), sort_keys=True)
    b = json.dumps(backfill.build_proposal(con), sort_keys=True)
    assert a == b


# ===========================================================================
# 3 — IT WORKS ON A PRE-0024 RECORD
# ===========================================================================

def test_it_reads_a_record_that_predates_its_own_migration(tmp_path):
    """BORN RED, AND IT CAUGHT A REAL DEFECT IN THIS BUILD.

    The first draft selected `entity_id` unconditionally and refused on the
    principal's live record — the exact record the list exists to describe,
    because 0024 applies on his NEXT SERVER RESTART and the proposal is what he
    reads BEFORE sanctioning it. Migrating his database to make the query work
    would have been precisely the real-state write this instrument is built to
    avoid."""
    db_path = tmp_path / "pre.db"
    for f in db.migration_files():
        if f.name > "0023_briefings_pending.sql":
            continue
        con = db.connect(db_path)
        try:
            con.executescript(f.read_text(encoding="utf-8"))
        finally:
            con.close()
    con = db.connect(db_path)
    try:
        _mixed(con)
        cols = {r[1] for r in con.execute("PRAGMA table_info(memory)")}
        assert "entity_id" not in cols          # the precondition is real
        proposal = backfill.build_proposal(con)
    finally:
        con.close()
    assert proposal["schema_0024_applied"] is False
    assert proposal["threads_total"] == 5
    assert proposal["counts"][backfill.BUCKET_ENTITY] == 1


# ===========================================================================
# 4 — THE BUCKETS, AND THE COUNT THE RULING TURNS ON
# ===========================================================================

def test_every_thread_lands_in_exactly_one_named_bucket(con):
    """BORN RED. The proposal is machine-applyable, so every row must carry an
    explicit decision — an unclassified thread would be a silent no-op at
    application time."""
    _mixed(con)
    p = backfill.build_proposal(con)
    assert p["counts"] == {backfill.BUCKET_ENTITY: 1, backfill.BUCKET_NONE: 3,
                           backfill.BUCKET_UNMAPPABLE: 1}
    assert sum(p["counts"].values()) == p["threads_total"] == 5
    for it in p["items"]:
        assert it["bucket"] in (backfill.BUCKET_ENTITY, backfill.BUCKET_NONE,
                                backfill.BUCKET_UNMAPPABLE)
        assert (it["proposed_entity"] is not None) == (
            it["bucket"] == backfill.BUCKET_ENTITY)
        if it["bucket"] != backfill.BUCKET_ENTITY:
            assert it["reason"], it


def test_the_entity_proposal_is_explicit_enough_to_apply(con):
    """BORN RED. "Machine-applyable" means the blessing script re-derives
    nothing: thread_id, canonical_name and kind are all stated. If application
    had to re-run classification, the list he blessed and the list that gets
    applied could differ."""
    _mixed(con)
    item = [i for i in backfill.build_proposal(con)["items"]
            if i["bucket"] == backfill.BUCKET_ENTITY][0]
    assert item["topic"] == "Federal Reserve"
    assert item["proposed_entity"] == {
        "canonical_name": "Federal Reserve", "kind": "org", "aliases": ["Fed"]}
    assert isinstance(item["thread_id"], int)


def test_an_unmappable_class_is_named_never_forced_into_a_fit(con):
    """BORN RED. The instrument's version of the door's refusal: a class outside
    org/place/person gets its own bucket with the class NAMED, so "is three
    kinds enough?" is a question the principal can answer from the report rather
    than discover later as mis-filed rows."""
    _mixed(con)
    p = backfill.build_proposal(con)
    bad = [i for i in p["items"] if i["bucket"] == backfill.BUCKET_UNMAPPABLE]
    assert len(bad) == 1 and bad[0]["topic"] == "Redemption Gates"
    assert "fund-withdrawal story" in bad[0]["reason"]
    assert p["unmapped_classes_seen"] == ["fund-withdrawal story"]
    assert bad[0]["proposed_entity"] is None


def test_terminal_none_is_counted_apart_from_never_examined(con):
    """BORN RED, AND IT IS THE RATIFICATION EVIDENCE ITSELF.

    Kass's demand (product council §6) is that the principal ratifies the
    case-(a) reading with the terminal-none count in front of him, and his
    falsifier is terminal-none DOMINATING. That number means: threads whose
    settle RAN and found no broader concept. A thread predating the resolver
    never ran one — it is unexamined, not terminal-none — so folding the two
    together would inflate the very figure the dissent is measured against, in
    the direction that flips the ruling. Reported apart, with an honest
    denominator."""
    _mixed(con)
    p = backfill.build_proposal(con)
    subs = p["no_entity_sub_buckets"]
    assert subs == {backfill.SUB_STORYLINE: 1, backfill.SUB_SEEDED: 1,
                    backfill.SUB_UNMIGRATED: 1, backfill.SUB_UNEXAMINED: 0,
                    backfill.SUB_LOW: 0}
    # terminal-none = examined and found actorless; the denominator is EXAMINED
    # threads, not the whole table.
    assert p["terminal_none_count"] == 2
    assert p["examined_threads"] == 4          # 1 entity + 1 unmappable + 2
    assert p["terminal_none_share_of_examined"] == 0.5


def test_a_failed_settle_is_not_terminal_none_evidence(con):
    """BORN RED — QA F-10, and it is the same class of error the instrument was
    built to avoid, one level down.

    Altitude alone cannot say WHY a thread is still at seed. A thread whose only
    settle FAILED was never examined; an UNCONFIDENT one found two things it
    would not choose between. Neither is "the settle looked and found nothing",
    and counting them as if they were inflates Kass's number in exactly the
    direction that flips the ruling."""
    memory.add_thread_at_altitude(con, "Failed thread", altitude="narrow",
                                  source="seed")
    memory.add_thread_at_altitude(con, "Unconfident thread", altitude="narrow",
                                  source="seed")
    memory.add_thread_at_altitude(con, "Genuinely bare", altitude="narrow",
                                  source="seed")
    ids = {r["topic"]: r["id"] for r in con.execute(
        "SELECT id, topic FROM memory")}
    with con:
        memory.log_settle_outcome(con, ids["Failed thread"], "Failed thread",
                                  "settle_failed", detail="dead lane")
        memory.log_settle_outcome(
            con, ids["Unconfident thread"], "Unconfident thread", "settled_low",
            detail=memory.encode_settle_candidates(
                [{"altitude": "entity", "disclosure": "X (company)"}]))
        memory.log_settle_outcome(con, ids["Genuinely bare"], "Genuinely bare",
                                  "settled_none", detail="storyline")
    p = backfill.build_proposal(con)
    subs = p["no_entity_sub_buckets"]
    assert subs[backfill.SUB_UNEXAMINED] == 1     # the failed one, quarantined
    assert subs[backfill.SUB_LOW] == 1            # the unconfident one, apart
    assert subs[backfill.SUB_SEEDED] == 1         # only the real terminal-none
    assert p["terminal_none_count"] == 1
    # the unconfident thread IS examined, so it belongs in the denominator; the
    # failed one is not, so it does not.
    assert p["examined_threads"] == 2             # 1 terminal-none + 1 low
    by_topic = {i["topic"]: i for i in p["items"]}
    assert "NEVER EXAMINED" in by_topic["Failed thread"]["reason"]
    assert "unconfident" in by_topic["Unconfident thread"]["reason"]


def test_a_pre_0025_record_says_so_rather_than_inferring_examination(con):
    """BORN RED. When there is no settle history at all — which is the state his
    own record was in when this list was first produced — the altitude column is
    all there is. The row says that in its own reason instead of quietly
    claiming the thread was examined."""
    memory.add_thread_at_altitude(con, "No history", altitude="narrow",
                                  source="seed")
    item = [i for i in backfill.build_proposal(con)["items"]
            if i["topic"] == "No history"][0]
    assert item["sub_bucket"] == backfill.SUB_SEEDED
    assert "no settle history on record" in item["reason"]


def test_shared_entities_are_surfaced_because_that_is_the_tables_whole_job(con):
    """BORN RED. If every thread collapses to its own entity, the table is doing
    no work — so the proposal states the collapse rather than leaving him to
    count it."""
    memory.add_thread_at_altitude(
        con, "Volkswagen", altitude="entity", disclosure="Volkswagen (company)",
        source="auto")
    memory.add_thread_at_altitude(
        con, "Volkswagen plant closures", altitude="entity",
        disclosure="Volkswagen (company)", primary_entity="VW", source="auto")
    p = backfill.build_proposal(con)
    assert p["distinct_entities_proposed"] == 1
    assert list(p["shared_entities"]) == ["volkswagen"]
    assert len(p["shared_entities"]["volkswagen"]) == 2


# ===========================================================================
# 6 — M3: THE APPLY PATH, AND ITS CEILING
# ===========================================================================
# The bless is the authority AND the limit (DECISIONS [2026-08-08] item 4: "the
# bless covers the LIST"). These pin both halves: the blessed item lands, and
# everything else is refused BY NAME rather than quietly carried along.

def _blessed_fed(con):
    """The blessed shape, in a sandbox: an entity thread whose stored
    disclosure mints `Federal Reserve (org)` through the real door."""
    _outcome, tid = memory.add_thread_at_altitude(
        con, "Federal Reserve", altitude="entity",
        disclosure="Federal Reserve (agency)", primary_entity="Fed",
        source="auto")
    return tid


def test_the_blessed_list_is_exactly_what_the_checkpoint_blessed():
    """ANTI-DRIFT, and it is the pin that makes the rest safe: the apply path's
    authority is a literal in the source, so a silent edit that widens it fails
    here. Growing this list is a checkpoint, and a checkpoint is a diff."""
    assert backfill.BLESSED_ENTITY_ITEMS == ((19, "Federal Reserve", "org"),)


def test_apply_points_the_blessed_thread_at_a_minted_entity(con, monkeypatch):
    """BORN RED (apply_proposal does not exist pre-diff). The milestone's own
    item 1: the blessed pair actually lands on the record."""
    tid = _blessed_fed(con)
    monkeypatch.setattr(backfill, "BLESSED_ENTITY_ITEMS",
                        ((tid, "Federal Reserve", "org"),))
    r = backfill.apply_proposal(con, backfill.build_proposal(con))
    assert len(r["applied"]) == 1
    eid = con.execute("SELECT entity_id FROM memory WHERE id = ?",
                      (tid,)).fetchone()["entity_id"]
    assert eid is not None
    row = con.execute("SELECT canonical_name, kind FROM entities WHERE id = ?",
                      (eid,)).fetchone()
    assert (row["canonical_name"], row["kind"]) == ("Federal Reserve", "org")


def test_apply_refuses_an_entity_item_the_bless_does_not_name(con, monkeypatch):
    """THE CEILING. His record moves — he follows things — so a proposal
    computed at apply time can carry entity items nobody blessed. They are
    refused by name; 'he blessed the backfill' must never launder them in."""
    blessed = _blessed_fed(con)
    _o, stranger = memory.add_thread_at_altitude(
        con, "Volkswagen", altitude="entity", disclosure="Volkswagen (company)",
        source="auto")
    monkeypatch.setattr(backfill, "BLESSED_ENTITY_ITEMS",
                        ((blessed, "Federal Reserve", "org"),))
    r = backfill.apply_proposal(con, backfill.build_proposal(con))
    assert [i["thread_id"] for i in r["applied"]] == [blessed]
    assert [i["thread_id"] for i in r["unblessed"]] == [stranger]
    assert con.execute("SELECT entity_id FROM memory WHERE id = ?",
                       (stranger,)).fetchone()["entity_id"] is None


def test_apply_never_writes_a_no_entity_row(con, monkeypatch):
    """entity_id staying NULL is a TERMINAL STATE, not a gap to be filled
    (0024's law, and the ruling Kass's falsifier turns on). The apply path has
    no business touching those rows at all."""
    _mixed(con)
    monkeypatch.setattr(backfill, "BLESSED_ENTITY_ITEMS", ())
    before = [tuple(r) for r in con.execute(
        "SELECT id, topic, status, altitude, disclosure, entity_id"
        " FROM memory ORDER BY id")]
    backfill.apply_proposal(con, backfill.build_proposal(con))
    after = [tuple(r) for r in con.execute(
        "SELECT id, topic, status, altitude, disclosure, entity_id"
        " FROM memory ORDER BY id")]
    assert after == before
    assert con.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0


def test_apply_is_idempotent(con, monkeypatch):
    """A second apply writes nothing and mints nothing — the same law the
    memory backfill carries. He may well run it twice."""
    tid = _blessed_fed(con)
    monkeypatch.setattr(backfill, "BLESSED_ENTITY_ITEMS",
                        ((tid, "Federal Reserve", "org"),))
    backfill.apply_proposal(con, backfill.build_proposal(con))
    r2 = backfill.apply_proposal(con, backfill.build_proposal(con))
    assert r2["applied"] == []
    assert len(r2["already"]) == 1
    assert con.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 1


def test_apply_refuses_to_repoint_a_thread_that_already_points_elsewhere(
        con, monkeypatch):
    """An entity's identity does not move because a backfill ran. The alias
    discipline accretes and never rewrites (0024); the pointer gets the same
    posture — CONFLICT is reported, never resolved by overwriting."""
    tid = _blessed_fed(con)
    con.execute("INSERT INTO entities (canonical_name, kind, aliases)"
                " VALUES ('Someone Else', 'org', '')")
    other = con.execute("SELECT id FROM entities WHERE canonical_name ="
                        " 'Someone Else'").fetchone()["id"]
    con.execute("UPDATE memory SET entity_id = ? WHERE id = ?", (other, tid))
    con.commit()
    monkeypatch.setattr(backfill, "BLESSED_ENTITY_ITEMS",
                        ((tid, "Federal Reserve", "org"),))
    r = backfill.apply_proposal(con, backfill.build_proposal(con))
    assert r["applied"] == []
    assert len(r["conflicts"]) == 1
    assert con.execute("SELECT entity_id FROM memory WHERE id = ?",
                       (tid,)).fetchone()["entity_id"] == other


def test_apply_writes_no_settle_event(con, monkeypatch):
    """A backfill is NOT a settle. 0025's outcomes describe what a settle found;
    forging 'settled_entity' rows here would corrupt the very history
    build_proposal reads back for the F-10 sub-bucket split — the instrument
    would poison its own evidence on the next run."""
    tid = _blessed_fed(con)
    monkeypatch.setattr(backfill, "BLESSED_ENTITY_ITEMS",
                        ((tid, "Federal Reserve", "org"),))
    before = con.execute(
        "SELECT COUNT(*) FROM follow_settle_events").fetchone()[0]
    backfill.apply_proposal(con, backfill.build_proposal(con))
    after = con.execute(
        "SELECT COUNT(*) FROM follow_settle_events").fetchone()[0]
    assert after == before


def test_apply_refuses_on_a_record_that_predates_0024(con, monkeypatch):
    """0024 applies on his NEXT SERVER RESTART, so an apply run before that has
    no column to point. It refuses loudly and says when to come back — never
    migrates his record to make itself work (the instrument's founding rule)."""
    monkeypatch.setattr(backfill, "BLESSED_ENTITY_ITEMS", ())
    proposal = dict(backfill.build_proposal(con), schema_0024_applied=False)
    with pytest.raises(SystemExit) as exc:
        backfill.apply_proposal(con, proposal)
    assert "0024" in str(exc.value)


# ===========================================================================
# 7 — M3 ADDENDUM: THE BLESS LIST SHOWS THE FOLLOW STATE
# ===========================================================================
# Origin (NL-17 M3, 2026-08-09): the single entity item on the M1-blessed list
# was thread 19 `Federal Reserve`, DISMISSED by the principal the day before the
# bless. This report printed topic and proposed entity but not status, so the
# bless was given blind and the resulting pointer is inert — dismissed threads
# are excluded by `steering.watched_entities`. A blesser cannot weigh what the
# report does not show.

def test_the_proposal_shows_each_threads_follow_state(con, capsys):
    """BORN RED. Every line carries its status, so `active` / `dormant` /
    `dismissed_user` are all visible at the moment of blessing.

    FIX LOOP 1, F-5: this pin used to assert only that `[active]` appeared
    somewhere in the output, which the ENTITY line alone satisfied — QA's
    mutation dropping `{state}` from the NO-ENTITY line (36 of his 37 rows)
    survived 23/23 green. The claim is "every item line", so now it counts."""
    _mixed(con)
    proposal = backfill.build_proposal(con)
    backfill._print_report(proposal)
    out = capsys.readouterr().out
    assert "[active]" in out
    stated = [ln for ln in out.splitlines()
              if re.match(r"^    \[\s*\d+\] .*\[[^\]]+\]\s+->", ln)]
    assert len(stated) == len(proposal["items"]), (
        f"{len(stated)} lines carry a state, {len(proposal['items'])} items")


def test_a_no_entity_line_carries_its_state_too(con, capsys):
    """BORN RED (F-5). The 36-of-37 case named on its own: a no-entity row is
    still a disposition he is blessing, and its follow state is just as
    load-bearing for reading the list as an entity row's."""
    memory.add_thread(con, "Strait of Hormuz")          # unmigrated -> no-entity
    backfill._print_report(backfill.build_proposal(con))
    out = capsys.readouterr().out
    line = [ln for ln in out.splitlines() if "Strait of Hormuz" in ln][0]
    assert "no-entity" in line
    assert "[active]" in line, line


def test_a_dismissed_thread_is_flagged_loudly_not_merely_labelled(con, capsys):
    """BORN RED, and it is the tooth this section exists for: `dismissed_user`
    is not a lifecycle state, it is "he stopped following this". An entity
    pointer on such a thread steers nothing, so the line must SAY so rather
    than print a status word a reader has to know the semantics of."""
    _o, tid = memory.add_thread_at_altitude(
        con, "Federal Reserve", altitude="entity",
        disclosure="Federal Reserve (agency)", primary_entity="Fed",
        source="auto")
    con.execute("UPDATE memory SET status = 'dismissed_user' WHERE id = ?",
                (tid,))
    con.commit()
    backfill._print_report(backfill.build_proposal(con))
    out = capsys.readouterr().out
    assert "DISMISSED" in out
    assert "steers NOTHING" in out
    # the entity proposal is still MADE — the report informs the bless, it does
    # not silently drop an item the classifier legitimately produced
    assert "Federal Reserve (org)" in out
