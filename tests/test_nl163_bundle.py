"""NL-163 Stage-A M1 — THE EDITION BECOMES AN ARTIFACT.

Binding sources: the engineering adjudication 2026-08-29 §Q1 (bundle spec) and
§Q7 (read-pure, unanimous); DIRECTION-phone-addendum §§1-11; the approved
mockup-phone-v1.html states 2 and 4; DECISIONS 2026-08-29 (the code-truth
bracket ① and the read-pure divergence ②).

WHAT THIS FILE PINS, and why each pin exists

  * THE ENVELOPE — exactly the adjudicated field set, `stories` deliberately
    absent in v1 (Ada's dissent is on record and was accepted-against), `audio`
    null as a RESERVED schema rather than an omission, `reconstructed` false at
    every mint.
  * READ-PURE (Q7) — no follow VERB, no /api, no fetch, no write vocabulary of
    any kind reaches the document; and the FURNITURE that states follow state
    survives, because withholding it would have been the easy over-correction.
  * THE SEAM DOES NOT LEAK — the read-pure flag is a ContextVar, and the reason
    is concrete: a generate can run on GEN_JOB's background thread inside a
    live `newslens serve`. A module global would have stripped the follow
    controls out of a page another thread was serving.
  * FROZEN AT PUBLISH, MEASURED — the artifact is byte-stable across a follow
    change made AFTER the mint, and the same test proves the LIVE renderer
    moves under that change. Byte-stability alone would pass on a renderer that
    reads nothing.
  * THE MOUNT — samples never mint; a raising mint never costs the edition; the
    mint runs after the log entry exists.
  * THE DESIGN LAW — tokens and the ratified phone scale are CONSUMED from
    webui (never a second table), the standalone frame law is plumbed, and
    every deep view is exitable on-page.

BORN-RED CLASS. Every test under "READ-PURE" and "THE MOUNT" fails at 350234d
(neither `newslens.editionbundle` nor `server.read_pure` exists there); the
HEAD-run list travels with the implementer's report. Tests marked
CARRIED-INVARIANT pin behaviour this milestone must not disturb.

Hermetic, $0, zero sockets — conftest's autouse sandbox and loopback-only guard
cover it, and `test_the_mint_opens_no_socket` measures it rather than asserting
it.
"""
from __future__ import annotations

import json
import re
from html import unescape
from pathlib import Path

import pytest

from newslens import db, editionbundle, labels, server, webui

DATE = "2026-08-02"
GEN_AT = "2026-08-02T06:02:00.000Z"


# ---------------------------------------------------------------------------
# A fixture world: one edition, three stories, one followed thread with prior
# coverage (so the memory stamp and the reason line both have something true to
# say), one tracked mark.
# ---------------------------------------------------------------------------

def _slot(n, *, title, tags=(), mem=(), override=False):
    return {
        "slot": n, "story_title": title, "summary": f"Summary {n}.",
        "item_ids": [n], "outlets": ["Outlet A", "Outlet B"],
        "matched_tags": [{"name": t} for t in tags],
        "matched_memory": list(mem), "matched_dormant": [],
        "followed_analyst": False,
        "personal_score": 1.0 if (tags or mem) else 0.0,
        "world_impact": 9 if override else 6,
        "combined_score": 0.5, "override": override,
        "corroboration_count": 2,
        "corroboration_label": "Reported by 2 named outlets",
        "wire_items_excluded": 0, "revived_threads": [],
        "still_tracking": False, "still_tracking_note": "",
    }


def _story(n, tier):
    return {"tier": tier, "headline": f"Headline {n}",
            "lede": f"The lede sentence for story {n}.",
            "why_label": "Why it matters", "why_it_matters": f"Effects {n}.",
            "watch_label": "Watch for", "watch_for": f"The vote {n}.",
            "my_read": None}


SLOTS = [
    _slot(1, title="Grid load", mem=("Grid load",)),
    _slot(2, title="Private credit", tags=("Private credit",)),
    _slot(3, title="Copper talks", override=True),
]
STORIES = [_story(1, "full"), _story(2, "medium"), _story(3, "quick")]
ENTRY = {
    "date": DATE, "variant": "A", "sample": False, "status": "ok",
    "tiers": ["full", "medium", "quick"],
    "stories": STORIES,
    "total_usd": 1.25,
    "memory": {"state_rewrites": []},
}


def _world(con, *, follow="Grid load", prior_date="2026-08-01"):
    """One published edition plus the memory the furniture reads.

    `prior_date` gives the followed thread a PRIOR covered edition, which is
    what makes `today_memory_stamp` return an ordinal — without it the "Nth
    entry on this thread · Updated" line has nothing true to say and correctly
    renders nothing."""
    con.execute(
        "INSERT INTO briefings (id, date, story_slots, narrative_text,"
        " generated_at) VALUES (1, ?, ?, ?, ?)",
        (prior_date, json.dumps(SLOTS), "# prior\n", "2026-08-01T06:00:00.000Z"))
    con.execute(
        "INSERT INTO briefings (id, date, story_slots, narrative_text,"
        " generated_at) VALUES (2, ?, ?, ?, ?)",
        (DATE, json.dumps(SLOTS),
         "# NewsLens\n\nBody.\n", GEN_AT))
    if follow:
        con.execute(
            "INSERT INTO memory (id, topic, status, last_referenced_briefing_id)"
            " VALUES (1, ?, 'active', 2)", (follow,))
        # One PRIOR delta, because prior coverage is the whole gate on the
        # continuation stamp: a day-one thread gets no "Nth entry" line, ever
        # (the arc's day-one silence, as furniture). Without this row the
        # fixture would quietly pin the absence of the stamp instead of its
        # presence.
        con.execute(
            "INSERT INTO thread_deltas (thread_id, edition_date, slot, verdict,"
            " what_happened, significance, cites_json)"
            " VALUES (1, ?, 1, 'advances', 'It moved.', 'Matters.',"
            " '[\"S1\"]')", (prior_date,))
    # F-1 TOOTH (QA 2026-08-29, adopted by the gate at its proven bytes): one
    # valid analysis brief, so the fixture bundle renders a REAL analyst deep
    # view — facts, mechanism, timeline and prior-briefing rows included —
    # which puts _render_deep_view inside the census tests' reach. Without
    # this row the allowlist census was fixture-blind to the analyst view: a
    # sixth-verb plant in its deterministic footer rode the full 4331-node
    # suite green (QA M-QA-1, measured; gate re-derived). With this row the
    # same plant reds test_the_only_js_call_sites_are_the_three_reads.
    con.execute(
        "INSERT INTO analysis_briefs (date, slot, tier, status, brief_json,"
        " model, cost_usd, created_at) VALUES (?, 1, 'full', 'valid', ?,"
        " 'none', 0, ?)",
        (DATE, json.dumps({
            "header": {},
            "brief": {
                "sources": [
                    {"key": "S1", "kind": "cluster-full-text",
                     "outlet": "Outlet A", "title": "A source",
                     "url": "https://example.com/a",
                     "retrieved_at": "2026-08-02T05:00:00.000Z"},
                    {"key": "S2", "kind": "prior-briefing", "outlet": "NewsLens",
                     "title": "briefing 2026-08-01", "url": "",
                     "retrieved_at": "2026-08-01T06:00:00Z"}],
                "pinned_facts": [{"fact": "A fact.", "cites": ["S1"]}],
                "mechanism": "How it works. [S1]",
                "effects": [], "unknowns": [], "watch": []}}),
         "2026-08-02T06:01:00.000Z"))
    con.execute(
        "INSERT INTO memory (id, topic, status) VALUES (2, 'Private credit',"
        " 'active')")
    con.commit()
    return con


