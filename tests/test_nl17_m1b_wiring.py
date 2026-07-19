"""NL-17-M1b — the implementer's WIRING PROOFS (the follow-altitude picker).

These are the born-red proofs the dispatch owes the gate (ENGINEERING.md R4
law): each fails at c7338d8 (pre-diff) and only the M1b wiring flips it. QA owns
the real-browser DoD; this file owns the three named wiring proofs plus the
server-side STRUCTURAL DOM verification.

BORN-RED at HEAD (c7338d8), by section:
  * RESOLVER FIELD — AltitudeResult has no `alt_label`; the prompt names no such
    field; the validator drops it. (AttributeError / missing-key at HEAD.)
  * SINGLE-DOM-NODE LAW — the committed deck verb and the follow-line are ONE
    persistent node: the steady committed verb carries aria-expanded (never
    aria-haspopup) and the follow-line container reuses the SAME node id the
    verb owns. At HEAD the verb renders `Following this story` with no
    aria-expanded and no follow-line node.
  * EXACT FAILURE COPY — the resolver-failure follow commits with EXACTLY the
    principal's string. Pinned by equality; a byte drifts, it bites.
  * PERSISTENCE / STORAGE — a follow created at an altitude round-trips its
    disclosure + confidence; the medium-auto instrument counts a
    corrected-within-a-day medium auto-commit. At HEAD memory has no altitude
    columns and no follow_altitude_events table.

Offline by construction: no network, no real key, sandbox autouse (conftest).
"""

from __future__ import annotations

import json

import pytest

from newslens import db, follow_altitude as fa, labels, llm, memory, paths, server


# ---------------------------------------------------------------------------
# harness (mirrors test_nl17_m1_altitude.py)
# ---------------------------------------------------------------------------

def _fake_response(content: str, finish: str = "stop",
                   pt: int = 1200, ct: int = 40) -> "llm.LaneResponse":
    raw = {
        "choices": [{"message": {"content": content}, "finish_reason": finish}],
        "usage": {"prompt_tokens": pt, "completion_tokens": ct,
                  "prompt_tokens_details": {"cached_tokens": 0},
                  "cache_creation_tokens": 0},
    }
    return llm.LaneResponse(
        content=content, usage=llm.Usage(prompt_tokens=pt, completion_tokens=ct),
        finish_reason=finish, raw=raw)


class _Chat:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def __call__(self, req):
        self.prompts.append(req.prompt)
        content, finish = self.replies.pop(0)
        return _fake_response(content, finish)


def _pick(altitude="entity", primary="Volkswagen",
          disclosure="Volkswagen (company)", confidence="high",
          alt_label="Volkswagen job cuts") -> str:
    payload = {"altitude": altitude, "primary_entity": primary,
               "disclosure": disclosure, "confidence": confidence}
    if alt_label is not None:
        payload["alt_label"] = alt_label
    return json.dumps(payload)


# ===========================================================================
# PROOF 1 — the resolver field (alt_label)
# ===========================================================================

def test_altituderesult_has_alt_label_field(monkeypatch):
    """The other rung's name is a first-class resolver field (build rider).
    BORN-RED: AltitudeResult has no `alt_label` at HEAD."""
    chat = _Chat([(_pick(alt_label="Volkswagen job cuts"), "stop")])
    monkeypatch.setattr(llm, "chat", chat)
    res = fa.resolve_altitude(fa.ThreadInput(1, "Volkswagen job cuts"))
    assert res.alt_label == "Volkswagen job cuts"


def test_validate_keeps_alt_label_when_present():
    parsed = fa._validate(_pick(alt_label="Volkswagen job cuts"))
    assert parsed["alt_label"] == "Volkswagen job cuts"


def test_alt_label_is_optional_lawful_fallback():
    """alt_label is a prompt-compatible EXTENSION: an answer without it is still
    valid (M1a back-compat), and the UI supplies the worded fallback. BORN-RED
    at HEAD only because _validate returns no such key."""
    parsed = fa._validate(_pick(alt_label=None))
    assert parsed["alt_label"] == ""


