"""NL-134 — the fresh-profile fix batch (principal's notes 2026-08-02, walk on
profile `fresh1`). Three items, red-first:

  * F1 — the override-note DOUBLE-RENDER. server.py rendered `override_label`
    (which ranking.py:1320 already stores as OVERRIDE_LABEL_PREFIX + the
    ranker's prose reason) and then appended `world_impact_reason` — the SAME
    text — in a <span class="reason"> with no separator. The principal's
    specimen read "…energy prices.Pause in potential…". The specimen strings in
    this file are that receipt, verbatim from
    profiles/fresh1/data/briefings/2026-08-02.md.

  * F2 — COLD-START HONESTY. (a) On a first briefing there is no "usual", so a
    your-usual-map framing is a false claim about a reader history that does
    not exist; the writer path now says so. (b) The example-becomes-template
    class (M9-M2 lineage): both variant prompts supplied "Off your usual map,
    but:" as a quotable example and the writer parroted it — two specimens
    (fresh1 2026-08-02, persona public-health 2026-07-28). The guidance now
    instructs by SHAPE and supplies no phrase to copy.

  * F3 — THE WHY-CHOSEN LINE, the principal's display spec (binding, verbatim):
    "The reason for the story should be just be displayed as 'Chosen because:'
    or 'Related to:' and then '{relevant topics the user follows} or Important
    World News.'" Folds NL-117. Front-page surfaces only — the model's full
    reason stays persisted on the slot and surfaces, labeled, in the deep view.

BORN-RED CLASS. Every test here except the three marked CARRIED-INVARIANT
(born-green) fails on HEAD c3778c9; the HEAD-run fail list travels with the
implementer's report per the 2026-07-18 gate ruling.

Offline by construction (conftest autouse sandbox + loopback-only guard).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from html import unescape

import pytest

from newslens import config, db, generate, labels, paths, ranking, server

_TAGS = re.compile(r"<[^>]+>")


def visible(html: str) -> str:
    """The page as a reader sees it — markup stripped — so a copy pin reads the
    COPY and not the furniture carrying it (the label rides its own <span>,
    following the _still_tracking_line idiom)."""
    return unescape(_TAGS.sub("", html))

DATE = "2026-08-02"

# The principal's specimen, verbatim (fresh1, 2026-08-02 edition). The reason
# ends "…energy prices" and the label form appends a period, which is exactly
# how the two renders butted together into "energy prices.Pause in potential".
SPECIMEN_REASON = (
    "Pause in potential military action affects global oil markets and Middle "
    "East regional stability with direct consequences for international "
    "shipping and energy prices"
)
SPECIMEN_CONCATENATION = "energy prices.Pause in potential"


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def slot(n=1, tags=(), mem=(), override=False, followed=False,
         outlets=("Outlet A",), reason="A systemic development."):
    """A persisted-shape slot, as _slots_for hands them to the renderer."""
    return {
        "slot": n, "story_title": f"Story {n}", "summary": "S.",
        "item_ids": [n], "outlets": list(outlets),
        "matched_tags": [dict(t) for t in tags],
        "matched_memory": list(mem), "matched_dormant": [],
        "followed_analyst": followed,
        "personal_score": 1.0 if (tags or mem) else 0.0,
        "world_impact": 9 if override else 6,
        # NL-138: `world_impact_reason` is deliberately STILL SET here. New
        # rows never carry it, but ARCHIVED story_slots rows on the founder's
        # machine do — so this fixture is now the archived-row shape, and the
        # F1/F3 pins below are testing that no surface reads it. The stored
        # `override_label` is gone with the constant that built it.
        "world_impact_reason": reason,
        "combined_score": 0.5, "override": override,
        "corroboration_count": 1,
        "corroboration_label": "Reported by 1 named outlet",
        "wire_items_excluded": 0, "revived_threads": [],
        "still_tracking": False, "still_tracking_note": "",
    }


def story(headline="A headline", lede="The lede sentence."):
    return {"tier": "full", "headline": headline, "lede": lede,
            "why_label": "Why it matters", "why_it_matters": "Effects.",
            "watch_label": "Watch for", "watch_for": "The vote.",
            "my_read": None}


def render(sl, role="story", tier="full", **kw):
    return server._render_story(0, story(), sl, tier, set(), date=DATE,
                                role=role, **kw)


def inputs_for(slots, continuity="none"):
    prior = ({"text_block": "PRIOR EDITION CONTEXT"}
             if continuity == "ok" else None)
    return {"slots": slots, "items_by_slot": {s["slot"]: [] for s in slots},
            "threads": [], "prior_ctx": prior,
            "continuity_status": continuity, "window_meta": None,
            "corroboration": {}}


# ===========================================================================
# F1 — the double-render dies
# ===========================================================================

def test_f1_the_specimen_reason_cannot_render_twice_on_a_story_card(tmp_paths):
    """BORN RED on c3778c9. The exact fresh1 specimen: the label already ends
    with the reason, then the old <span class="reason"> appended it again with
    no separator. The concatenation artefact the principal saw must be
    unrenderable, and the reason must not appear on the card at all."""
    html = render(slot(override=True, reason=SPECIMEN_REASON))
    assert SPECIMEN_CONCATENATION not in html, (
        "the F1 concatenation is back — the reason is rendering twice")
    assert html.count(SPECIMEN_REASON) == 0, (
        "the ranker's prose reason is on the front page; the principal's spec "
        "allows only the short why-chosen line there")


def test_f1_the_old_override_note_furniture_is_gone_everywhere(tmp_paths):
    """BORN RED on c3778c9. The <p class="override-note"> and its .reason child
    were the double-render's whole mechanism; neither the markup nor the
    OVERRIDE_LABEL_PREFIX prose reaches a front-page story any more."""
    html = render(slot(override=True, reason=SPECIMEN_REASON))
    assert 'class="override-note"' not in html
    assert 'class="reason"' not in html
    # NL-138 deleted ranking.OVERRIDE_LABEL_PREFIX along with the prose it
    # prefixed. Its text is quoted literally here so this pin keeps guarding
    # the exact string that must never come back.
    assert "This story doesn't match your tagged interests" not in html


def test_f1_holds_on_every_tier_including_the_strip(tmp_paths):
    """BORN RED on c3778c9. The old callout rendered on every tier "including a
    strip", so the duplication did too. Check all three shapes."""
    for role in ("lead", "story", "strip"):
        html = render(slot(override=True, reason=SPECIMEN_REASON), role=role,
                      tier="quick" if role == "strip" else "full")
        assert SPECIMEN_CONCATENATION not in html, f"{role} still duplicates"
        assert SPECIMEN_REASON not in html, f"{role} still recites the reason"