@pytest.fixture
def con():
    db.migrate()
    c = db.connect()
    _world(c)
    yield c
    c.close()


@pytest.fixture
def bundle(con):
    return editionbundle.build_bundle(con, DATE, ENTRY)


_TAGS = re.compile(r"<[^>]+>")


def visible(html: str) -> str:
    return unescape(_TAGS.sub(" ", html))


def markup_only(html: str) -> str:
    """The document with its <style> and <script> blocks removed, so a pin on
    the MARKUP is never satisfied (or broken) by a CSS rule or a comment that
    merely names the same word."""
    out = re.sub(r"<style>.*?</style>", "", html, flags=re.S)
    return re.sub(r"<script>.*?</script>", "", out, flags=re.S)


# ===========================================================================
# THE ENVELOPE (adjudication Q1)
# ===========================================================================

def test_the_envelope_carries_exactly_the_adjudicated_fields(bundle):
    assert set(bundle) == {
        "bundle_version", "edition_date", "generated_at", "variant",
        "content_sha256", "meta", "audio", "html"}
    assert bundle["bundle_version"] == editionbundle.BUNDLE_VERSION == 1
    assert bundle["edition_date"] == DATE
    assert bundle["generated_at"] == GEN_AT
    assert bundle["variant"] == "A"
    assert set(bundle["meta"]) == {
        "story_count", "tier_counts", "cost_usd", "arc_present", "reconstructed"}


def test_stories_are_not_emitted_in_v1_and_the_version_is_why(bundle):
    """Ada's dissent, on the record and accepted-against: shipping `stories[]`
    would let a later stage re-render the past, and re-rendering the past is
    exactly what this milestone exists to stop. `bundle_version` is the escape
    hatch — v2 can add the field without a migration, so the omission is a
    choice rather than a wall."""
    assert "stories" not in bundle
    assert bundle["bundle_version"] == 1


def test_audio_is_a_reserved_null_not_an_omission(bundle):
    """Audio is deferred from Stage A (a 10-minute WAV is ~25-30MB). The field
    is PRESENT and null so the reader has one rule — render no edition bar when
    it is null — instead of two shapes to tell apart, and so no dead player can
    ever appear."""
    assert "audio" in bundle
    assert bundle["audio"] is None
    markup = markup_only(bundle["html"])
    assert "episode-affordance" not in markup
    assert "<audio" not in markup


def test_meta_counts_come_from_the_entry(bundle):
    assert bundle["meta"]["story_count"] == 3
    assert bundle["meta"]["tier_counts"] == {"full": 1, "medium": 1, "quick": 1}
    assert bundle["meta"]["cost_usd"] == 1.25
    assert bundle["meta"]["reconstructed"] is False


def test_content_sha256_digests_the_document_that_ships(bundle):
    """The push endpoint's idempotency key (Q4: same sha -> 200 no-op). It has
    to digest the thing that travels, or a changed document could push as a
    no-op."""
    import hashlib
    assert bundle["content_sha256"] == \
        hashlib.sha256(bundle["html"].encode("utf-8")).hexdigest()


def test_a_changed_document_changes_the_sha(con):
    a = editionbundle.build_bundle(con, DATE, ENTRY)
    other = dict(ENTRY, stories=[_story(9, "full")], tiers=["full"])
    b = editionbundle.build_bundle(con, DATE, other)
    assert a["html"] != b["html"]
    assert a["content_sha256"] != b["content_sha256"]


def test_arc_present_reuses_the_doctors_own_predicate(con):
    """One definition, two readers. `doctor._arc_editions_from_log` keys on
    memory_core's ARC marker constants; re-deriving the answer here is how the
    drought detector and the bundle would start disagreeing about whether an
    edition carried a continuity line."""
    from newslens import memory_core
    authored = dict(ENTRY, memory={"state_rewrites": [
        {"detail": f"x {memory_core.ARC_AUTHORED_MARK} y"}]})
    omitted = dict(ENTRY, memory={"state_rewrites": [
        {"detail": f"x {memory_core.ARC_OMITTED_MARK} y"}]})
    assert editionbundle.build_bundle(con, DATE, authored)["meta"]["arc_present"]
    assert not editionbundle.build_bundle(con, DATE, omitted)["meta"]["arc_present"]
    # No eligible thread at all is NOT a failure to carry an arc — it is a day
    # nothing was due, the same population the NL-160 detector speaks for.
    assert not editionbundle.build_bundle(con, DATE, ENTRY)["meta"]["arc_present"]


def test_a_date_with_no_edition_refuses_rather_than_freezing_nothing(con):
    with pytest.raises(editionbundle.BundleError) as exc:
        editionbundle.build_bundle(con, "2020-01-01", ENTRY)
    assert "nothing to freeze" in str(exc.value)


# ===========================================================================
# READ-PURE (adjudication Q7) — the verbs are withheld, the state is not
# ===========================================================================

# Every write-vocabulary token the shipped renderer can emit on the surfaces a
# bundle carries. Measured, not guessed: this list is the census of
# `onclick="<name>("` over a real edition's today body, deep views and thread
# pages at 350234d.
WRITE_VERBS = ("followTap", "flUnfollow", "flSwitch", "flTap", "flRender",
               "threadAction", "openEditNote", "openDeleteConfirm",
               "removeToken", "generateAgain", "openSettings", "toggleSchedule",
               "openScheduleHour", "commission", "suggestPick")


