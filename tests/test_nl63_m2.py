"""NL-63 M2 — the selection layer + the amended slot contract + the gate's
M2-pinned memory fixes (implementer-written contract tests; QA extends
adversarially — selection + trust surfaces).

Offline, deterministic, no network, no spend. Covers:
  * item 1 — the amended slot contract (6-7 stories, min-6 floor, thin-day);
  * item 2 — the fragmentation cap (one prominent slot per arc + tripwire);
  * item 3 — quiet-thread demotion (zero -> Following only; small -> snippet);
  * item 4a — year-agnostic cite resolution (cross-year + ambiguity fails closed);
  * item 4e — regen-dedup by writing slot (rephrased regen dedups; split doesn't).
"""

from __future__ import annotations

import json

import pytest

from newslens import db, memory_core, ranking


# --- local helpers (fixtures don't import across test modules) --------------

def _item(id, outlet="Outlet", source_type="rss", wire=0):
    return {"id": id, "outlet": outlet, "source_type": source_type,
            "wire_syndication_flag": wire}


def _cluster(ids, title="Story", summary="Summary.", tags=(), memory=(),
             impact=5, reason="Reason here."):
    return {"story_title": title, "summary": summary, "item_ids": list(ids),
            "matched_tags": [dict(t) for t in tags], "matched_memory": list(memory),
            "world_impact": impact, "world_impact_reason": reason}


TOPIC = ({"name": "world", "level": "topic"},)

# Distinct, non-overlapping headlines so the same-story dedupe never collapses
# them (each carries unique significant tokens).
DISTINCT = [
    ("Fed holds interest rates steady", "Central bank pauses tightening."),
    ("Chip export controls tighten", "Commerce widens semiconductor curbs."),
    ("Grain corridor deal renewed", "Black Sea shipping resumes exports."),
    ("Supreme Court docket reshuffled", "Justices grant certiorari petitions."),
    ("Power grid strains under heat", "Utilities warn of rolling outages."),
    ("Housing starts rebound sharply", "Builders cite falling mortgage costs."),
    ("Airline merger clears review", "Regulators approve carrier consolidation."),
    ("Wildfire evacuations expand west", "Crews battle mountain blazes."),
    ("Pension fund overhaul advances", "Legislators debate retirement reform."),
    ("Vaccine trial reports results", "Researchers publish efficacy data."),
]


def _distinct_clusters(n, impact=7):
    return [_cluster([i + 1], title=DISTINCT[i][0], summary=DISTINCT[i][1],
                     tags=TOPIC, impact=impact) for i in range(n)]


def _items_for(clusters):
    return {i: _item(i) for c in clusters for i in c["item_ids"]}


@pytest.fixture
def con(tmp_path):
    p = tmp_path / "m2.db"
    db.migrate(db_path=p)
    c = db.connect(p)
    yield c
    c.close()


def _seed_thread_with_ledger(con, topic, date="2026-07-05"):
    now = "2026-07-01T00:00:00.000Z"
    cur = con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES (?, 'active', ?, ?, ?)", (topic, now, now, now))
    tid = cur.lastrowid
    con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, verdict,"
        " what_happened, significance, cites_json) VALUES (?, ?, 'advances',"
        " 'A dated development.', 'Changed the frame.', '[\"S1\"]')", (tid, date))
    con.commit()
    return tid


def _seed_prior_edition(con, date, stories):
    con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                (date, json.dumps(stories)))
    con.commit()


# --- item 1: the amended slot contract --------------------------------------

def test_slot_count_clamps_to_seven_on_a_rich_day():
    clusters = _distinct_clusters(10)
    slots, meta = ranking.select_slots(clusters, _items_for(clusters), set())
    assert len(slots) == 7
    assert meta["slot_contract"]["count"] == 7
    assert meta["slot_contract"]["thin_day"] is False


def test_thin_day_ships_fewer_with_a_disclosure_never_padded():
    clusters = _distinct_clusters(3)                        # only 3 candidates
    slots, meta = ranking.select_slots(clusters, _items_for(clusters), set())
    assert len(slots) == 3                                  # never padded to the floor
    assert meta["slot_contract"]["thin_day"] is True
    assert meta["slot_contract"]["floor"] == 6


# --- item 2: the fragmentation cap ------------------------------------------

