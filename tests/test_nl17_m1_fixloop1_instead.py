"""NL-17 M1 FIX LOOP 1 — the supplemental ruling (product RECONVENE §R.3):
LOW-settle candidates ride the ratified management-surface "Instead:" row.

THE RULING, and what each half has to be true for:

  THE SETTLE NEVER ANNOUNCES — a LOW settle attaches nothing, the thread stays at
  seed, the state layer is silent everywhere, today cards are untouched. That is
  case (a)'s surface and it is unchanged.
  MANAGEMENT SURFACES MAY AFFORD — deep view and Following (v11 ruling ②'s own
  carve-out) render "Instead: <Name (kind)> · <Storyline name>" from the
  candidates the settle persisted. ZERO NEW STRINGS: the row is ruling ②'s, the
  compact grammar is the principal's 07-18 menu, the bare storyline name is that
  menu's option 3.

The announce/afford distinction is the ORG's gloss on his silence law, not his
ruling — it goes to his eye in one sentence, and Kass's dissent says so. If he
strikes it, this collapses to silence by deleting one render; the candidates in
the event stay as record. Nothing here is built as if that were settled.

FOUR THINGS THIS FILE GUARDS, in descending order of what breaking them costs:

  1. TODAY STAYS CLEAN. Ruling ②'s ban is absolute and this is the change most
     likely to violate it by accident.
  2. THE DENOMINATOR STAYS CLEAN. `settled_low` never lands in the terminal-none
     bucket the principal rules Kass's falsifier from.
  3. THE PIN SCOPE HOLDS. "A settled thread is never re-aimed" guards the SYSTEM
     settle route; the reader swap door must stay distinct from it. This was the
     dispatch's binding tripwire — asserted here, not assumed.
  4. A MIS-SWAP IS ONE TAP FROM HOME. The prior aim rejoins the targets.

Client pins are STRUCTURAL and comment-stripped (no JS engine in this suite —
the R4 lesson: a pin a comment can satisfy is not a pin). Behavioural proof of
the swap is QA's browser pass.

Offline by construction: autouse sandbox (conftest), no network, no key, $0.
"""

from __future__ import annotations

import json

import pytest

from newslens import db, entities, follow_altitude, labels, memory, paths, server

from test_nl143_follow_surface_truth import _fn, _js_code
from test_nl17_m1_entity_identity import (_FollowHandler, _Res, _settle,
                                          _seed_and_settle, HEADLINE, STORY)


CANDS = [{"altitude": "entity", "disclosure": "Volkswagen (company)"},
         {"altitude": "storyline", "disclosure": "Volkswagen job cuts"}]


def _py_code(src: str) -> str:
    """Python source with its COMMENTS AND DOCSTRINGS stripped — the same R4
    discipline `_js_code` applies to the client, and for the same reason it was
    established: this file's first draft asserted `settle_allowed` was absent
    from a function whose DOCSTRING explains why it is absent, and went red on
    its own prose. A pin a comment can satisfy — or falsify — is not a pin."""
    import io
    import tokenize
    out, prev_end, at_stmt_start = [], (1, 0), True
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        ttype, text, start, end, _ = tok
        if ttype == tokenize.COMMENT:
            continue
        if ttype == tokenize.STRING and at_stmt_start:
            continue                      # a bare string statement = docstring
        if ttype not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                         tokenize.DEDENT):
            at_stmt_start = False
        if ttype in (tokenize.NEWLINE, tokenize.NL, tokenize.INDENT,
                     tokenize.DEDENT):
            at_stmt_start = True
        out.append(text)
    return " ".join(out)


@pytest.fixture()
def con():
    db.migrate(db_path=paths.DB_PATH)
    c = db.connect(paths.DB_PATH)
    yield c
    c.close()


@pytest.fixture()
def quiet_memory(monkeypatch):
    monkeypatch.setattr(memory, "sync_memory", lambda con, **kw: memory.SyncResult())
    monkeypatch.setattr(memory, "write_memory_file", lambda con: 0)


class _AtHandler:
    _with_memory = server.Handler._with_memory
    _ref_id_for = server.Handler._ref_id_for
    _commit_altitude = server.Handler._commit_altitude
    _api_follow_at = server.Handler._api_follow_at

    def __init__(self):
        self.sent = []

    def _send_json(self, obj, status=200):
        self.sent.append((obj, status))
        return obj