def test_no_write_verb_reaches_the_document(bundle):
    """BORN-RED. Q7 is unanimous: a verb here would mutate the Mac-bound memory
    DB through a hosted write path — the general inbound command channel the
    Stage-B vocabulary pin forbids before Stage B exists."""
    html = bundle["html"]
    for verb in WRITE_VERBS:
        assert verb not in html, f"write verb {verb} reached the bundle"


def test_the_document_can_talk_to_nothing(bundle):
    """No network vocabulary at all — not an endpoint, not a client, not a
    form. The document IS the product; there is no server behind it."""
    html = bundle["html"]
    for token in ("/api/", "fetch(", "XMLHttpRequest", "WebSocket",
                  "navigator.sendBeacon", "<form", "EventSource"):
        assert token not in html, f"{token} reached the bundle"


def test_the_only_js_call_sites_are_the_three_reads(bundle):
    """The whole interactive surface, enumerated. If a fourth name ever appears
    here it is a new behaviour that has not been ruled on."""
    calls = set(re.findall(r'on(?:click|change|input|submit)="([^"(]+)\(',
                           bundle["html"]))
    assert calls == {"openDeepView", "closeDeepView", "toggleFooterDisclosure"}


def test_no_button_in_the_document_does_anything_but_disclose(bundle):
    """The no-dead-buttons law, measured. A read-pure surface with a button
    that looks live and is not would be worse than one with no button."""
    buttons = re.findall(r"<button[^>]*>", markup_only(bundle["html"]))
    for b in buttons:
        assert "toggleFooterDisclosure" in b, f"unexpected live control: {b}"


def test_follow_STATE_still_renders_as_furniture(con):
    """The guard is NARROW on purpose, and this is the pin that keeps it narrow.
    Q7 preserves the furniture — the tracked-ongoing marker is a bare <span>
    stating that a story matches a thread the reader follows, with no button
    and no handler. Withholding it too would have been the easy
    over-correction, and it would have left the phone unable to say why a story
    is in the edition."""
    # A tracked thread with NO prior coverage, deliberately: where the "Nth
    # entry" stamp shows, the shipped renderer suppresses the marker as
    # redundant (NL-68 item 7), so a stamped story would pin nothing here.
    marked = _slot(1, title="Private credit", mem=("Private credit",))
    kw = dict(date=DATE, con=con, role="lead", held_topics={"private credit"})
    with server.read_pure_render():
        pure = server._render_story(0, _story(1, "full"), marked, "full",
                                    set(), **kw)
    live = server._render_story(0, _story(1, "full"), marked, "full", set(), **kw)
    assert 'data-state="tracked"' in pure
    assert labels.TRACKED_ONGOING_PREFIX in visible(pure)
    assert "<button" not in pure and "onclick" not in pure
    # CONTROL: the marker is not an accident of read-pure — it is what the Mac
    # renders for this story too, and it is the ONE arm the guard sits after.
    assert 'data-state="tracked"' in live


def test_the_reason_line_and_the_updated_stamp_survive(bundle):
    """The ruled mockup-v13 flags, on the phone, unshrunk: the NAME-LED reason
    line and the stamp word "Updated". These are the two things that tell a
    reader why a story is in their edition, and they are frozen at publish
    exactly like everything else."""
    text = visible(markup_only(bundle["html"]))
    assert labels.WHY_TOPIC_ONE in text or labels.WHY_THREAD_ONE in text
    assert labels.MEMLINE_UPDATED in text
    assert 'class="furniture"' in bundle["html"]


_PRIOR_BRIEFING_DOC = {"brief": {
    "sources": [{"key": "S1", "kind": "prior-briefing", "outlet": "NewsLens",
                 "title": "briefing 2026-08-01", "url": "",
                 "retrieved_at": "2026-08-01T06:00:00Z"}],
    "pinned_facts": [{"fact": "A fact.", "cites": ["S1"]}]}}


def test_the_thread_timeline_keeps_the_date_and_drops_the_door(con):
    """A bundle carries ONE edition. `openEdition` fetches another one from the
    Mac server, which this document has no door to.

    CALLED DIRECTLY, on purpose. Routed through a whole edition render this pin
    passed with the enforcement reverted — the fixture's deep view never
    reached either openEdition site, so the assertion was measuring an absence
    that had nothing to do with the guard. Caught by the mutation leg, not by
    reading it and nodding (ENGINEERING.md: a pin whose red has never been
    observed is not proof-class currency)."""
    live = server._deep_timeline_html(con, SLOTS[0], DATE, "story-0")
    with server.read_pure_render():
        pure = server._deep_timeline_html(con, SLOTS[0], DATE, "story-0")
    assert live.count("openEdition") == 1, "the control must reach the door"
    assert "openEdition" not in pure
    # The FACT survives; only the door is gone. A reader still learns the
    # thread moved on that date.
    assert 'class="tl-date"' in pure
    assert "Aug 1" in visible(live) and "Aug 1" in visible(pure)


def test_a_prior_briefing_source_keeps_its_human_title_and_drops_the_door(con):
    """The shipped branch exists to replace a machine title ("briefing
    2026-08-01") with a human one; a bundle reader deserves that too. Only the
    anchor goes."""
    kw = dict(back_label=labels.BACK_TO_TODAYS_EDITION, con=con,
              slot=SLOTS[0], story=STORIES[0])
    live = server._render_deep_view("story-0", "Headline 1",
                                    _PRIOR_BRIEFING_DOC, DATE, **kw)
    with server.read_pure_render():
        pure = server._render_deep_view("story-0", "Headline 1",
                                        _PRIOR_BRIEFING_DOC, DATE, **kw)
    assert live.count("openEdition") >= 1, "the control must reach the door"
    assert "openEdition" not in pure
    human = "NewsLens — Saturday, August 1 edition"
    assert human in visible(live) and human in visible(pure)
    assert "briefing 2026-08-01" not in visible(pure), \
        "the machine title must not resurface when the link is dropped"


# ---- the seam itself ------------------------------------------------------

