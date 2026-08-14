"""NL-151 — the fetch-SKIP contract (principal's ruling 2026-08-13).

His three laws, and where each is pinned:

  L1  NO degraded (excerpt-only) coverage in the depth tier, EVER.
      Pinned as an INVARIANT over persisted rows, not as a behaviour of one
      path: no brief carrying a `degraded` header may exist at a depth slot,
      whatever route produced it. That is the law as a test.
  L2  a fetch-failed prioritized story is SKIPPED, DISCLOSED at the bottom of
      the briefing in his words, and the next prioritized story is PROMOTED
      into the depth treatment.
  L3  degraded coverage is PERMITTED (not mandated) for In-Brief stories —
      which is one half of the FORK below, so it is scaffolded, not ruled.

WHAT IS BUILT AND WHAT IS DELIBERATELY NOT, because a file that pins a whole
contract when half of it is a checkpoint is itself a false claim:

  BUILT   detection (Gates A and B in `analyze_story`), the depth-tier WALK in
          `run_analysis`, the clause-3 disclosure, and the run record. All of
          it is arm-INDEPENDENT: both readings of his words need exactly this.
  NOT     the writer/reader propagation of the reassigned tier vector. The
          depth tier is positional in NINE places across four modules, and the
          two arms diverge precisely there. `FETCH_SKIP_ARM` therefore defaults
          to "off" and these pins arm it themselves.

THE FORK, which is the checkpoint question and NOT decided here: his words
support (i) the skipped story LEAVING the edition body entirely — "drop" — and
(ii) it landing as an In-Brief story with degraded treatment, lawful under L3 —
"in-brief". Every behavioural pin below runs under BOTH arms, so whichever he
rules is already proven; none of them assumes one.

PROOF CLASSES, labelled per pin. Everything here is BORN RED at HEAD (8ac0492)
in the strongest available sense — the module under test has no
`FETCH_SKIP_ARM`, no `FETCH_SKIP_OUTCOME`, no `fetch_skipped`, and no
`generate.FETCH_SKIP_LINE`, so these pins cannot even import their subjects
there. That is a weak red on its own (anything new fails against the old tree),
so the pins that guard a JUDGEMENT rather than an addition carry a MUTATION
RECEIPT instead: armed, with the enforcement removed, quoted in the build
report. The distinction is the whole point of the born-red law.

Offline by construction: autouse sandbox (conftest), no network, no key, $0.
"""

from __future__ import annotations

import json

import pytest

from newslens import analysis, generate

from test_analysis_brief_qa import (  # the stage's own harness, reused
    DATE, ENV_OK, fetch_fixture, s_brief, sonar_none,
)
from test_nl148_fetch_failure import _fetch_all_fail


ARMS = (analysis.FETCH_SKIP_ARM_IN_BRIEF, analysis.FETCH_SKIP_ARM_DROP)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def db_con(tmp_paths):
    from newslens import db
    db.migrate()
    con = db.connect()
    try:
        yield con
    finally:
        con.close()


def _footer(fetch_skipped):
    """The assembled briefing for a stories-free edition — the footer is what
    these pins are about, and an empty body keeps them off the writer's shape."""
    return generate.assemble_narrative(
        DATE, "A", [], {"slots": [], "window_meta": {},
                        "fetch_skipped": fetch_skipped})

def _seed_slots(con, n, date=DATE):
    """n prioritized slots, each backed by 2 source_items whose URLs name the
    slot — so a fetch fake can fail one story and serve another."""
    slots = []
    with con:
        for s in range(1, n + 1):
            ids = []
            for i in range(2):
                cur = con.execute(
                    "INSERT INTO source_items (source_type, outlet, url,"
                    " title, raw_excerpt) VALUES ('rss', ?, ?, ?, ?)",
                    ("The Hill", f"https://thehill.com/s{s}i{i}",
                     f"Story {s} item {i}",
                     "The president travels to the summit midweek."))
                ids.append(cur.lastrowid)
            slots.append({"slot": str(s), "story_title": f"Story {s}",
                          "summary": f"Summary {s}.", "item_ids": ids,
                          "outlets": ["The Hill"], "matched_tags": [],
                          "matched_memory": [], "override": False,
                          "corroboration_label": "Reported by 1 named outlet"})
        con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                    (date, json.dumps(slots)))
    return slots


def _fetch_failing_slots(*dead):
    """Fetches fail for the named slots and succeed everywhere else. robots
    404s so the DENY arm never masks the outcome under test."""
    dead_marks = tuple(f"/s{n}i" for n in dead)

    def _fetch(url, timeout, cap=0, user_agent=""):
        if url.endswith("/robots.txt"):
            return _fetch_all_fail(url, timeout, cap, user_agent)
        if any(m in url for m in dead_marks):
            raise OSError("connection reset by peer")
        return fetch_fixture(url, timeout, cap, user_agent)
    return _fetch