def _low_settled(monkeypatch, quiet, topic=STORY, headline=HEADLINE):
    """Seed a thread and land an UNCONFIDENT settle on it."""
    return _seed_and_settle(monkeypatch, _Res(confidence="low"),
                            topic=topic, headline=headline)


# ===========================================================================
# 1 — PERSISTENCE: the candidates survive the settle, on the event
# ===========================================================================

def test_a_low_settle_persists_its_candidates_and_attaches_nothing(
        monkeypatch, quiet_memory, con):
    """BORN RED. The whole shape in one test: nothing attached, thread still at
    seed, state layer silent — and the two named rungs kept, on the event, where
    a management surface can read them."""
    h = _low_settled(monkeypatch, quiet_memory)
    out, _ = h.sent[-1]
    assert out["settled"] is False and out["state"] == "unsettled"
    row = con.execute("SELECT id, altitude, disclosure, entity_id"
                      " FROM memory").fetchone()
    assert row["altitude"] == "narrow"        # still at seed
    assert row["disclosure"] == ""            # nothing attached
    assert row["entity_id"] is None
    assert con.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0

    ev = con.execute("SELECT outcome, detail FROM follow_settle_events").fetchone()
    assert ev["outcome"] == "settled_low"
    assert memory.settle_candidates(con, row["id"]) == CANDS


def test_the_candidates_are_the_resolvers_own_two_rungs_reshaped_not_authored():
    """BORN RED. The prompt already requires both rungs named cleanly at low
    ("make BOTH the disclosure … and alt_label … clean, named options"), so this
    re-shapes an answer we already have. Leaned-toward rung first — the only
    ranking information the resolver gave, and the order the row renders."""
    pair = server._settle_candidate_pair(_Res(confidence="low"))
    assert pair == CANDS
    flipped = server._settle_candidate_pair(_Res(
        confidence="low", altitude="storyline",
        disclosure="Volkswagen job cuts", alt_label="Volkswagen (company)"))
    assert flipped[0]["altitude"] == "storyline"
    assert flipped[1]["altitude"] == "entity"
    # an M1a-shaped answer with no alt_label degrades to the ONE candidate it
    # has — a one-item Instead: row is a lawful sentence; a fabricated second
    # option would not be.
    lone = server._settle_candidate_pair(_Res(confidence="low", alt_label=""))
    assert len(lone) == 1


def test_a_malformed_or_legacy_detail_degrades_to_no_affordance(con):
    """BORN RED. This runs on a RENDER path. A management surface losing an
    affordance is recoverable; a 500 on the Following page is not."""
    for junk in ("", "low-confidence", "{", "{}", '{"candidates": "nope"}',
                 '{"candidates": [{"altitude": "narrow", "disclosure": "x"}]}'):
        assert memory.decode_settle_candidates(junk) == [], junk
    # …and 'narrow' can never arrive as a candidate: it is not a pickable rung
    # (amendment (i)), so it is dropped on the way IN as well as on the way out.
    assert memory.decode_settle_candidates(
        memory.encode_settle_candidates(
            [{"altitude": "narrow", "disclosure": "this story"}])) == []


# ===========================================================================
# 2 — THE DENOMINATOR (Kass's falsifier stays measurable)
# ===========================================================================

def test_settled_low_is_a_distinct_kind_from_settled_none(con):
    """BORN RED, AND IT IS WHY THE SCHEMA MOVED. Kass's terminal-none falsifier
    is evaluated at the checkpoint on the count of threads whose settle looked
    and found NOTHING. A thread that found two plausible things and could not
    choose between them is not one of those. Folding them together would inflate
    the exact number his dissent is measured against — in the direction that
    flips the ruling."""
    assert "settled_low" in memory.SETTLE_OUTCOMES
    assert "settled_low" != "settled_none"
    memory.add_thread(con, "T")
    tid = con.execute("SELECT id FROM memory").fetchone()["id"]
    with con:
        memory.log_settle_outcome(con, tid, "T", "settled_low",
                                  detail=memory.encode_settle_candidates(CANDS))
    con.commit()
    terminal_none = con.execute(
        "SELECT COUNT(*) FROM follow_settle_events"
        " WHERE outcome = 'settled_none'").fetchone()[0]
    assert terminal_none == 0


def test_settled_low_stays_widenable_exactly_like_settled_none(con):
    """BORN RED. Both mean "the settle ran and attached nothing", so both stay
    eligible indefinitely — a later story with new evidence gets a fresh
    attempt, not a retry. Only FAILURE is bounded (0025 header)."""
    memory.add_thread(con, "T")
    tid = con.execute("SELECT id FROM memory").fetchone()["id"]
    for _ in range(4):
        with con:
            memory.log_settle_outcome(
                con, tid, "T", "settled_low",
                detail=memory.encode_settle_candidates(CANDS))
        assert memory.settle_allowed(con, tid) is True