def test_read_pure_is_off_by_default_and_the_verbs_are_there(con):
    """CARRIED-INVARIANT, and the control for every born-red pin above: the Mac
    surface is UNCHANGED. If this ever goes red, the phone milestone has
    quietly stripped his own web UI."""
    assert server.read_pure() is False
    row = server._briefing_row(con, DATE)
    live = server._render_briefing_body(con, row, ENTRY, None, "", "view-today")
    assert "followTap" in live, "the Mac surface must still offer its verbs"
    assert 'class="deck-follow' in live


def test_the_seam_restores_itself_even_when_the_render_raises():
    """A mint failure must never leave a serve thread rendering read-pure."""
    with pytest.raises(ValueError):
        with server.read_pure_render():
            assert server.read_pure() is True
            raise ValueError("boom")
    assert server.read_pure() is False


def test_the_seam_does_not_leak_across_threads(con):
    """WHY A ContextVar AND NOT A GLOBAL, measured. A generate can run on
    GEN_JOB's background thread INSIDE a live `newslens serve`, so the mint's
    read-pure window overlaps HTTP handler threads rendering his real page. A
    module global would have deleted the follow controls out from under a page
    that was being served at that instant."""
    import threading
    seen = {}

    def other_thread():
        # Its OWN connection: sqlite objects are thread-bound, and a serve
        # handler thread has its own anyway (server.Handler opens one per
        # request). Sharing one here would test sqlite, not the seam.
        seen["read_pure"] = server.read_pure()
        own = db.connect()
        try:
            row = server._briefing_row(own, DATE)
            seen["has_verb"] = "followTap" in server._render_briefing_body(
                own, row, ENTRY, None, "", "view-today")
        finally:
            own.close()

    with server.read_pure_render():
        assert server.read_pure() is True
        t = threading.Thread(target=other_thread)
        t.start()
        t.join()
    assert seen["read_pure"] is False
    assert seen["has_verb"] is True


# ===========================================================================
# FROZEN AT PUBLISH — measured, with its own control
# ===========================================================================

def test_the_artifact_does_not_move_when_follow_state_moves_after_it(con,
                                                                     tmp_path):
    """THE WHOLE POINT OF THE MILESTONE, and it needs both halves.

    Half one: mint, then unfollow the thread, then re-read the artifact — the
    bytes are identical, so the edition of record stopped changing after it was
    published.

    Half two, the CONTROL: re-render LIVE after the same unfollow and prove the
    document WOULD have changed. Without it, half one would pass just as well
    on a renderer that reads nothing at all, and would be proof of nothing."""
    path = editionbundle.write_bundle(
        editionbundle.build_bundle(con, DATE, ENTRY),
        tmp_path / f"{DATE}.phone.json")
    frozen = path.read_bytes()

    live_before = editionbundle.build_html(con, DATE, ENTRY)
    con.execute("UPDATE memory SET status = 'dismissed_user' WHERE topic = ?",
                ("Grid load",))
    con.execute("DELETE FROM memory WHERE topic = 'Private credit'")
    con.commit()
    live_after = editionbundle.build_html(con, DATE, ENTRY)

    assert live_before != live_after, (
        "the control failed: the live render did not move under a follow "
        "change, so byte-stability proves nothing")
    assert path.read_bytes() == frozen, "the published edition changed under us"
    assert json.loads(frozen.decode("utf-8"))["html"] == live_before


def test_the_mint_never_reads_the_generation_log(con, monkeypatch):
    """The entry is handed in as an OBJECT. Reading it back off disk would give
    one edition two sources of truth for a fact the process already holds — and
    on a torn append it would give the phone a different edition from the Mac."""
    def refuse(*a, **kw):
        raise AssertionError("the mint read generation_log.jsonl")
    monkeypatch.setattr(server, "_log_entry_for", refuse)
    monkeypatch.setattr(server, "_run_log_entries", refuse)
    assert editionbundle.build_bundle(con, DATE, ENTRY)["html"]


def test_the_mint_opens_no_socket(con, no_network):
    editionbundle.build_bundle(con, DATE, ENTRY)
    assert no_network == [], f"the mint attempted network: {no_network}"


# ===========================================================================
# THE DESIGN LAW (DIRECTION-phone-addendum)
# ===========================================================================

def test_tokens_are_consumed_from_webui_and_never_retyped(bundle):
    """DESIGN_SYSTEM.md's rule: tokens live in variables, and two files
    declaring the same hexes is how they drift. Every colour literal in the
    document — including the two `theme-color` metas, which are attributes and
    cannot hold a var() — must be a value webui already declares."""
    html = bundle["html"]
    assert webui.TOKENS in html, "the bundle must carry webui's own token block"
    for hexval in set(re.findall(r"#[0-9A-Fa-f]{6}\b", html)):
        assert hexval in webui.CSS, (
            f"{hexval} is declared in the bundle but not in webui.CSS — that is "
            "a second palette")


def test_the_theme_colors_are_derived_from_the_palette(bundle):
    light, dark = editionbundle.theme_colors()
    assert light == "#FCFAF5" and dark == "#1A1713"
    assert f'media="(prefers-color-scheme: light)" content="{light}"' in \
        bundle["html"]
    assert f'media="(prefers-color-scheme: dark)" content="{dark}"' in \
        bundle["html"]


def test_the_ratified_phone_scale_is_lifted_out_of_its_media_query():
    """Addendum §3 ratifies webui's mobile pass AS the phone scale, and §2 says
    the desktop grid must not survive the shrink. The bundle is a phone
    artifact at every width, so the pass is applied unconditionally — extracted
    from the shipped stylesheet rather than retyped, so a re-pin in webui.py
    reaches the next edition with no second table to remember."""
    rules = editionbundle.phone_pass_rules()
    for ruled in (".dateline { font-size: 2.6rem; }",
                  ".signature { font-size: 1.1rem; }",
                  ".dispatch-strip { font-size: 0.74rem; }",
                  ".lead h2.headline { font-size: 2.5rem; line-height: 1.08; }",
                  ".deep-title { font-size: 1.8rem; }",
                  ".page-title { font-size: 2.1rem; }"):
        assert ruled in rules, f"the ratified scale lost {ruled!r}"
    assert "grid-template-columns: 1fr" in rules, "the desktop grid must die"
    assert ".cal-stamp { display: none; }" in rules


def test_the_phone_pass_rides_outside_any_media_query(bundle):
    css = bundle["html"][bundle["html"].index("<style>"):
                         bundle["html"].index("</style>")]
    tail = css[css.rindex("@media (max-width: 900px)"):]
    assert ".dateline { font-size: 2.6rem; }" in tail.split("}\n")[-40:][0] or \
        tail.count(".dateline { font-size: 2.6rem; }") >= 2, \
        "the phone scale must appear again outside the media query"


