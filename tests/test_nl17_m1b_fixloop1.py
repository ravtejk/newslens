"""NL-17-M1b — FIX LOOP 1 born-red proofs (QA NO-GO 2026-07-18, loop 1 of 3).

FIX-LOOP BASELINE — READ THIS ABOUT "BORN RED": these fail against the CURRENT
working tree, which is the full picker build (c7338d8 + uncommitted) MINUS this
fix loop. They are NOT red against pristine HEAD (the picker build itself is
uncommitted); they are red against build-minus-loop-1. Each flips only when its
named fix lands. Some FIX-1 proofs are red by ERROR pre-fix (the origin_story
column/param does not exist yet) — labelled inline; the rest are red by
assertion. FIX-2/FIX-4 are STRUCTURAL source pins on webui.JS — live keyboard /
in-browser verification is QA's re-check (dispatch: "as behavioral as the
harness allows").

  FIX-1  altitude-renamed follow recognized on the origin card:
    (a) after a HIGH/MED commit stored under the RESOLVER's name, the origin card
        renders COMMITTED across reload AND across a regenerate (headline drift);
        data-topic carries the STORED name so unfollow/switch hit the real row.
    (b) a tap on a recognized origin runs NO second paid resolve and creates NO
        divergent second active row (the QA double-follow: rows "…job cuts" +
        "Volkswagen" for one story).
  FIX-2  focus continuity: every fl* morph restores focus into the persistent
         slot (roving tabindex). Pre-fix: zero focus calls in the flow.
  FIX-3  the follow_altitude seat has an interactive (short) timeout. Pre-fix
         60/180s.
  FIX-4  the expanded committed line carries aria-expanded="true" and a wired
         collapse tap. Pre-fix a bare <span>, no aria-expanded, unreachable
         flCollapseCommitted.

Offline by construction (conftest sandbox — no network, no real key, per-test DB).
"""

from __future__ import annotations

import types

from newslens import db, follow_altitude as fa, labels, llm, memory, paths, server, webui


# ---------------------------------------------------------------------------
# harness — drive the real follow endpoints through a lightweight handler double
# (borrows Handler's verb helpers; _send_json is captured, not written to a
# socket). The resolver + memory-file I/O are stubbed so the proof is $0 and
# isolates the resolve/commit/guard behaviour.
# ---------------------------------------------------------------------------

class _FollowHandler:
    _topic_arg = server.Handler._topic_arg
    _with_memory = server.Handler._with_memory
    _ref_id_for = server.Handler._ref_id_for
    _commit_altitude = server.Handler._commit_altitude
    _api_follow_resolve = server.Handler._api_follow_resolve
    _api_follow_at = server.Handler._api_follow_at

    def __init__(self):
        self.sent = []

    def _send_json(self, obj, status=200):
        self.sent.append((obj, status))
        return obj


def _seq_resolver(calls, specs):
    """A stub follow_altitude.resolve_altitude: records each call and returns the
    next canned AltitudeResult-shaped object. $0 — never touches a provider."""
    seq = list(specs)
    def _resolve(thread, **kwargs):
        calls.append(getattr(thread, "topic", None))
        spec = seq.pop(0) if seq else specs[-1]
        return types.SimpleNamespace(**spec)
    return _resolve


_ENTITY = dict(confidence="high", altitude="entity", primary_entity="Volkswagen",
               disclosure="Volkswagen (company)", alt_label="Volkswagen job cuts")
_STORYLINE = dict(confidence="high", altitude="storyline",
                  primary_entity="Volkswagen", disclosure="Volkswagen job cuts",
                  alt_label="Volkswagen (company)")


def _quiet_memory(monkeypatch):
    monkeypatch.setattr(memory, "sync_memory", lambda con: None)
    monkeypatch.setattr(memory, "write_memory_file", lambda con: None)


def _active_count(where="status = 'active'"):
    con = db.connect(paths.DB_PATH)
    try:
        return con.execute(
            f"SELECT COUNT(*) AS n FROM memory WHERE {where}").fetchone()["n"]
    finally:
        con.close()


# ===========================================================================
# FIX-1(a) — the altitude-renamed follow is recognized on the origin card
# ===========================================================================