# ===========================================================================
# F3 — the reason line, in the principal's format
#
# RE-PINNED 2026-08-24 (NL-117/NL-121, mockup-v13 PASSED — DECISIONS
# "[2026-08-24] THE SLATE RULED" item 4). His browser gate ruled flag ②
# NAME-LED, which retires the STEM this section was written around: the line no
# longer opens "Related to:" / "Chosen because:" — it leads with the mechanism's
# own output and states the CLASS in words after an em dash. The gate also moved
# the line's MOUNT off the position above the headline (the passed artifact
# draws no line there) into the trailing furniture / the strip smeta / both deep
# views.
#
# WHAT DID NOT CHANGE, and is what these pins are actually for: the line is
# code-owned and never prose; it names EVERY match, never the first before a
# comma; the writer credit names an outlet only when sources.yaml resolves one
# and never fabricates a byline; a config failure degrades instead of taking the
# page down; the line is never empty; it rides every tier including the strip;
# the NL-68 doubling exhibit stays dead; hostile names stay escaped. Each pin
# below keeps its own property and moves only the string it asserts.
# ===========================================================================

def test_f3_interest_matched_story_names_the_topics_and_their_class(tmp_paths):
    """BORN RED on c3778c9 (the line did not exist). Form 1: the reader follows
    these topics, so the line names them — every match, not the first one before
    a comma — and says which class they are."""
    html = render(slot(tags=({"name": "Energy policy", "level": "broad"},
                             {"name": "Oil markets", "level": "specific"})))
    assert ("Energy policy, Oil markets — topics you follow"
            in visible(html))
    assert labels.WHY_WORLD_NEWS not in html