def test_the_extraction_fails_loudly_if_webui_moves_the_block(monkeypatch):
    """A silently empty phone pass would ship a desktop-scaled document that
    LOOKS finished, so the reader raises instead. The mount contains it, which
    is why a loud failure here costs an artifact and never an edition."""
    monkeypatch.setattr(webui, "CSS", "body { color: red; }")
    with pytest.raises(editionbundle.BundleError) as exc:
        editionbundle.phone_pass_rules()
    assert "ratified scale" in str(exc.value)


def test_a_second_occurrence_of_the_opener_raises_instead_of_guessing():
    """The reader anchors on the FIRST occurrence, so anything else that merely
    NAMES the opener earlier — a comment, a second rule — would silently move
    the brace scan into the wrong block and return a plausible value from it.

    The measured shape, and why it is not hypothetical: webui's dark register is
    OPEN design work (addendum §10), so edits to exactly these blocks are
    named-coming, and a comment in `:root` mentioning `body.dark` is an ordinary
    thing for a designer to write. Pre-guard this CSS returned `#111111` — the
    `:root` value — where the `body.dark` rule plainly says `#222222`, with no
    error at all. A wrong status-bar hex that nothing complains about is the
    precise failure the derive-never-retype law exists to prevent."""
    poisoned = ("/* these are inverted in body.dark below */\n"
                ":root { --paper: #111111; }\n"
                "body.dark { --paper: #222222; }\n")
    with pytest.raises(editionbundle.BundleError) as exc:
        editionbundle._token_value(poisoned, "body.dark", "--paper")
    msg = str(exc.value)
    assert "body.dark" in msg and "occurs 2 times" in msg
    assert "FIRST occurrence" in msg, "the message must name the mechanism"
    # The single-occurrence case is untouched — the guard is a tripwire on a
    # collision, never a new obstacle for the real stylesheet.
    clean = "body.dark { --paper: #222222; }\n"
    assert editionbundle._token_value(clean, "body.dark", "--paper") == "#222222"


def test_the_standalone_frame_law_is_plumbed(bundle):
    """Addendum §5. The insets cannot be MEASURED off a device — `env()`
    resolves to 0 in any desktop browser — so what is pinned here is that every
    surface that needs one declares one. The on-device pass is named in M4's
    only-verifiable-after-his-hands list."""
    html = bundle["html"]
    assert 'content="width=device-width, initial-scale=1.0, viewport-fit=cover"' \
        in html
    assert "calc(1.15rem + env(safe-area-inset-left))" in html
    assert "calc(1.15rem + env(safe-area-inset-right))" in html
    assert "calc(1.6rem + env(safe-area-inset-top))" in html
    assert "calc(2rem + env(safe-area-inset-bottom))" in html
    # A deep view has no masthead, so nothing else absorbs inset-top for it —
    # and its first element is the only way out of the view.
    assert 'section[id^="view-deep-"] { padding-top: env(safe-area-inset-top); }' \
        in html
    assert 'name="apple-mobile-web-app-status-bar-style" content="default"' in html


def test_the_reading_column_is_capped_on_the_view_not_on_page(bundle):
    """MEASURED DEFECT, pinned so it cannot come back. On the Mac `.page` IS
    the reading column, but only part of an edition is wrapped in one — the
    masthead is a `.page`, the story grid and the trust footer are not. With
    the cap on `.page`, a window wider than the column split the document in
    two: at 1400px the masthead rendered 430px centred while the today-grid ran
    1348px full-bleed. The cap belongs on the VIEW, and every `.page` inside
    one is transparent."""
    html = bundle["html"]
    assert '#view-today, section[id^="view-deep-"] {\n  max-width: 430px;' in html
    assert '#view-today .page, section[id^="view-deep-"] .page {\n' \
           '  max-width: none;' in html
    # ...and the grid really is outside any .page, which is why this matters.
    assert '<div class="page' not in \
        html[html.index("</header>"):html.index("today-grid")]


def test_the_skip_link_targets_a_focusable_element(bundle):
    """A skip link that scrolls without moving focus leaves a keyboard reader
    exactly where they were. `#main` carries tabindex="-1" for that reason —
    the shipped shell's own pattern."""
    html = bundle["html"]
    assert '<a class="skip-link" href="#main">' in html
    assert '<main id="main" tabindex="-1">' in html


def test_every_deep_view_is_exitable_on_the_page(bundle):
    """Addendum §6, and the milestone's one stated BLOCK condition: a view
    reachable only by browser chrome is a build BLOCK, not a polish item —
    standalone mode has no browser back button."""
    html = bundle["html"]
    sections = re.findall(r'<section id="view-deep-[^"]+" class="view".*?</section>',
                          html, flags=re.S)
    assert sections, "the fixture must produce at least one deep view"
    # Derived from the constant, never retyped, so a copy re-pin carries here.
    aria = labels.BACK_TO_TODAYS_EDITION.lstrip("←").strip()
    for sec in sections:
        assert 'class="deep-back"' in sec, "a deep view with no way out"
        assert labels.BACK_TO_TODAYS_EDITION in unescape(sec)
        assert f'aria-label="{aria}"' in sec


def test_the_phone_back_label_is_its_own_accessible_name():
    """The addendum §6 copy already CONTAINS the "Back to" that NL-103 row 17's
    derivation prefixes. Deriving on top would have named the link "Back to
    Back to today's edition" — WCAG 2.5.3 label-in-name holds either way, but
    only one of them is a sentence."""
    html = server._back_link(labels.BACK_TO_TODAYS_EDITION, "closeDeepView(event)")
    assert 'aria-label="Back to today’s edition"' in html
    assert html.count("Back to") == 2, "one in the aria name, one visible"


def test_the_phone_back_label_ships_a_typographic_apostrophe():
    """CARRIED-INVARIANT, and it bit on this milestone's first full suite run:
    the addendum prints the copy with a straight apostrophe in markdown prose,
    reader copy in labels.py ships curly, and
    test_nl17_m1c_follow_surface::test_f1_live_reader_copy_uses_typographic_apostrophes
    is the tooth. Pinned here too so the two files fail together rather than
    one of them drifting quietly."""
    assert "'" not in labels.BACK_TO_TODAYS_EDITION
    assert "’" in labels.BACK_TO_TODAYS_EDITION


