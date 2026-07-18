"""NL-63 M2 QA — adversarial pass on the selection layer (the AMENDED slot
contract as ranker law) + the M1 gate's M2-pinned fixes (QA-written; extends
tests/test_nl63_m2.py). Fully offline: model calls injected, server rendered
in-process, ZERO consumption events, $0.

Adversarial focus per dispatch: (1) contract arithmetic + edges (thin day /
floor day / analyst-tier refill / retired slot-3 demotion vs archived data);
(2) fragmentation cap details (multi-thread stories, tripwire FP class);
(3) quiet-thread demotion (precedence vs the urgency override, Following
integrity, note date honesty, fail-open on corrupt priors); (4) year-agnostic
cites at the year boundary (Dec/Jan, ISO-wrong-year); (5) ordering — the
post-persist memory-failure window; (6) regen-dedup NULL-slot seed shape
(the A'-row fallback); (7) the split-day render contract (4f residual — QA
RULING: needed; red below).

KNOWN-RED (acceptance contracts; fix contracts in each docstring):
  BUG-33  exhausted analyst pool: same-arc siblings positionally occupy
          full-picture slots 2-3 with no under-fill disclosure — the demotion
          log says "demoted out of the prominent tier" while position
          contradicts it.
  BUG-34  post-persist memory-pass failure crashes a PUBLISHED edition: the
          raise propagates as a non-GenerateError (no generation_log entry),
          the artifact never writes, and the error panel's "Today's edition
          failed" claim is false — the edition is on the record.
  BUG-35  split-day arc duplication: two same-thread slots in one edition
          render the IDENTICAL arc line under both stories (per-slot `seen`
          set only). QA ruling on the 4f residual: the arc renders ONCE per
          edition, under the most prominent same-thread slot; the kill-test
          stays per-story (scope ruling respected).

Calibration note (real data, read-only): cross-day Jaccard over the four real
editions puts the one true same-story re-surface at J=0.692 (OPEC+ Jul 5->6)
and every genuine same-thread development at J<=0.261 — the 0.60/0.35 knobs
sit inside a wide measured gap. See the QA report for the knob assessment.
"""

from __future__ import annotations

import json
import sqlite3
import time

import pytest

from newslens import analysis, db, generate, memory_core, paths, ranking, server
from test_generate import (compliant_script, seed_briefing, slot,
                           stories_payload)

DATE = "2026-07-07"
ENV = {"OPENAI_API_KEY": "sk-qa-fake"}
TOPIC = ({"name": "world", "level": "topic"},)


# --- local helpers (mirroring tests/test_nl63_m2.py; fixtures stay local) ---

def _item(id, outlet="Outlet"):
    return {"id": id, "outlet": outlet, "source_type": "rss",
            "wire_syndication_flag": 0}


def _cluster(ids, title="Story", summary="Summary.", tags=(), memory=(),
             impact=5, reason="Reason here."):
    return {"story_title": title, "summary": summary, "item_ids": list(ids),
            "matched_tags": [dict(t) for t in tags],
            "matched_memory": list(memory),
            "world_impact": impact, "world_impact_reason": reason}


def _items_for(clusters):
    return {i: _item(i) for c in clusters for i in c["item_ids"]}


DISTINCT = [
    ("Fed holds interest rates steady", "Central bank pauses tightening."),
    ("Chip export controls tighten", "Commerce widens semiconductor curbs."),
    ("Grain corridor deal renewed", "Black Sea shipping resumes exports."),
    ("Supreme Court docket reshuffled", "Justices grant certiorari petitions."),
    ("Power grid strains under heat", "Utilities warn of rolling outages."),
    ("Housing starts rebound sharply", "Builders cite falling mortgage costs."),
    ("Airline merger clears review", "Regulators approve carrier consolidation."),
]


def _distinct_clusters(n, impact=7, start_id=1):
    return [_cluster([start_id + i], title=DISTINCT[i][0], summary=DISTINCT[i][1],
                     tags=TOPIC, impact=impact) for i in range(n)]


@pytest.fixture
def con(tmp_path):
    p = tmp_path / "m2qa.db"
    db.migrate(db_path=p)
    c = db.connect(p)
    yield c
    c.close()


def _seed_thread(con, topic, ledger_dates=("2026-07-05",), slot_vals=None):
    now = "2026-07-01T00:00:00.000Z"
    cur = con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES (?, 'active', ?, ?, ?)", (topic, now, now, now))
    tid = cur.lastrowid
    for i, d in enumerate(ledger_dates):
        sv = None if slot_vals is None else slot_vals[i]
        con.execute(
            "INSERT INTO thread_deltas (thread_id, edition_date, verdict,"
            " what_happened, significance, cites_json, slot)"
            " VALUES (?, ?, 'advances', ?, ?, '[\"S1\"]', ?)",
            (tid, d, f"A dated development on {d}.",
             "A pricing dispute framed the strait.", sv))
    con.commit()
    return tid