def test_f3_world_impact_override_says_important_world_news(tmp_paths):
    """BORN RED on c3778c9. Form 2: his exact class words, and with the stem
    ruled away they are the whole fill. No name, nothing accented."""
    html = render(slot(override=True, reason=SPECIMEN_REASON))
    assert "Important World News." in visible(html)
    assert "why-name" not in html, "the world fill accents nothing"


def test_f3_thread_selected_story_names_the_thread_and_its_class(tmp_paths):
    """BORN RED on c3778c9. Form 3: no tag match, but a tracked thread put it
    here — the thread's display name is the honest answer, and the class word
    is 'thread', never the verb 'Following' (that word opens a control)."""
    html = render(slot(mem=("Hormuz Grain Corridor",)))
    assert "Hormuz Grain Corridor — a thread you follow" in visible(html)
    assert "Following" not in visible(html).split("Hormuz Grain Corridor")[1]


def test_f3_followed_writer_credit_names_the_outlet_when_it_resolves(
        tmp_paths, monkeypatch):
    """BORN RED on c3778c9. Form 4: followed_analyst is the selection basis and
    the outlet name comes from sources.yaml, never from the slot (which carries
    only a bool). The name must be the reader's own configured outlet."""
    monkeypatch.setattr(server, "_followed_writer_outlets",
                        lambda: {"Stratechery"})
    sl = slot(followed=True, outlets=("Stratechery", "Wire Co"))
    line = server._reason_line_text(sl, server._followed_writer_outlets())
    assert line == "Stratechery — a writer you follow"
    assert "Wire Co" not in line, "an un-followed outlet was credited"


def test_f3_followed_writer_degrades_to_the_un_named_credit(tmp_paths):
    """BORN RED on c3778c9. When sources.yaml resolves no matching outlet (an
    unreadable config, or a followed outlet the corroboration count excluded),
    the credit renders WITHOUT a name — never a fabricated byline, never an
    empty line. Name-led leaves the class words leading the sentence, so this
    state has its own sentence-initial constant."""
    sl = slot(followed=True, outlets=("Some Wire",))
    assert server._reason_line_text(sl, set()) == labels.WHY_WRITER_LED


def test_f3_unreadable_sources_file_degrades_instead_of_raising(
        tmp_paths, monkeypatch):
    """BORN RED on c3778c9. A config problem must not take the front page
    down: the resolver returns the empty set and the line still renders."""
    def boom(*a, **kw):
        raise config.SourcesParseError("sources.yaml is not valid YAML")
    monkeypatch.setattr(config, "load_sources", boom)
    assert server._followed_writer_outlets() == set()
    html = render(slot(followed=True))
    assert labels.WHY_WRITER_LED in visible(html)


def test_f3_the_line_is_never_empty_for_any_slot_shape(tmp_paths):
    """BORN RED on c3778c9. "NEVER render an empty, false, or model-verbose
    reason on front surfaces" — including a slot with nothing on it at all
    (an older persisted row), which falls to the world-news form because
    world impact is literally why it is there."""
    closing = (labels.WHY_TOPIC_ONE, labels.WHY_TOPIC_MANY,
               labels.WHY_THREAD_ONE, labels.WHY_THREAD_MANY,
               labels.WHY_FOLLOWED_WRITER, labels.WHY_WRITER_MANY,
               labels.WHY_WRITER_LED, labels.WHY_WORLD_NEWS)
    for sl in ({}, slot(), slot(override=True), slot(followed=True),
               {"matched_tags": None, "matched_memory": None}):
        line = server._reason_line_text(sl)
        assert line.strip(), f"empty reason line for {sl!r}"
        assert line.endswith(closing), line
    assert server._reason_line_text({}) == labels.WHY_WORLD_NEWS