def test_the_existing_back_labels_are_byte_identical():
    """CARRIED-INVARIANT. The Mac surface keeps NL-103 row 17's bare
    destination; the new branch must reach none of them."""
    for label, expect in ((labels.BACK_TO_TODAY, "Back to Today"),
                          (labels.BACK_TO_EDITION, "Back to This edition"),
                          (labels.BACK_TO_ARCHIVE, "Back to Archive")):
        assert f'aria-label="{expect}"' in server._back_link(label, "x(event)")


def test_dark_mode_is_three_state_over_the_shipped_inversion(bundle):
    """Addendum §10: prefers-color-scheme is the DEFAULT and the manual control
    becomes a three-state override persisted per device, AMENDING the shipped
    two-state localStorage toggle rather than removing it. The palette itself
    is untouched — the runtime only decides whether `body.dark` is on, which is
    why there is no second hex table in this document."""
    html = bundle["html"]
    assert "prefers-color-scheme: dark" in html
    assert "newslens-theme" in html
    for mode in ("'system'", "'light'", "'dark'"):
        assert mode in html
    assert "body.dark" in html          # the shipped inversion, consumed
    assert "setTheme" in html


def test_the_dynamic_type_seed_is_present(bundle):
    """Addendum §4 (Axel, adopted): the only mechanism by which a standalone
    web app honours the iOS text-size setting."""
    assert "-apple-system-body" in bundle["html"]
    assert "document.documentElement.style.fontSize" in bundle["html"]
    # ...and it is GATED ON A TOUCH DEVICE. The ruled mechanism is a reader's
    # iOS text-size PREFERENCE; desktop Safari resolves the same shorthand to a
    # plain 13px system body, which is not a preference and would render
    # `bundle --open` on the desk at 81% scale. REFERENCE-GRADE TRIPWIRE — a
    # string pin, not a behavioural one: the two branches are measured in a
    # real pane (desktop → no seed, 390px touch-emulated → seed) in the
    # fixloop receipts. This exists so the gate cannot be deleted silently.
    assert "navigator.maxTouchPoints > 1" in bundle["html"]


def test_the_offline_stamp_slot_exists_and_is_empty(bundle):
    """Addendum §7: the stamp EXTENDS the dispatch-strip block and is never a
    banner. M1 renders the slot so M2's service worker fills a place that is
    already in the fixed masthead order (§1) instead of inserting one into it."""
    html = bundle["html"]
    assert '<p class="offline-line" id="offline-stamp" hidden></p>' in html
    assert "staleness-banner" not in markup_only(html), \
        "the local generation-failure banner does not migrate to the phone (§7)"
    # ...and it sits after the dispatch strip, inside the masthead.
    mast = html[html.index('class="page masthead"'):html.index("</header>")]
    assert mast.index("dispatch-strip") < mast.index("offline-stamp")
    assert mast.index("offline-stamp") < mast.index("section-line")


def test_the_masthead_ceremony_keeps_its_fixed_order(bundle):
    """§1: wordmark -> dateline -> [signature] -> dispatch strip -> section
    line, and nothing is ever inserted above the wordmark."""
    html = bundle["html"]
    mast = html[html.index('class="page masthead"'):html.index("</header>")]
    order = [mast.index(t) for t in ('class="wordmark"', 'class="dateline"',
                                     'class="dispatch-strip"',
                                     'class="section-line"')]
    assert order == sorted(order)
    assert "settings-corner" not in markup_only(html), \
        "the settings gear opens nothing in a bundle (no-dead-buttons)"


def test_the_dispatch_strip_line_is_consumed_from_the_mac_not_retyped(bundle):
    """The strip's grammar is ONE sentence on both surfaces. It used to be
    written out in both server.py and editionbundle.py, so a re-pin of the
    wording on the Mac would have left the bundle's copy behind — silently, and
    only visible on a phone. The bundle already consumes `_e`, `_dateline_html`
    and `_utc_hm` from server; the strip is the fourth.

    Pinned two ways, because either alone is weak: the emitter's own output must
    appear verbatim in the document, and the literal must exist in exactly one
    module (a re-inlined copy passes the first check and fails the second)."""
    import inspect

    assert server._dispatch_strip_html("06:12") == \
        '<p class="dispatch-strip">Edition assembled 06:12 UTC</p>'

    # the document ships the emitter's bytes, not a look-alike
    assert '<p class="dispatch-strip">Edition assembled ' in bundle["html"]

    # ...and the literal lives in exactly ONE module. A re-inlined copy would
    # pass the check above and fail here, which is the whole point.
    literal = '<p class="dispatch-strip">Edition assembled '
    counts = {m.__name__: inspect.getsource(m).count(literal)
              for m in (server, editionbundle)}
    assert counts["newslens.server"] == 1, counts
    assert counts["newslens.editionbundle"] == 0, \
        f"the strip line was retyped in editionbundle ({counts})"


def test_following_and_archive_do_not_ride_the_bundle(bundle):
    """§4 no-chrome, as the adjudication scoped it: today is the single
    top-level view; Following and Archive are Mac/host surfaces. The section
    line still closes the ceremony, naming the one destination this document
    has — as a word, not a link to nowhere."""
    html = bundle["html"]
    assert 'id="view-following"' not in html
    assert 'id="view-archive"' not in html
    assert f'<span class="section-current" aria-current="page">' \
        f'{labels.NAV_TODAY}</span>' in html
    assert labels.NAV_ARCHIVE not in visible(markup_only(html))


def test_the_document_loads_nothing_from_anywhere(bundle):
    """ONE self-contained document: inline CSS, inline JS, no external fetches.
    Reader-chosen source links may leave the app (§6) — those are anchors, not
    loads — so the pin is on the RESOURCE attributes."""
    markup = markup_only(bundle["html"])
    assert "<link" not in markup
    assert not re.search(r"<script[^>]+src=", markup)
    assert not re.search(r"<(?:img|iframe|video|audio|object|embed)\b", markup)
    assert "<style>" in bundle["html"] and "<script>" in bundle["html"]


def test_motion_stance_adds_no_transition(bundle):
    """§11: no page-transition animation, no pull-to-refresh theatre, no
    skeleton shimmer. The view switch is an immediate jump by construction —
    see the flush in the runtime's jump()."""
    assert "de.style.scrollBehavior = 'auto'" in bundle["html"]
    assert "void de.offsetHeight" in bundle["html"]
    assert "overflow-anchor: none" in bundle["html"]