def _run(con, fetch, tiers=("full", "medium", "medium"), chat=None):
    return analysis.run_analysis(
        date=DATE, con=con, env=dict(ENV_OK),
        chat=chat or (lambda k, p: (s_brief(), 0.0)),
        sonar=sonar_none, fetch=fetch, sleep=lambda s: None,
        tiers_override=list(tiers))


def _persisted(con, date=DATE):
    """Every persisted brief row as (slot, tier, status, header)."""
    return [(r["slot"], r["tier"], r["status"],
             json.loads(r["brief_json"])["header"])
            for r in con.execute(
                "SELECT slot, tier, status, brief_json FROM analysis_briefs"
                " WHERE date = ? ORDER BY slot", (date,))]


# ===========================================================================
# L1 — THE LAW AS A TEST
# ===========================================================================

@pytest.mark.parametrize("arm", ARMS)
def test_L1_no_degraded_brief_can_ever_hold_a_depth_slot(db_con, monkeypatch,
                                                         arm):
    """L1, stated as an invariant over the RECORD rather than a behaviour of
    one code path: after a run where the lead's fetches all failed, no brief
    persisted at any depth slot carries a `degraded` header.

    Written this way on purpose. A pin that asserted "Gate A returned" would
    pass over a future route that mints a degraded brief some other way; this
    one asks the question the principal actually ruled on — can a reader ever
    meet excerpt-only analysis in the main slots — and it is answered from the
    rows, which is where the reader's copy comes from.

    MUTATION RECEIPT (build report): armed, with Gates A and B removed, this
    goes red on slot 1's header — the degraded brief mints and persists."""
    monkeypatch.setattr(analysis, "FETCH_SKIP_ARM", arm)
    _seed_slots(db_con, 5)
    # The analyst returns a brief that VALIDATES against an excerpt-only map.
    # That choice is load-bearing: with the default S1-citing brief a degraded
    # slot rejects for an unrelated reason, and this pin would then pass over a
    # tree whose gates had been deleted. The scenario has to be one where a
    # degraded brief genuinely CAN reach the reader, or the law is untested.
    rep = _run(db_con, _fetch_failing_slots(1),
               chat=lambda k, p: (_excerpt_brief(), 0.0))

    depth = set(rep["depth_slots"])
    assert depth, "no depth slots ran — the invariant would hold vacuously"
    checked = 0
    for slot, _tier, status, header in _persisted(db_con):
        if slot not in depth:
            continue
        checked += 1
        assert not header.get("degraded"), (
            f"slot {slot} holds a DEGRADED brief in the depth tier — L1 "
            f"forbids this unconditionally; header={header}")
    assert checked, "no depth-slot rows examined — the pin proved nothing"


@pytest.mark.parametrize("arm", ARMS)
def test_L1_holds_even_when_every_depth_candidate_fails(db_con, monkeypatch,
                                                        arm):
    """The hard case for L1: nothing in the edition can be fetched, so there is
    no healthy story to promote. The law still may not bend — the answer is
    zero depth briefs, never a degraded one."""
    monkeypatch.setattr(analysis, "FETCH_SKIP_ARM", arm)
    _seed_slots(db_con, 5)
    _run(db_con, _fetch_all_fail)

    assert not [r for r in _persisted(db_con) if r[2] == "valid"]
    assert all(not h.get("degraded") for _s, _t, _st, h in _persisted(db_con))


# ===========================================================================
# L2 — SKIP, PROMOTE, DISCLOSE
# ===========================================================================

@pytest.mark.parametrize("arm", ARMS)
def test_L2_the_depth_tier_moves_to_the_next_prioritized_story(db_con,
                                                               monkeypatch,
                                                               arm):
    """The lead's fetches fail; the depth tier becomes slots 2-4 and the FULL
    tier moves with it. His words are "the next prioritized story is PROMOTED
    into the depth treatment" — the depth treatment includes the lead."""
    monkeypatch.setattr(analysis, "FETCH_SKIP_ARM", arm)
    _seed_slots(db_con, 5)
    rep = _run(db_con, _fetch_failing_slots(1))

    assert rep["depth_slots"] == [2, 3, 4]
    assert rep["depth_tiers"][:5] == ["quick", "full", "medium", "medium",
                                      "quick"]
    assert [s["slot"] for s in rep["fetch_skipped"]] == [1]