def test_f3_the_line_rides_every_tier_including_the_strip(tmp_paths):
    """BORN RED on c3778c9. NL-117's order was a provenance line on EVERY
    story; the strip (the quick-tier grout) is a story. The MOUNT differs by
    tier per mockup-v13 (cards carry it in the trailing furniture, the strip in
    its smeta) — the SENTENCE does not."""
    sl = slot(tags=({"name": "Energy policy", "level": "broad"},))
    for role, tier, mount in (("lead", "full", 'class="furniture"'),
                              ("story", "medium", 'class="furniture"'),
                              ("strip", "quick", 'class="smeta"')):
        html = render(sl, role=role, tier=tier)
        assert mount in html, f"{role} has no reason-line mount"
        assert "Energy policy — a topic you follow" in visible(html), role


def test_f3_the_reason_shows_just_once_per_card(tmp_paths):
    """BORN RED on c3778c9. "just be displayed as" is load-bearing: the old
    card answered the same question twice — the override note above the title
    AND "Here for: …" in the bottom furniture. ONE line, and the count is what
    proves it: the mount moved to the furniture at the 2026-08-24 gate, so a
    second rendering above the headline would show up here as a second
    occurrence, exactly as the pre-F3 duplication did."""
    html = render(slot(tags=({"name": "Energy policy", "level": "broad"},)))
    assert html.count("Energy policy") == 1
    assert "Here for:" not in html
    assert 'class="furniture"' in html
    assert "Reported by 1 named outlet" in html


def test_f3_strip_smeta_carries_the_class_worded_reason_not_a_name_slice(
        tmp_paths):
    """RE-PINNED 2026-08-24 (mockup-v13 R2). The strip's machine meta line used
    to carry the first name BEFORE THE FIRST COMMA — a truncation, not an
    answer — and NL-134 F3 deleted that echo because the line above the headline
    then held the whole answer. The gate moved the answer back INTO this line,
    but class-worded and whole: the truncation stays dead, the ambiguity it
    caused stays dead, and the strip is still the one tier with no bottom
    furniture, so the pin lives inside the smeta element."""
    html = render(slot(tags=({"name": "Energy policy", "level": "broad"},
                             {"name": "Oil markets", "level": "specific"})),
                  role="strip", tier="quick")
    smeta = html.split('<p class="smeta">')[1].split("</p>")[0]
    assert "Reported by 1 named outlet" in smeta     # smeta itself survives
    assert "Energy policy, Oil markets — topics you follow" in visible(smeta)
    assert html.count("Energy policy") == 1, "the reason renders twice"


def test_f3_full_reason_is_gone_from_the_deep_view_too_nl138(tmp_paths):
    """SUPERSEDED SUBJECT — this pin used to assert the opposite.

    F3 parked the ranker's full prose reason in the deep view: off the front
    page but preserved, labeled, as provenance. The principal's ruling ④ later
    the same day (NL-138) retired that compromise — the sentence is not
    provenance, it is a claim about the reader's interests the model was never
    shown ("implies the user had global oil ... as one of their topics, which
    they didn't"), and preserving it on a quieter surface preserves the same
    inaccuracy in smaller type.

    Driven with the ARCHIVED shape (the fixture still sets the field), because
    "we stopped writing it" would not be enough: rows carrying it exist."""
    db.migrate()
    con = db.connect()
    try:
        sec = server._render_sources_context_view(
            "s0", "A headline", story(),
            slot(override=True, reason=SPECIMEN_REASON), con, DATE)
    finally:
        con.close()
    assert labels.WHY_FULL_REASON not in sec
    assert SPECIMEN_REASON not in sec
    # RE-PINNED 2026-08-24: `.sc-reason` was the label-block's own class and its
    # absence was how NL-138's kill was pinned. That class is now the REASON
    # LINE's mount in this view (it replaced the "Here for: …" sentence), so
    # absence-of-class no longer discriminates. The property is unchanged and
    # pinned on what actually carried the defect: the model's prose sentence and
    # its label, neither of which any surface renders.
    assert labels.WHY_FULL_REASON.rstrip(":") not in sec
    # The structured provenance this view exists for is untouched.
    assert "Matched topics:" in sec or "Tracked threads:" in sec \
        or labels.WHY_WORLD_NEWS in sec