def test_no_founder_assumption_in_any_string(bundle):
    """THE USERBASE DIRECTIVE (principal 2026-08-24): the register is "the
    paper" and "the paper's operator", never a name and never "your Mac"."""
    text = visible(bundle["html"]).lower()
    for banned in ("your mac", "ravtej", "the founder", "my mac"):
        assert banned not in text, f"founder assumption in the document: {banned}"


# ===========================================================================
# THE ARTIFACT AND THE VERB
# ===========================================================================

def test_the_artifact_is_written_atomically(con, tmp_path, monkeypatch):
    target = tmp_path / "sub" / f"{DATE}.phone.json"
    path = editionbundle.write_bundle(
        editionbundle.build_bundle(con, DATE, ENTRY), target)
    assert path == target and path.exists()
    assert not list(target.parent.glob("*.tmp")), "a temp file survived the write"
    assert json.loads(path.read_text(encoding="utf-8"))["edition_date"] == DATE

    # ...AND THE CRASH WINDOW. The atomicity that matters is that a reader
    # never catches a half-written document, and that half — a good previous
    # artifact surviving a failed re-mint — was already proven. This is the
    # other half: the failed write leaves no `.tmp` litter beside the
    # briefings. A crash is injected at `os.replace`, the last failure point,
    # when the tmp is fully written and most tempting to leave behind.
    good = json.loads(path.read_text(encoding="utf-8"))

    class _CrashingOS:
        """Module-local: patching the real `os.replace` would arm the crash for
        every other caller in the process, not just this write."""

        def __getattr__(self, name):
            return getattr(editionbundle_os, name)

        @staticmethod
        def replace(*a, **kw):
            raise OSError("crash between write and rename")

    editionbundle_os = editionbundle.os
    monkeypatch.setattr(editionbundle, "os", _CrashingOS())
    with pytest.raises(OSError):
        editionbundle.write_bundle(
            editionbundle.build_bundle(con, DATE, ENTRY), target)
    monkeypatch.undo()

    leaked = list(target.parent.glob("*.tmp"))
    assert not leaked, f"the crash window leaked {[p.name for p in leaked]}"
    assert json.loads(path.read_text(encoding="utf-8")) == good, \
        "the already-published artifact must survive a failed re-mint"


def test_the_artifact_lands_beside_the_briefing_and_is_gitignored(con):
    editionbundle.mint(con, DATE, ENTRY)
    path = editionbundle.bundle_path(DATE)
    assert path.exists()
    assert path.name == f"{DATE}.phone.json"
    assert path.parent.name == "briefings"
    assert editionbundle.available_dates() == [DATE]


def test_available_dates_ignores_anything_that_is_not_a_bundle(con):
    editionbundle.mint(con, DATE, ENTRY)
    d = editionbundle.bundle_dir()
    (d / "2026-08-02.md").write_text("x", encoding="utf-8")
    (d / "notes.phone.json").write_text("{}", encoding="utf-8")
    (d / "2026-08-02-variant-B-SAMPLE.md").write_text("x", encoding="utf-8")
    assert editionbundle.available_dates() == [DATE]


def test_a_missing_bundle_is_refused_and_never_reconstructed(con):
    """THE NO-SEEDING RULING. A bundle for a back edition could be built at any
    moment; it would render TODAY's follow state, today's thread names and
    today's reason lines and label the result frozen-at-publish — a lie the
    reader has no way to detect. The archive is honestly empty until the
    mornings fill it (at his eye as consolidated item ④)."""
    editionbundle.mint(con, DATE, ENTRY)
    with pytest.raises(editionbundle.BundleError) as exc:
        editionbundle.load("2026-07-01")
    msg = str(exc.value)
    assert "2026-07-01" in msg
    assert "NOT be reconstructed" in msg
    assert DATE in msg, "the refusal names what IS on file"


def test_load_refuses_a_file_that_is_not_a_bundle(con):
    editionbundle.bundle_dir().mkdir(parents=True, exist_ok=True)
    editionbundle.bundle_path(DATE).write_text("{}", encoding="utf-8")
    with pytest.raises(editionbundle.BundleError) as exc:
        editionbundle.load(DATE)
    assert "no html field" in str(exc.value)


def test_the_cli_shows_a_bundle_and_refuses_a_missing_one(con, capsys):
    from newslens import cli
    editionbundle.mint(con, DATE, ENTRY)
    assert cli.main(["bundle"]) == 0
    out = capsys.readouterr().out
    assert DATE in out and "bundle v1" in out and "3 stories" in out
    assert cli.main(["bundle", "--date", "2020-01-01"]) == 1
    assert "NOT be reconstructed" in capsys.readouterr().err


def test_the_cli_says_so_when_nothing_has_been_bundled_yet(capsys):
    from newslens import cli
    assert cli.main(["bundle"]) == 1
    assert "no frozen editions yet" in capsys.readouterr().err


def test_cli_open_extracts_a_temp_copy_and_never_hands_over_the_artifact(
        con, monkeypatch, capsys):
    """`--open` is the verb the charter names, so its one side effect is
    exercised rather than described. It writes a TEMP COPY: the .json is the
    durable record and the push client's source, and handing a browser a file
    it might be asked to save over is not a trade worth making for one
    convenience."""
    import webbrowser

    from newslens import cli
    editionbundle.mint(con, DATE, ENTRY)
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))

    assert cli.main(["bundle", "--date", DATE, "--open"]) == 0
    capsys.readouterr()
    assert len(opened) == 1
    url = opened[0]
    assert url.startswith("file://") and url.endswith(".html")
    tmp = Path(url[len("file://"):])
    assert tmp.exists()
    assert tmp.read_text(encoding="utf-8") == editionbundle.load(DATE)["html"]
    # the artifact itself is not what the browser was handed
    assert str(editionbundle.bundle_path(DATE)) not in url
    assert editionbundle.bundle_path(DATE).exists()
    tmp.unlink()


def test_the_status_bar_colour_fails_loudly_if_the_dark_block_moves(monkeypatch):
    """The two theme-color metas are the one place a hex has to be spelled (a
    meta attribute cannot hold a var()), so they are READ OUT of webui's own
    :root / body.dark. If either block moves, this raises rather than shipping
    a stale colour that only shows up as a wrong status bar on a phone."""
    monkeypatch.setattr(webui, "CSS", webui.TOKENS + "\nbody { color: red; }")
    with pytest.raises(editionbundle.BundleError) as exc:
        editionbundle.theme_colors()
    assert "body.dark" in str(exc.value)