def _seed_prior_edition(con, date, stories):
    con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                (date, json.dumps(stories)))
    con.commit()


PRIOR_HORMUZ = {"story_title": "Hormuz tanker blockade military conflict",
                "summary": "Iran blockade Tehran naval."}


# ===========================================================================
# 1. The amended contract — arithmetic and edges
# ===========================================================================

def test_five_story_material_day_ships_five_thin_flagged_never_padded():
    """A 5-story material day ships FIVE — under the 6 floor, flagged thin,
    and the output is exactly the input stories (nothing padded or invented
    to reach the floor — Rook's thin-day rule as ruled in DECISIONS
    2026-07-13 item 2)."""
    clusters = _distinct_clusters(5)
    slots, meta = ranking.select_slots(clusters, _items_for(clusters), set())
    assert len(slots) == 5
    assert meta["slot_contract"]["thin_day"] is True
    in_titles = {c["story_title"] for c in clusters}
    assert all(s.story_title in in_titles for s in slots)  # never invented


def test_six_story_day_meets_the_floor_exactly_no_thin_flag():
    clusters = _distinct_clusters(6)
    slots, meta = ranking.select_slots(clusters, _items_for(clusters), set())
    assert len(slots) == 6
    assert meta["slot_contract"]["thin_day"] is False


def test_analyst_tier_refills_to_exactly_three_past_demoted_siblings():
    """TWO same-arc siblings ranked #2/#3 must not shrink the analyst tier:
    the refill walks past them and the tier lands EXACTLY 3, thread-distinct;
    the siblings surface below it (non-destructive) with logged demotions."""
    clusters = [
        _cluster([1], title="Iran strikes oil tankers offshore",
                 summary="Naval clash escalates.", tags=TOPIC,
                 memory=("Hormuz",), impact=10),
        _cluster([2], title="Iran pulls crude export waiver",
                 summary="Sanctions relief revoked.", tags=TOPIC,
                 memory=("Hormuz",), impact=9),
        _cluster([3], title="Tehran conscription drive widens",
                 summary="Reserve callups accelerate nationwide.", tags=TOPIC,
                 memory=("Hormuz",), impact=8),
        _cluster([4], title="Fed holds interest rates steady",
                 summary="Central bank pauses tightening.", tags=TOPIC, impact=7),
        _cluster([5], title="Supreme Court docket reshuffled",
                 summary="Justices grant certiorari petitions.", tags=TOPIC, impact=6),
        _cluster([6], title="Grain corridor deal renewed",
                 summary="Black Sea shipping resumes exports.", tags=TOPIC, impact=5),
    ]
    slots, meta = ranking.select_slots(clusters, _items_for(clusters), set(),
                                       memory_steers=True)
    analyst = [s for s in slots if s.slot <= ranking.ANALYST_TIER_SLOTS]
    assert len(analyst) == 3                       # refilled, never shrunk
    hormuz = [s for s in analyst if "Hormuz" in s.matched_memory]
    assert len(hormuz) == 1                        # one prominent slot per arc
    assert {s.story_title for s in analyst} == {
        "Iran strikes oil tankers offshore", "Fed holds interest rates steady",
        "Supreme Court docket reshuffled"}
    demo_reasons = [d["reason"] for d in meta["fragmentation"]["demotions"]]
    assert sum(r.startswith("same-arc sibling") for r in demo_reasons) == 2
    assert len(slots) == 6                         # nothing dropped