def test_prompt_names_alt_label_and_compact_grammar():
    """Prompts are code: the re-landed prompt instructs the compact qualifier
    grammar and the alt_label field. BORN-RED: neither token is in the M1a
    prompt."""
    law = fa._system_law()
    assert "alt_label" in law
    # the tail grammar is gone; the compact parenthetical class is taught
    assert "(company)" in law
    # the OUTPUT SPEC re-lands compact — the M1a "sentence" instruction is gone
    assert "compact qualifier-grammar name" in law
    assert "altitude-naming sentence" not in law


# ===========================================================================
# PROOF 3 — the EXACT failure copy (string-equality pin)
# ===========================================================================

def test_exact_resolver_failure_copy():
    """Principal's verbatim string, pinned by equality. BORN-RED: the label
    does not exist at HEAD."""
    assert labels.FOLLOW_DEGRADE_COMMITTED == (
        "Following — this story. Couldn't fetch broader follow — "
        "choose it anytime.")


def test_failure_copy_split_matches_mockup():
    """The moment surface renders the degrade as two lines (mockup STATE 4);
    the ROW upgrade line is the second sentence alone. Both are pinned so the
    label table and the render can never drift apart."""
    assert labels.FOLLOW_DEGRADE_LEAD == "Following — this story."
    assert labels.FOLLOW_DEGRADE_UPGRADE == (
        "Couldn't fetch broader follow — choose it anytime.")
    # the two halves compose the exact whole (one grammar, one place)
    assert (labels.FOLLOW_DEGRADE_LEAD + " " + labels.FOLLOW_DEGRADE_UPGRADE
            == labels.FOLLOW_DEGRADE_COMMITTED)


# ===========================================================================
# PROOF 4 — persistence + the medium-auto instrument
# ===========================================================================

def _fresh_db():
    db.migrate(db_path=paths.DB_PATH)
    return db.connect(paths.DB_PATH)


def test_follow_at_altitude_roundtrips_disclosure():
    """A follow created through the picker stores its altitude disclosure +
    confidence; the accessor reads them back. BORN-RED: memory has no altitude
    columns at HEAD."""
    from newslens import memory
    con = _fresh_db()
    try:
        memory.add_thread_at_altitude(
            con, "Volkswagen", altitude="entity", primary_entity="Volkswagen",
            disclosure="Volkswagen (company)", alt_label="Volkswagen job cuts",
            confidence="high", source="auto")
        # display columns live on memory (render reads them verbatim — 0018 law)
        row = con.execute(
            "SELECT altitude, primary_entity, disclosure, alt_label,"
            " altitude_source FROM memory WHERE lower(topic) = 'volkswagen'"
        ).fetchone()
        assert row["altitude"] == "entity"
        assert row["primary_entity"] == "Volkswagen"
        assert row["disclosure"] == "Volkswagen (company)"
        assert row["alt_label"] == "Volkswagen job cuts"
        assert row["altitude_source"] == "auto"
        # confidence is instrument data — it lives in the append-only event log
        ev = con.execute(
            "SELECT kind, confidence, source FROM follow_altitude_events"
            " WHERE kind = 'commit'").fetchone()
        assert ev["confidence"] == "high" and ev["source"] == "auto"
    finally:
        con.close()


# ===========================================================================
# PROOF 2 — the SINGLE-DOM-NODE law (structural DOM; QA owns the browser DoD)
# ===========================================================================