def test_latest_date_picks_the_newest_bundle_not_the_newest_edition(con):
    """The distinction is the whole no-seeding ruling in one function: editions
    published before this milestone have no bundle and will not get one, so the
    default date is the newest BUNDLE."""
    editionbundle.mint(con, DATE, ENTRY)
    con.execute("INSERT INTO briefings (id, date, story_slots, narrative_text,"
                " generated_at) VALUES (3, '2026-08-03', ?, '# x', ?)",
                (json.dumps(SLOTS), "2026-08-03T06:00:00.000Z"))
    con.commit()
    # a NEWER edition exists, with no bundle
    assert server._briefing_row(con)["date"] == "2026-08-03"
    assert editionbundle.latest_date() == DATE
    editionbundle.mint(con, "2026-08-03", ENTRY)
    assert editionbundle.latest_date() == "2026-08-03"
    assert editionbundle.available_dates() == [DATE, "2026-08-03"]


# ===========================================================================
# THE MOUNT (generate.py, at the publish seam)
# ===========================================================================

def test_the_mount_is_wired_at_the_publish_seam():
    """WIRING PROOF (ENGINEERING.md): the landed call site, not the intent.
    The order is load-bearing — the log entry must EXIST before the bundle that
    describes that run is minted."""
    import inspect

    from newslens import generate
    src = inspect.getsource(generate._run_generate_body)
    assert "log_generation(log_entry)" in src
    assert "editionbundle.mint(con, date, log_entry)" in src
    assert src.index("log_generation(log_entry)") < \
        src.index("editionbundle.mint(con, date, log_entry)")


def test_a_sample_never_mints(con, monkeypatch):
    """A sample is explicitly NOT the briefing of record (write_artifact stamps
    it so) and never persists a row. A phone surface able to show one would be
    showing a comparison variant as the morning's edition."""
    import inspect

    from newslens import generate
    src = inspect.getsource(generate._run_generate_body)
    mount = src[src.index("editionbundle.mint"):]
    head = src[:src.index("editionbundle.mint")]
    guard = head.rindex("if not report.sample:")
    assert guard > head.rindex("log_generation(log_entry)"), \
        "the mint must sit under its OWN sample guard, after the log"
    assert mount  # the mount exists at all

    # ...and behaviourally, BY EXECUTING THE REAL MOUNT. The previous form of
    # this half constructed a GenReport and asserted `bundle_path == ""` — the
    # dataclass DEFAULT, true of any report ever made, reached without running
    # one line of mount code. It would have stayed green over a mount that
    # minted samples every morning. This runs the landed block with a counting
    # stub in the mint's place, so the assertion is about the guard.
    calls = []

    def counting_mint(*a, **kw):
        calls.append(a)
        return editionbundle.bundle_path(DATE)

    monkeypatch.setattr(editionbundle, "mint", counting_mint)

    report = generate.GenReport(date=DATE, variant="A", sample=True)
    exec(compile(_mount_block(), "<mount>", "exec"),
         {"report": report, "con": con, "date": DATE, "log_entry": ENTRY,
          "editionbundle": editionbundle, "__name__": "mount"})
    assert calls == [], f"a sample reached the mint ({len(calls)} call(s))"
    assert report.bundle_path == ""
    assert report.warnings == []

    # ...and the control, so the zero above is the guard's doing and not a
    # broken harness that could never have minted anything.
    control = generate.GenReport(date=DATE, variant="A", sample=False)
    exec(compile(_mount_block(), "<mount>", "exec"),
         {"report": control, "con": con, "date": DATE, "log_entry": ENTRY,
          "editionbundle": editionbundle, "__name__": "mount"})
    assert len(calls) == 1, "the control must prove the mount can mint"


def test_a_raising_mint_costs_the_artifact_and_never_the_edition(con,
                                                                 monkeypatch,
                                                                 tmp_path):
    """CONTAINMENT, MEASURED — the :5485 post-publish precedent. By the time
    control reaches the mount the edition is published, persisted and logged;
    a failure here is a missing convenience artifact, and turning it into a
    crash would be the wrong trade in both directions.

    This exercises the mount's own code path (the try/except as it is written),
    with the failure injected at the module boundary the mount imports."""
    from newslens import generate

    def boom(*a, **kw):
        raise RuntimeError("disk full")
    monkeypatch.setattr(editionbundle, "mint", boom)

    report = generate.GenReport(date=DATE, variant="A", sample=False)
    # Re-run the mount block exactly as generate.py writes it.
    src = _mount_block()
    exec(compile(src, "<mount>", "exec"),
         {"report": report, "con": con, "date": DATE, "log_entry": ENTRY,
          "editionbundle": editionbundle, "__name__": "mount"})

    assert report.bundle_path == ""
    assert len(report.warnings) == 1
    warn = report.warnings[0]
    assert "phone bundle" in warn and "disk full" in warn
    assert "PUBLISHED and unaffected" in warn
    assert "not reconstructed later" in warn
    # the edition's own artifacts are untouched by the failure
    assert server._briefing_row(con, DATE) is not None


def _mount_block() -> str:
    """The mount's body, lifted from the landed source so this test can execute
    the REAL containment rather than a paraphrase of it (a paraphrase would go
    green over a mount that had been deleted)."""
    import inspect
    import textwrap

    from newslens import generate
    src = inspect.getsource(generate._run_generate_body)
    start = src.index("    if not report.sample:\n        try:\n"
                      "            from . import editionbundle")
    end = src.index("    return report", start)
    block = textwrap.dedent(src[start:end])
    return block.replace("from . import editionbundle", "pass")


def test_the_mount_block_extraction_actually_found_the_mount():
    """The helper above is only proof-class if it is reading the real thing."""
    block = _mount_block()
    assert "editionbundle.mint(con, date, log_entry)" in block
    assert "except Exception as exc:" in block
    assert "report.warnings.append" in block


def test_the_report_carries_the_bundle_path_on_success(con, monkeypatch):
    from newslens import generate
    report = generate.GenReport(date=DATE, variant="A", sample=False)
    exec(compile(_mount_block(), "<mount>", "exec"),
         {"report": report, "con": con, "date": DATE, "log_entry": ENTRY,
          "editionbundle": editionbundle, "__name__": "mount"})
    assert report.warnings == []
    assert report.bundle_path.endswith(f"{DATE}.phone.json")
    assert editionbundle.load(DATE)["edition_date"] == DATE