def test_BUG33_exhausted_pool_sibling_in_analyst_tier_must_be_disclosed():
    """RED (acceptance contract). A monoculture day — every candidate on ONE
    causal arc — exhausts the thread-distinct pool: _apply_thread_cap promotes
    only the first entry, then the 'demoted' siblings positionally re-enter
    slots 2-3 anyway. Two lies result: (a) the ratified fragmentation law (one
    slot per causal arc, sibling never re-ledes — NL-61/62 item D, v1 split
    cap = 1) is violated at full-picture prominence; (b) the demotions log
    says "demoted out of the prominent tier" for entries that SIT in the
    prominent tier.

    FIX CONTRACT (either satisfies this red):
      * meta["fragmentation"]["tier_underfilled"] is truthy whenever fewer
        than ANALYST_TIER_SLOTS thread-distinct/non-quiet entries could be
        promoted AND a colliding/quiet entry occupies an analyst-tier
        position — plus a run-report warning naming the sibling(s); the
        demotion log entries for in-tier siblings must say so honestly; OR
      * the selection layer carries per-slot tier downgrades so a sibling in
        positions 2-3 is In Brief by DATA, not full-picture by position (the
        cross-layer fix — bigger; gate's call).
    Reachability is low (needs a near-monoculture pool) but the failure is
    exactly the Jul-8 shape the principal reviewed against (NL-58 item 12).
    """
    clusters = [
        _cluster([1], title="Iran strikes oil tankers offshore",
                 summary="Naval clash escalates.", tags=TOPIC,
                 memory=("Hormuz",), impact=9),
        _cluster([2], title="Iran pulls crude export waiver",
                 summary="Sanctions relief revoked.", tags=TOPIC,
                 memory=("Hormuz",), impact=8),
        _cluster([3], title="Tehran conscription drive widens",
                 summary="Reserve callups accelerate nationwide.", tags=TOPIC,
                 memory=("Hormuz",), impact=7),
    ]
    slots, meta = ranking.select_slots(clusters, _items_for(clusters), set(),
                                       memory_steers=True)
    analyst = [s for s in slots if s.slot <= ranking.ANALYST_TIER_SLOTS]
    arcs_in_tier = [s for s in analyst if "Hormuz" in s.matched_memory]
    if len(arcs_in_tier) > 1:
        assert meta["fragmentation"].get("tier_underfilled"), (
            "same-arc siblings occupy analyst-tier positions with no "
            "under-fill disclosure — the demotion log claims the opposite")


def test_multi_thread_story_reserves_every_matched_arc():
    """A story matching TWO arcs reserves BOTH: a later candidate sharing
    EITHER thread demotes (conservative — one prominent slot per causal arc
    applies per arc, not per exact thread-set)."""
    clusters = [
        _cluster([1], title="Iran strikes oil tankers offshore",
                 summary="Naval clash escalates.", tags=TOPIC,
                 memory=("Iran war", "Hormuz"), impact=10),
        _cluster([2], title="Insurers reroute tanker traffic",
                 summary="Premiums spike on gulf transits.", tags=TOPIC,
                 memory=("Hormuz", "Shipping"), impact=9),
        _cluster([3], title="Container rates jump on rerouting",
                 summary="Freight indexes climb sharply.", tags=TOPIC,
                 memory=("Shipping",), impact=8),
        _cluster([4], title="Fed holds interest rates steady",
                 summary="Central bank pauses tightening.", tags=TOPIC, impact=7),
        _cluster([5], title="Supreme Court docket reshuffled",
                 summary="Justices grant certiorari petitions.", tags=TOPIC, impact=6),
    ]
    slots, meta = ranking.select_slots(clusters, _items_for(clusters), set(),
                                       memory_steers=True)
    analyst_titles = [s.story_title for s in slots
                      if s.slot <= ranking.ANALYST_TIER_SLOTS]
    # cluster 2 collides on Hormuz -> demoted; cluster 3's Shipping arc was
    # NOT reserved (2 never promoted), so 3 promotes.
    assert analyst_titles == ["Iran strikes oil tankers offshore",
                              "Container rates jump on rerouting",
                              "Fed holds interest rates steady"]
    demoted = [d for d in meta["fragmentation"]["demotions"]
               if d["story"] == "Insurers reroute tanker traffic"]
    assert demoted and demoted[0]["threads"] == ["Hormuz"]


def test_tripwire_fires_on_shared_capitalized_furniture_documented_fp():
    """KNOB documentation (day-14 read): _proper_nouns counts ANY capitalized
    len>=4 token — sentence-leading words and weekdays included. Two genuinely
    DISTINCT stories sharing {President, Trump, Wednesday}-class furniture
    trip the >=3 threshold. Pinned GREEN because the design is flag-never-fold
    (warn-only, zero reader impact) — but this is the measured false-positive
    class the day-14 threshold read must weigh (see QA report)."""
    clusters = [
        _cluster([1], title="President Trump signs Wednesday trade order",
                 summary="Tariff schedule shifts.", tags=TOPIC, impact=9),
        _cluster([2], title="President Trump hosts Wednesday budget summit",
                 summary="Spending caps debated.", tags=TOPIC, impact=8),
        _cluster([3], title="Grain corridor deal renewed",
                 summary="Black Sea shipping resumes exports.", tags=TOPIC, impact=7),
    ]
    slots, meta = ranking.select_slots(clusters, _items_for(clusters), set())
    flags = meta["fragmentation"]["family_flags"]
    assert flags, "expected the documented FP shape to trip the >=3 threshold"
    assert {"president", "trump", "wednesday"} <= set(flags[0]["shared"])
    assert len(slots) == 3                          # flagged, NEVER folded