# ===========================================================================
# 3 — THE RENDER: management only, zero new strings
# ===========================================================================

def test_the_instead_row_renders_both_candidates_on_a_seed_thread():
    """BORN RED — the affordance itself. A thread at seed with two named rungs
    offers them; every component is a ratified string and none is new."""
    acts = server._follow_acts_line({"altitude": "narrow"}, "this thread",
                                    candidates=CANDS)
    assert labels.FOLLOW_INSTEAD_PREFIX in acts
    assert "Volkswagen" in acts and "(company)" in acts
    assert "Volkswagen job cuts" in acts
    assert labels.FOLLOW_UNFOLLOW in acts
    # the compact grammar, rendered by the SAME splitter the rest of the
    # surface uses: name bold, class in the quiet parenthetical
    assert "<strong>Volkswagen</strong>" in acts
    assert '<span class="oq">(company)</span>' in acts
    # the storyline name renders BARE (his 07-18 menu, option 3)
    assert "<strong>Volkswagen job cuts</strong>" in acts
    # …and no string was invented for this row
    assert "this story" not in acts


def test_todays_cards_never_carry_the_instead_row(tmp_paths):
    """BORN RED, AND THE MOST IMPORTANT PIN IN THIS FILE. Ruling ②'s ban is
    absolute: today cards carry no acts at all. The new affordance is exactly
    the kind of change that violates it by accident, so this asserts the
    structural reason it cannot — `_follow_control` does not call the acts line,
    and no card mount is ever handed candidates."""
    import inspect, textwrap
    src = _py_code(textwrap.dedent(inspect.getsource(server._follow_control)))
    assert "_follow_acts_line" not in src
    assert "candidates" not in src
    con = db.connect(paths.DB_PATH)
    try:
        db.migrate(db_path=paths.DB_PATH)
        memory.add_thread_at_altitude(con, STORY, altitude="narrow",
                                      source="seed", origin_story=STORY)
        tid = con.execute("SELECT id FROM memory").fetchone()["id"]
        with con:
            memory.log_settle_outcome(
                con, tid, STORY, "settled_low",
                detail=memory.encode_settle_candidates(CANDS))
        card = server._follow_control(
            {"headline": HEADLINE}, {"story_title": STORY}, [], {STORY.lower()},
            "2026-08-08", slug="story-0", con=con)
    finally:
        con.close()
    assert labels.FOLLOW_INSTEAD_PREFIX not in card
    assert "Volkswagen job cuts" not in card
    assert "data-candidates" not in card


def test_both_management_surfaces_afford_and_stamp_the_candidates(con):
    """BORN RED. Deep view AND Following — ruling ②'s carve-out names both, and
    a surface that afforded on one but not the other would make the same thread
    offer different things in two places (the single-rendering law's own
    failure mode)."""
    deep = server._follow_slot_html(
        slot_id="s", mount="deep", followed=True, committed_topic=STORY,
        story_topic=STORY, headline=HEADLINE, date="", alt={"altitude": "narrow"},
        candidates=CANDS)
    row = server._follow_slot_html(
        slot_id="r", mount="row", followed=True, committed_topic=STORY,
        story_topic=STORY, headline="", date="", alt={"altitude": "narrow"},
        candidates=CANDS)
    for html in (deep, row):
        assert labels.FOLLOW_INSTEAD_PREFIX in html
        assert 'data-alt="entity"' in html
        assert 'data-disc="Volkswagen (company)"' in html
        # the candidates ride the SLOT so the client re-renders the same set
        # after a swap — the single-rendering law applied to the affordance
        assert "data-candidates=" in html
        assert json.dumps(CANDS).replace('"', "&quot;") in html


def test_a_seed_thread_with_no_candidates_stays_silent(con):
    """CARRIED-INVARIANT (born green) — the ruling's degenerate case, which is
    also the shipped behaviour: option (2) CONTAINS option (3). No candidates,
    no row, pure silence, nothing to unbuild if the class turns out empty."""
    acts = server._follow_acts_line({"altitude": "narrow"}, "this thread",
                                    candidates=[])
    assert labels.FOLLOW_INSTEAD_PREFIX not in acts
    assert labels.FOLLOW_UNFOLLOW in acts