def test_f3_deep_view_reason_is_absent_on_every_slot_shape_nl138(tmp_paths):
    """F3's original property was "override-only and empty-safe". NL-138 made
    it unconditional: no slot shape renders the label, so the matched and
    empty arms below now prove ABSENCE everywhere rather than absence in two
    special cases. Kept rather than folded into the test above because these
    two shapes are the ones that used to take the other branch."""
    db.migrate()
    con = db.connect()
    try:
        matched = server._render_sources_context_view(
            "s0", "H", story(),
            slot(tags=({"name": "Energy policy", "level": "broad"},)), con,
            DATE)
        empty = server._render_sources_context_view(
            "s0", "H", story(), slot(override=True, reason=""), con, DATE)
    finally:
        con.close()
    assert labels.WHY_FULL_REASON not in matched
    assert labels.WHY_FULL_REASON not in empty


def test_f3_labels_are_live_not_captured(tmp_paths, monkeypatch):
    """WIRING PROOF (the label-table liveness idiom): the render reads
    labels.<NAME> at call time, so a re-pin of the string table reaches the
    page. A captured import-time constant fails this."""
    monkeypatch.setattr(labels, "WHY_TOPIC_ONE", "REPIN-TOPIC")
    monkeypatch.setattr(labels, "WHY_THREAD_ONE", "REPIN-THREAD")
    monkeypatch.setattr(labels, "WHY_WORLD_NEWS", "REPIN-WORLD")
    monkeypatch.setattr(labels, "WHY_WRITER_LED", "REPIN-WRITER")
    monkeypatch.setattr(labels, "WHY_ALSO_TOPIC_ONE", "REPIN-ALSO")
    assert "REPIN-TOPIC" in render(
        slot(tags=({"name": "Energy policy", "level": "broad"},)))
    assert "REPIN-THREAD" in render(slot(mem=("A thread",)))
    assert "REPIN-WORLD" in render(slot(override=True))
    assert "REPIN-WRITER" in render(slot(followed=True))
    mixed = render(slot(mem=("A thread",),
                        tags=({"name": "A topic", "level": "broad"},)))
    assert "REPIN-ALSO" in mixed


def test_f3_carried_invariant_the_line_escapes_hostile_names(tmp_paths):
    """CARRIED-INVARIANT (born-green): tag and thread names come from the
    ranked web through the model, and every carrier of them escapes through _e.
    HEAD passed this through the "Here for" furniture; the why-chosen line is
    the new carrier and inherits the obligation, so the pin travels with it."""
    hostile = '</p><img src=x onerror=alert(1)>'
    html = render(slot(tags=({"name": hostile, "level": "broad"},)))
    assert "<img" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_f3_the_nl68_dedupe_holds_on_the_new_surface_too(tmp_paths):
    """BORN RED on c3778c9 — the reason-line half does not exist there. (The
    _here_for half alone is a carried invariant, pinned separately below.) The
    NL-68 exhibit — a tag and a tracked thread of the same name doubling the
    line, "Strait of Hormuz, Strait of Hormuz" — must stay dead on BOTH
    surfaces.

    RE-PINNED 2026-08-24: the class-worded grammar resolves the collision the
    OTHER way from selection_names — the THREAD wins the class seat, because
    the thread fill is the one that carries the continuity/delta obligation
    (2026-07-31 round §6, mixed-match rule). One name, one class, one
    appearance, on both surfaces; only the surviving class differs.

    RE-AIMED 2026-08-27 (NL-165 ③): "BOTH surfaces" now means the reason line
    and the MARKDOWN lane, which is what the phrase was always supposed to mean
    — the merged-list half moved to generate.here_for_text when the dead server
    shim was retired, and it is read by an artifact the principal opens."""
    sl = {"matched_tags": [{"name": "Strait of Hormuz", "level": "specific"}],
          "matched_memory": ["strait of hormuz"]}
    assert generate.here_for_text(sl) == "Strait of Hormuz"
    assert server._reason_line_text(sl).lower().count("strait of hormuz") == 1
    # Same exhibit, both names in the record's own casing: the thread's
    # spelling is what renders, because the thread is what the line names.
    exact = {"matched_tags": [{"name": "Strait of Hormuz", "level": "specific"}],
             "matched_memory": ["Strait of Hormuz"]}
    assert server._reason_line_text(exact) == \
        "Strait of Hormuz — a thread you follow"