def test_resting_follow_is_one_slot_aria_expanded_not_haspopup():
    """The follow control is ONE persistent .follow-slot node carrying the deck
    verb; the verb declares aria-expanded (never the retired aria-haspopup /
    aria-pressed). BORN-RED: at HEAD it renders `.follow-story-btn` with
    aria-pressed and no slot/aria-expanded."""
    st = {"headline": "Volkswagen plans significant job cuts"}
    slot = {"story_title": "Volkswagen plans significant job cuts"}
    html = server._follow_control(st, slot, [], set(), "2026-07-18",
                                  slug="story-0", con=None)
    assert html.count('class="follow-slot"') == 1        # exactly one node
    assert 'id="follow-story-0"' in html
    assert 'aria-expanded="false"' in html
    assert "aria-haspopup" not in html                   # build rider: retired
    assert "aria-pressed" not in html                    # old toggle grammar gone
    assert labels.FOLLOW_STORY_INACTIVE in html          # "○ Follow this story"
    # the resting target is the HEADLINE (the resolver names the altitude on tap)
    assert "data-topic=" in html and "job cuts" in html


def test_committed_verb_carries_the_disclosure_qualifier():
    """STATE 5 (single-rendering steady): the committed deck verb renders the
    stored disclosure — "● Following — Volkswagen (company)" — as ONE .follow-slot
    node with aria-expanded and no second follow-state surface.

    HONESTY FIX (fix-loop 1): the story_title now DIVERGES from the stored follow
    name (story "Volkswagen job cuts", follow "Volkswagen") — the real HIGH/MED
    shape, where the resolver renames the follow. The prior version hand-aligned
    story_title="Volkswagen" with the entity name, so name-match recognition
    passed and MASKED the FIX-1 gap (origin card unrecognized after reload).
    Recognition here can fire ONLY through the origin_story bridge; data-topic
    must be the STORED name so unfollow/switch hit the real row."""
    con = _fresh_db()
    try:
        memory.add_thread_at_altitude(
            con, "Volkswagen", altitude="entity", primary_entity="Volkswagen",
            disclosure="Volkswagen (company)", alt_label="Volkswagen job cuts",
            confidence="high", source="auto", origin_story="Volkswagen job cuts")
        st = {"headline": "Volkswagen plans significant job cuts"}
        slot = {"story_title": "Volkswagen job cuts"}     # DIVERGES from the name
        html = server._follow_control(st, slot, [], {"volkswagen"}, "2026-07-18",
                                      slug="story-1", con=con)
        assert html.count('class="follow-slot"') == 1
        assert 'data-state="committed"' in html
        assert 'aria-expanded="false"' in html and "aria-haspopup" not in html
        assert labels.FOLLOW_STEADY_PREFIX in html       # "Following —"
        assert 'data-topic="Volkswagen"' in html         # STORED name — unfollow target
        assert '<span class="oq">(company)</span>' in html   # quiet class split
        assert 'data-alt-label=' in html                 # switch offer stored
    finally:
        con.close()


def test_committed_verb_narrow_and_unmigrated_bare():
    """narrow -> "— this story"; an UNMIGRATED follow (no stored disclosure) ->
    bare "● Following", never a fabricated qualifier (honest v1 mix)."""
    con = _fresh_db()
    try:
        memory.add_thread_at_altitude(con, "Some headline", altitude="narrow",
                                      source="degrade")
        st = {"headline": "Some headline"}
        slot = {"story_title": "Some headline"}
        html = server._follow_control(st, slot, [], {"some headline"},
                                      "2026-07-18", slug="s", con=con)
        assert labels.FOLLOW_NARROW in html              # "this story"
        # unmigrated: an active follow with NO altitude columns -> bare
        memory.add_thread(con, "Old Thread")
        html2 = server._follow_control(
            {"headline": "Old Thread"}, {"story_title": "Old Thread"}, [],
            {"old thread"}, "2026-07-18", slug="o", con=con)
        assert labels.FOLLOW_COMMITTED_VERB in html2     # "Following"
        assert '<span class="oq">' not in html2          # bare — no fabricated class
        assert labels.FOLLOW_STEADY_PREFIX not in html2  # no "— <name>" either
    finally:
        con.close()