def test_the_client_twin_renders_the_same_target_set():
    """BORN RED. The server renders the row at load; the client re-renders it
    after every swap. Two implementations of "what is on offer" is how the four
    mounts drifted apart before NL-143, so the rule lives in one function per
    side and the two are twins."""
    js = _js_code(_fn("flSwapTargets"))
    assert "data-candidates" in _js_code(_fn("flSwapTargets")) or "candidates" in js
    assert "alt-label" in js                       # the shipped fallback source
    assert "'entity'" in js and "'storyline'" in js
    acts = _js_code(_fn("flActsLine"))
    assert "flSwapTargets(slot)" in acts
    assert "data-alt=" in acts and "data-disc=" in acts
    assert "flQualified(t.disclosure)" in acts


# ===========================================================================
# 4 — THE SWAP DOOR
# ===========================================================================

def test_a_swap_is_one_tap_zero_dollars_and_takes_the_identity_door(
        monkeypatch, quiet_memory, con):
    """BORN RED. The reader taps a candidate: the thread MOVES to that rung and
    gains that actor's identity through the same mint-or-match door the system
    settle uses. $0 — the resolver raises if anything reaches it."""
    memory.add_thread_at_altitude(con, STORY, altitude="narrow", source="seed",
                                  origin_story=STORY)

    def _never(*a, **kw):
        raise AssertionError("a swap must never consult a model")
    monkeypatch.setattr(follow_altitude, "resolve_altitude", _never)
    monkeypatch.setattr(follow_altitude, "resolve_cost_gate", _never)

    h = _AtHandler()
    h._api_follow_at({"name": "Volkswagen", "altitude": "entity",
                      "disclosure": "Volkswagen (company)",
                      "alt_label": "Volkswagen job cuts",
                      "primary_entity": "Volkswagen", "from_topic": STORY})
    out, status = h.sent[-1]
    assert status == 200 and out["outcome"] == "moved"
    row = con.execute("SELECT id, topic, altitude, entity_id, altitude_source"
                      " FROM memory WHERE status = 'active'").fetchone()
    assert row["topic"] == "Volkswagen" and row["altitude"] == "entity"
    assert row["altitude_source"] == "pick"          # a READER act, on the record
    assert row["entity_id"] is not None
    ent = entities.get(con, row["entity_id"])
    assert (ent["canonical_name"], ent["kind"]) == ("Volkswagen", "org")


def test_weight_replaces_never_stacks(monkeypatch, quiet_memory, con):
    """BORN RED — criterion (b), expressed as a schema fact rather than as a
    ranking-time subtraction (ranking is M2; nothing here scores). A thread
    holds AT MOST ONE identity at any instant: swapping to a storyline CLEARS
    the entity, so there is no reachable state where an old aim's entity and a
    new aim's entity both apply."""
    memory.add_thread_at_altitude(con, STORY, altitude="narrow", source="seed",
                                  origin_story=STORY)
    h = _AtHandler()
    h._api_follow_at({"name": "Volkswagen", "altitude": "entity",
                      "disclosure": "Volkswagen (company)",
                      "primary_entity": "Volkswagen", "from_topic": STORY})
    first = con.execute("SELECT id, entity_id FROM memory"
                        " WHERE status = 'active'").fetchone()
    assert first["entity_id"] is not None

    h2 = _AtHandler()
    h2._api_follow_at({"name": "Volkswagen job cuts", "altitude": "storyline",
                       "disclosure": "Volkswagen job cuts",
                       "from_topic": "Volkswagen"})
    after = con.execute("SELECT entity_id FROM memory"
                        " WHERE status = 'active'").fetchone()
    assert after["entity_id"] is None            # replaced, not stacked
    assert con.execute(
        "SELECT COUNT(*) FROM memory WHERE entity_id IS NOT NULL"
    ).fetchone()[0] == 0


def test_the_prior_aim_joins_the_targets_after_any_swap():
    """BORN RED — Ines's fixability clause: a mis-swap must be ONE TAP from home.

    It falls out of the subtraction rather than needing machinery: the candidate
    list does not change when the reader swaps, so whichever rung they just left
    is still in it and is no longer the aim — so it renders."""
    seed = server._swap_targets({"altitude": "narrow"}, CANDS)
    assert [t["disclosure"] for t in seed] == [
        "Volkswagen (company)", "Volkswagen job cuts"]

    aimed_entity = server._swap_targets(
        {"altitude": "entity", "disclosure": "Volkswagen (company)"}, CANDS)
    assert [t["disclosure"] for t in aimed_entity] == ["Volkswagen job cuts"]

    aimed_story = server._swap_targets(
        {"altitude": "storyline", "disclosure": "Volkswagen job cuts"}, CANDS)
    assert [t["disclosure"] for t in aimed_story] == ["Volkswagen (company)"]