def test_f3_carried_invariant_here_for_is_unchanged_for_its_own_surfaces(
        tmp_paths):
    """CARRIED-INVARIANT (born-green): F3 took the "Here for" clause off the
    front page only. The rationale keeps every branch it had — but since the
    NL-117/121 increment (2026-08-24) NO HTML surface calls it: the deep views
    render the name-led reason line and generate.py's markdown lane composed its
    meta-line locally.

    RE-AIMED 2026-08-27 — this is the pin gate R-2 named. The retirement it was
    waiting on happened (NL-165 ③) and it went the way the charter's CAUTION
    asked: the shim is gone, the pin is not. "Generate.py composes its meta-line
    locally" was the sentence that mattered — that local composition WAS the
    NL-68 violation, and closing it gave this pin a live surface to hold. It now
    guards the branches of the rationale the markdown lane actually renders,
    rather than a law-of-record with no reader."""
    assert generate.here_for_text({"matched_tags": [{"name": "AI regulation"}],
                                   "matched_memory": []}) == "AI regulation"
    assert generate.here_for_text({"override": True}) == \
        "editor's override — see note above"
    assert generate.here_for_text({}) == \
        "world-impact selection (no tag or thread match)"


def test_f3_full_edition_render_carries_the_line_and_not_the_reason(tmp_paths):
    """BORN RED on c3778c9. End-to-end through the real edition body renderer
    (the path Today and the archive-in-place edition share), not just the story
    helper: the line is present, the prose reason is not."""
    db.migrate()
    con = db.connect()
    try:
        slots = [slot(1, tags=({"name": "Energy policy", "level": "broad"},)),
                 slot(2, override=True, reason=SPECIMEN_REASON)]
        stories = [story("Matched story", "Lede one."),
                   story("Override story", "Lede two.")]
        narrative = generate.assemble_narrative(
            DATE, "A", stories, inputs_for(slots))
        con.execute(
            "INSERT INTO briefings (date, story_slots, corroboration_labels,"
            " narrative_text, generated_at) VALUES (?, ?, ?, ?, ?)",
            (DATE, json.dumps(slots),
             json.dumps({"standing_caveat": ranking.CORROBORATION_CAVEAT,
                         "per_story": []}), narrative, iso_now()))
        con.commit()
        row = con.execute("SELECT * FROM briefings WHERE date = ?",
                          (DATE,)).fetchone()
        body = server._render_briefing_body(con, row, None, None, "",
                                            "view-today")
    finally:
        con.close()
    seen = visible(body)
    assert "Energy policy — a topic you follow" in seen
    assert f"{labels.WHY_WORLD_NEWS}." in seen
    assert SPECIMEN_CONCATENATION not in body
    assert "Here for:" not in body