# ===========================================================================
# 2. Quiet-thread demotion — precedence, integrity, honesty
# ===========================================================================

def test_quiet_zero_drop_wins_over_the_urgency_override_precedence_pinned(con):
    """PRECEDENCE PIN (dispatch item 3): a tracked thread's zero-delta
    re-surface is dropped from Today even when its world_impact clears the
    urgency-override bar — demotion runs BEFORE the override pool forms, so
    NL-57 ("don't re-surface the same story") outranks the override's
    "unmissable world event" claim for content that is textually yesterday's.
    Proven both ways: the SAME candidate with no prior edition DOES fire the
    override. The failure mode this accepts: a day-2 mega-story whose cluster
    text reads like day-1's (J>=0.60) leaves Today entirely (disclosed,
    Following keeps it). Real-data calibration puts genuine developments at
    J<=0.261 — but this pin is the knob's teeth; if the day-14 read shows a
    real story dropped, the knob (not this precedence) is the first suspect.
    Escalation to the principal rides the QA report."""
    _seed_thread(con, "Hormuz")
    _seed_prior_edition(con, "2026-07-06", [PRIOR_HORMUZ])
    clusters = _distinct_clusters(6)
    clusters.append(_cluster([9], title=PRIOR_HORMUZ["story_title"],
                             summary=PRIOR_HORMUZ["summary"],
                             memory=("Hormuz",), impact=10))  # p=0: no tags
    items = _items_for(clusters)
    prior = ranking._prior_edition(con, DATE)
    slots, meta = ranking.select_slots(clusters, items, set(),
                                       con=con, prior_edition=prior)
    assert PRIOR_HORMUZ["story_title"] not in [s.story_title for s in slots]
    assert meta["override"]["fired"] is False
    assert meta["quiet_threads"]["following_only"][0]["story"] == \
        PRIOR_HORMUZ["story_title"]
    # counterfactual: same inputs, no prior edition -> the override DOES fire
    slots2, meta2 = ranking.select_slots(clusters, items, set(),
                                         con=con, prior_edition=None)
    assert meta2["override"]["fired"] is True
    assert PRIOR_HORMUZ["story_title"] in [s.story_title for s in slots2]


def test_quiet_zero_drop_leaves_the_following_surface_intact(con):
    """NL-57's other half: the dropped thread stays fully visible under
    Following — active status, state card fields, ledger-backed last delta.
    The drop is a Today-selection measure, never a lifecycle write."""
    tid = _seed_thread(con, "Hormuz")
    con.execute(
        "INSERT INTO thread_state (thread_id, briefing_id, as_of_date,"
        " state_text, cites_json, created_at) VALUES (?, NULL, '2026-07-05',"
        " 'The standoff is economic, not military (Jul 5).', '[\"2026-07-05\"]',"
        " '2026-07-05T00:00:00.000Z')", (tid,))
    con.commit()
    _seed_prior_edition(con, "2026-07-06", [PRIOR_HORMUZ])
    clusters = _distinct_clusters(6)
    clusters.append(_cluster([9], title=PRIOR_HORMUZ["story_title"],
                             summary=PRIOR_HORMUZ["summary"],
                             memory=("Hormuz",), impact=9))
    prior = ranking._prior_edition(con, DATE)
    before = con.execute("SELECT status, topic FROM memory WHERE id=?",
                         (tid,)).fetchone()
    slots, meta = ranking.select_slots(clusters, _items_for(clusters), set(),
                                       con=con, prior_edition=prior)
    assert meta["quiet_threads"]["following_only"]      # dropped from Today
    after = con.execute("SELECT status, topic FROM memory WHERE id=?",
                        (tid,)).fetchone()
    assert (after["status"], after["topic"]) == (before["status"], before["topic"])
    rows = server._following_rows(con)
    active = [r for r in rows["active"] if r["topic"] == "Hormuz"]
    assert active, "quiet-dropped thread vanished from Following"
    assert "economic, not military" in active[0]["state_text"]
    assert active[0]["last_delta"] is not None


