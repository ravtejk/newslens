"""NL-117 + NL-121, the UI half — THE REASON LINE and THE HONEST QUIET.

Ruling of record: mockup-v13 PASSED 2026-08-24 (DECISIONS "[2026-08-24] THE
SLATE RULED" item 4), with both gate flags ruled — deck stamp = "Updated" (NOT
"New today"), stem = NAME-LED (NOT "Here for:"). Binding spec:
workspace/debates/2026-07-31--newslens--design.md §5 (Spec A, the honest quiet)
and §6 (Spec B, the rank-reason line).

WHAT THIS FILE PINS

  * THE GRAMMAR — one line, code-owned, NAME-LED, three fills of a closed
    vocabulary (topics you follow · a thread you follow · Important World News),
    plus the shipped writer class and the ruled mixed form. No stem anywhere.
  * ONE MOUNT PER SURFACE — the trailing furniture on cards, the smeta's final
    clause on strips, the same sentence in both deep views (superset law).
    Nothing above the headline any more: that was the NL-134 F1/F3 position and
    the passed artifact draws no line there.
  * NO PER-SURFACE SYNONYM DRIFT — the class words are the identical strings on
    every mount; a strip never says "world news" where a card says "Important
    World News".
  * THE NO-FABRICATION LADDER — mechanisms only, never a guess; no hedging
    vocabulary exists in this component by construction; a deep view with no
    slot renders no line at all.
  * FLAG ① — the deck stamp's word is "Updated", and it is one constant reaching
    both the card stamp and the strip's degraded form.
  * NL-121 §5 — the honest quiet: Today renders NOTHING for a thread with no
    candidate (no slot, no placeholder, no "nothing new" line), the Following
    row's LAST UPDATED does not advance, and the thread counts into the existing
    counted quiet-fold. Zero new strings, which is the point.

NOT IN THIS INCREMENT, and deliberately unpinned: the continuation rider
("· New since <Mon D>: <delta ≤12w>"). Its delta clause has no ≤12-word source
in the tree — the only candidate field, thread_deltas.what_happened, measures a
median 29 words on the founder's own 65 rows — and producing a bounded
substantiated diff is the delta gate's job, which is the principal's gate before
build. Reported, not faked from the UI side.

BORN-RED CLASS: every test here fails at b5fcde5 except those marked
CARRIED-INVARIANT (born-green), which pin behaviour this increment must not
disturb. The HEAD-run fail list travels with the implementer's report (gate
ruling 2026-07-18).

Offline by construction (conftest autouse sandbox + loopback-only guard).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from html import unescape

from newslens import db, labels, server

_TAGS = re.compile(r"<[^>]+>")

DATE = "2026-08-02"
TODAY = datetime.now().strftime("%Y-%m-%d")


def visible(html: str) -> str:
    """The page as a reader sees it — markup stripped — so a copy pin reads the
    COPY and not the furniture carrying it."""
    return unescape(_TAGS.sub("", html))


def _con():
    db.migrate()
    return db.connect()


def slot(n=1, tags=(), mem=(), override=False, followed=False,
         outlets=("Outlet A",)):
    return {
        "slot": n, "story_title": f"Story {n}", "summary": "S.",
        "item_ids": [n], "outlets": list(outlets),
        "matched_tags": [{"name": t} for t in tags],
        "matched_memory": list(mem), "matched_dormant": [],
        "followed_analyst": followed,
        "personal_score": 1.0 if (tags or mem) else 0.0,
        "world_impact": 9 if override else 6,
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


# ===========================================================================
# THE GRAMMAR — name-led, closed vocabulary, one line
# ===========================================================================

def test_the_stem_is_gone_from_every_reader_surface():
    """FLAG ② RULED NAME-LED. Neither the spec's default stem ("Here for:") nor
    the NL-134 F3 stems it superseded ("Related to:" / "Chosen because:") may
    open the line on any web surface. The line leads with the mechanism's own
    output."""
    for sl in (slot(tags=("Inflation",)), slot(mem=("A thread",)),
               slot(followed=True), slot(override=True), {}):
        line = server._reason_line_text(sl)
        for stem in ("Here for:", labels.WHY_RELATED_TO,
                     labels.WHY_CHOSEN_BECAUSE):
            assert not line.startswith(stem), f"{line!r} still opens with a stem"


def test_the_three_ruled_fills_render_name_led_with_their_class_words():
    """The closed vocabulary, one grammar, three fills. The class words are the
    load-bearing half: a bare name is class-ambiguous (a followed topic? a
    followed thread? a world pick?), which is why first-name-only lost."""
    assert server._reason_line_text(slot(tags=("Inflation",))) == \
        f"Inflation — {labels.WHY_TOPIC_ONE}"
    assert server._reason_line_text(
        slot(tags=("Private credit", "Markets"))) == \
        f"Private credit, Markets — {labels.WHY_TOPIC_MANY}"
    assert server._reason_line_text(slot(mem=("ERCOT curb-load",))) == \
        f"ERCOT curb-load — {labels.WHY_THREAD_ONE}"
    assert server._reason_line_text(slot(override=True)) == \
        labels.WHY_WORLD_NEWS


def test_the_mixed_match_gives_the_thread_the_class_seat():
    """Ruled form (§6): "thread fill wins the class seat (it carries the delta
    obligation); topic names join the list". The thread leads; the topics ride a
    trailing clause. The plural is mechanical — same words, number agreeing with
    the count the mechanism produced."""
    one = server._reason_line_text(
        slot(mem=("ERCOT curb-load",), tags=("Power grids",)))
    assert one == (f"ERCOT curb-load — {labels.WHY_THREAD_ONE} · "
                   f"{labels.WHY_ALSO_TOPIC_ONE} Power grids")
    many = server._reason_line_text(
        slot(mem=("ERCOT curb-load",), tags=("Power grids", "Texas")))
    assert many.endswith(f"{labels.WHY_ALSO_TOPIC_MANY} Power grids, Texas")


def test_the_class_verb_is_never_the_follow_control_s_verb():
    """Cleo's collision rule, ratified in the 2026-07-31 round: "Following"
    opens a control, and a reason line that borrows it reads as a second toggle
    — the reader taps furniture. No fill may contain the word."""
    for sl in (slot(tags=("Inflation",)), slot(mem=("A thread",)),
               slot(followed=True, outlets=("Stratechery",)),
               slot(mem=("A thread",), tags=("A topic",)), {}):
        for _names, words, _wf in server._reason_segments(sl, {"Stratechery"}):
            assert "Following" not in words, words


def test_no_hedging_vocabulary_exists_in_the_component():
    """"No hedging vocabulary exists in this component, by construction" (§6
    adjudication): a mechanism that fired IS the reason. This greps the whole
    closed vocabulary the composer can emit, so a hedge cannot be introduced by
    adding a fill."""
    banned = ("possibly", "probably", "may be", "might be", "appears",
              "seems", "likely", "we think", "believed")
    vocabulary = " ".join(
        words
        for sl in (slot(tags=("A",)), slot(tags=("A", "B")),
                   slot(mem=("T",)), slot(mem=("T",), tags=("A",)),
                   slot(mem=("T",), tags=("A", "B")),
                   slot(followed=True, outlets=("Stratechery",)),
                   slot(followed=True, outlets=("Wire",)), {})
        for _n, words, _wf in server._reason_segments(sl, {"Stratechery"})
    ).lower()
    for word in banned:
        assert word not in vocabulary, word


def test_hostile_names_are_escaped_on_the_html_mount():
    """BORN RED at b5fcde5 — but MECHANICALLY, and the label says so rather
    than inflating the proof class: the composer this names does not exist
    there, so the red is an AttributeError, not a missing property. The PROPERTY
    is carried — tag and thread names come from the ranked web through the
    model and the line that carried them before escaped them too. The new
    composer inherits the obligation and this is the tooth on it."""
    html = server._reason_line_html(
        slot(tags=('</p><img src=x onerror=alert(1)>',)))
    assert "<img" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


# ===========================================================================
# ONE MOUNT PER SURFACE
# ===========================================================================

def test_no_reason_line_renders_above_the_headline_any_more():
    """THE PLACEMENT MOVE (mockup-v13 R1). The NL-134 F1/F3 line rendered above
    the title on every tier. The passed artifact draws NO line there: the reason
    is trailing furniture. Pinned structurally — everything before the headline
    element must be free of the reason — so a re-add cannot hide behind a
    whole-card substring check."""
    for role, tier in (("lead", "full"), ("story", "medium"),
                       ("strip", "quick")):
        html = render(slot(tags=("Energy policy",)), role=role, tier=tier)
        head = html.split("<h2")[0].split("<h3")[0]
        assert "Energy policy" not in head, f"{role}: reason above the headline"
        assert labels.WHY_TOPIC_ONE not in head, role


def test_the_card_mounts_the_reason_as_its_own_trailing_furniture_sibling(
        tmp_paths):
    """HIS OPTION (b), RULED 2026-08-25 (gate R-5, flag 3). This pin previously
    read the other way — `furniture[1].startswith("Reported by 1 named outlet —
    Outlet A. ")`, i.e. the reason APPENDED to the corroboration sentence inside
    one paragraph. The passed artifact's `[specimen]` corroboration ran ~120
    characters and the shape looked fine; his real edition's card furniture runs
    390, so in the tree the reason was the tail of a long source list. Option
    (b) keeps the ruled POSITION (still the last thing in the story block) and
    gives the reason its own `<p class="furniture">` SIBLING.

    The strip is untouched by this ruling — its reason stays the smeta's final
    clause (mockup-v13 R2), pinned by the next test.

    Corroboration renders only when it has content: a slot with no label and no
    outlets leaves ONE paragraph — the reason — never the bare "." this line
    used to emit in that state (the bare-dot precedent)."""
    html = render(slot(tags=("Energy policy",)), role="lead", tier="full")
    furn = [p.split("</p>")[0]
            for p in html.split('<p class="furniture">')[1:]]
    assert len(furn) == 2, f"corroboration + reason must be siblings: {furn}"
    corroboration, reason = furn
    assert corroboration == "Reported by 1 named outlet — Outlet A."
    assert labels.WHY_TOPIC_ONE not in corroboration, "the reason is still buried"
    assert "Reported by" not in reason, "corroboration leaked into the reason"
    assert f"Energy policy — {labels.WHY_TOPIC_ONE}." in visible(reason)

    bare = slot(tags=("Energy policy",), outlets=())
    bare["corroboration_label"] = ""
    lone = [p.split("</p>")[0]
            for p in render(bare, role="lead", tier="full")
                     .split('<p class="furniture">')[1:]]
    assert len(lone) == 1, f"an empty corroboration must not mount: {lone}"
    assert not visible(lone[0]).startswith("."), "the bare-dot fragment is back"
    assert f"Energy policy — {labels.WHY_TOPIC_ONE}." in visible(lone[0])


def test_the_strip_mounts_the_reason_as_the_smeta_s_final_clause(tmp_paths):
    """mockup-v13 R2: the strip has no bottom furniture, so its reason is the
    machine line's LAST clause — compact, class-worded, plain (no accent: in a
    one-register mono line the words alone carry the class)."""
    html = render(slot(tags=("Energy policy",)), role="strip", tier="quick")
    smeta = html.split('<p class="smeta">')[1].split("</p>")[0]
    assert smeta.endswith(f"Energy policy — {labels.WHY_TOPIC_ONE}")
    assert "why-name" not in smeta, "the strip accents nothing"
    assert 'class="furniture"' not in html


def test_the_names_are_accented_inline_with_no_container_on_cards():
    """DIRECTION/token law carried by the artifact: names take accent + weight,
    INLINE — no fill, no border, no container (the chip ban). Colour is never
    the sole channel, which is why the class words are in the text."""
    html = server._reason_line_html(slot(tags=("Energy policy",)))
    assert '<span class="why-name">Energy policy</span>' in html
    assert labels.WHY_TOPIC_ONE in html          # the class, in plain text
    for container in ("<div", "<button", "<li", "border", "background"):
        assert container not in html


def test_the_deep_view_carries_the_card_s_sentence_verbatim(tmp_paths):
    """SUPERSET LAW (NL-68 item 3, §6 placement): the deep view always contains
    at least the card's content, so the reason is the SAME sentence — not a
    variant, not a re-word.

    RE-AIMED for his option (b), 2026-08-25: the card's reason is no longer the
    tail of the corroboration paragraph, so this pin no longer slices it off
    `"Outlet A. "` — it reads the reason's OWN furniture sibling. Red on pre-fix
    bytes by property (one furniture paragraph, not two), not by exception; the
    superset property it tests is unchanged and was true before."""
    sl = slot(tags=("Energy policy",))
    card = render(sl, role="lead", tier="full")
    furn = [p.split("</p>")[0]
            for p in card.split('<p class="furniture">')[1:]]
    assert len(furn) == 2, f"option (b): the reason is its own sibling: {furn}"
    card_reason = furn[1]
    con = _con()
    try:
        deep = server._render_deep_view(
            "story-0", "A headline",
            {"brief": {"sources": [], "facts": [], "mechanism": "M."},
             "header": {}}, DATE, con=con, slot=sl, story=story())
    finally:
        con.close()
    assert card_reason in deep, "the deep view re-words the card's reason"


def test_the_quick_tier_deep_view_mounts_the_same_line(tmp_paths):
    """The $0 sources-&-context view's "Here for: …" sentence IS this line now —
    one spelling across card, strip and both deep views."""
    con = _con()
    try:
        html = server._render_sources_context_view(
            "story-0", "A headline", story(), slot(mem=("ERCOT curb-load",)),
            con, DATE)
    finally:
        con.close()
    assert "Here for:" not in html
    reason = html.split('<p class="sc-reason">')[1].split("</p>")[0]
    assert f"ERCOT curb-load — {labels.WHY_THREAD_ONE}." in visible(reason)


def test_no_per_surface_synonym_drift_across_the_three_mounts(tmp_paths):
    """Cleo's drift rule: the class words are IDENTICAL strings on every mount.
    Same slot, three surfaces, one sentence."""
    sl = slot(mem=("ERCOT curb-load",), tags=("Power grids",))
    expected = visible(server._reason_line_html(sl)).rstrip(".")
    card = visible(render(sl, role="story", tier="medium"))
    strip = visible(render(sl, role="strip", tier="quick"))
    con = _con()
    try:
        quick_deep = visible(server._render_sources_context_view(
            "story-0", "A headline", story(), sl, con, DATE))
    finally:
        con.close()
    for surface, name in ((card, "card"), (strip, "strip"),
                          (quick_deep, "quick deep view")):
        assert expected in surface, name


def test_a_deep_view_with_no_slot_renders_no_reason_line(tmp_paths):
    """THE LADDER'S FIRST RUNG: no mechanism data on the slot -> the line is
    ABSENT, never a guessed or generic reason. An archived edition whose slots
    did not persist hits exactly this."""
    con = _con()
    try:
        deep = server._render_deep_view(
            "story-0", "A headline",
            {"brief": {"sources": [], "facts": [], "mechanism": "M."},
             "header": {}}, DATE, con=con, slot=None, story=story())
    finally:
        con.close()
    for words in (labels.WHY_TOPIC_ONE, labels.WHY_THREAD_ONE,
                  labels.WHY_WORLD_NEWS, labels.WHY_FOLLOWED_WRITER):
        assert words not in deep, words


# --- the rung at all FOUR mounts (gate R-1, 2026-08-24) --------------------
#
# The increment legislated the ladder's first rung and built it on ONE mount of
# four, falsy-only. The gate widened the class and ruled the predicate: a slot
# carries a mechanism RECORD iff it is a DICT with "matched_tags" or
# "matched_memory" PRESENT. Keys present but empty is a REAL world pick and
# still fills (the composer's never-empty contract is untouched — the mounts
# decline to CALL it without a record). The four mounts are the card's trailing
# furniture, the strip's smeta clause, the analyst deep view, and the quick-tier
# deep view.


def test_the_quick_deep_mount_declines_a_slot_that_carries_no_record(tmp_paths):
    """MOUNT 4 OF 4 (gate F-1, the original finding). This mount appended its
    reason UNCONDITIONALLY and `_collect_deep_views` feeds it `slot or {}`, so a
    quick story whose slot did not persist rendered "Important World News." — a
    world-class CLAIM with no recorded mechanism behind it, indistinguishable
    from a real world pick. (The pre-NL-117 bytes said "world-impact selection
    (no tag or thread match)" here, an honest process disclosure; that is why
    this rung became a fabrication-class fix rather than parity.)"""
    con = _con()
    try:
        html = server._render_sources_context_view(
            "story-0", "A headline", story(), {}, con, DATE)
    finally:
        con.close()
    assert 'class="sc-reason"' not in html, "an empty paragraph is still a mount"
    for words in (labels.WHY_TOPIC_ONE, labels.WHY_THREAD_ONE,
                  labels.WHY_WORLD_NEWS, labels.WHY_FOLLOWED_WRITER):
        assert words not in html, words


def test_both_deep_mounts_decline_a_truthy_slot_with_no_mechanism_keys(
        tmp_paths):
    """THE RUNG IS A RECORD QUESTION, NOT A TRUTHINESS QUESTION (gate R-1). The
    analyst mount's `if slot:` was falsy-only, so a truthy-but-mechanism-less
    slot — a foreign shape, a half-written row — walked past the rung into the
    world fill on BOTH deep mounts. Same slot, both mounts, no line.

    The second half is the bound in the other direction, and it is what keeps
    this from being a silent feature kill: mechanism keys PRESENT BUT EMPTY is
    the shape a genuine world pick has, and it still fills."""
    junk = {"junk": 1}
    real_world_pick = {"matched_tags": [], "matched_memory": []}
    doc = {"brief": {"sources": [], "facts": [], "mechanism": "M."},
           "header": {}}
    con = _con()
    try:
        analyst = server._render_deep_view("story-0", "A headline", doc, DATE,
                                           con=con, slot=junk, story=story())
        quick = server._render_sources_context_view(
            "story-0", "A headline", story(), junk, con, DATE)
        analyst_lawful = server._render_deep_view(
            "story-0", "A headline", doc, DATE, con=con,
            slot=real_world_pick, story=story())
        quick_lawful = server._render_sources_context_view(
            "story-0", "A headline", story(), real_world_pick, con, DATE)
    finally:
        con.close()
    for surface, name in ((analyst, "analyst deep"), (quick, "quick deep")):
        for words in (labels.WHY_TOPIC_ONE, labels.WHY_THREAD_ONE,
                      labels.WHY_WORLD_NEWS, labels.WHY_FOLLOWED_WRITER):
            assert words not in surface, f"{name}: {words}"
    for surface, name in ((analyst_lawful, "analyst deep"),
                          (quick_lawful, "quick deep")):
        assert labels.WHY_WORLD_NEWS in surface, f"{name}: lawful fill lost"


def test_the_degraded_edition_route_renders_no_reason_on_any_surface(tmp_paths):
    """THE ROUTE, not the mount (the gate's F-1 widening). A persisted row whose
    `story_slots` is `[]` while its entry carries stories degrades EVERY slot to
    `{}` — `_render_briefing_body`'s own `else {}` and `_collect_deep_views`'s
    `slot or {}`. Both degrades stay (other consumers want a dict); what changes
    is that the reason mounts read the record and decline.

    Measured on pre-fix bytes with this exact fixture: the body carried TWO
    world fills (the lead card's trailing furniture AND the strip's smeta) and
    the quick deep view a third. Three surfaces, one degraded edition, no
    mechanism data anywhere on it — so no reason line anywhere on it.

    The lawful in-DB members of this class are NULL, valid-JSON-non-list, and a
    SHORT slot list; corrupt JSON cannot enter through the write door
    (`CHECK json_valid(story_slots)`), but a short list needs no corruption at
    all. Constructed-only on the founder's rows today, which is why the gate
    ruled it FIX-NOW rather than BLOCK."""
    con = _con()
    try:
        con.execute(
            "INSERT INTO briefings (id, date, story_slots, narrative_text,"
            " generated_at) VALUES (1, ?, '[]', ?, ?)",
            (TODAY, "## Story 1\n\nLede one.\n\n## Story 2\n\nLede two.\n",
             "2026-08-02T00:00:00.000Z"))
        con.commit()
        row = con.execute("SELECT * FROM briefings WHERE id = 1").fetchone()
        entry = {"tiers": ["full", "quick"],
                 "stories": [{"headline": "H one", "lede": "Lede one."},
                             {"headline": "H two", "lede": "Lede two."}]}
        body = server._render_briefing_body(con, row, entry, None, "",
                                            "view-today")
        _, sections = server._collect_deep_views(con, row, entry, "", "Back",
                                                 "view-today")
    finally:
        con.close()
    assert body.count("<article") == 2, "fixture must render a card AND a strip"
    assert len(sections) == 1, "fixture must reach the quick deep mount"
    for surface, name in ([(body, "today body")]
                          + [(s, "quick deep view") for s in sections]):
        for words in (labels.WHY_TOPIC_ONE, labels.WHY_THREAD_ONE,
                      labels.WHY_WORLD_NEWS, labels.WHY_FOLLOWED_WRITER):
            assert words not in surface, f"{name}: {words}"
    assert 'class="sc-reason"' not in "".join(sections)


# ===========================================================================
# FLAG ① — the deck stamp's word
# ===========================================================================

def test_flag_one_the_stamp_word_is_updated_on_the_card(tmp_paths):
    """CARRIED-INVARIANT (born-green) — his ruling, measured against the tree.
    Flag ① was MANDATORY at the gate and he ruled "Updated", which is the word
    already shipping: this pin is the receipt that the ruling and the code agree,
    and the tooth that catches a later drift to "New today"."""
    con = _con()
    try:
        now = "2026-07-01T00:00:00.000Z"
        cur = con.execute(
            "INSERT INTO memory (topic, status, status_changed_at, created_at,"
            " updated_at) VALUES ('ERCOT curb-load', 'active', ?, ?, ?)",
            (now, now, now))
        tid = cur.lastrowid
        con.execute(
            "INSERT INTO thread_deltas (thread_id, edition_date, slot, verdict,"
            " what_happened, significance, cites_json)"
            " VALUES (?, '2026-07-25', 1, 'advances', 'It moved.', 'Matters.',"
            " '[\"S1\"]')", (tid,))
        con.commit()
        full = server._memory_stamp_inner(
            con, slot(mem=("ERCOT curb-load",)), DATE)
        degraded = server._memory_stamp_inner(
            con, slot(mem=("ERCOT curb-load",)), DATE, degraded=True)
    finally:
        con.close()
    assert full == f"2nd entry on this thread · {labels.MEMLINE_UPDATED}"
    assert labels.MEMLINE_UPDATED == "Updated"
    assert "New today" not in full
    # ONE constant, both mounts — the artifact's build note (6): a swap here
    # would change the card stamp and the strip's degraded form together.
    assert degraded == labels.MEMLINE_UPDATED


# ===========================================================================
# NL-121 §5 — THE HONEST QUIET (what already ships, receipted; zero new strings)
# ===========================================================================

def _seed_quiet_and_moved(con):
    """One thread that MOVED this edition and one that has been quiet since an
    earlier edition — the 2026-07-26 shape: the world moved, our record did
    not."""
    now = "2026-07-01T00:00:00.000Z"
    con.execute("INSERT INTO briefings (id, date, story_slots) VALUES (1, ?, ?)",
                (TODAY, json.dumps([])))
    ids = {}
    for topic in ("ERCOT curb-load", "City transit chief succession"):
        cur = con.execute(
            "INSERT INTO memory (topic, status, status_changed_at, created_at,"
            " updated_at, last_referenced_briefing_id)"
            " VALUES (?, 'active', ?, ?, ?, 1)", (topic, now, now, now))
        ids[topic] = cur.lastrowid
    con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, slot, verdict,"
        " what_happened, significance, cites_json)"
        " VALUES (?, ?, 1, 'advances', 'Window extended through Sunday.',"
        " 'Matters.', '[\"S1\"]')", (ids["ERCOT curb-load"], TODAY))
    con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, slot, verdict,"
        " what_happened, significance, cites_json)"
        " VALUES (?, '2026-07-25', 1, 'advances', 'The first window opened.',"
        " 'Matters.', '[\"S1\"]')", (ids["City transit chief succession"],))
    con.commit()
    return ids


def test_quiet_day_the_following_row_keeps_its_unadvanced_last_updated(
        tmp_paths):
    """CARRIED-INVARIANT (born-green) — §5 item 2, measured rather than assumed.
    On the chased day the quiet thread's stamp is the record's REAL last-covered
    date and it does NOT advance; the moved thread takes the loud UPDATED row.
    The date comes from the ledger's own newest edition_date, so there is no way
    for a quiet day to move it."""
    con = _con()
    try:
        _seed_quiet_and_moved(con)
        html = server._render_following(con)
    finally:
        con.close()
    fold = html.split('<details class="quiet-fold"')[1]
    assert "City transit chief succession" in fold
    assert f"{labels.LAST_UPDATED} JUL 25" in fold
    assert "ERCOT curb-load" not in fold          # it moved: a loud row, not quiet
    assert labels.UPDATED_STAMP in html.split('<details class="quiet-fold"')[0]


def test_quiet_day_the_thread_counts_into_the_existing_counted_fold(tmp_paths):
    """CARRIED-INVARIANT (born-green) — §5 item 2, second half. The quiet thread
    is COUNTED, in the shipped constants, and the line is honest on the chased
    day by its own scoping words: no movement THIS EDITION — a statement about
    our record, which is true."""
    con = _con()
    try:
        _seed_quiet_and_moved(con)
        html = server._render_following(con)
    finally:
        con.close()
    summary = html.split("<summary>")[1].split("</summary>")[0]
    assert f"1 {labels.QUIET_FOLD_NOUN_ONE}" in visible(summary)
    assert labels.QUIET_FOLD_SUFFIX in visible(summary)


def test_quiet_day_today_renders_no_placeholder_for_the_absent_thread(
        tmp_paths):
    """§5 items 1 and 5, the KILLED-ON-SIGHT list: Today renders NOTHING for a
    thread with no candidate — no slot, no placeholder, no gap marker, no
    "nothing new on <thread>" line. The edition composes as if the thread had no
    candidate and runs shorter, lawfully.

    Driven through the real edition body renderer with an edition that carries
    ONE story and a followed thread that is not in it."""
    con = _con()
    try:
        ids = _seed_quiet_and_moved(con)
        slots = [slot(1, mem=("ERCOT curb-load",))]
        con.execute(
            "UPDATE briefings SET story_slots = ?, narrative_text = ?,"
            " generated_at = ? WHERE id = 1",
            (json.dumps(slots), "## Story 1\n\nThe lede sentence.\n",
             "2026-08-02T00:00:00.000Z"))
        con.commit()
        row = con.execute("SELECT * FROM briefings WHERE id = 1").fetchone()
        body = server._render_briefing_body(con, row, None, None, "",
                                            "view-today")
    finally:
        con.close()
    assert ids  # the quiet thread exists and is followed
    seen = visible(body)
    # THE ABSENCE IS THE STATE: the followed thread appears NOWHERE on Today.
    # Naming the thread is the honest form of this pin — a bare-word grep would
    # be tripped by an ordinary lede one day and stop meaning anything.
    assert "City transit chief succession" not in seen
    assert "transit chief" not in seen.lower()
    # The banned copy CLASS, whether or not it names the thread: no gap marker,
    # no "we checked" line, no thin delta-brief in costume.
    for banned in ("Nothing new", "nothing new", "Still watching",
                   "Checked today", "No movement", "not yet named"):
        assert banned not in seen, banned