def test_render_story_emits_exactly_one_follow_state_node():
    """SINGLE-RENDERING at the card grain: a story card renders the follow state
    ONCE — one .follow-slot, and the retired .follow-story-btn class is gone."""
    con = _fresh_db()
    try:
        st = {"headline": "A headline", "lede": "Body."}
        slot = {"story_title": "A headline", "outlets": [], "matched_memory": []}
        html = server._render_story(0, st, slot, "analyst", set(), has_file=False,
                                    slug="story-0", date="2026-07-18", con=con)
        assert html.count('class="follow-slot"') == 1
        assert "follow-story-btn" not in html            # old node retired
    finally:
        con.close()


# ===========================================================================
# WAVE E — the altitude qualifier on every Following surface (Kass's law)
# ===========================================================================

def test_following_rows_render_the_altitude_qualifier():
    """Screen 2: entity + ambiguous-storyline carry the quiet '(class)'; narrow
    carries '— this story'; a descriptive storyline AND an unmigrated follow
    render BARE (name states its class, or no altitude — nothing fabricated)."""
    con = _fresh_db()
    try:
        memory.add_thread_at_altitude(con, "Volkswagen", altitude="entity",
                                      disclosure="Volkswagen (company)",
                                      source="auto")
        memory.add_thread_at_altitude(con, "Volkswagen job cuts",
                                      altitude="storyline",
                                      disclosure="Volkswagen job cuts",
                                      source="pick")
        memory.add_thread_at_altitude(
            con, "Redemption Gates", altitude="storyline",
            disclosure="Redemption Gates (fund-withdrawal story)", source="pick")
        memory.add_thread_at_altitude(con, "Some headline", altitude="narrow",
                                      source="pick")
        memory.add_thread(con, "Old Bare Thread")           # unmigrated
        html = server._following_threads_subview(server._following_rows(con))
        assert '<span class="alt-q">(company)</span>' in html
        assert '<span class="alt-q">(fund-withdrawal story)</span>' in html
        assert '<span class="alt-q">— this story</span>' in html
        # ONLY the three qualified rows carry alt-q (bare storyline + unmigrated
        # do not — unconditional disclosure, but the NAME may carry it)
        assert html.count('class="alt-q"') == 3
    finally:
        con.close()


def test_degrade_narrow_full_row_keeps_the_upgrade_door():
    """A resolver-failure narrow follow keeps the exact quiet upgrade line in its
    full row; a reader's deliberate 'just this story' pick shows no nag."""
    degrade = {"id": 1, "topic": "Fund gating at Meridian", "altitude": "narrow",
               "altitude_source": "degrade", "disclosure": "",
               "this_delta": {"date": "2026-07-18", "what_happened": "x"},
               "note": ""}
    pick = dict(degrade, altitude_source="pick", id=2)
    # the upgrade sentence renders HTML-escaped (the apostrophe -> &#x27;); assert
    # on its apostrophe-free tail so the pin tracks the copy, not the escaping
    tail = "fetch broader follow — choose it anytime."
    assert tail in server._spine_updated_row(degrade)
    assert tail not in server._spine_updated_row(pick)


def test_medium_auto_commit_corrected_within_a_day_is_counted():
    """Axel's instrument: a MEDIUM-confidence AUTO commit, corrected (altitude
    changed / unfollowed) within 24h, is counted. BORN-RED: no
    follow_altitude_events table + no stats fn at HEAD."""
    from newslens import memory
    con = _fresh_db()
    try:
        # a medium auto-commit, then a same-moment correction (switch)
        memory.add_thread_at_altitude(
            con, "Acme", altitude="entity", primary_entity="Acme",
            disclosure="Acme (company)", alt_label="Acme lawsuit",
            confidence="medium", source="auto")
        memory.record_altitude_correction(con, "Acme")
        # a high-confidence auto-commit corrected the same day must NOT count
        memory.add_thread_at_altitude(
            con, "Globex", altitude="entity", primary_entity="Globex",
            disclosure="Globex (company)", alt_label="Globex merger",
            confidence="high", source="auto")
        memory.record_altitude_correction(con, "Globex")
        stats = memory.medium_correction_stats(con)
        assert stats["medium_auto_commits"] == 1
        assert stats["corrected_within_day"] == 1
    finally:
        con.close()