def test_still_tracking_note_carries_the_latest_ledger_date(con):
    """Date honesty: the note anchors to the thread's LATEST ledger entry
    (ledger is oldest-first; entries[-1]), not the first or the prior
    edition's date."""
    _seed_thread(con, "Hormuz", ledger_dates=("2026-07-03", "2026-07-05"))
    _seed_prior_edition(con, "2026-07-06", [PRIOR_HORMUZ])
    clusters = [
        _cluster([1], title="Fed holds interest rates steady",
                 summary="Central bank pauses tightening.", tags=TOPIC, impact=9),
        _cluster([2], title="Supreme Court docket reshuffled",
                 summary="Justices grant certiorari petitions.", tags=TOPIC, impact=8),
        _cluster([3], title="Grain corridor deal renewed",
                 summary="Black Sea shipping resumes exports.", tags=TOPIC, impact=7),
        # partial overlap vs the prior story: J in [0.35, 0.60) -> small
        _cluster([4], title="Hormuz tanker blockade military",
                 summary="Fresh update report.", tags=TOPIC,
                 memory=("Hormuz",), impact=6),
    ]
    prior = ranking._prior_edition(con, DATE)
    slots, _ = ranking.select_slots(clusters, _items_for(clusters), set(),
                                    con=con, prior_edition=prior,
                                    memory_steers=True)
    still = [s for s in slots if s.still_tracking]
    assert len(still) == 1
    assert still[0].still_tracking_note == "no movement since Jul 5"


def test_still_tracking_flags_ride_into_persisted_story_slots(con):
    """The still-tracking register renders at READ time (the v7 UI build,
    queued after M2/M3) — so the persisted slot JSON is the register's data
    contract. persist() must carry both fields into briefings.story_slots."""
    _seed_thread(con, "Hormuz")
    _seed_prior_edition(con, "2026-07-06", [PRIOR_HORMUZ])
    clusters = [
        _cluster([1], title="Fed holds interest rates steady",
                 summary="Central bank pauses tightening.", tags=TOPIC, impact=9),
        _cluster([2], title="Hormuz tanker blockade military",
                 summary="Fresh update report.", tags=TOPIC,
                 memory=("Hormuz",), impact=6),
    ]
    prior = ranking._prior_edition(con, DATE)
    slots, meta = ranking.select_slots(clusters, _items_for(clusters), set(),
                                       con=con, prior_edition=prior,
                                       memory_steers=True)
    assert any(s.still_tracking for s in slots)
    report = ranking.RankReport(date=DATE, slots=slots)
    ranking.persist(con, report, meta)
    stored = json.loads(con.execute(
        "SELECT story_slots FROM briefings WHERE date=?", (DATE,)
    ).fetchone()["story_slots"])
    flagged = [s for s in stored if s.get("still_tracking")]
    assert len(flagged) == 1
    assert flagged[0]["still_tracking_note"] == "no movement since Jul 5"
    # and the day-14 readout keys persist into ranking_runs.meta verbatim
    run_meta = json.loads(con.execute(
        "SELECT meta FROM ranking_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()["meta"])
    for key in ("slot_contract", "fragmentation", "quiet_threads"):
        assert key in run_meta


def test_quiet_drop_below_floor_reads_as_thin_day(con):
    """A quiet-zero drop can take a 6-candidate day under the floor: 5 ship,
    thin_day flags. PINNED with a nuance for the record: the run-level thin
    warning says "the material wasn't there", which on THIS shape is loose —
    the material existed and was consciously dropped (its own disclosure line
    says so). Two disclosures together tell the true story; wording cleanup
    is cosmetic (QA report, LOW)."""
    _seed_thread(con, "Hormuz")
    _seed_prior_edition(con, "2026-07-06", [PRIOR_HORMUZ])
    clusters = _distinct_clusters(5)
    clusters.append(_cluster([9], title=PRIOR_HORMUZ["story_title"],
                             summary=PRIOR_HORMUZ["summary"], tags=TOPIC,
                             memory=("Hormuz",), impact=9))
    prior = ranking._prior_edition(con, DATE)
    slots, meta = ranking.select_slots(clusters, _items_for(clusters), set(),
                                       con=con, prior_edition=prior,
                                       memory_steers=True)
    assert len(slots) == 5
    assert meta["slot_contract"]["thin_day"] is True
    assert meta["quiet_threads"]["following_only"]


def test_corrupt_prior_story_slots_fails_open_to_normal_selection(con):
    """_prior_edition returns None on shape-corrupt story_slots -> quiet
    classification disables -> normal selection (the pre-M2 behavior). Pinned
    as the chosen failure direction: failing OPEN re-surfaces a quiet thread
    prominently (an editorial miss), never drops a story on garbage data (a
    trust miss). Two notes made conscious here: (a) MALFORMED JSON cannot
    even enter the column — migration 0002's json_valid CHECK rejects it at
    the door (verified while writing this test) — so the guard's live class
    is valid-JSON-wrong-SHAPE, exercised below; (b) the corrupt row SHADOWS
    older intact editions (the query takes the newest row and gives up) —
    acceptable at personal scale."""
    _seed_thread(con, "Hormuz")
    with pytest.raises(sqlite3.IntegrityError):     # (a) malformed JSON: blocked
        con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                    ("2026-07-06", "{not json"))
    con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                ("2026-07-06", '"a valid-JSON string, not a slot list"'))
    con.commit()
    # (b) shape-corrupt: parses, yields ZERO stories -> classification empty.
    # (_prior_edition's None-on-parse-failure branch is DEAD for DB rows —
    # the CHECK guarantees loads succeeds; belt-and-braces, kept.)
    prior = ranking._prior_edition(con, DATE)
    assert prior is not None and prior["stories"] == []
    clusters = [
        _cluster([1], title=PRIOR_HORMUZ["story_title"],
                 summary=PRIOR_HORMUZ["summary"], memory=("Hormuz",), impact=9),
        _cluster([2], title="Fed holds interest rates steady",
                 summary="Central bank pauses tightening.", tags=TOPIC, impact=7),
    ]
    slots, meta = ranking.select_slots(
        clusters, _items_for(clusters), set(), con=con,
        prior_edition=ranking._prior_edition(con, DATE))
    assert PRIOR_HORMUZ["story_title"] in [s.story_title for s in slots]
    assert not meta["quiet_threads"]["following_only"]