def test_fix1a_origin_card_committed_after_reload(monkeypatch):
    """The story "Volkswagen job cuts" resolves HIGH to the ENTITY "Volkswagen";
    the auto-commit stores the follow under "Volkswagen". On reload the origin
    card must render COMMITTED (not resting) with the stored disclosure, and
    data-topic must be the STORED name so a later unfollow/switch targets it.
    BORN-RED (assertion): build-minus-loop recognizes only story_title/headline
    ∈ active_topics, so the renamed follow renders RESTING after reload."""
    db.migrate(db_path=paths.DB_PATH)
    _quiet_memory(monkeypatch)
    calls = []
    monkeypatch.setattr(fa, "resolve_altitude", _seq_resolver(calls, [_ENTITY]))
    h = _FollowHandler()
    h._api_follow_resolve({"topic": "Volkswagen job cuts",
                           "origin": "Volkswagen plans significant job cuts"})
    assert len(calls) == 1                                   # first tap resolved

    con = db.connect(paths.DB_PATH)
    try:
        active = server._active_topics_lower(con)            # {"volkswagen"}
        html = server._follow_control(
            {"headline": "Volkswagen plans significant job cuts"},
            {"story_title": "Volkswagen job cuts"}, [], active, "2026-07-18",
            slug="story-1", con=con)
    finally:
        con.close()
    assert 'data-state="committed"' in html                 # recognized, not resting
    assert 'data-state="resting"' not in html
    assert '<span class="oq">(company)</span>' in html      # the stored disclosure
    assert labels.FOLLOW_STEADY_PREFIX in html
    assert 'data-topic="Volkswagen"' in html                # STORED name — unfollow target


def test_fix1a_recognized_across_regenerate_headline_drift(monkeypatch):
    """"across a regenerate where the same story recurs": the origin is keyed on
    the story's canonical topic (story_title), so the follow survives a HEADLINE
    drift on the next edition. BORN-RED by ERROR pre-fix (origin_story absent)."""
    db.migrate(db_path=paths.DB_PATH)
    con = db.connect(paths.DB_PATH)
    try:
        memory.add_thread_at_altitude(
            con, "Volkswagen", altitude="entity", primary_entity="Volkswagen",
            disclosure="Volkswagen (company)", alt_label="Volkswagen job cuts",
            confidence="high", source="auto", origin_story="Volkswagen job cuts")
        # a regenerate: SAME story_title, a DRIFTED headline.
        html = server._follow_control(
            {"headline": "VW confirms German plant cuts"},
            {"story_title": "Volkswagen job cuts"}, [], {"volkswagen"},
            "2026-07-19", slug="story-1", con=con)
    finally:
        con.close()
    assert 'data-state="committed"' in html
    assert 'data-topic="Volkswagen"' in html


def test_fix1_storage_persists_and_reads_back_origin_story():
    """The seam: add_thread_at_altitude records the origin story key; the
    render-side accessor reads it back. BORN-RED by ERROR pre-fix (no such
    kwarg / no origin_story column)."""
    db.migrate(db_path=paths.DB_PATH)
    con = db.connect(paths.DB_PATH)
    try:
        memory.add_thread_at_altitude(
            con, "Volkswagen", altitude="entity", primary_entity="Volkswagen",
            disclosure="Volkswagen (company)", source="auto",
            origin_story="Volkswagen job cuts")
        row = server._origin_follow_row(
            con, "Volkswagen job cuts", "Volkswagen plans significant job cuts")
        assert row and row["topic"] == "Volkswagen"
        assert row["disclosure"] == "Volkswagen (company)"
        # a story that is NOT this follow's origin does not match
        assert server._origin_follow_row(con, "Unrelated story", "Unrelated") == {}
    finally:
        con.close()


def test_fix1a_low_pick_origin_recognized_on_reload(monkeypatch):
    """COMPANION path-coverage (the born-red mechanism proof is
    test_fix1a_origin_card_committed_after_reload + _storage): the OTHER commit
    entry — a low-confidence PICK (/api/follow/at, mockup STATE 3) — also stores
    origin_story, so its altitude-renamed follow ("Redemption Gates" for the
    story "Two funds impose redemption gates") is recognized on reload."""
    db.migrate(db_path=paths.DB_PATH)
    _quiet_memory(monkeypatch)
    h = _FollowHandler()
    h._api_follow_at({
        "name": "Redemption Gates", "altitude": "storyline",
        "disclosure": "Redemption Gates (fund-withdrawal story)",
        "alt_label": "Bill Gates (person)",
        "origin": "Two funds impose redemption gates"})
    con = db.connect(paths.DB_PATH)
    try:
        active = server._active_topics_lower(con)            # {"redemption gates"}
        html = server._follow_control(
            {"headline": "Two more funds impose redemption gates as outflows spread"},
            {"story_title": "Two funds impose redemption gates"}, [], active,
            "2026-07-18", slug="story-2", con=con)
    finally:
        con.close()
    assert 'data-state="committed"' in html
    assert 'data-topic="Redemption Gates"' in html          # STORED name
    assert '(fund-withdrawal story)' in html                # the picked disclosure


# ===========================================================================
# FIX-1(b) — no second resolve, no divergent second follow (the QA double)
# ===========================================================================