@pytest.mark.parametrize("arm", ARMS)
def test_L2_slot_NUMBERING_never_moves_so_no_analysis_is_misattributed(
        db_con, monkeypatch, arm):
    """NL-148's finding (b) is AVOIDED, not resolved, and this is the pin that
    says so. The promote that would have needed a story-identity column never
    happens: only the TIER moves, so every persisted brief still sits at the
    slot number of the story it describes, and `latest_valid_brief(date, N)`
    cannot return another story's analysis.

    This is the cardinal-breach guard for the whole design. If it ever goes
    red, the no-schema shape has stopped being the shape that was built."""
    monkeypatch.setattr(analysis, "FETCH_SKIP_ARM", arm)
    slots = _seed_slots(db_con, 5)
    _run(db_con, _fetch_failing_slots(1))

    titles = {int(s["slot"]): s["story_title"] for s in slots}
    for slot, _tier, status, header in _persisted(db_con):
        if status != "valid":
            continue
        doc = analysis.latest_valid_brief(db_con, DATE, slot)
        assert doc is not None
        # the brief at slot N was built for the story ranked at slot N
        assert header["slot"] == slot, (
            f"brief at slot {slot} carries header slot {header['slot']} — "
            "slot numbering moved, which is the mis-attribution class")
        assert titles[slot]
    # the skipped story has NO valid brief at its own slot, and nobody else's
    assert analysis.latest_valid_brief(db_con, DATE, 1) is None


@pytest.mark.parametrize("arm", ARMS)
def test_L2_the_disclosure_is_his_sentence_verbatim_at_the_bottom(db_con,
                                                                  monkeypatch,
                                                                  arm):
    """Clause 3: his wording, the story's own title, the literal bottom of the
    briefing — below the standing corroboration caveat.

    The ORDER is asserted, not just the presence: "at the bottom" is the ruled
    placement, and a disclosure that drifts up into the body stops being the
    quiet footnote he asked for and becomes machinery theater."""
    monkeypatch.setattr(analysis, "FETCH_SKIP_ARM", arm)
    _seed_slots(db_con, 5)
    rep = _run(db_con, _fetch_failing_slots(1))

    from newslens import ranking
    text = _footer(rep["fetch_skipped"])

    assert "Fetch failed for prioritized story Story 1." in text
    assert text.find(ranking.CORROBORATION_CAVEAT) < text.find("Fetch failed")


def test_the_disclosure_never_renders_when_nothing_was_skipped():
    """The footer of an ordinary edition is untouched. A disclosure that
    appears on a healthy day is a false statement about the run."""
    from newslens import ranking
    text = _footer([])
    assert "Fetch failed for prioritized story" not in text
    assert ranking.CORROBORATION_CAVEAT in text


# ===========================================================================
# THE HONESTY SPLIT — what is a fetch failure and what only looks like one
# ===========================================================================

@pytest.mark.parametrize("arm", ARMS)
def test_a_never_attempted_slot_is_skipped_but_NOT_disclosed_as_a_failure(
        db_con, monkeypatch, arm):
    """A slot whose every source sits outside the 2026-07-06 tier boundaries
    opened no socket. L1 still bars it from the depth tier — the reader cannot
    tell excerpt-only coverage apart by its cause — but nothing FAILED, so his
    sentence must not render over it. Saying "Fetch failed" about a run that
    never fetched is exactly the kind of false reader-facing claim the
    fabrication rule exists to stop.

    Same reasoning the clause-4 any/all split already encodes: a slot that
    never opened a socket casts no vote about fetch health.

    MEASURED REACHABILITY (build report): 0 of 72 prioritized slots on the
    principal's real generation_log. Structurally reachable, never observed —
    this pin exists so L1's "ever" is literally true."""
    monkeypatch.setattr(analysis, "FETCH_SKIP_ARM", arm)
    monkeypatch.setattr(analysis, "tier_allows_fetch", lambda tier: False)
    _seed_slots(db_con, 5)
    rep = _run(db_con, _fetch_failing_slots())

    skipped = rep["fetch_skipped"]
    assert skipped, "an unfetchable slot must still leave the depth tier (L1)"
    assert all(s["outcome"] == analysis.NO_FETCHABLE_OUTCOME for s in skipped)
    assert all(s["disclose"] is False for s in skipped)

    assert "Fetch failed for prioritized story" not in _footer(skipped)


# ===========================================================================
# CLAUSE-4 COMPOSITION — the pause must survive the skip
# ===========================================================================