# ===========================================================================
# 3. Year-agnostic cites — the year boundary (M1 gate F, DEADLINE class)
# ===========================================================================

def test_dec_jan_cross_year_cites_resolve_uniquely_each_side():
    """The DEADLINE scenario: a ledger straddling New Year. '(Dec 30)' has
    exactly one year in the record (2026) and '(Jan 5)' exactly one (2027) —
    both resolve; the state validates with no year hardcoded anywhere."""
    resolvable = {"2026-12-30", "2027-01-05"}
    resolved, unresolved, ambiguous = memory_core._resolve_cites(
        "Talks froze (Dec 30). They resumed (Jan 5).", resolvable)
    assert resolved == {"2026-12-30", "2027-01-05"}
    assert not unresolved and not ambiguous
    clean, _ = memory_core.validate_state(
        "Talks froze (Dec 30). They resumed (Jan 5).",
        ledger_dates=resolvable)
    assert clean.startswith("Talks froze")


def test_jan5_repeating_across_years_fails_closed():
    """'(Jan 5)' against a record carrying BOTH 2026-01-05 and 2027-01-05 is
    ambiguous — REJECTED, never guessed (the fail-closed pin the dispatch
    names). The reject message tells the writer the remedy (pin the year)."""
    both = {"2026-01-05", "2027-01-05"}
    _, _, ambiguous = memory_core._resolve_cites("As of (Jan 5).", both)
    assert ambiguous == {"01-05"}
    with pytest.raises(memory_core.StateRejected, match="ambiguous, fails closed"):
        memory_core.validate_state("As of (Jan 5).", both)


def test_iso_cite_wrong_year_rejects_as_fabrication_never_reanchors():
    """An ISO cite carries its year explicitly: '(2026-07-10)' against a
    record holding only 2027-07-10 is the fabrication class (a past the
    record never published) — it must NOT year-shift to the nearest match."""
    with pytest.raises(memory_core.StateRejected, match="fabrication"):
        memory_core.validate_state(
            "The strait closed (2026-07-10).", {"2027-07-10"})


# ===========================================================================
# 4. Regen-dedup — the NULL-slot A'-seed shape
# ===========================================================================

def _arc_doc(what):
    return {"brief": {"arc": {"delta": "advances", "what_happened": what,
                              "significance": "It shifted the frame.",
                              "cites": ["S1"]}}}