def test_the_seed_is_never_a_swap_target():
    """CARRIED-INVARIANT (born green) — measured against the pre-loop baseline,
    which had no candidate targets at all and so passed this trivially. Kept and
    labelled honestly, because it guards a property a plausible NEXT change
    breaks: it bites the moment anyone adds the seed to the target list.

    It is where two rulings meet. "The prior aim joins the
    targets" and amendment (i) both bind — and the prior aim of a FIRST swap is
    the story-seeded state, which amendment (i) bans offering back. Targets come
    from the CANDIDATE list, and 'narrow' is not a rung the resolver ever names,
    so the banned offer cannot appear. Leaving the thread entirely is still one
    tap on the same line, under the symmetry law."""
    targets = server._swap_targets(
        {"altitude": "entity", "disclosure": "Volkswagen (company)"},
        CANDS + [{"altitude": "narrow", "disclosure": STORY}])
    assert all(t["altitude"] in memory.PICKABLE_ALTITUDES for t in targets)
    acts = server._follow_acts_line(
        {"altitude": "entity", "disclosure": "Volkswagen (company)"},
        "Volkswagen", candidates=CANDS)
    assert "this story" not in acts
    assert labels.FOLLOW_RUNG_THIS_STORY not in acts


def test_one_swap_path_and_the_target_comes_off_the_link():
    """BORN RED. The row can now carry SEVERAL targets; a second handler for the
    new ones is how two handlers start disagreeing about what a swap is. So
    every target renders as the same link shape and one function performs all of
    them.

    REWRITTEN IN FIX LOOP 2b, as the ruling directs. This pin used to assert the
    short-circuit guard `if (!disc || !newAlt) return;` as though a targetless
    link were a design — it was ENSHRINING THE DEAD TAP QA found (F-4). The arm
    that produced such links is deleted; the guard survives as a backstop, so
    the pin no longer celebrates it. What the render must do is asserted in
    `test_the_empty_swap_state_mounts_no_affordance_at_all` below."""
    body = _js_code(_fn("flSwitch"))
    assert "data-alt" in body and "data-disc" in body
    assert "flOtherAltitude" not in body        # no longer derived from the slot
    assert _js_code(webui_js()).count("api('/api/follow/at'") == 1


def test_the_empty_swap_state_mounts_no_affordance_at_all():
    """BORN RED against the pre-2b render — THE RULING, asserted directly
    (product RECONVENE-2, unanimous, ruling (b); QA F-4).

    A settled thread with no alt_label and no candidates used to render
    `Instead: the wider story · Unfollow`: an anchor carrying an aria promise
    and no target identity, which fix loop 1 turned into a silent no-op — the
    bucket-1051 class M1c exists to have killed.

    The state now renders EXACTLY Unfollow. Four things must all be absent, and
    they are asserted separately because a partial fix passes a sloppy pin: no
    anchor, no prose, no aria action, and no bare "Instead:" label (which would
    assert alternatives that do not exist)."""
    for altitude in ("entity", "storyline"):
        acts = server._follow_acts_line({"altitude": altitude, "alt_label": ""},
                                        "Volkswagen", candidates=[])
        assert "<a " not in acts, (altitude, acts)          # no anchor
        assert "flSwitch" not in acts, (altitude, acts)     # no swap action
        assert "Switch to" not in acts, (altitude, acts)    # no aria promise
        assert labels.FOLLOW_INSTEAD_PREFIX not in acts, (altitude, acts)
        assert labels.FOLLOW_UNFOLLOW in acts               # …the act that remains
        # exactly one control, and it is the Unfollow button
        assert acts.count("<button") == 1, acts

    # …and the label RETURNS lawfully the moment a named target exists — the
    # empty case is silence, not a permanent kill of the row.
    named = server._follow_acts_line(
        {"altitude": "entity", "alt_label": "Volkswagen job cuts"}, "Volkswagen")
    assert labels.FOLLOW_INSTEAD_PREFIX in named
    assert "Volkswagen job cuts" in named


