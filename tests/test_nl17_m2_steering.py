"""NL-17 M2 — THE STEERING MACHINERY, BUILT DARK: migration 0026, the
weight-atomic derivation, the deterministic alias match, reserved looks, the
per-entity receipts, and the replay/audit instruments.

WHAT THIS FILE GUARDS. M2 builds every part of entity steering and turns none
of it on. That combination is exactly what makes it hard to test honestly — a
milestone whose correct behaviour is "nothing changes" can be satisfied by code
that does nothing at all. So the teeth come in pairs: every steering behaviour
is pinned BOTH ways, dark and armed.

  1. DARK IS INERT, AND PROVABLY. With the flag false and zero ledger rows, the
     scorer takes byte-identical branches and selection is byte-identical to
     pre-M2 HEAD — on a fixture that WOULD steer if armed, because a dark-proof
     over a fixture with nothing to steer proves nothing.
  2. THE LEDGER ROW IS THE ATOMIC SWITCH. Tag suppression and entity
     steer-eligibility are derived together or not at all, and a move that
     cannot deliver both is skipped WHOLE — never leaving a concept at zero
     weight in both vocabularies (Ruth's blocking condition; the NL-14
     starvation this program exists to cure).
  3. NO STACKING, COUNT-ONCE, CAP-INDEPENDENT. `min(base, 1.0)` can launder a
     sum, so the contribution-level pins read the POOL and the audit reads the
     RECORDED score against a summing counterfactual. A pin that asserts on the
     capped return value cannot tell max() from sum() and is not a pin.
  4. A LOOK IS NOT A SLOT. A followed entity with pool items is guaranteed to be
     clustered and scored; it is guaranteed nothing else. Contention beyond the
     reserve produces a `seen>0 ∧ look=0` RECEIPT, never silence.
  5. THE REPLAY PROVES ITSELF BY HASH. A rebuild that is not sha-equal to the
     bytes actually sent is a failed rebuild, and the audit default-denies a run
     it cannot recompute.

Offline by construction: autouse sandbox (conftest), no network, no key, $0. No
model is ever called — every cluster here is a literal, because this file is
about what the deterministic half does with clusters, not about getting them.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from conftest import anthropic_envelope
from newslens import config, db, llm as llm_mod, paths, ranking, replay, steering


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------

def _con(tmp_path):
    p = tmp_path / "m2.db"
    db.migrate(db_path=p)
    con = db.connect(p)
    return con


def _entity(con, name, kind="org", aliases="", *, altitude="entity",
            status="active", topic=None):
    """One entity plus the followed thread that points at it — the only shape in
    which an entity is ever WATCHED (nothing is watched that nobody follows)."""
    eid = con.execute(
        "INSERT INTO entities (canonical_name, kind, aliases) VALUES (?,?,?)",
        (name, kind, aliases)).lastrowid
    con.execute(
        "INSERT INTO memory (topic, status, altitude, entity_id)"
        " VALUES (?,?,?,?)", (topic or name, status, altitude, eid))
    con.commit()
    return eid


def _move(con, concept, entity_id, *, reversal_of=None, to_vocab="entity",
          from_vocab="tag"):
    mid = con.execute(
        "INSERT INTO vocabulary_moves (concept, from_vocab, to_vocab,"
        " entity_id, blessed_by, reversal_of, note) VALUES (?,?,?,?,?,?,?)",
        (concept, from_vocab, to_vocab, entity_id, "principal", reversal_of,
         "test")).lastrowid
    con.commit()
    return mid


def _item(con, iid, title, outlet="Reuters", fetched="2026-08-08T06:00:00Z"):
    con.execute(
        "INSERT INTO source_items (id, source_type, outlet, url, title,"
        " published_at, fetched_at, wire_syndication_flag)"
        " VALUES (?, 'rss', ?, ?, ?, ?, ?, 0)",
        (iid, outlet, f"https://x/{iid}", title, fetched, fetched))
    con.commit()
    return con.execute("SELECT * FROM source_items WHERE id = ?",
                       (iid,)).fetchone()


def _cluster(title="A story", *, tags=(), memory_=(), ids=(1,), wi=5,
             summary="s"):
    return {"story_title": title, "summary": summary, "item_ids": list(ids),
            "matched_tags": [{"name": n, "level": lv} for n, lv in tags],
            "matched_memory": list(memory_), "matched_dormant": [],
            "world_impact": wi}


# ---------------------------------------------------------------------------
# 1. Migration 0026 — shape, append-only, and the both-or-neither CHECK
# ---------------------------------------------------------------------------

def test_0026_creates_the_ledger_and_is_append_only(tmp_path):
    con = _con(tmp_path)
    try:
        eid = _entity(con, "Federal Reserve")
        mid = _move(con, "Federal Reserve", eid)
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute("UPDATE vocabulary_moves SET concept='x' WHERE id=?",
                        (mid,))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute("DELETE FROM vocabulary_moves WHERE id=?", (mid,))
    finally:
        con.close()


def test_0026_refuses_an_entity_move_with_no_entity(tmp_path):
    """The both-or-neither law, enforced in SQL. A move into the entity
    vocabulary with no entity to point at would derive as "tag suppressed,
    nothing steerable" — zero weight in BOTH vocabularies, the exact starvation
    the ledger exists to prevent. The CHECK makes the row unwritable."""
    con = _con(tmp_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO vocabulary_moves (concept, from_vocab, to_vocab,"
                " entity_id, blessed_by) VALUES ('Fed','tag','entity',NULL,'p')")
        with pytest.raises(sqlite3.IntegrityError):   # same vocabulary both ends
            con.execute(
                "INSERT INTO vocabulary_moves (concept, from_vocab, to_vocab,"
                " blessed_by) VALUES ('Fed','tag','tag','p')")
    finally:
        con.close()


def test_reversal_is_a_new_row_not_a_mutated_one(tmp_path):
    """Append-only decides the reversal shape: liveness is DERIVED from a later
    row naming the earlier one, because marking a column would be an UPDATE and
    the trigger refuses it."""
    con = _con(tmp_path)
    try:
        eid = _entity(con, "ECB")
        mid = _move(con, "ECB", eid)
        assert [m.id for m in steering.live_moves(con)] == [mid]
        _move(con, "ECB", None, reversal_of=mid, to_vocab="tag",
              from_vocab="entity")
        assert steering.live_moves(con) == []
        assert con.execute(
            "SELECT COUNT(*) c FROM vocabulary_moves").fetchone()["c"] == 2
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 2. DARK — the inertness proofs
# ---------------------------------------------------------------------------

def test_dark_scoring_is_byte_identical_to_head(tmp_path):
    """The scorer over a matrix of clusters, INERT state vs no state at all.

    Deliberately run on clusters that DO carry matched_entities: a dark proof
    over a fixture with nothing to steer would prove only that zero times
    anything is zero."""
    con = _con(tmp_path)
    try:
        eid = _entity(con, "Federal Reserve")
        state = steering.for_run(con, armed=False)
        assert state.armed is False
        assert state.suppressed_tags == frozenset()
        assert state.weighted_entities == frozenset()
        for tags in ((), (("Energy", "domain"),), (("Federal Reserve", "topic"),),
                     (("Energy", "domain"), ("Federal Reserve", "topic"))):
            for mem in ((), ("t",)):
                for followed in (False, True):
                    for steers in (False, True):
                        c = _cluster(tags=tags, memory_=mem)
                        c["matched_entities"] = [eid]
                        head = ranking.personal_score(c, followed, steers)
                        now = ranking.personal_score(c, followed, steers, state)
                        assert head == now, (tags, mem, followed, steers)
    finally:
        con.close()


def test_dark_selection_is_byte_identical_on_a_would_steer_fixture(tmp_path):
    """Full selection, dark, on a fixture that WOULD steer if armed: same slots,
    same order, same scores — and the machinery still emits its receipts."""
    con = _con(tmp_path)
    try:
        eid = _entity(con, "Federal Reserve", aliases="Fed")
        rows = [_item(con, 1, "Fed holds rates steady"),
                _item(con, 2, "Storm hits coast")]
        items_by_id = {r["id"]: r for r in rows}
        # The ranker OMITTED the Fed item — the exact shape that would mint a
        # reserved look if the flag were true. Dark, it mints nothing.
        clusters = [_cluster("Storm", ids=(2,), wi=7,
                             tags=(("Weather", "domain"),))]
        hits = steering.match_items(((r["id"], r["title"]) for r in rows),
                                    steering.for_run(con, armed=False).watched)
        assert hits == {eid: [1]}          # observation runs dark
        state = steering.for_run(con, armed=False)
        cl, srcs, notes, _lm = ranking.apply_looks(
            [dict(c) for c in clusters], hits, state, items_by_id)
        assert len(cl) == 1                # NO injection while dark
        dark, meta = ranking.select_slots(
            cl, items_by_id, set(), state=state, entity_hits=hits,
            look_sources=srcs, look_notes=notes)
        head, _ = ranking.select_slots([dict(c) for c in clusters],
                                       items_by_id, set())
        assert [(s.slot, s.story_title, s.personal_score, s.combined_score)
                for s in dark] == \
               [(s.slot, s.story_title, s.personal_score, s.combined_score)
                for s in head]
        rec = meta["entities"]["receipts"]
        assert [r["entity_id"] for r in rec] == [eid]
        assert rec[0]["seen"] == 1 and rec[0]["look"] == 0
        assert "steering is dark" in rec[0]["note"]
    finally:
        con.close()


def test_the_dark_law_is_guarded_at_both_layers(tmp_path):
    """BELT AND BRACES, each proven ALONE — because a redundant guard that is
    only ever tested through the other one is indistinguishable from a dead one.

    Measured: mutation runs deleting EITHER guard on its own left the whole M2
    file green, since `for_run` short-circuits before `derive` and returns a
    state whose `weighted_entities` is empty anyway. Both survivors were real
    defence-in-depth, not unguarded law — and this pin is what makes that
    statement checkable instead of a claim."""
    watched = [steering.Watched(id=1, canonical_name="X", kind="org",
                                forms=("Xco",), steers=True,
                                statuses=("active",), topics=("X",))]
    moves = [steering.Move(id=1, concept="X", from_vocab="tag",
                           to_vocab="entity", entity_id=1, blessed_at="",
                           blessed_by="principal", note="")]
    # LAYER 1 — derive() builds no effects at all when unarmed.
    assert steering.derive(moves, watched, armed=False) is steering.INERT
    assert steering.derive(moves, watched,
                           armed=True).weighted_entities == frozenset({1})
    # LAYER 2 — the scorer's own `armed` check, exercised on a state NO
    # constructor produces (unarmed yet carrying weight-bearing entities). That
    # is the only way to reach the second guard while the first is satisfied.
    rogue = steering.SteeringState(armed=False, weighted_entities=frozenset({1}))
    c = _cluster()
    c["matched_entities"] = [1]
    assert ranking.personal_score(c, False, False, rogue) == 0.0
    assert ranking.personal_score(
        c, False, False,
        steering.SteeringState(armed=True,
                               weighted_entities=frozenset({1}))) == 1.0


# ---------------------------------------------------------------------------
# 3. THE WEIGHT-ATOMIC DERIVATION
# ---------------------------------------------------------------------------

def test_one_row_derives_both_effects(tmp_path):
    con = _con(tmp_path)
    try:
        eid = _entity(con, "Federal Reserve")
        _move(con, "Federal Reserve", eid)
        st = steering.for_run(con, armed=True)
        assert st.suppressed_tags == frozenset({"federal reserve"})
        assert st.weighted_entities == frozenset({eid})
        assert st.tag_entity == {"federal reserve": eid}
        assert st.integrity == ()
    finally:
        con.close()


def test_a_move_whose_entity_cannot_steer_is_skipped_whole(tmp_path):
    """BOTH-OR-NEITHER. The entity sits at storyline altitude, so it bears no
    weight; suppressing its tag would leave the concept at zero in BOTH
    vocabularies. The move is not applied, the tag KEEPS its contribution, and
    the skip is recorded loudly."""
    con = _con(tmp_path)
    try:
        eid = _entity(con, "Recession Risk", altitude="storyline")
        _move(con, "Recession Risk", eid)
        st = steering.for_run(con, armed=True)
        assert st.suppressed_tags == frozenset()
        assert [i["kind"] for i in st.integrity] == ["move-not-applied"]
        c = _cluster(tags=(("Recession Risk", "topic"),))
        assert ranking.personal_score(c, False, False, st) == 1.0
    finally:
        con.close()


def test_unmoved_twin_keeps_its_tag(tmp_path):
    """Ruth's blocking condition (product R2, "where we still disagree" item 1):
    the tag-drop scopes to LIVE-MOVED concepts only. During the sequenced 3-then-5
    window the unmoved twins must keep scoring, or the enforcement mechanism
    manufactures the starvation the program was chartered to cure."""
    con = _con(tmp_path)
    try:
        moved = _entity(con, "Federal Reserve")
        _entity(con, "Strait of Hormuz")            # followed, NOT moved
        _move(con, "Federal Reserve", moved)
        st = steering.for_run(con, armed=True)
        assert "strait of hormuz" not in st.suppressed_tags
        c = _cluster(tags=(("Strait of Hormuz", "topic"),))
        assert ranking.personal_score(c, False, False, st) == 1.0
    finally:
        con.close()


def test_two_live_moves_claiming_one_concept_apply_neither(tmp_path):
    con = _con(tmp_path)
    try:
        a = _entity(con, "Fed A", topic="A")
        b = _entity(con, "Fed B", topic="B")
        _move(con, "Federal Reserve", a)
        _move(con, "Federal Reserve", b)
        st = steering.for_run(con, armed=True)
        assert st.suppressed_tags == frozenset()
        assert "concept-contested" in {i["kind"] for i in st.integrity}
    finally:
        con.close()


def test_domain_level_move_is_flagged_as_a_promotion(tmp_path):
    """Replacement is byte-neutral for a TOPIC tag (1.0 -> 1.0) and is NOT for a
    DOMAIN tag (0.5 -> 1.0). Inside the ceiling, but not neutral — so it is
    named rather than allowed to pass as a no-op."""
    con = _con(tmp_path)
    try:
        eid = _entity(con, "Energy")
        _move(con, "Energy", eid)
        st = steering.for_run(con, armed=True,
                              tag_levels={"Energy": "domain"})
        assert "level-promotion" in {i["kind"] for i in st.integrity}
        assert st.suppressed_tags == frozenset({"energy"})   # still applied
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 4. NO STACKING / XOR / STORYLINE-ZERO — contribution-level, cap-independent
# ---------------------------------------------------------------------------

def test_entity_weight_equals_topic_weight(tmp_path):
    """Replacement neutrality is a property of these being the SAME number, not
    a coincidence to be re-derived at each call site."""
    assert steering.ENTITY_WEIGHT == ranking.TOPIC_WEIGHT == 1.0


def test_pool_carries_exactly_one_entity_contribution(tmp_path):
    """CAP-INDEPENDENT count-once. `personal_score` returns min(base, 1.0), and
    that cap reads 1.0 whether the law is max() or sum() — so no assertion on
    the returned score can distinguish them. The pool can."""
    con = _con(tmp_path)
    try:
        a = _entity(con, "Federal Reserve", topic="A")
        b = _entity(con, "ECB", topic="B")
        c3 = _entity(con, "OPEC+", topic="C")
        st = steering.for_run(con, armed=True)
        c = _cluster(tags=(("Energy", "domain"),))
        c["matched_entities"] = [a, b, c3]
        pool = replay._pool(c, False, False, st.suppressed_tags, True,
                            st.weighted_entities)
        assert pool.count(steering.ENTITY_WEIGHT) == 1
        assert pool == [ranking.DOMAIN_WEIGHT, steering.ENTITY_WEIGHT]
        assert ranking.personal_score(c, False, False, st) == max(pool)
    finally:
        con.close()


def test_the_pool_is_a_max_not_a_sum(tmp_path):
    """THE DISCRIMINATOR, and it needs a specific shape to exist at all.

    `min(base, 1.0)` masks a summing bug wherever the sum clears 1.0 — a domain
    tag (0.5) plus an entity (1.0) reads 1.0 under BOTH laws. TWO DOMAIN TAGS is
    the shape where they diverge below the cap: max 0.5 vs sum 1.0. Measured
    gap, not a hypothetical — a mutation run swapping max() for sum() in the
    scorer passed the whole M2 file until this pin existed."""
    c = _cluster(tags=(("Energy", "domain"), ("Metals", "domain")))
    assert ranking.personal_score(c, False, False) == ranking.DOMAIN_WEIGHT
    con = _con(tmp_path)
    try:
        eid = _entity(con, "Federal Reserve")
        st = steering.for_run(con, armed=True)
        c["matched_entities"] = [eid]
        # entity + two domain tags: max 1.0, sum 2.0 -> both cap to 1.0, so the
        # capped score cannot carry this tooth. The POOL can, and does.
        pool = replay._pool(c, False, False, st.suppressed_tags, True,
                            st.weighted_entities)
        assert sorted(pool) == [ranking.DOMAIN_WEIGHT, ranking.DOMAIN_WEIGHT,
                                steering.ENTITY_WEIGHT]
        assert ranking.personal_score(c, False, False, st) == max(pool) == 1.0
        assert ranking.personal_score(c, False, False, st) != sum(pool)
    finally:
        con.close()


def test_a_moved_concept_contributes_once_not_twice(tmp_path):
    """The count-once case that matters: the model matched the TAG and the alias
    match found the ENTITY. One concept, one contribution — the suppressed tag
    leaves the pool entirely and the entity replaces it."""
    con = _con(tmp_path)
    try:
        eid = _entity(con, "Federal Reserve", aliases="Fed")
        _move(con, "Federal Reserve", eid)
        st = steering.for_run(con, armed=True)
        c = _cluster(tags=(("Federal Reserve", "topic"),))
        steering.annotate([c], {eid: [1]}, st)
        assert c["matched_entities"] == [eid]
        pool = replay._pool(c, False, False, st.suppressed_tags, True,
                            st.weighted_entities)
        assert pool == [steering.ENTITY_WEIGHT]      # the tag is GONE, not added
        assert ranking.personal_score(c, False, False, st) == 1.0
    finally:
        con.close()


def test_storyline_altitude_contributes_zero_weight(tmp_path):
    """Criterion (c), structural: a storyline follow is WATCHED (it appears in
    the receipts) and WEIGHTLESS (it never enters the pool)."""
    con = _con(tmp_path)
    try:
        eid = _entity(con, "Recession Risk", altitude="storyline")
        st = steering.for_run(con, armed=True)
        assert st.weighted_entities == frozenset()
        assert [w.id for w in st.watched] == [eid]      # watched, not weighted
        c = _cluster()
        c["matched_entities"] = [eid]
        assert ranking.personal_score(c, False, False, st) == 0.0
        assert replay._pool(c, False, False, st.suppressed_tags, True,
                            st.weighted_entities) == []
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 5. THE ALIAS MATCH — deterministic, word-bounded, injection-proof
# ---------------------------------------------------------------------------

def test_alias_match_is_word_bounded_and_case_insensitive(tmp_path):
    con = _con(tmp_path)
    try:
        eid = _entity(con, "Federal Reserve", aliases="Fed")
        w = steering.for_run(con, armed=False).watched
        hits = steering.match_items([
            (1, "FED HOLDS RATES"),          # case-insensitive
            (2, "Federal Reserve minutes"),  # canonical
            (3, "Fedex expands network"),    # NOT a match: no right boundary
            (4, "Unfed markets rally"),      # NOT a match: no left boundary
        ], w)
        assert hits == {eid: [1, 2]}
    finally:
        con.close()


def test_non_word_final_forms_match(tmp_path):
    r"""`\b` is wrong for "OPEC+": the trailing '+' is a non-word character, so
    `\b` would demand a word character after it and never match."""
    con = _con(tmp_path)
    try:
        eid = _entity(con, "OPEC+")
        w = steering.for_run(con, armed=False).watched
        assert steering.match_items([(1, "OPEC+ agrees quota rise")], w) \
            == {eid: [1]}
    finally:
        con.close()


def test_a_hostile_headline_cannot_mint_a_match(tmp_path):
    """Injection resistance is STRUCTURAL, not defensive: forms come only from
    `entities` rows, whose only writer is M1's mint-or-match door behind a
    successful settle. A headline cannot add a form to the table it is matched
    against."""
    con = _con(tmp_path)
    try:
        eid = _entity(con, "Federal Reserve")
        w = steering.for_run(con, armed=False).watched
        hits = steering.match_items([
            (1, "Analysis: why the Federal Reserve alias should be 'Acme Corp'"),
            (2, "Acme Corp posts record quarter"),
        ], w)
        assert hits == {eid: [1]}      # names the Fed; mints nothing for Acme
        assert con.execute(
            "SELECT COUNT(*) c FROM entities").fetchone()["c"] == 1
    finally:
        con.close()


def test_match_order_is_intake_order_newest_first(tmp_path):
    con = _con(tmp_path)
    try:
        eid = _entity(con, "ECB")
        w = steering.for_run(con, armed=False).watched
        hits = steering.match_items([(9, "ECB later"), (3, "ECB earlier")], w)
        assert hits[eid] == [9, 3]     # input order preserved, not re-sorted
    finally:
        con.close()


def test_one_character_forms_are_refused(tmp_path):
    con = _con(tmp_path)
    try:
        _entity(con, "X")
        w = steering.for_run(con, armed=False).watched
        assert w[0].forms == ()
        assert steering.match_items([(1, "X marks the spot")], w) == {w[0].id: []}
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 6. RESERVED LOOKS — guaranteed look, never a guaranteed slot
# ---------------------------------------------------------------------------

def test_followed_entity_with_items_is_clustered_and_scored(tmp_path):
    """The guarantee. The ranker omitted the Fed entirely; the reserve mints a
    deterministic cluster over its real items and that cluster is SCORED."""
    con = _con(tmp_path)
    try:
        eid = _entity(con, "Federal Reserve", aliases="Fed")
        rows = [_item(con, 1, "Fed holds rates steady"),
                _item(con, 2, "Storm hits coast")]
        items_by_id = {r["id"]: r for r in rows}
        st = steering.for_run(con, armed=True)
        hits = steering.match_items(((r["id"], r["title"]) for r in rows),
                                    st.watched)
        cl, srcs, notes, lm = ranking.apply_looks(
            [_cluster("Storm", ids=(2,), wi=7)], hits, st, items_by_id)
        assert len(cl) == 2 and srcs[eid] == "injected"
        look = cl[-1]
        assert look["look_injected"] is True
        assert look["item_ids"] == [1]
        assert look["story_title"] == "Fed holds rates steady"   # a real headline
        assert look["world_impact"] == 0                          # honest floor
        slots, meta = ranking.select_slots(
            cl, items_by_id, set(), state=st, entity_hits=hits,
            look_sources=srcs, look_notes=notes)
        titles = [s.story_title for s in slots]
        assert "Fed holds rates steady" in titles                 # scored + slotted
        rec = next(r for r in meta["entities"]["receipts"]
                   if r["entity_id"] == eid)
        assert rec["seen"] == 1 and rec["look"] == 1
        assert rec["look_source"] == "injected" and rec["selected"] == 1
    finally:
        con.close()


def test_a_look_the_model_already_formed_costs_no_reserve(tmp_path):
    con = _con(tmp_path)
    try:
        eid = _entity(con, "Federal Reserve", aliases="Fed")
        rows = [_item(con, 1, "Fed holds rates steady")]
        items_by_id = {r["id"]: r for r in rows}
        st = steering.for_run(con, armed=True)
        hits = steering.match_items(((r["id"], r["title"]) for r in rows),
                                    st.watched)
        cl, srcs, _n, lm = ranking.apply_looks(
            [_cluster("Fed decision", ids=(1,), wi=6)], hits, st, items_by_id)
        assert len(cl) == 1                      # nothing injected
        assert srcs[eid] == "model"
        assert lm["injected"] == [] and lm["contended"] == 0
    finally:
        con.close()


def test_contention_beyond_the_reserve_emits_seen_gt_zero_look_zero(tmp_path):
    """Kass's narrowed dissent, receipted. THREE entities with pool items, TWO
    reserved looks: the third is a first-class recorded state carrying the
    allocation rule, never an absence."""
    con = _con(tmp_path)
    try:
        a = _entity(con, "Alpha Bank", topic="A")
        b = _entity(con, "Beta Corp", topic="B")
        c = _entity(con, "Gamma Group", topic="C")
        rows = [_item(con, 1, "Alpha Bank one"), _item(con, 2, "Alpha Bank two"),
                _item(con, 3, "Beta Corp one"), _item(con, 4, "Beta Corp two"),
                _item(con, 5, "Gamma Group one")]
        items_by_id = {r["id"]: r for r in rows}
        st = steering.for_run(con, armed=True)
        hits = steering.match_items(((r["id"], r["title"]) for r in rows),
                                    st.watched)
        cl, srcs, notes, lm = ranking.apply_looks(
            [_cluster("Unrelated", ids=(), wi=9)], hits, st, items_by_id)
        assert lm["contended"] == 3
        assert len(lm["injected"]) == steering.ENTITY_LOOK_RESERVE == 2
        # allocation: pool-signal strength first — Alpha(2) and Beta(2) beat
        # Gamma(1); the Alpha/Beta tie breaks on freshest item id (4 > 2).
        assert lm["denied"] == [c]
        _slots, meta = ranking.select_slots(
            cl, items_by_id, set(), state=st, entity_hits=hits,
            look_sources=srcs, look_notes=notes)
        rec = next(r for r in meta["entities"]["receipts"] if r["entity_id"] == c)
        assert rec["seen"] == 1 and rec["look"] == 0
        assert "contended" in rec["note"] and "pool-signal" in rec["note"]
    finally:
        con.close()


def test_allocation_rule_is_deterministic_and_published():
    """The published order: pool-signal strength desc, freshest item desc,
    entity id asc. Total, so the rule never depends on dict order."""
    cands = [{"entity_id": 7, "seen": 2, "newest_item_id": 5},
             {"entity_id": 3, "seen": 2, "newest_item_id": 9},
             {"entity_id": 9, "seen": 5, "newest_item_id": 1},
             {"entity_id": 1, "seen": 2, "newest_item_id": 5}]
    granted, denied = steering.allocate_looks(cands, 3)
    assert [c["entity_id"] for c in granted] == [9, 3, 1]
    assert [c["entity_id"] for c in denied] == [7]


def test_a_watched_entity_with_no_items_still_gets_a_receipt(tmp_path):
    """Quiet must render (Uma/Onna). Absence is a WRITTEN state with a note —
    which is what makes the seven-run empty streak structurally impossible."""
    con = _con(tmp_path)
    try:
        eid = _entity(con, "Federal Reserve", status="dormant")
        rows = [_item(con, 1, "Storm hits coast")]
        items_by_id = {r["id"]: r for r in rows}
        st = steering.for_run(con, armed=True)
        hits = steering.match_items(((r["id"], r["title"]) for r in rows),
                                    st.watched)
        cl, srcs, notes, _lm = ranking.apply_looks(
            [_cluster("Storm", ids=(1,), wi=7)], hits, st, items_by_id)
        _s, meta = ranking.select_slots(cl, items_by_id, set(), state=st,
                                        entity_hits=hits, look_sources=srcs,
                                        look_notes=notes)
        rec = meta["entities"]["receipts"][0]
        assert rec["entity_id"] == eid and rec["seen"] == 0 and rec["look"] == 0
        assert rec["note"] == steering.WATCHED_NO_ITEMS_NOTE
        assert "dormant" not in rec["note"]     # never in reader vocabulary
    finally:
        con.close()


def test_a_dormant_follow_still_gets_its_look(tmp_path):
    """Pool-signal dormancy (Onna's dissolution): the look reads the entity table
    and the intake, NOT the active-thread list — so a dormant Fed still gets its
    FOMC-morning look from pool pressure."""
    con = _con(tmp_path)
    try:
        eid = _entity(con, "Federal Reserve", aliases="Fed", status="dormant")
        rows = [_item(con, 1, "Fed holds rates steady")]
        items_by_id = {r["id"]: r for r in rows}
        st = steering.for_run(con, armed=True)
        hits = steering.match_items(((r["id"], r["title"]) for r in rows),
                                    st.watched)
        _cl, srcs, _n, lm = ranking.apply_looks([], hits, st, items_by_id)
        assert srcs[eid] == "injected" and len(lm["injected"]) == 1
    finally:
        con.close()


def test_a_dismissed_follow_is_not_watched_at_all(tmp_path):
    con = _con(tmp_path)
    try:
        _entity(con, "Federal Reserve", status="dismissed_user")
        assert steering.for_run(con, armed=True).watched == ()
    finally:
        con.close()


def test_the_cluster_cap_does_not_move(tmp_path):
    """No cap change (engineering :110) — MAX_CLUSTERS is coupled to
    PROMPT_MARGIN_CHARS through NL-133's arithmetic pin. A look that would
    exceed the cap DISPLACES, and the displacement is disclosed."""
    con = _con(tmp_path)
    try:
        eid = _entity(con, "Federal Reserve", aliases="Fed")
        rows = [_item(con, 100, "Fed holds rates steady")]
        clusters = []
        for i in range(ranking.MAX_CLUSTERS):
            rows.append(_item(con, i + 1, f"Story {i}"))
            clusters.append(_cluster(f"Story {i}", ids=(i + 1,), wi=i))
        items_by_id = {r["id"]: r for r in rows}
        st = steering.for_run(con, armed=True)
        hits = steering.match_items([(100, "Fed holds rates steady")], st.watched)
        cl, _s, _n, lm = ranking.apply_looks(clusters, hits, st, items_by_id)
        assert len(cl) == ranking.MAX_CLUSTERS
        assert [d["story"] for d in lm["displaced"]] == ["Story 0"]  # lowest wi
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 7. REPLAY — hash-proved rebuild, flip attribution, default-deny audit
# ---------------------------------------------------------------------------

def _envelope_run(con, date="2026-08-08"):
    rows = [_item(con, 1, "Fed holds rates steady"),
            _item(con, 2, "Storm hits coast")]
    clusters = [_cluster("Fed decision", ids=(1,), wi=6),
                _cluster("Storm", ids=(2,), wi=7)]
    items_by_id = {r["id"]: r for r in rows}
    st = steering.for_run(con, armed=False)
    hits = steering.match_items(((r["id"], r["title"]) for r in rows), st.watched)
    cl, srcs, notes, _lm = ranking.apply_looks(
        [dict(c) for c in clusters], hits, st, items_by_id)
    slots, meta = ranking.select_slots(cl, items_by_id, set(), state=st,
                                       entity_hits=hits, look_sources=srcs,
                                       look_notes=notes)

    class _Cfg:
        interests_broad = ["Energy"]
        interests_granular = ["Federal Reserve"]
        followed_analyst_sources = []
    prompt = ranking.build_prompt(date, rows, _Cfg(), ["t"], "the last 2 day(s)",
                                 [])
    meta["replay"] = ranking._replay_envelope(prompt, rows, ["t"], [], _Cfg(),
                                              "the last 2 day(s)", date, cl)
    meta["threads_steer_selection"] = False
    con.execute("INSERT INTO ranking_runs (date, meta) VALUES (?,?)",
                (date, json.dumps(meta)))
    con.commit()
    return prompt, replay.runs_for(con, date)[0]


def test_rebuild_is_proved_byte_faithful_by_hash(tmp_path):
    con = _con(tmp_path)
    try:
        prompt, run = _envelope_run(con)
        env = replay.envelope(run)
        rebuilt, proof = replay.rebuild_prompt(con, env)
        assert rebuilt == prompt
        assert proof["prompt_sha_match"] and proof["items_sha_match"]
        assert proof["template_sha_match"] and proof["faithful"]
        assert proof["missing_item_ids"] == []
    finally:
        con.close()


def test_a_revised_headline_makes_the_rebuild_fail_loud(tmp_path):
    """The named durability bound: ingest UPDATEs source_items.title in place on
    every later sighting (ingest.py:226). The instrument reports the drift
    instead of replaying a prompt that was never sent, and items_sha256 is
    stored separately so the mismatch LOCALISES."""
    con = _con(tmp_path)
    try:
        _prompt, run = _envelope_run(con)
        con.execute("UPDATE source_items SET title = 'Fed holds rates (revised)'"
                    " WHERE id = 1")
        con.commit()
        _rebuilt, proof = replay.rebuild_prompt(con, replay.envelope(run))
        assert proof["faithful"] is False
        assert proof["items_sha_match"] is False
        assert proof["template_sha_match"] is True      # localised, not a blur
    finally:
        con.close()


def test_flip_replay_attributes_a_planted_delta(tmp_path):
    """n=1-conclusive causality: everything except the flipped bit is byte
    identical, so a story that appears in one selection and not the other did so
    BECAUSE of steering."""
    con = _con(tmp_path)
    try:
        eid = _entity(con, "Federal Reserve", aliases="Fed")
        _prompt, run = _envelope_run(con)
        env = replay.envelope(run)
        # plant: the Fed cluster carries the entity; nothing else changes.
        env["clusters"][0]["matched_entities"] = [eid]
        armed = steering.derive(steering.live_moves(con),
                                steering.watched_entities(con), armed=True)
        out = replay.flip_replay(con, env, armed)
        assert "Fed decision" in out["gained"]
        assert "Fed decision" not in out["dark"]
        assert out["attributed_to_steering"] is True and out["flips"] == 1
    finally:
        con.close()


def test_audit_passes_a_clean_run_and_default_denies_a_bare_one(tmp_path):
    con = _con(tmp_path)
    try:
        _prompt, run = _envelope_run(con)
        result = replay.audit(con, run)
        assert result["status"] == "PASS", result["violations"]
        assert all(result["checks"].values())
        con.execute("INSERT INTO ranking_runs (date, meta) VALUES (?,?)",
                    ("2026-08-07", json.dumps({"status": "ok"})))
        con.commit()
        bare = replay.runs_for(con, "2026-08-07")[0]
        denied = replay.audit(con, bare)
        assert denied["status"] == "FAIL"           # missing == FAIL
        assert denied["violations"][0]["check"] == "C1"
    finally:
        con.close()


def test_audit_catches_a_planted_stack(tmp_path):
    """The audit's own bite, proved by mutation rather than asserted. A recorded
    score that matches a SUMMED pool and not the max law is a stacking
    violation, and C3 says so by name."""
    con = _con(tmp_path)
    try:
        eid = _entity(con, "Federal Reserve", aliases="Fed")
        _move(con, "Federal Reserve", eid)
        rows = [_item(con, 1, "Fed holds rates steady")]
        cl = [_cluster("Fed decision", ids=(1,), wi=6,
                       tags=(("Energy", "domain"),))]
        cl[0]["matched_entities"] = [eid]
        cl[0]["personal_score"] = 1.0            # honest max()
        cl[0]["combined_score"] = ranking.combined_score(1.0, 6)
        meta = {
            "threads_steer_selection": True,
            "entities": {"armed": True, "suppressed_tags": ["federal reserve"],
                         "receipts": [{"entity_id": eid, "steers": True}]},
            "replay": {"prompt_sha256": "x", "clusters": cl,
                       "followed_outlets": [], "item_ids": [1]},
        }
        con.execute("INSERT INTO ranking_runs (date, meta) VALUES (?,?)",
                    ("2026-08-06", json.dumps(meta)))
        con.commit()
        clean = replay.audit(con, replay.runs_for(con, "2026-08-06")[0])
        assert clean["status"] == "PASS", clean["violations"]

        # plant the stack: domain tag 0.5 + entity 1.0 SUMMED = 1.5 -> capped 1.0
        # would be masked, so plant a shape where the two laws differ: drop the
        # cluster to a lone domain tag and record the summed value.
        cl[0]["matched_tags"] = [{"name": "Energy", "level": "domain"},
                                 {"name": "Metals", "level": "domain"}]
        cl[0]["matched_entities"] = []
        meta["entities"]["armed"] = False
        cl[0]["personal_score"] = 1.0            # 0.5 + 0.5 summed
        cl[0]["combined_score"] = ranking.combined_score(1.0, 6)
        con.execute("INSERT INTO ranking_runs (date, meta) VALUES (?,?)",
                    ("2026-08-05", json.dumps(meta)))
        con.commit()
        bad = replay.audit(con, replay.runs_for(con, "2026-08-05")[0])
        assert bad["status"] == "FAIL"
        assert bad["checks"]["recompute"] is False
        assert any("!= recomputed" in v["reason"] for v in bad["violations"])
    finally:
        con.close()


def test_xor_scan_names_a_concept_in_both_vocabularies(tmp_path):
    con = _con(tmp_path)
    try:
        eid = _entity(con, "Federal Reserve")
        _move(con, "Federal Reserve", eid)

        class _Cfg:
            interests_broad = []
            interests_granular = ["Federal Reserve"]
        scan = replay.xor_scan(con, _Cfg())
        assert scan["live_moves"] == 1
        assert scan["in_both_vocabularies"] == ["Federal Reserve"]
        assert scan["clean"] is False
    finally:
        con.close()


def test_replay_makes_no_model_call(tmp_path):
    """$0 on every path this module owns — structural, not budgeted: there is no
    llm import to gate."""
    import newslens.replay as mod
    src = (paths.PROTOTYPE_ROOT / "src" / "newslens" / "replay.py").read_text(
        encoding="utf-8") if hasattr(paths, "PROTOTYPE_ROOT") else ""
    assert not hasattr(mod, "llm")
    if src:
        assert "import llm" not in src and "from . import llm" not in src


# ---------------------------------------------------------------------------
# 8. FIX LOOP 1 — the shapes QA's adversarial pass reached and this file did not
#
# Every pin below was RED on the batch as first built. They are grouped here
# rather than folded into their sections so the loop's own coverage is legible:
# what the first pass missed was never a law it got wrong, it was an INPUT CLASS
# it never fed the law.
# ---------------------------------------------------------------------------

def _over_cap_dark_board(con):
    """QA's `override13` differential, as a fixture: 12 personal-matched
    clusters plus ONE zero-match wi=9 — the override contract's own material —
    and a watched entity the ranker omitted, so the reserve WOULD mint if armed.

    13 clusters is a LAWFUL live payload: `parse_clusters` refuses only
    > MAX_CLUSTERS * 2 (ranking.py:989-990), so the 12-cap is a SELECTION-side
    cap, never an intake one. This board is the class the batch's first dark
    pins never exercised (their selection fixture carried one cluster)."""
    eid = _entity(con, "Federal Reserve", aliases="Fed")
    rows = [_item(con, 100, "Fed holds rates steady")]
    clusters = []
    for i in range(ranking.MAX_CLUSTERS):
        rows.append(_item(con, i + 1, f"Tagged story {i}"))
        clusters.append(_cluster(f"Tagged story {i}", ids=(i + 1,), wi=i,
                                 tags=(("Energy", "domain"),)))
    rows.append(_item(con, 200, "Quake hits region"))
    clusters.append(_cluster("Quake hits region", ids=(200,), wi=9))
    return eid, clusters, {r["id"]: r for r in rows}


def test_dark_apply_looks_is_pure_pass_through_above_the_cluster_cap(tmp_path):
    """F-3, the NO-GO driver. `apply_looks` displaced ranker clusters on ANY
    board over the cap, because the branch was gated on cluster COUNT and not on
    a look having been MINTED. Dark, nothing is minted, so nothing may move —
    and the eviction order made it worse than random: personal-matched clusters
    are protected, so the FIRST thing evicted from a 12-personal board was the
    zero-match wi>=8 cluster, i.e. the override contract's own material.

    Measured on the batch as first built (QA's transcript): HEAD slotted
    'Quake hits region' through the override; the batch at flag-false did not.
    Displacement is now gated on `injected`, so a run that mints nothing is a
    pass-through by construction."""
    con = _con(tmp_path)
    try:
        eid, clusters, items_by_id = _over_cap_dark_board(con)
        st = steering.for_run(con, armed=False)
        hits = steering.match_items([(100, "Fed holds rates steady")], st.watched)
        assert hits == {eid: [100]}          # the look WOULD mint if armed
        cl, srcs, notes, lm = ranking.apply_looks(clusters, hits, st, items_by_id)

        # PASS-THROUGH, object for object: not "the same stories", the same list.
        assert len(cl) == 13
        assert [id(c) for c in cl] == [id(c) for c in clusters]
        assert lm["injected"] == [] and lm["displaced"] == []

        # And the override contract survives to selection, which is the thing
        # the eviction was silently taking away.
        slots, meta = ranking.select_slots(
            cl, items_by_id, set(), state=st, entity_hits=hits,
            look_sources=srcs, look_notes=notes)
        assert meta["override"]["fired"] is True
        assert meta["override"]["story"] == "Quake hits region"
        assert meta["override"]["pool_size"] == 1
        assert "Quake hits region" in [s.story_title for s in slots]
    finally:
        con.close()


def test_armed_with_no_injection_is_also_a_pass_through(tmp_path):
    """The other half of the same gate, and the reason it is `injected` and not
    `not armed`: an ARMED run that mints nothing has no look to make room for
    either. Same board, flag true, the entity already covered by a model
    cluster so the reserve is never spent."""
    con = _con(tmp_path)
    try:
        eid, clusters, items_by_id = _over_cap_dark_board(con)
        clusters[0]["item_ids"] = [1, 100]       # the ranker DID form the Fed
        st = steering.for_run(con, armed=True)
        hits = steering.match_items([(100, "Fed holds rates steady")], st.watched)
        cl, _s, _n, lm = ranking.apply_looks(clusters, hits, st, items_by_id)
        assert lm["injected"] == [] and lm["displaced"] == []
        assert len(cl) == 13
    finally:
        con.close()


def test_a_dark_run_over_the_cap_discloses_no_displacement(
        migrated_con, fake_api, monkeypatch):
    """The disclosure half of F-3, end to end through `run_rank` — which is also
    what proves 13 clusters is REACHABLE and not a constructed shape: this
    payload goes through the real parse gate.

    The batch as first built emitted 'reserved look displaced a ranker cluster
    ... to make room' on a run where `injected == []`. A warning naming a cause
    that did not happen is worse than silence: the day-N read counts it."""
    import time as _time
    monkeypatch.setattr(
        ranking, "OPENAI_CHAT_URL", fake_api.base_url + "/chat/completions")
    monkeypatch.setattr(
        llm_mod, "ANTHROPIC_MESSAGES_URL", fake_api.base_url + "/v1/messages")
    monkeypatch.setenv("ANTHROPIC_API_KEY", fake_api.good_key)
    monkeypatch.setenv("NEWSLENS_LANE_RANK", "api")
    monkeypatch.setattr(_time, "sleep", lambda s: None)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    payload_clusters = []
    for i in range(ranking.MAX_CLUSTERS):
        migrated_con.execute(
            "INSERT INTO source_items (id, source_type, outlet, url, title,"
            " fetched_at, wire_syndication_flag) VALUES (?, 'rss', 'Outlet A',"
            " ?, ?, ?, 0)",
            (i + 1, f"https://a.example/{i}", f"Tagged story {i}", now))
        payload_clusters.append({
            "story_title": f"Tagged story {i}",
            "summary": f"Distinct summary number {i} about regulation.",
            "item_ids": [i + 1],
            "matched_tags": [{"name": "AI regulation", "level": "topic"}],
            "matched_memory": [], "world_impact": (i % 7) + 1,
            "world_impact_reason": "Sector-wide effect",
        })
    migrated_con.execute(
        "INSERT INTO source_items (id, source_type, outlet, url, title,"
        " fetched_at, wire_syndication_flag) VALUES (200, 'rss', 'Outlet B',"
        " 'https://b.example/q', 'Quake hits region', ?, 0)", (now,))
    payload_clusters.append({
        "story_title": "Quake hits region", "summary": "Seismic damage widespread.",
        "item_ids": [200], "matched_tags": [], "matched_memory": [],
        "world_impact": 9, "world_impact_reason": "Global systemic consequence",
    })
    migrated_con.commit()
    fake_api.add_route(
        "/v1/messages", status=200,
        body=anthropic_envelope({"clusters": payload_clusters}),
        content_type="application/json")

    cfg = config.SourcesConfig(
        sources=[config.Source(name="Outlet A", rss_url="https://a.example/f")],
        interests_broad=["economy"], interests_granular=["AI regulation"])
    report = ranking.run_rank(date="2026-08-08", con=migrated_con, cfg=cfg,
                              env={"OPENAI_API_KEY": "sk-qa-fake"})

    assert not any("displaced a ranker cluster" in w for w in report.warnings), \
        [w for w in report.warnings if "displaced" in w]
    meta = json.loads(migrated_con.execute(
        "SELECT meta FROM ranking_runs ORDER BY id DESC LIMIT 1").fetchone()["meta"])
    assert meta["entities"]["injected"] == []
    assert meta["entities"]["displaced"] == []
    assert meta["entities"]["armed"] is False            # dark, as charted
    # the override contract's material survived the run it never should have left
    assert meta["override"]["fired"] is True
    assert meta["override"]["story"] == "Quake hits region"
    assert "Quake hits region" in [s.story_title for s in report.slots]


def test_audit_default_denies_a_run_with_no_entity_receipts_block(tmp_path):
    """F-5. `meta.entities` is written by `select_slots` on EVERY post-M2 run, so
    its absence on an envelope-bearing run is a receipt GAP — and data-M3's
    words are 'missing = FAIL, incl. any receipt gap'. Before this, such a run
    audited PASS: it read as a clean dark run, because `armed` defaulted false
    and every check then agreed with itself."""
    con = _con(tmp_path)
    try:
        _prompt, run = _envelope_run(con, date="2026-08-04")
        assert replay.audit(con, run)["status"] == "PASS"    # control

        meta = json.loads(run["meta"])
        stripped = {k: v for k, v in meta.items() if k != "entities"}
        con.execute("INSERT INTO ranking_runs (date, meta) VALUES (?,?)",
                    ("2026-08-03", json.dumps(stripped)))
        con.commit()
        out = replay.audit(con, replay.runs_for(con, "2026-08-03")[0])
        assert out["status"] == "FAIL"
        assert out["checks"]["receipts"] is False
        # The REASON is pinned, not just the verdict. Both C1 arms would refuse
        # this run — with the block gone, `ents` degrades to {} and the receipts
        # arm fires too — so an audit that only had to say FAIL would leave the
        # first arm indistinguishable from a dead one. A violation naming the
        # wrong gap costs a full re-derivation to act on, which is the reason
        # this module names the run and the cluster on every check.
        assert any(v["check"] == "C1"
                   and "no meta.entities receipts block" in v["reason"]
                   for v in out["violations"]), out["violations"]

        # and the narrower gap: block present, receipts list gone.
        half = dict(meta)
        half["entities"] = {k: v for k, v in meta["entities"].items()
                            if k != "receipts"}
        con.execute("INSERT INTO ranking_runs (date, meta) VALUES (?,?)",
                    ("2026-08-02", json.dumps(half)))
        con.commit()
        out2 = replay.audit(con, replay.runs_for(con, "2026-08-02")[0])
        assert out2["status"] == "FAIL"
        assert out2["checks"]["receipts"] is False
    finally:
        con.close()


def test_xor_scan_refuses_an_absent_catalog(tmp_path):
    """F-4. `xor_scan(con, None)` returned `clean: true` with live moves on the
    books — a default-deny instrument passing by silence. The scan's claim is
    about the INTERSECTION of the ledger and the tag catalog; with no catalog it
    has no claim to make, and saying so is the only honest answer."""
    con = _con(tmp_path)
    try:
        eid = _entity(con, "Federal Reserve")
        _move(con, "Federal Reserve", eid)
        with pytest.raises(replay.ReplayError) as exc:
            replay.xor_scan(con, None)
        assert "catalog" in str(exc.value)
        # and it refuses on an EMPTY ledger too: the refusal is a property of
        # the missing catalog, never of how much there happened to be to find.
        (tmp_path / "empty").mkdir()
        con2 = _con(tmp_path / "empty")
        try:
            with pytest.raises(replay.ReplayError):
                replay.xor_scan(con2, None)
        finally:
            con2.close()
    finally:
        con.close()


def _seed_ledgered_thread(con, topic):
    now = "2026-08-01T00:00:00.000Z"
    tid = con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES (?, 'active', ?, ?, ?)", (topic, now, now, now)
    ).lastrowid
    con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, verdict,"
        " what_happened, significance, cites_json) VALUES (?, '2026-08-05',"
        " 'advances', 'A dated development.', 'Changed the frame.', '[\"S1\"]')",
        (tid,))
    con.commit()
    return tid


def _quiet_zero_board(con):
    """QA's F-7 construction: a cluster that re-covers a LEDGERED thread with
    nothing new (Jaccard 1.0 against the prior edition) AND carries a
    weight-bearing entity. Live, the quiet-zero exclusion removes it from Today
    unconditionally and BEFORE selection — so steering cannot move it, armed or
    dark."""
    eid = _entity(con, "Federal Reserve", aliases="Fed", topic="Fed watch")
    _seed_ledgered_thread(con, "Hormuz")
    con.execute("INSERT INTO briefings (date, story_slots) VALUES (?,?)",
                ("2026-08-07", json.dumps(
                    [{"story_title": "Quiet cluster repeats itself again",
                      "summary": "Nothing new happened here today."}])))
    con.commit()
    rows = [_item(con, 1, "Fed holds rates steady"),
            _item(con, 2, "Quiet tanker story")]
    quiet = _cluster("Quiet cluster repeats itself again", ids=(2,), wi=2,
                     memory_=("Hormuz",),
                     summary="Nothing new happened here today.")
    quiet["matched_entities"] = [eid]
    # The companion cluster carries a tag so it SLOTS: without it both live
    # slates are empty and the delta assertion below would hold vacuously.
    clusters = [_cluster("Fed decision", ids=(1,), wi=6,
                         tags=(("Energy", "domain"),)), quiet]
    return eid, clusters, {r["id"]: r for r in rows}


def test_the_envelope_records_the_quiet_zero_verdict(tmp_path):
    """The receipt F-7's fix rests on. The quiet verdict is reachable only with
    the run's OWN ledger and prior edition — both of which drift — so it is
    persisted at run time exactly like personal_score/combined_score, never
    re-derived at replay time from today's tables."""
    con = _con(tmp_path)
    try:
        _eid, clusters, items_by_id = _quiet_zero_board(con)
        prior = ranking._prior_edition(con, "2026-08-08")
        slots, _meta = ranking.select_slots(clusters, items_by_id, set(),
                                            con=con, prior_edition=prior)
        assert "Quiet cluster repeats itself again" not in \
            [s.story_title for s in slots]                  # live: dropped
        assert clusters[0]["quiet_zero"] is False
        assert clusters[1]["quiet_zero"] is True

        class _Cfg:
            interests_broad = []
            interests_granular = []
            followed_analyst_sources = []
        env = ranking._replay_envelope("p", list(items_by_id.values()), [], [],
                                       _Cfg(), "w", "2026-08-08", clusters)
        assert [c["quiet_zero"] for c in env["clusters"]] == [False, True]
    finally:
        con.close()


def test_flip_replay_does_not_attribute_a_flip_the_live_pipeline_drops(tmp_path):
    """F-7. `flip_replay` re-runs `select_slots` without a `con`, so quiet-thread
    classification degraded on BOTH slates — symmetric in mechanism, but not
    verdict-preserving: a cluster the live pipeline removes before selection
    became a counted flip, and metric-M1's T3 bound (mean <= 1.0, any run <= 2)
    is a review trigger fed by that count.

    Measured on the batch as first built: live delta [], replay flips 1,
    attributed_to_steering TRUE."""
    con = _con(tmp_path)
    try:
        eid, clusters, items_by_id = _quiet_zero_board(con)
        prior = ranking._prior_edition(con, "2026-08-08")
        armed = steering.derive(steering.live_moves(con),
                                steering.watched_entities(con), armed=True)
        assert eid in armed.weighted_entities        # it WOULD weigh, if reachable

        # THE LIVE MEASUREMENT both slates have to agree with. The dark call runs
        # on `clusters` itself — it is THE RUN, and the envelope below is built
        # from the objects it stamped, exactly as run_rank does (:2660/:2668).
        dark_live, _ = ranking.select_slots(
            clusters, items_by_id, set(), con=con, prior_edition=prior,
            state=steering.INERT)
        armed_live, _ = ranking.select_slots(
            [dict(c) for c in clusters], items_by_id, set(), con=con,
            prior_edition=prior, state=armed)
        assert [s.story_title for s in dark_live] == ["Fed decision"]
        assert [s.story_title for s in armed_live] == \
               [s.story_title for s in dark_live]            # live delta = []

        class _Cfg:
            interests_broad = []
            interests_granular = []
            followed_analyst_sources = []
        env = ranking._replay_envelope("p", list(items_by_id.values()), [], [],
                                       _Cfg(), "w", "2026-08-08", clusters)
        out = replay.flip_replay(con, env, armed)
        assert out["quiet_receipt"] == "envelope"
        assert out["quiet_zero_excluded"] == ["Quiet cluster repeats itself again"]
        assert out["flips"] == 0
        assert out["gained"] == [] and out["lost"] == []
        assert out["attributed_to_steering"] is False
    finally:
        con.close()


def test_a_pre_receipt_envelope_is_named_non_attributable(tmp_path):
    """The bound, stated in the instrument rather than in a report. An envelope
    written before the quiet-zero receipt existed cannot be corrected after the
    fact — the ledger and the prior edition have moved on. So the replay says
    which class it is in, and metric-M1's readout has something to exclude
    instead of a number that looks like every other number."""
    con = _con(tmp_path)
    try:
        _eid, clusters, items_by_id = _quiet_zero_board(con)
        prior = ranking._prior_edition(con, "2026-08-08")
        ranking.select_slots(clusters, items_by_id, set(), con=con,
                             prior_edition=prior)

        class _Cfg:
            interests_broad = []
            interests_granular = []
            followed_analyst_sources = []
        env = ranking._replay_envelope("p", list(items_by_id.values()), [], [],
                                       _Cfg(), "w", "2026-08-08", clusters)
        assert [c["quiet_zero"] for c in env["clusters"]] == [False, True]
        for c in env["clusters"]:                # the pre-fix envelope shape
            c.pop("quiet_zero")
        out = replay.flip_replay(con, env)
        assert out["quiet_receipt"] == "absent"
        assert "not attributable" in out["caveat"].lower()
        assert out["quiet_zero_excluded"] == []
    finally:
        con.close()