def test_same_arc_siblings_get_one_prominent_slot():
    """Two clusters on the SAME thread -> only ONE lands in the analyst tier
    (top 3); the sibling demotes below it (non-destructive — still surfaced)."""
    clusters = [
        _cluster([1], title="Iran strikes oil tankers offshore",
                 summary="Naval clash escalates.", tags=TOPIC,
                 memory=("Hormuz",), impact=9),
        _cluster([2], title="Iran pulls crude export waiver",
                 summary="Sanctions relief revoked.", tags=TOPIC,
                 memory=("Hormuz",), impact=9),
        _cluster([3], title="Fed holds interest rates steady",
                 summary="Central bank pauses.", tags=TOPIC, impact=8),
        _cluster([4], title="Supreme Court docket reshuffled",
                 summary="Justices grant petitions.", tags=TOPIC, impact=8),
    ]
    slots, meta = ranking.select_slots(clusters, _items_for(clusters), set(),
                                       memory_steers=True)
    analyst = [s for s in slots if s.slot <= ranking.ANALYST_TIER_SLOTS]
    hormuz_in_analyst = [s for s in analyst if "Hormuz" in s.matched_memory]
    assert len(hormuz_in_analyst) == 1                     # one prominent slot per arc
    assert any(d["reason"].startswith("same-arc sibling")
               for d in meta["fragmentation"]["demotions"])


def test_tripwire_flags_a_no_thread_family_never_folds():
    """Rook's tripwire: two thread-distinct analyst slots sharing proper nouns
    are FLAGGED as a suspected family (never merged)."""
    clusters = [
        _cluster([1], title="Gaza ceasefire talks collapse in Cairo",
                 summary="Hamas Israel Cairo mediators.", tags=TOPIC, impact=9),
        _cluster([2], title="Gaza aid convoy blocked near Cairo crossing",
                 summary="Hamas Israel Cairo trucks.", tags=TOPIC, impact=8),
        _cluster([3], title="Unrelated market story", summary="Stocks rose.",
                 tags=TOPIC, impact=7),
    ]
    slots, meta = ranking.select_slots(clusters, _items_for(clusters), set())
    flags = meta["fragmentation"]["family_flags"]
    assert flags and flags[0]["slots"] == [1, 2]
    # flagged, not folded — both stories still present
    assert len(slots) == 3


# --- item 3: quiet-thread demotion ------------------------------------------

def test_quiet_zero_thread_leaves_today_following_only(con):
    tid = _seed_thread_with_ledger(con, "Hormuz")
    _seed_prior_edition(con, "2026-07-12", [
        {"story_title": "Hormuz tanker blockade military conflict",
         "summary": "Iran blockade Tehran naval."}])
    clusters = [
        # a verbatim re-surface of the prior story (Jaccard 1.0 >= ZERO)
        _cluster([1], title="Hormuz tanker blockade military conflict",
                 summary="Iran blockade Tehran naval.", memory=("Hormuz",), impact=9),
        _cluster([2], title="Fed holds rates", summary="Central bank pause.",
                 tags=TOPIC, impact=7),
        _cluster([3], title="Court ruling lands", summary="Justices decide.",
                 tags=TOPIC, impact=6),
    ]
    prior = ranking._prior_edition(con, "2026-07-13")
    slots, meta = ranking.select_slots(clusters, _items_for(clusters), set(),
                                       con=con, prior_edition=prior)
    titles = [s.story_title for s in slots]
    assert "Hormuz tanker blockade military conflict" not in titles   # dropped
    assert meta["quiet_threads"]["following_only"]
    assert "no movement since" in meta["quiet_threads"]["following_only"][0]["note"]


def test_quiet_small_thread_demotes_to_a_still_tracking_snippet(con):
    _seed_thread_with_ledger(con, "Hormuz")
    _seed_prior_edition(con, "2026-07-12", [
        {"story_title": "Hormuz tanker blockade military conflict",
         "summary": "Iran blockade Tehran naval."}])
    clusters = [
        _cluster([1], title="Fed holds interest rates steady",
                 summary="Central bank pauses.", tags=TOPIC, impact=9),
        _cluster([2], title="Supreme Court docket reshuffled",
                 summary="Justices grant petitions.", tags=TOPIC, impact=8),
        _cluster([3], title="Grain corridor deal renewed",
                 summary="Black Sea exports resume.", tags=TOPIC, impact=7),
        # partial overlap (~0.36, between SMALL and ZERO) -> still-tracking
        _cluster([4], title="Hormuz tanker blockade military",
                 summary="Fresh update report.", tags=TOPIC,
                 memory=("Hormuz",), impact=6),
    ]
    prior = ranking._prior_edition(con, "2026-07-13")
    slots, meta = ranking.select_slots(clusters, _items_for(clusters), set(),
                                       con=con, prior_edition=prior,
                                       memory_steers=True)
    still = [s for s in slots if s.still_tracking]
    assert len(still) == 1
    assert still[0].story_title.startswith("Hormuz")
    assert still[0].slot > ranking.ANALYST_TIER_SLOTS      # never a prominent slot
    assert "no movement since" in still[0].still_tracking_note