def test_the_worded_fallback_strings_reach_no_emitted_surface():
    """BORN RED against the pre-2b render. The strings are retired in labels.py
    under the retired-but-named convention (the record of what was ruled), and
    they must appear in NOTHING that ships — not the server render, not the
    client label table, not the emitted script's own comments, which are shipped
    bytes like any other."""
    for dead in ("the wider story", "the company", "altFallback",
                 "Switch to the wider story"):
        assert dead not in server._nl_labels_js(), dead
        assert dead not in webui_js(), dead
    for altitude in ("entity", "storyline"):
        acts = server._follow_acts_line({"altitude": altitude, "alt_label": ""},
                                        "X", candidates=[])
        assert "wider story" not in acts and "the company" not in acts
    # kept named, so nothing imports a dangling constant and the ruling's own
    # strings stay on the record
    assert labels.FOLLOW_ALT_FALLBACK_STORYLINE == "the wider story"
    assert labels.FOLLOW_ALT_FALLBACK_ENTITY == "the company"


def webui_js():
    from newslens import webui
    return webui.JS


# ===========================================================================
# 5 — THE BINDING TRIPWIRE (dispatch: STOP if this cannot hold)
# ===========================================================================

def test_the_swap_door_is_distinct_from_the_system_settle_route(
        monkeypatch, quiet_memory, con):
    """CARRIED-INVARIANT (born green) — AND THAT IS THE FINDING, not a weakness.

    This is the dispatch's binding tripwire, and it measured green against the
    pre-loop baseline for the reason the tripwire hoped for: the two doors were
    ALREADY distinct endpoints, so wiring the identity half into the swap moved
    nothing across that line and the pin never had to widen. The dispatch asked
    for this asserted rather than assumed; a green here is the assertion.

    "A settled thread is never re-aimed" must keep guarding the SYSTEM route
    only. This proves the two doors are distinct on the SAME thread at the SAME
    moment: the settle route refuses (the thread is settled; the mutation law
    holds), while the reader's swap succeeds (a reader may always re-aim their
    own follow — that is the symmetry law's neighbour, not its enemy).

    The structural half matters as much as the behavioural: the swap route never
    asks whether a settle is allowed, never appends a settle outcome, and never
    calls the resolver. That is why the pin did not have to widen."""
    import inspect, textwrap
    src = _py_code(textwrap.dedent(
        inspect.getsource(server.Handler._api_follow_at)))
    assert "settle_allowed" not in src
    assert "log_settle_outcome" not in src
    assert "_log_settle" not in src
    assert "resolve_altitude" not in src

    # …and behaviourally, on one settled thread.
    _seed_and_settle(monkeypatch, _Res())          # lands at entity 'Volkswagen'
    h = _FollowHandler()
    h._api_follow_seed({"topic": "Volkswagen", "origin": HEADLINE})
    assert h.sent[-1][0]["settle"] is False        # SYSTEM route: refused

    sw = _AtHandler()
    sw._api_follow_at({"name": "Volkswagen job cuts", "altitude": "storyline",
                       "disclosure": "Volkswagen job cuts",
                       "from_topic": "Volkswagen"})
    out, status = sw.sent[-1]
    assert status == 200 and out["outcome"] == "moved"   # READER route: allowed
    assert con.execute(
        "SELECT altitude FROM memory WHERE status = 'active'"
    ).fetchone()["altitude"] == "storyline"


def test_a_swap_never_moves_the_retry_bound(monkeypatch, quiet_memory, con):
    """CARRIED-INVARIANT (born green) — the other half of the tripwire, and
    green for the same reason: the swap route never wrote to the settle log
    before this loop and still does not. Pinned because the identity write this
    loop DID add to that route sits inches from the logger.

    The bound is decided from the settle log. If a reader's tap
    wrote to that log, a reader could spend the system's retry — or restore one
    — by fiddling with their own follow. Swaps are silent in that record."""
    memory.add_thread_at_altitude(con, STORY, altitude="narrow", source="seed",
                                  origin_story=STORY)
    tid = con.execute("SELECT id FROM memory").fetchone()["id"]
    with con:
        memory.log_settle_outcome(con, tid, STORY, "settle_failed", attempt=1)
    before = memory.settle_allowed(con, tid)

    h = _AtHandler()
    h._api_follow_at({"name": "Volkswagen", "altitude": "entity",
                      "disclosure": "Volkswagen (company)",
                      "primary_entity": "Volkswagen", "from_topic": STORY})
    assert h.sent[-1][0]["outcome"] == "moved"
    assert con.execute(
        "SELECT COUNT(*) FROM follow_settle_events").fetchone()[0] == 1
    assert memory.settle_allowed(con, tid) == before