def test_fix1b_recognized_origin_no_second_resolve_no_double(monkeypatch):
    """Reproduces the QA double-follow: the resolver returns STORYLINE on tap 1
    and ENTITY on tap 2 (two different stored names). Post-fix a tap on the
    already-followed origin short-circuits to committed — the resolver is NOT
    called again and NO second active row appears. BORN-RED (assertion): build-
    minus-loop re-resolves the resting card -> calls == 2 and two active rows
    ("Volkswagen job cuts" + "Volkswagen") for one story."""
    db.migrate(db_path=paths.DB_PATH)
    _quiet_memory(monkeypatch)
    calls = []
    monkeypatch.setattr(fa, "resolve_altitude",
                        _seq_resolver(calls, [_STORYLINE, _ENTITY]))
    h = _FollowHandler()
    body = {"topic": "Volkswagen job cuts",
            "origin": "Volkswagen plans significant job cuts"}
    h._api_follow_resolve(dict(body))                        # tap 1
    h._api_follow_resolve(dict(body))                        # tap 2, same origin

    assert len(calls) == 1                                   # NO second paid resolve
    assert _active_count() == 1                              # XOR — one follow, one story
    # the second tap answered committed (steady-state expand), never re-asked
    assert h.sent[-1][0].get("state") == "committed"


# ===========================================================================
# FIX-2 — focus continuity through the single-node morph (STRUCTURAL pins;
#         live keyboard verification is QA's re-check)
# ===========================================================================

def _fn_body(js: str, name: str) -> str:
    """The source of JS function `name` from webui.JS: from its `function name(`
    to the next top-level `\\nfunction ` (or end). Naive but exact for this flat
    function table — enough to pin per-transition wiring."""
    i = js.index("function " + name + "(")
    j = js.find("\nfunction ", i + 1)
    return js[i:(j if j != -1 else len(js))]


def test_fix2_focus_helper_holds_the_persistent_slot():
    """The roving-tabindex focus keeper exists and lands focus on the slot (the
    ONE node that survives every morph). BORN-RED: no flHold, no focus call."""
    js = webui.JS
    assert "function flHold" in js
    body = _fn_body(js, "flHold")
    assert "slot.focus(" in body                             # focus never dropped to body
    assert "tabindex" in body                                # roving tabindex on the slot


def test_fix2_every_transition_path_restores_focus():
    """Every fl* renderer that replaces .follow-slot innerHTML restores focus
    first. BORN-RED: zero .focus() calls anywhere in the follow flow."""
    js = webui.JS
    for fn in ("flStartResolve", "flRenderCommitted", "flRenderAsk",
               "flRenderDegrade", "flRenderResting", "flCollapseCommitted"):
        assert "flHold(slot)" in _fn_body(js, fn), fn


# ===========================================================================
# FIX-3 — the follow_altitude seat's interactive timeout
# ===========================================================================

def test_fix3_follow_altitude_seat_has_a_short_interactive_timeout():
    """A reader waits on the resolve; a stuck provider must fall to the proven
    degrade fast, not pin "Deciding…" for minutes. BORN-RED: 60/180s."""
    cfg = llm.SEATS["follow_altitude"]
    assert cfg.timeout_s == 8                                # api-lane interactive
    assert cfg.timeout_sub_s == 12                           # subscription-lane interactive
    # the BATCH seats stay generous — only this interactive seat is short
    assert llm.SEATS["rank"].timeout_sub_s == 300
    assert llm.SEATS["writer"].timeout_sub_s == 900


def test_fix3_degrade_copy_is_byte_identical_carried_invariant():
    """CARRIED-INVARIANT (born-GREEN): FIX-3 tunes only the timeout; the degrade
    copy path stays byte-identical. Guards against a copy drift riding the
    timeout change."""
    assert labels.FOLLOW_DEGRADE_COMMITTED == (
        "Following — this story. Couldn't fetch broader follow — "
        "choose it anytime.")


# ===========================================================================
# FIX-4 — the expanded committed line collapses on a second tap
# ===========================================================================

def test_fix4_expanded_committed_carries_aria_expanded_true():
    """The expanded committed sentence declares aria-expanded="true" (honest the
    open way). BORN-RED: the committed render is a bare <span>, no aria.

    R4 (fix loop 2): anchored on the MARKUP literal, not the bare attribute — the
    prior pin matched the FIX-4 doc COMMENT too (comment-satisfiable: re-QA proved
    stripping the markup left it green). The button literal only appears in the
    emitted markup."""
    body = _fn_body(webui.JS, "flRenderCommitted")
    assert '<button class="fl-sentence" type="button" aria-expanded="true"' in body


def test_fix4_expanded_sentence_wires_the_collapse_tap():
    """The committed sentence carries the tap back through followTap ->
    flExpandCommitted -> flCollapseCommitted, and the collapse renders
    aria-expanded="false" (honest the closed way). BORN-RED: the sentence is a
    non-interactive <span>, so flCollapseCommitted is unreachable."""
    committed = _fn_body(webui.JS, "flRenderCommitted")
    assert 'class="fl-sentence"' in committed
    assert "followTap(this)" in committed                    # the sentence is the toggle
    expand = _fn_body(webui.JS, "flExpandCommitted")
    assert "flCollapseCommitted" in expand                   # reachable
    collapse = _fn_body(webui.JS, "flCollapseCommitted")
    assert 'aria-expanded="false"' in collapse               # honest the closed way