@pytest.mark.parametrize("arm", ARMS)
def test_clause4_a_systemic_day_still_PAUSES_it_does_not_become_seven_skips(
        db_con, monkeypatch, arm):
    """THE COMPOSITION PIN. NL-151 introduces a per-story skip; NL-148 clause 4
    is a whole-run pause. The failure mode this guards is precise and it would
    be silent: a day when nothing fetches anywhere degrading into seven
    disclosed skips and a published edition with no depth coverage at all,
    instead of the pause the principal ruled.

    It holds because the walk does not change any of clause 4's four
    conjuncts' MEANING — it only lets `per_story` grow as the walk looks
    further down the ranking for a survivor. Every one of those extra slots
    attempted and came back empty, so `any(attempted)` stays true, `all(not
    ok)` stays true, and no valid brief exists. The verdict is unmoved."""
    monkeypatch.setattr(analysis, "FETCH_SKIP_ARM", arm)
    _seed_slots(db_con, 7)
    rep = _run(db_con, _fetch_all_fail)

    assert rep["fetch_systemic_failure"] is True, (
        "a whole-network outage degraded into per-story skips — the pause "
        "clause 4 ruled has been dissolved by clause 2's walk")
    assert rep["depth_slots"] == []


@pytest.mark.parametrize("arm", ARMS)
def test_clause4_does_not_fire_when_the_walk_finds_a_survivor(db_con,
                                                              monkeypatch,
                                                              arm):
    """The other side of the composition, and the reason the walk is worth
    having: slots 1-3 are dead, slot 4 is healthy, so an edition CAN be built
    and pausing would throw away coverage the reader could have had."""
    monkeypatch.setattr(analysis, "FETCH_SKIP_ARM", arm)
    _seed_slots(db_con, 7)
    rep = _run(db_con, _fetch_failing_slots(1, 2, 3))

    assert rep["fetch_systemic_failure"] is False
    assert rep["depth_slots"] == [4, 5, 6]
    assert [s["slot"] for s in rep["fetch_skipped"]] == [1, 2, 3]


# ===========================================================================
# COST — the skip must not buy what it cannot publish
# ===========================================================================

@pytest.mark.parametrize("arm", ARMS)
def test_the_skip_spends_nothing_on_the_slot_it_skips(db_con, monkeypatch,
                                                      arm):
    """Gate A returns ABOVE the ladder, so a slot L1 bars from depth buys
    neither Sonar verification nor a synthesis it can never publish.

    This is not a micro-optimisation: QA measured $0.74 of billed-then-rejected
    briefs on one clause-4 pause, and that spend is exactly this. The sentinels
    are the proof — either callable firing means the gate moved below the
    ladder and the money came back."""
    monkeypatch.setattr(analysis, "FETCH_SKIP_ARM", arm)
    _seed_slots(db_con, 3)

    def chat_sentinel(key, prompt):
        raise AssertionError("synthesis was called for a slot the depth tier "
                             "had already refused — Gate A is below the ladder")

    def sonar_sentinel(key, title, claims):
        raise AssertionError("Sonar was paid for a slot the depth tier had "
                             "already refused — Gate A is below the ladder")

    rep = analysis.run_analysis(
        date=DATE, con=db_con, env=dict(ENV_OK), chat=chat_sentinel,
        sonar=sonar_sentinel, fetch=_fetch_all_fail, sleep=lambda s: None,
        tiers_override=["full", "medium", "medium"])

    assert rep["total_usd"] == 0.0
    assert rep["total_usd_shadow"] == 0.0


# ===========================================================================
# THE OFF DEFAULT — today's behaviour, unchanged, until he rules
# ===========================================================================

def test_off_is_the_default_and_the_fork_is_still_open(db_con):
    """The arm is a CHECKPOINT, not an implementer's pick. This pin fails the
    day someone quietly defaults it, which is the day the principal's editorial
    call gets made by a diff instead of by him."""
    assert analysis.FETCH_SKIP_ARM == analysis.FETCH_SKIP_ARM_OFF
    assert analysis.depth_skip_armed() is False


def test_off_default_preserves_todays_degraded_depth_brief(db_con):
    """CARRIED INVARIANT, born GREEN and labelled — the control.

    At the OFF default a fetch-failed depth slot still mints its degraded
    brief exactly as it does today. Two things are pinned by that: the change
    is genuinely inert until armed, and the L1 pins above are measuring a real
    difference rather than a world where degraded briefs never existed."""
    _seed_slots(db_con, 3)
    _run(db_con, _fetch_failing_slots(1),
         chat=lambda k, p: (_excerpt_brief(), 0.0))

    slot1 = [r for r in _persisted(db_con) if r[0] == 1]
    assert slot1, "slot 1 produced no row at all — the control is not measuring"
    assert any(h.get("degraded") for _s, _t, _st, h in slot1), (
        "the OFF default no longer reproduces today's degraded depth brief — "
        "either the gates leaked past the switch, or the control is stale")


def _excerpt_brief():
    b = s_brief()
    for entry in b["pinned_facts"]:
        entry["cites"] = ["C1"]
    for entry in b["ledger"]:
        entry["cites"] = ["C1"]
    b["mechanism"] = ("Attendance is traded for spending commitments; each "
                      "ally answers to its own parliament [C1].")
    return b