def test_f3_writer_credit_absorbs_a_same_named_tag_or_thread(tmp_paths):
    """BORN RED on the NL-134 land (gate FIX-2, 2026-08-02): a followed tag or
    thread named identically to a followed-writer outlet must not stutter the
    line — 'Stratechery, Stratechery (a writer you follow)' (gate probe
    receipt, both exact-case and case-variant). The credit form carries the
    name; the bare name folds into it, case-insensitively, per
    selection_names' own dedupe convention (live at generate.selection_names
    since NL-165 ③)."""
    sl = slot(tags=({"name": "Stratechery", "level": "specific"},),
              followed=True, outlets=("Stratechery",))
    assert server._reason_line_text(sl, {"Stratechery"}) == \
        "Stratechery — a writer you follow"
    sl_case = slot(tags=({"name": "stratechery", "level": "specific"},),
                   followed=True, outlets=("Stratechery",))
    line = server._reason_line_text(sl_case, {"Stratechery"})
    assert line.lower().count("stratechery") == 1


# ===========================================================================
# F2(a) — cold-start honesty reaches the writer
# ===========================================================================

def test_f2a_first_briefing_override_line_forbids_reader_history_claims(
        tmp_paths):
    """BORN RED on c3778c9. WIRING PROOF: continuity_status 'none' (no prior
    briefing row exists) reaches the writer's per-story OVERRIDE line, which
    now states there is no "usual" and bans every reader-history claim."""
    prompt = generate.build_narrative_prompt(
        DATE, "A", inputs_for([slot(1, override=True)], continuity="none"))
    assert "THIS IS THE READER'S FIRST BRIEFING" in prompt
    assert "no 'usual' for this story to be off" in prompt
    assert "Make NO claim about what the reader normally reads" in prompt


def test_f2a_established_reader_arm_now_forbids_the_prose_acknowledgement(
        tmp_paths):
    """REVERSED BY NL-166 (2026-08-27) — recorded, not quietly rewritten.

    WAS (this pin, NL-134 F2(a)): "acknowledging off-interest inclusion stays
    legal on a non-first edition". NL-134's complaint was the FALSE history
    claim and the verbatim crutch; it left the acknowledgment legal rather
    than ruling that print owed one.

    NOW: his format law (DECISIONS 2026-08-27, "HIS FORMAT LAW (fourth
    sitting)") plus his specimen complaint ("I dont like the weird long form
    way the 'chosen for' is being explained in the briefing, and how its part
    of the prose/narrative of the story") make the in-prose acknowledgment a
    doubling of a disclosure the printed label already pays once.

    WHAT THIS PIN STILL GUARDS, unchanged: an established reader takes the
    ESTABLISHED arm, never the cold-start one."""
    prompt = generate.build_narrative_prompt(
        DATE, "A", inputs_for([slot(1, override=True)], continuity="ok"))
    assert "ALREADY MADE" in prompt                  # the established arm
    assert "do NOT acknowledge" in prompt
    assert "may acknowledge" not in prompt
    assert "THIS IS THE READER'S FIRST BRIEFING" not in prompt


def test_f2a_corrupt_continuity_is_not_a_first_edition(tmp_paths):
    """BORN RED on c3778c9 (via the established arm's new clause). The
    distinction the M4 gate mandated: a prior briefing whose record is
    unreadable is NOT "no prior briefing" — the reader HAS a history there, so
    the cold-start claim would itself be false. 'corrupt' takes the ESTABLISHED
    arm, and this pin fails if it ever takes the cold-start one."""
    prompt = generate.build_narrative_prompt(
        DATE, "A", inputs_for([slot(1, override=True)], continuity="corrupt"))
    assert "THIS IS THE READER'S FIRST BRIEFING" not in prompt
    # NL-166 re-points the DISCRIMINATOR only: the established arm's text
    # changed (the prose acknowledgment is now forbidden, not licensed), so
    # the string that identifies the arm changed with it. What this pin
    # asserts — that 'corrupt' takes the established arm — is untouched.
    assert "ALREADY MADE" in prompt                   # the established arm
    assert "do NOT acknowledge" in prompt