def test_untracked_thread_is_never_quiet(con):
    """A candidate matching a thread with NO ledger (brand new / cold start) is
    never demoted as quiet, even if it echoes the prior edition."""
    _seed_prior_edition(con, "2026-07-12", [
        {"story_title": "Hormuz tanker blockade military conflict",
         "summary": "Iran blockade Tehran naval."}])
    clusters = [
        _cluster([1], title="Hormuz tanker blockade military conflict",
                 summary="Iran blockade Tehran naval.", memory=("Hormuz",), impact=9),
        _cluster([2], title="Fed holds rates", tags=TOPIC, impact=7),
    ]
    prior = ranking._prior_edition(con, "2026-07-13")
    slots, meta = ranking.select_slots(clusters, _items_for(clusters), set(),
                                       con=con, prior_edition=prior)
    assert "Hormuz tanker blockade military conflict" in [s.story_title for s in slots]
    assert not meta["quiet_threads"]["following_only"]


# --- item 4a: year-agnostic cite resolution ---------------------------------

def test_human_cite_resolves_against_the_ledgers_actual_year():
    """From 2027 a '(Jul 10)' cite must resolve to 2027-07-10 when that is the
    edition in the record — never a hardcoded 2026."""
    resolved, unresolved, ambiguous = memory_core._resolve_cites(
        "The state stands (Jul 10).", {"2027-07-10"})
    assert resolved == {"2027-07-10"} and not unresolved and not ambiguous


def test_ambiguous_human_cite_fails_closed():
    resolved, unresolved, ambiguous = memory_core._resolve_cites(
        "As of (Jul 10).", {"2026-07-10", "2027-07-10"})
    assert ambiguous == {"07-10"} and not resolved
    with pytest.raises(memory_core.StateRejected, match="ambiguous, fails closed"):
        memory_core.validate_state("As of (Jul 10).", {"2026-07-10", "2027-07-10"})


def test_validate_state_accepts_a_cross_year_human_cite():
    clean, warns = memory_core.validate_state(
        "The confrontation holds (Jul 10).", {"2027-07-10"})
    assert clean.startswith("The confrontation holds")


# --- item 4e: regen-dedup by writing slot -----------------------------------

def _seed_thread(con, topic="Hormuz"):
    now = "2026-07-01T00:00:00.000Z"
    cur = con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES (?, 'active', ?, ?, ?)", (topic, now, now, now))
    con.commit()
    return cur.lastrowid


def _arc_doc(what, signif="It shifted the frame.", cites=("S1",)):
    return {"brief": {"arc": {"delta": "advances", "what_happened": what,
                              "significance": signif, "cites": list(cites)}}}


def test_rephrased_same_slot_regeneration_dedups(con):
    """A same-day full refresh that REPHRASES the arc must NOT double-write the
    ledger — the slot key catches the rephrase the what_happened key missed."""
    tid = _seed_thread(con)
    slot = {"slot": 1, "matched_memory": ["Hormuz"]}
    memory_core.write_deltas_for_edition(
        con, "2026-07-13", None, {1: _arc_doc("Iran struck the tanker.")}, [slot])
    memory_core.write_deltas_for_edition(
        con, "2026-07-13", None, {1: _arc_doc("Iran attacked the vessel.")}, [slot])
    n = con.execute("SELECT COUNT(*) c FROM thread_deltas WHERE thread_id=?",
                    (tid,)).fetchone()["c"]
    assert n == 1                                          # one, not two


def test_sanctioned_split_different_slots_both_land(con):
    """BUG-27 preserved: two DISTINCT same-day developments for one thread from
    DIFFERENT slots both record (the slot key discriminates them)."""
    tid = _seed_thread(con)
    slots = [{"slot": 1, "matched_memory": ["Hormuz"]},
             {"slot": 3, "matched_memory": ["Hormuz"]}]
    briefs = {1: _arc_doc("The strikes began."),
              3: _arc_doc("Talks survived the strikes.")}
    memory_core.write_deltas_for_edition(con, "2026-07-13", None, briefs, slots)
    n = con.execute("SELECT COUNT(*) c FROM thread_deltas WHERE thread_id=?",
                    (tid,)).fetchone()["c"]
    assert n == 2                                          # both distinct facets land