def test_null_slot_seed_exact_clause_stays_idempotent(con):
    """The A'-row shape: a hand-traced seed row with slot=NULL. A re-run
    producing the seed's EXACT clause at any slot dedups via the what_happened
    fallback (verified against the real rows on a scratch COPY — this is the
    committed sandbox twin). The known residual — a REPHRASED historical
    regen would double-write past a NULL-slot seed — is accepted per the
    implementer's disclosed reasoning (seeds are never regenerated); recorded
    in the QA report, not asserted here."""
    now = "2026-07-01T00:00:00.000Z"
    tid = con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES ('Hormuz', 'active', ?, ?, ?)",
        (now, now, now)).lastrowid
    seed_clause = "Iran's envoy offered special treatment in the Strait."
    con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, verdict,"
        " what_happened, significance, cites_json, slot) VALUES"
        " (?, '2026-07-05', 'advances', ?, 'Sig.', '[\"S1\"]', NULL)",
        (tid, seed_clause))
    con.commit()
    rep = memory_core.write_deltas_for_edition(
        con, "2026-07-05", None, {2: _arc_doc(seed_clause)},
        [{"slot": 2, "matched_memory": ["Hormuz"]}])
    assert any("already on file" in s for s in rep.skipped)
    n = con.execute("SELECT COUNT(*) c FROM thread_deltas WHERE thread_id=?",
                    (tid,)).fetchone()["c"]
    assert n == 1


# ===========================================================================
# 5. Ordering — the post-persist memory-failure window (dispatch item 5)
# ===========================================================================

@pytest.fixture
def fake_chat(monkeypatch):
    """Local clone of the m3_qa stateful fake (fixtures stay module-local):
    json calls serve the narrative (editor echoes), non-json the script."""
    state = type("S", (), {})()
    state.calls, state.narrative, state.script = [], None, None

    def chat(key, prompt, max_tokens, temperature, json_mode):
        state.calls.append({"json_mode": json_mode, "prompt": prompt})
        content = (json.dumps(state.narrative) if json_mode else state.script)
        return {"choices": [{"finish_reason": "stop",
                             "message": {"content": content}}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 200}}

    monkeypatch.setattr(generate, "_chat", chat)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return state


def _stage_fakes(monkeypatch):
    from newslens import ingest as ingest_mod

    def fake_ingest(con=None, env=None, **kw):
        r = type("R", (), {})()
        r.succeeded, r.attempted, r.items_new = ["A"], 1, 3
        r.discovery_status, r.degradation_message = "not attempted", None
        return r

    def fake_rank(date=None, con=None, env=None, **kw):
        slots = [slot(1), slot(2), slot(3)]
        seed_briefing(con, date, slots)
        r = type("R", (), {})()
        r.warnings = []
        return r

    def fake_analysis(**kw):
        return {"ts": "2026-07-07T05:00:00Z", "stage": "analysis",
                "date": DATE, "status": "ok", "model": "gpt-4o",
                "total_usd": 0.0, "derating": True,
                "warnings": [], "per_story": []}

    monkeypatch.setattr(ingest_mod, "run_ingest", fake_ingest)
    monkeypatch.setattr(ranking, "run_rank", fake_rank)
    monkeypatch.setattr(analysis, "run_analysis", fake_analysis)
    paths.SOURCES_FILE.write_text(
        "sources:\n  - name: The Hill\n    rss_url: https://x.example/f\n"
        "interests:\n  tags:\n    - AI regulation\n", encoding="utf-8")
    return [slot(1), slot(2), slot(3)]