def test_f2a_carried_invariant_cold_start_line_is_override_scoped(tmp_paths):
    """CARRIED-INVARIANT (born-green) — a NEGATIVE-SPACE guard, and it can
    only be born green: it asserts the absence of furniture, which HEAD also
    lacked. It earns its place by pinning SCOPE — the cold-start line rides the
    override story it belongs to and never leaks onto a matched story."""
    prompt = generate.build_narrative_prompt(
        DATE, "A", inputs_for([slot(1)], continuity="none"))
    assert "THIS IS THE READER'S FIRST BRIEFING" not in prompt
    assert "OVERRIDE STORY" not in prompt


@pytest.mark.parametrize("variant", ["A", "B"])
def test_f2a_both_variants_carry_the_cold_start_arm(tmp_paths, variant):
    """BORN RED on c3778c9. The override line is composed in generate.py, so it
    must reach BOTH writer variants — the 07-28 public-health specimen was one
    variant and the 08-02 fresh1 specimen the other."""
    prompt = generate.build_narrative_prompt(
        DATE, variant, inputs_for([slot(1, override=True)], continuity="none"))
    assert "THIS IS THE READER'S FIRST BRIEFING" in prompt


# ===========================================================================
# F2(b) — the example-becomes-template class
# ===========================================================================

_VARIANTS = ("narrative_variant_a.txt", "narrative_variant_b.txt")


@pytest.mark.parametrize("fname", _VARIANTS)
def test_f2b_no_variant_supplies_the_quotable_override_phrase(fname):
    """BORN RED on c3778c9 (a:229, b:148). The M9-M2 class: the prompt's own
    example becomes the writer's template. Two specimens parroted this exact
    phrase — it is not guidance any more, in either file."""
    text = (paths.PROMPTS_DIR / fname).read_text(encoding="utf-8")
    assert "Off your usual map" not in text
    assert "acknowledge that naturally" not in text


@pytest.mark.parametrize("fname", _VARIANTS)
def test_f2b_both_variants_instruct_by_shape_instead(fname):
    """BORN RED on c3778c9. The replacement teaches the move without handing
    over a phrase, and names the cold-start case in the file the principal
    edits — the prompts stay plain and principal-editable.

    NL-166 (2026-08-27) RETIRES TWO OF THE THREE ASSERTIONS, and says why
    rather than deleting them quietly: "in YOUR OWN WORDS" and "no phrase to
    copy" shaped an in-prose acknowledgment that his format law abolishes.
    There is no sentence left to shape, so there is no phrase to withhold —
    the guidance instructs by PROHIBITION now. The third assertion is the one
    that outlived the license (the cold-start case is still named in the file
    the principal edits) and it stays, joined by the new prohibition."""
    text = (paths.PROMPTS_DIR / fname).read_text(encoding="utf-8")
    assert "FIRST briefing" in text
    assert "do NOT\n  acknowledge, explain, or allude to that" in text


@pytest.mark.parametrize("fname", _VARIANTS)
def test_f2b_rewritten_guidance_stays_above_the_cache_sentinel(fname):
    """BORN RED on c3778c9 — the anchor text is new there. The INVARIANT is
    old (ADR-0016 §6: the law sits in the cached system prefix); this pin asks
    whether the REWRITTEN guidance still precedes the split sentinel, which
    only the post-diff files can answer.

    NL-166 RE-ANCHORS the pin — the guidance was rewritten again, in place, so
    the anchor string moved with it. The INVARIANT it guards is untouched:
    this law sits in the cached system prefix, above the per-run tags block."""
    text = (paths.PROMPTS_DIR / fname).read_text(encoding="utf-8")
    at = text.find("THE PIPELINE'S LABEL CARRIES IT")
    sentinel = text.find(generate._NARRATIVE_CACHE_SENTINEL)
    assert at != -1 and sentinel != -1
    assert at < sentinel, "the override guidance drifted below the sentinel"