def test_BUG34_post_persist_memory_failure_must_not_crash_a_published_edition(
        tmp_paths, fake_chat, monkeypatch):
    """RED (acceptance contract). The 4b reorder moved the memory pass into
    the window BETWEEN persist_generation (the edition is ON THE RECORD) and
    write_artifact. A raise there today: propagates as a non-GenerateError
    (run_generate's failed-run logger catches GenerateError only, so the
    generation log NEVER records the run), the artifact never writes, and the
    UI error panel claims "Today's edition failed" over an edition that in
    fact published — the exact claim-truth class the M8 gate fixed once
    already (NOTES item 23).

    FIX CONTRACT: a memory-pass failure after persist is DISCLOSED AND
    CONTAINED — the run completes (no raise), report.warnings carries a line
    naming the memory failure AND stating the edition is already published,
    the artifact still writes, and the run lands in the generation log with
    the warning; _fold_cost_steps semantics for any partial memory spend stay
    honest. (A failure BEFORE persist keeps its current abort behavior — the
    existing red proves no delta strands.)"""
    _stage_fakes(monkeypatch)
    slots3 = [slot(1), slot(2), slot(3)]
    fake_chat.narrative = stories_payload(slots3)
    fake_chat.script = compliant_script(slots3)

    def exploding_memory_pass(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(generate, "run_memory_pass", exploding_memory_pass)
    db.migrate()
    con = db.connect()
    try:
        rep = generate.run_generate(date=DATE, con=con, env=dict(ENV),
                                    refresh=True)
        # the edition must be on the record AND the failure disclosed
        row = con.execute("SELECT narrative_text FROM briefings WHERE date=?",
                          (DATE,)).fetchone()
        assert row and row["narrative_text"]
        assert any("memory" in w.lower() and "publish" in w.lower()
                   for w in rep.warnings), (
            "memory-pass failure after persist must be disclosed as "
            "edition-published-memory-failed")
        assert rep.artifact_path, "artifact export must still run"
    finally:
        con.close()


# ===========================================================================
# 6. The split-day render contract (4f residual — QA ruling: needed)
# ===========================================================================

def test_BUG35_same_thread_split_day_arc_renders_once_per_edition(tmp_paths):
    """v8-M2 form of the BUG-35 acceptance contract: on a sanctioned-split day
    (two same-thread slots in one edition — the real Jul-10 A' shape), the
    covered-before signal renders ONCE under the most prominent same-thread
    slot; the sibling suppresses its duplicate. The signal is now the slim
    memory STAMP (the arc PROSE is gone from Today entirely); the per-edition
    dedup set (keyed on thread id) carries the same anti-doubling guarantee the
    principal reviewed against (NL-58 item 11)."""
    db.migrate()
    con = db.connect()
    try:
        now = "2026-07-01T00:00:00.000Z"
        tid = con.execute(
            "INSERT INTO memory (topic, status, status_changed_at, created_at,"
            " updated_at) VALUES ('Strait of Hormuz', 'active', ?, ?, ?)",
            (now, now, now)).lastrowid
        con.execute(
            "INSERT INTO thread_deltas (thread_id, edition_date, verdict,"
            " what_happened, significance, cites_json, slot) VALUES"
            " (?, '2026-07-05', 'advances', 'Transit fees were imposed.',"
            " 'A pricing dispute framed the strait.', '[\"S1\"]', NULL)", (tid,))
        slots = [
            {"slot": "1", "story_title": "Strikes exchanged", "summary": "s1",
             "item_ids": [], "outlets": ["The Hill"], "matched_tags": [],
             "matched_memory": ["Strait of Hormuz"], "override": False,
             "corroboration_label": "Reported by 1 named outlet"},
            {"slot": "2", "story_title": "Talks survive strikes", "summary": "s2",
             "item_ids": [], "outlets": ["The Hill"], "matched_tags": [],
             "matched_memory": ["Strait of Hormuz"], "override": False,
             "corroboration_label": "Reported by 1 named outlet"},
        ]
        con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                    (DATE, json.dumps(slots)))
        con.commit()
        entry = {"ts": "2026-07-07T01:00:00Z", "date": DATE, "status": "ok",
                 "sample": False, "tiers": ["full", "medium"],
                 "stories": [
                     {"headline": "Strikes exchanged", "lede": "The strait closed."},
                     {"headline": "Talks survive strikes", "lede": "Channels held."},
                 ]}
        (paths.DATA_DIR / "generation_log.jsonl").write_text(
            json.dumps(entry) + "\n", encoding="utf-8")
        page, _ = server.build_page(con, DATE)
        assert "When we last covered this" not in page      # arc PROSE gone from Today
        assert page.count("entry on this thread") == 1, (
            "the identical memory stamp rendered under BOTH same-thread slots — "
            "per-edition dedup missing (prominent slot wins)")
    finally:
        con.close()


# ===========================================================================
# 7. Archived-data interactions (the retired demotion cannot resurface)
# ===========================================================================

def test_archived_headline_only_quick_story_still_renders_clean():
    """Pre-amendment archived rows carry headline-only quick stories (no
    why/watch). The amended assembler must render them without movements and
    without crashing — archive fidelity, not re-validation."""
    slots4 = [slot(i) for i in range(1, 5)]
    stories, _ = generate.validate_narrative_payload(
        stories_payload(slots4), slots4, "A")
    # simulate the ARCHIVED shape: strip the old-quick story's movements
    old = dict(stories[3])
    old.pop("why_it_matters", None)
    old.pop("watch_for", None)
    old.pop("why_label", None)
    old.pop("watch_label", None)
    archived = stories[:3] + [old]
    text = generate.assemble_narrative(DATE, "A", archived, _min_inputs(slots4))
    # three structured stories carry watch movements; the archived quick does not
    watch_labels = sum(text.count(f"**{w}:**") for w in generate.WATCH_FRAMINGS)
    assert watch_labels == 3
    assert "Rewritten headline 4" in text            # the story itself renders


def _min_inputs(slots4):
    return {
        "slots": [dict(s) for s in slots4],
        "memory": [], "prior": None, "tags": [],
        "briefs_by_slot": {}, "deep_views": {},
        "analyst_slot3_tier": None,
    }
