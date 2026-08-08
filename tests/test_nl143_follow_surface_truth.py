"""NL-143 — FOLLOW-SURFACE TRUTH + the revived 07-18 polish package.

Four things, from his 2026-08-07 feedback batch:

  1. THE CROSS-MOUNT SYNC BUG (his repro, born red). Today and every deep view
     are ONE document. Every .follow-slot renders server-true at load through
     one predicate, but the client morphed only the tapped node, so the others
     lied until reload: follow a card -> the deep mount still says "Follow";
     unfollow in the deep view -> the card still says "Following". Display-only
     (the seed's XOR guard kept the data clean), which is what made it
     believable.
  2. QUICK TIER FOLLOWABLE (his directive). The strip branch returned before
     the follow control existed, and the $0 sources-&-context deep view never
     mounted the follow line — so an In-Brief item was the one story tier a
     reader could neither follow nor manage.
  3. THE 07-18 POLISH PACKAGE, revived whole: the In-Brief label returns as a
     section slug, strips clamp at 4 lines, the container steps to 84rem with a
     root step. Approved at that evening's polish gate, never built; his label
     ask on 08-07 was the second raise.
  4. THE MUST-NOTS, pinned as carried invariants so this batch's blast radius
     is provable: the 7fr/5fr rectangle law (his 65% ratio was DECLINED the same
     evening), the heading tree, and the Following row's own clamp.

WHAT THIS FILE CAN AND CANNOT PROVE. The sync fix is client behavior and this
repo runs no JS engine in the suite (adding one would be a new dependency and a
machine-dependent suite). So the client pins here are STRUCTURAL, read against
comment-stripped source via _js_code — the R4 lesson from M1c fix loop 2 is that
a pin a comment can satisfy is not a pin. The behavioral proof of the repro is
the real-browser pass, in the build report and re-run by QA.

Offline by construction: autouse sandbox (conftest), no network, no real key, $0.
"""

from __future__ import annotations

import re

import pytest

from newslens import db, labels, paths, server, webui

from test_ui_polish import slot, story, seed, TODAY


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------

def _con():
    db.migrate()
    return db.connect()


def _fn(name: str) -> str:
    """The source of one JS function from webui.JS (the flat function table)."""
    i = webui.JS.index("function " + name + "(")
    j = webui.JS.find("\nfunction ", i + 1)
    return webui.JS[i:(j if j != -1 else len(webui.JS))]


def _js_code(block: str) -> str:
    """A JS block with its comments stripped — so a structural pin reads the
    CODE, not the prose explaining it (M1c fix loop 2, R4)."""
    out = re.sub(r"/\*.*?\*/", " ", block, flags=re.S)
    return re.sub(r"//[^\n]*", " ", out)


def _css_code(block: str = "") -> str:
    """The same discipline for CSS, and it is not theoretical: this file's first
    draft asserted a dead class name was absent from webui.CSS and went red at
    HEAD — on the COMMENT that records the class being deleted. A pin that a
    comment can satisfy (or falsify) is not a pin."""
    return re.sub(r"/\*.*?\*/", " ", block or webui.CSS, flags=re.S)


def _today_view(page: str) -> str:
    return page.split('id="view-today"')[1].split('id="view-following"')[0]


def _slots(html: str):
    """Every .follow-slot open tag in a chunk of markup, as attr dicts."""
    out = []
    for tag in re.findall(r'<span class="follow-slot"[^>]*>', html):
        attrs = dict(re.findall(r'(data-[a-z-]+|aria-live)="([^"]*)"', tag))
        out.append(attrs)
    return out


def _quick_page(con, followed: str = ""):
    """A page whose tail is quick tier: lead + 2 cards + 4 strips."""
    slots = [slot(i, f"S{i}") for i in range(1, 8)]
    stories = ([story(1, "S1")]
               + [story(i, f"S{i}", "medium") for i in (2, 3)]
               + [story(i, f"S{i}", "quick") for i in (4, 5, 6, 7)])
    seed(con, slots, stories)
    if followed:
        from newslens import memory
        memory.add_thread_at_altitude(
            con, followed, altitude="narrow", disclosure="", alt_label="",
            source="seed", origin_story=followed)
    page, _ = server.build_page(con)
    return page


# ===========================================================================
# 1 — THE CROSS-MOUNT SWEEP (his repro)
# ===========================================================================

def test_1a_one_sweep_mechanism_not_a_copy_per_verb():
    """BORN RED. Four state-changing verbs used to each morph their own node
    and stop. The fix is ONE mechanism: exactly one function in the whole
    client walks the document for follow slots. A second walker is the
    beginning of the same drift that produced this bug."""
    # NL-17 M1 / F-6: the ONE walker's selector grew to cover follow-state-
    # DERIVED chrome (a Following row's h2) as well as slots — same walk, same
    # flSameThread predicate, a renderer chosen by node type. The tooth is
    # unchanged and is the point: still exactly one function walks the document.
    walkers = [name for name in re.findall(r"function (\w+)\(", webui.JS)
               if ".follow-slot" in _js_code(_fn(name))
               and "querySelectorAll" in _js_code(_fn(name))]
    assert walkers == ["flSyncOthers"], walkers


@pytest.mark.parametrize("verb", ["flFollow", "flSettle", "flSwitch",
                                  "flUnfollow"])   # flPickNarrow deleted (NL-17 M1)
def test_1b_every_state_change_routes_through_the_sweep(verb):
    """BORN RED — THE BUG ITSELF. His repro is exactly the case where a verb
    morphs its own node and leaves the others lying. No verb may call a
    single-node renderer directly any more; every one goes through the sweep
    entries, which morph the acting node AND every other mount of the thread."""
    code = _js_code(_fn(verb))
    assert "flRenderCommitted(" not in code, verb
    assert "flReceipt(" not in code, verb
    assert ("flCommitAll(" in code) or ("flRestAll(" in code), verb


def test_1c_the_sweep_matches_on_stable_keys_not_the_new_name():
    """BORN RED. A confident settle RENAMES the thread: the acting node's
    data-topic becomes the settled name while every other mount still carries
    the seeded one. Matching on topic alone sweeps nothing at exactly that
    moment, so identity is the union of data-story / data-origin / data-topic."""
    code = _js_code(_fn("flIdentity"))
    for key in ("'story'", "'origin'", "'topic'"):
        assert key in code, key
    # …and it is compared case-insensitively, because _follow_recognition
    # matches against a lowercased active-topic set.
    assert "toLowerCase()" in code


def test_1d_identity_is_captured_before_the_morph():
    """BORN RED. flRenderCommitted REWRITES data-topic/disclosure on the node it
    renders. If the sweep read identity after that, the settle-rename case would
    look for the NEW name on mounts that only carry the old one — the sweep
    would silently find nothing and the bug would survive its own fix."""
    code = _js_code(_fn("flCommitAll"))
    assert code.index("flIdentity(") < code.index("flRenderCommitted(")
    code = _js_code(_fn("flRestAll"))
    assert code.index("flIdentity(") < code.index("flReceipt(")


def test_1e_each_swept_mount_renders_its_own_form():
    """BORN RED. The sweep morphs through the SAME renderers, which branch on
    data-mount — so a card comes back a card verb and a deep view comes back a
    state line plus acts. A sweep that stamped one form onto every mount would
    replace a stale-state bug with a wrong-form bug."""
    sweep = _js_code(_fn("flSyncOthers"))
    assert "data-mount" not in sweep          # the sweep knows nothing of form
    assert "fn(o)" in sweep
    committed = _js_code(_fn("flRenderCommitted"))
    resting = _js_code(_fn("flRenderResting"))
    assert "flMount(slot)" in committed       # the renderer is what branches
    assert "flMount(slot)" in resting


def test_1f_the_sweep_skips_the_acting_node():
    """BORN RED. The acting node is rendered by its own call (it is the one that
    carries the receipt / the resume clause). Re-rendering it inside the sweep
    would overwrite exactly that."""
    assert "o === slot" in _js_code(_fn("flSyncOthers"))


def test_1g_the_reader_receipts_render_only_where_the_reader_acted():
    """BORN RED. `kept` (the resume clause) and the unfollow receipt are
    receipts of an ACT, not properties of a thread. Rendering either on three
    other mounts announces one act four times and leaves the claim standing on
    surfaces the reader never touched."""
    tapped, sep, swept = _js_code(_fn("flCommitAll")).partition("flSyncOthers(")
    assert sep, "flCommitAll must sweep"
    assert "kept" in tapped and "kept" not in swept
    tapped, sep, swept = _js_code(_fn("flRestAll")).partition("flSyncOthers(")
    assert sep, "flRestAll must sweep"
    assert "flReceipt(" in tapped and "flReceipt(" not in swept
    assert "flRenderResting" in swept


def test_1h_a_management_mount_stays_a_live_region_at_rest():
    """BORN RED. The server builds deep/row mounts AS live regions. Before the
    sweep, resting only ever happened to the node the reader touched; now it
    happens to a deep mount because of a tap on a card, and stripping aria-live
    there would silently downgrade a surface the server built to announce."""
    code = _js_code(_fn("flRenderResting"))
    assert "flMount(slot) === 'card'" in code
    con = _con()
    try:
        from newslens import memory
        memory.add_thread_at_altitude(con, "S1", altitude="narrow",
                                      disclosure="", alt_label="",
                                      source="seed", origin_story="S1")
        html = server._deep_follow_line(con, {"story_title": "S1"}, "S1",
                                        TODAY, "story-0")
    finally:
        con.close()
    assert 'aria-live="polite"' in html       # the server-built live region


def test_1i_all_mounts_of_one_story_share_an_identity_key(tmp_paths):
    """BORN RED — and the load-bearing precondition of the whole fix: the sweep
    can only find the other mounts if the server stamps keys that intersect.

    PROOF CLASS, stated exactly (born-red law): the identity CONTRACT is a
    carried invariant — card and deep mounts have always shared data-story and
    data-origin, because both come from _follow_recognition. This test is
    nevertheless born red, for a different reason: it reads the QUICK tier,
    where at HEAD there was no card-side mount at all to compare against. It
    goes green on item 2, and it is the pin that stops a later attribute rename
    from silently re-opening his repro."""
    con = _con()
    try:
        page = _quick_page(con, followed="S4")
    finally:
        con.close()
    deep = page.split('id="view-deep-story-3"')[1].split("</section>")[0]
    card = _today_view(page).split('id="story-3"')[1].split("</article>")[0]
    dm, cm = _slots(deep), _slots(card)
    assert dm and cm, (len(dm), len(cm))

    def keys(a):
        return {a.get(k, "").lower() for k in
                ("data-story", "data-origin", "data-topic") if a.get(k)}

    assert keys(dm[0]) & keys(cm[0]), (dm[0], cm[0])


def test_1j_every_mount_agrees_at_load(tmp_paths):
    """BORN RED (same reason as 1i: no quick-tier mounts existed at HEAD),
    guarding a CARRIED invariant — the SERVER half of the single-rendering law.
    That half was never the broken one: a reload has always produced a page
    that agrees with itself. It is pinned here because it is exactly the state
    the client sweep now has to hold WITHOUT the reload."""
    con = _con()
    try:
        page = _quick_page(con, followed="S4")
    finally:
        con.close()
    deep = page.split('id="view-deep-story-3"')[1].split("</section>")[0]
    card = _today_view(page).split('id="story-3"')[1].split("</article>")[0]
    mounts = _slots(deep) + _slots(card)
    # not vacuous: at unpatched HEAD this story had NO mounts at all, and a bare
    # loop over an empty list is a green pin over a dead path.
    assert len(mounts) == 2, mounts
    for attrs in mounts:
        assert attrs.get("data-state") in ("committed", "expanded"), attrs


# ===========================================================================
# 2 — THE QUICK TIER IS FOLLOWABLE (his directive)
# ===========================================================================

def test_2a_a_strip_mounts_the_follow_slot(tmp_paths):
    """BORN RED — his directive. The strip branch returned before any follow
    control was built, so an In-Brief item could not be followed at all."""
    con = _con()
    try:
        page = _quick_page(con)
    finally:
        con.close()
    strip = _today_view(page).split('id="story-3"')[1].split("</article>")[0]
    assert '<article class="strip' in _today_view(page)
    assert 'class="follow-slot"' in strip
    assert 'data-mount="card"' in strip          # the SAME form, not a new one


def test_2b_the_strip_reuses_the_deck_verb_and_no_new_vocabulary(tmp_paths):
    """BORN RED. "No new UI vocabulary" is the constraint on this item: the
    strip renders the identical resting control the cards render — same class,
    same words, same label constants."""
    con = _con()
    try:
        page = _quick_page(con)
    finally:
        con.close()
    today = _today_view(page)
    strip = today.split('id="story-3"')[1].split("</article>")[0]
    card = today.split('id="story-1"')[1].split("</article>")[0]
    assert 'class="deck-follow not-following"' in strip
    assert labels.FOLLOW_THREAD_INACTIVE in strip
    assert 'class="deck-follow not-following"' in card
    # austerity: the strip mounts the control WITHOUT the card's .deck row,
    # which carries a rule and card margins the strip register forbids.
    assert 'class="deck"' not in strip
    assert 'class="strip-follow"' in strip


def test_2b2_the_tracked_marker_never_resurrects_on_a_strip(tmp_paths):
    """SELF-CAUGHT, then pinned. _follow_control renders the "Tracked ongoing
    story — <thread>" MARKER instead of a control when a story matches a
    tracked thread. Mounting it on strips unchanged resurrected that marker on
    the austere tier — a second vocabulary where the smeta's degraded stamp is
    already the memory signal. (v8-M2's own QA pin caught it; this one states
    the rule at the surface that has to keep it.)

    Nothing is withheld: a marks-carrying story is one the reader ALREADY
    follows, so no follow was on offer — the card behaves the same way."""
    con = _con()
    try:
        from newslens import memory
        memory.add_thread(con, "Hormuz")
        slots = [slot(i, f"S{i}") for i in range(1, 8)]
        slots[3]["matched_memory"] = ["Hormuz"]          # story-3, a strip
        stories = ([story(1, "S1")]
                   + [story(i, f"S{i}", "medium") for i in (2, 3)]
                   + [story(i, f"S{i}", "quick") for i in (4, 5, 6, 7)])
        seed(con, slots, stories)
        page, _ = server.build_page(con)
    finally:
        con.close()
    today = _today_view(page)
    strip = today.split('id="story-3"')[1].split("</article>")[0]
    assert 'class="tracked-marker"' not in strip
    assert 'class="strip-follow"' not in strip
    # …and an unmatched sibling strip still carries its control
    other = today.split('id="story-4"')[1].split("</article>")[0]
    assert 'class="strip-follow"' in other


def test_2c_the_strip_control_comes_from_the_one_component():
    """BORN RED. One producer, or the single-rendering law is words. The strip
    must not grow its own follow markup."""
    import inspect
    body = inspect.getsource(server._render_story)
    assert body.count("_follow_control(") == 1, "one call, shared by both roles"
    assert 'class="strip-follow"' in body
    assert 'class="follow-slot"' not in body     # markup lives in the component


def test_2d_the_zero_dollar_deep_view_mounts_the_follow_line(tmp_paths):
    """BORN RED. M1c's mount-3 law — the deep view is the thread's MANAGEMENT
    HOME, where Unfollow lives — never carried a tier qualifier; the $0 view
    just never mounted it, so a followed In-Brief item had a door that opened
    onto a room with no controls."""
    con = _con()
    try:
        page = _quick_page(con, followed="S4")
        deep = server._render_sources_context_view(
            "story-3", "S4", story(4, "S4", "quick"), slot(4, "S4"), con, TODAY)
    finally:
        con.close()
    assert 'class="follow-slot"' in deep
    assert 'data-mount="deep"' in deep
    assert labels.FOLLOW_UNFOLLOW in deep        # the acts line reached it
    assert 'id="view-deep-story-3"' in page


def test_2e_both_deep_views_mount_through_the_same_call():
    """BORN RED for the $0 arm. Two deep views, ONE follow mount."""
    import inspect
    for fn in (server._render_deep_view, server._render_sources_context_view):
        assert "_deep_follow_line(" in inspect.getsource(fn), fn.__name__


def test_2f_a_followed_quick_story_reads_committed_on_both_its_mounts(tmp_paths):
    """BORN RED — the behavioral one. Follow a quick-tier story and BOTH of its
    new mounts must render the committed state at load. This is the server half
    of his repro: before this batch neither mount existed, so there was nothing
    to be stale."""
    con = _con()
    try:
        page = _quick_page(con, followed="S4")
    finally:
        con.close()
    strip = _today_view(page).split('id="story-3"')[1].split("</article>")[0]
    deep = page.split('id="view-deep-story-3"')[1].split("</section>")[0]
    assert [a["data-state"] for a in _slots(strip)] == ["committed"]
    assert [a["data-state"] for a in _slots(deep)] == ["expanded"]
    # an UNfollowed sibling strip still rests — the state is read, not assumed
    other = _today_view(page).split('id="story-4"')[1].split("</article>")[0]
    assert [a["data-state"] for a in _slots(other)] == ["resting"]


# ===========================================================================
# 3 — THE REVIVED 07-18 POLISH PACKAGE
# ===========================================================================

def test_3a_the_in_brief_slug_returns_over_the_strips(tmp_paths):
    """BORN RED — his 07-18 item 2, approved that evening, never built, raised
    again 08-07. v8-M2's "scale and placement are the label" held at lead/card
    scale and misreads at strip scale: a clamped strip reads as truncated
    coverage until something names the register as deliberate brevity."""
    con = _con()
    try:
        page = _quick_page(con)
    finally:
        con.close()
    today = _today_view(page)
    assert 'class="brief-slug' in today
    assert labels.IN_BRIEF in today
    # it heads a STRIP, never a lead or a card
    for m in re.finditer(r'<p class="brief-slug[^"]*"', today):
        before = today[:m.start()]
        assert before.rindex("<article") == before.rindex('<article class="strip')


def test_3b_the_slug_is_ornament_and_the_heading_tree_is_untouched(tmp_paths):
    """BORN RED (the aria-hidden half) + CARRIED-INVARIANT (the tree). Two
    structural renderings of one semantic tier is phantom structure: the h3
    stays the only one, so the slug is an aria-hidden <p>, never a heading."""
    con = _con()
    try:
        page = _quick_page(con)
    finally:
        con.close()
    today = _today_view(page)
    # not vacuous: there must BE a slug for the ornament claim to mean anything
    # (the first draft of this test passed at unpatched HEAD on an empty loop).
    slugs = re.findall(r'<p class="brief-slug[^"]*"([^>]*)>', today)
    assert slugs, "no slug rendered — the rest of this test would be vacuous"
    assert re.search(r'<h[1-6][^>]*class="brief-slug', today) is None
    for rest in slugs:
        assert 'aria-hidden="true"' in rest
    # the tier is still carried by the heading tree, unchanged
    strip = today.split('id="story-3"')[1].split("</article>")[0]
    assert '<h3 class="headline"' in strip
    lead = today.split('<article class="lead')[1].split("</article>")[0]
    assert '<h2 class="headline"' in lead


def test_3c_one_slug_per_column_leg_never_one_per_card(tmp_paths):
    """BORN RED. The polish gate approved the slug under an explicit busy-guard
    audit: "one two-word slug per run, <=2 per page". The grid's balance is
    greedy shorter-column-first, so once the columns level out the assignment
    ALTERNATES and a literal per-contiguous-run rule emits one slug per strip —
    the per-card labelling his own charge ruled out. One per column leg holds
    the audit for every edition shape."""
    con = _con()
    try:
        page = _quick_page(con)
    finally:
        con.close()
    today = _today_view(page)
    n_slugs = today.count('class="brief-slug')
    n_strips = today.count('<article class="strip')
    assert n_strips >= 4, n_strips
    assert 1 <= n_slugs <= 2, (n_slugs, n_strips)
    assert n_slugs < n_strips


def test_3d_only_the_rank_first_slug_survives_the_mobile_stack(tmp_paths):
    """BORN RED. Below 900px the strips stack contiguously by rank, so the
    second leg's slug would read as a stutter mid-run."""
    con = _con()
    try:
        page = _quick_page(con)
    finally:
        con.close()
    today = _today_view(page)
    classes = re.findall(r'<p class="(brief-slug[^"]*)"', today)
    assert classes, classes
    assert classes[0] == "brief-slug"                       # the rank-first one
    assert all(c.endswith("--secondary") for c in classes[1:]), classes
    mobile = _css_code().split("@media (max-width: 900px)")[1]
    assert ".brief-slug--secondary { display: none; }" in mobile


def test_3e_strips_clamp_at_four_lines_with_a_measure_cap():
    """BORN RED — his 07-18 item 3 ("+1-2 lines"), approved at the top of his
    range because the container widens in the same batch. The 32rem cap rides
    with it: at 84rem an uncapped strip line runs ~88ch, and the 4-line clamp
    would then quadruple an unreadable measure."""
    rule = _css_code().split(".strip .sum {")[1].split("}")[0]
    assert "-webkit-line-clamp: 4" in rule
    assert "max-width: 32rem" in rule
    assert "-webkit-line-clamp: 2" not in rule


def test_3f_the_container_steps_to_84rem_on_every_view():
    """BORN RED — his 07-18 item 4, approved. Both containers step: a Today page
    that widened while its deep views stayed at 72rem would read as a layout bug
    on the first click-through."""
    css = _css_code()
    page_rule = css.split(".page {")[1].split("}")[0]
    assert "max-width: 84rem" in page_rule
    deep_rule = css.split('section[id^="view-deep-"], '
                          'section[id^="view-thread-"] {')[1].split("}")[0]
    assert "max-width: 84rem" in deep_rule
    # no CONTAINER is left behind at the old width. Anchored on the property,
    # not the bare number: 0.72rem is a legitimate type size on this page, and
    # a bare "72rem" substring match hits it.
    assert "max-width: 72rem" not in css


def test_3g_the_root_step_grows_the_page_on_very_wide_glass():
    """BORN RED — approved with the container. A fixed rem container is a
    shrinking fraction of a large display; the honest answer there is scale, so
    the root steps and every measure stays constant IN CHARACTERS."""
    css = _css_code()
    assert "@media (min-width: 1800px) { html { font-size: 17px; } }" in css
    assert "@media (min-width: 2200px) { html { font-size: 18px; } }" in css
    # disjoint from the mobile query — the phone column is untouched
    assert "min-width: 1800px" not in css.split("@media (max-width: 900px)")[1]


# ===========================================================================
# 4 — THE MUST-NOTS (carried invariants: this batch's blast radius, pinned)
# ===========================================================================

def test_4a_the_rectangle_law_is_untouched():
    """CARRIED-INVARIANT (born green) — and the sharpest one in the file. He
    floated a ~65% lead the same evening the polish package was approved and
    DECLINED it explicitly; the ratio is his law. The container widened; the
    rectangle did not move."""
    css = _css_code()
    grid = css.split(".today-grid {")[1].split("}")[0]
    assert "grid-template-columns: 7fr 5fr" in grid
    assert "gap: 0 4rem" in grid
    # the shapes a 65% lead would take. Read off the RULES: the comments have
    # to be free to record WHY the ratio did not move.
    for banned in ("13fr", "7fr 3fr", "65%"):
        assert banned not in css, banned


def test_4b_the_grid_machinery_never_learns_about_the_slug():
    """BORN RED (on the signature — _brief_slug_heads does not exist at HEAD),
    guarding a CARRIED invariant: the slug reads the balance assignment and
    never feeds it, so no In-Brief label can shift where a story lands. The
    signature check is the tooth — a slug that took `grid_stories` and returned
    a NEW assignment would be a rectangle-law change wearing a label's name."""
    import inspect
    for fn in (server._grid_columns, server._grid_row_spans, server._grid_est):
        src = inspect.getsource(fn)
        assert "brief_slug" not in src and "IN_BRIEF" not in src, fn.__name__
    sig = inspect.signature(server._brief_slug_heads)
    assert list(sig.parameters) == ["grid_stories", "cols"]


def test_4c_the_following_row_clamp_is_not_the_strip_clamp():
    """CARRIED-INVARIANT (born green). Two 2-line clamps shipped; only ONE of
    them is the Today strip. .thread-note is the Following row's note and keeps
    its own clamp — pinned because the sibling rule is the obvious mis-hit."""
    note = _css_code().split(".thread-note {")[1].split("}")[0]
    assert "-webkit-line-clamp: 2" in note


def test_4d_the_dead_in_brief_region_stays_dead(tmp_paths):
    """CARRIED-INVARIANT (born green). What returned is the LABEL, not v8-M2's
    labelled REGION: no wrapper around the strips, and none of the four deleted
    classes come back (a wrapper would break DOM rank order, which is what
    screen readers hear)."""
    con = _con()
    try:
        page = _quick_page(con)
    finally:
        con.close()
    today = _today_view(page)
    for dead in ('class="in-brief"', 'class="brief-label"', 'class="snippet"',
                 'class="quick-hit"'):
        assert dead not in today, dead
    css = _css_code()                    # RULES only — the comments record the
    assert ".in-brief" not in css        # deletion and would falsify this
    assert ".brief-label" not in css


# ===========================================================================
# 5 — FIX LOOP 1 (QA's F-1 and F-2, both MEDIUM, 2026-08-08)
#
# QA's contracts are the spec for both. F-1 is server-side and fully behavioral
# here. F-2 is client behavior, and the file's standing constraint still holds —
# no JS engine in this suite — so its pin does the strongest dependency-free
# thing available: it reads the SHIPPED comparison pairs out of flSameThread
# itself and evaluates THAT rule against slots this repo really rendered. The
# pairs are the whole semantics; what is not re-implemented here is the walk.
# The behavioral proof of both repros is the real-browser re-run in the fix-loop
# report.
# ===========================================================================

def _thread_pairs():
    """The shipped key-comparison pairs, read out of flSameThread's code."""
    return set(re.findall(r"keys\.(\w+),\s*id\.(\w+)",
                          _js_code(_fn("flSameThread"))))


def _sweeps(acting: dict, other: dict) -> bool:
    """Would the SHIPPED rule sweep `other` when `acting` acts? Pair set from
    the client source, values from real server-rendered data-* attributes."""
    for ka, kb in _thread_pairs():
        a = (acting.get("data-" + ka) or "").lower()
        b = (other.get("data-" + kb) or "").lower()
        if a and a == b:
            return True
    return False


def _union_sweeps(acting: dict, other: dict) -> bool:
    """The PRE-FIX rule: any key of one equal to any key of the other."""
    names = ("data-story", "data-origin", "data-topic")
    a = {(acting.get(n) or "").lower() for n in names} - {""}
    b = {(other.get(n) or "").lower() for n in names} - {""}
    return bool(a & b)


def _collision_page(con):
    """QA's F-2 divergent-edge fixture, rendered by the real page builder.

      story-1  CARD   story_title "Chip exports" / headline "US tightens chip exports"
      story-3  STRIP  story_title "US tightens chip exports" / headline "Ports talks…"

    Two DIFFERENT stories. The card's data-origin equals the strip's data-story,
    which is the entire crossing — and the crossing is real markup, not a
    thought experiment."""
    slots = [slot(i, f"S{i}") for i in range(1, 8)]
    slots[1] = slot(2, "Chip exports")
    slots[3] = slot(4, "US tightens chip exports")
    stories = ([story(1, "S1")]
               + [story(2, "US tightens chip exports", "medium"),
                  story(3, "S3", "medium")]
               + [story(4, "Ports talks stall on cranes", "quick")]
               + [story(i, f"S{i}", "quick") for i in (5, 6, 7)])
    seed(con, slots, stories)
    page, _ = server.build_page(con)
    return page


def test_f1_a_tracked_story_is_never_offered_the_picker_in_its_deep_view(
        tmp_paths):
    """BORN RED — QA's F-1 repro, the second-thread mint.

    Thread "Hormuz" is tracked and this story matches it (matched_memory), but
    the story's own title is different — the NORMAL case for marks, which is why
    the seed's XOR guard cannot catch what follows. The strip correctly shows no
    control. Pre-fix, the same story's $0 management home rendered "○ Follow
    this thread", and one tap minted a SECOND active thread beside the tracked
    one. Two surfaces of one story disagreeing about whether a follow is on
    offer, with the offered act manufacturing the divergent double-follow the
    guard exists to prevent."""
    con = _con()
    try:
        from newslens import memory
        memory.add_thread(con, "Hormuz")
        tracked = slot(5, "Hormuz shipping watch")
        tracked["matched_memory"] = ["Hormuz"]
        st = story(5, "Tankers reroute around the strait", "quick")
        deep = server._render_sources_context_view(
            "story-4", st["headline"], st, tracked, con, TODAY)
        plain = server._render_sources_context_view(
            "story-5", "S6", story(6, "S6", "quick"), slot(6, "S6"), con, TODAY)
    finally:
        con.close()
    line = deep.split('class="follow-line"')[1].split("</div>")[0]
    # the marker STATE, naming the thread it is tracked against…
    assert 'class="tracked-marker"' in line
    assert labels.TRACKED_ONGOING_PREFIX in line and "Hormuz" in line
    # …and no picker anywhere on the surface: nothing to tap, nothing to mint.
    # NL-17 M1 / F-6: the marker is now itself a .follow-slot (state "tracked")
    # so the cross-mount sweep can reach it on a remote unfollow. "No slot" was
    # only ever a PROXY for "no picker"; the tooth is pinned directly now, and
    # more tightly than the proxy was — no button, no tap handler, no CTA.
    assert 'data-state="tracked"' in line
    assert "<button" not in line and "followTap" not in line
    assert labels.FOLLOW_THREAD_INACTIVE not in deep
    # CONTROL — the mount item 2b added is not blanket-killed: an unmatched
    # quick story's $0 view still offers the follow it always should have.
    assert 'class="follow-slot"' in plain
    assert labels.FOLLOW_THREAD_INACTIVE in plain


def test_f1_b_a_tracked_story_the_reader_also_follows_keeps_its_acts(tmp_paths):
    """CARRIED-INVARIANT (born green) — and the judgment call in the F-1 fix,
    stated where the gate can flip it in one line.

    The marks arm is `marks and not followed`, not a blanket `marks`. The bug is
    the resting PICKER on a tracked story; `not followed` is exactly the picker
    branch. A story can be marks-carrying AND followed under its own name, and
    this mount is M1c's MANAGEMENT HOME — where Unfollow lives. Blanking it
    there would strand a thread the reader actually holds with no acts surface
    outside the Following row (F-4's dead-end class, self-inflicted). Green both
    sides of the fix: pre-fix this mount took no marks at all."""
    con = _con()
    try:
        from newslens import memory
        memory.add_thread(con, "Hormuz")
        memory.add_thread(con, "Hormuz shipping watch")
        both = slot(5, "Hormuz shipping watch")
        both["matched_memory"] = ["Hormuz"]
        deep = server._render_sources_context_view(
            "story-4", "Tankers reroute around the strait",
            story(5, "Tankers reroute around the strait", "quick"),
            both, con, TODAY)
    finally:
        con.close()
    assert 'class="follow-slot"' in deep
    assert 'data-mount="deep"' in deep
    assert labels.FOLLOW_UNFOLLOW in deep          # the acts line survived
    assert 'class="tracked-marker"' not in deep


def test_f2_a_the_cross_type_crossing_never_sweeps(tmp_paths):
    """BORN RED — QA's F-2 divergent-edge repro.

    The key union let ANY key of one slot meet ANY key of another. Two different
    stories whose headline and story_title cross therefore swept together: the
    ports strip rendered "● Following — Semiconductor policy", the server rested
    it again on reload, and — the part that exceeded the disclosed display-only
    framing — the contaminated deep mount carried the CHIP thread's acts, so
    tapping its "this story" rung re-aimed a real follow onto a ports story.

    Typed, the pairs are story↔story, origin↔origin, topic↔topic and topic↔story
    both ways. Only the cross-type crossing stops sweeping."""
    assert _thread_pairs() == {("story", "story"), ("origin", "origin"),
                               ("topic", "topic"), ("topic", "story"),
                               ("story", "topic")}
    con = _con()
    try:
        page = _collision_page(con)
    finally:
        con.close()
    today = _today_view(page)
    card = _slots(today.split('id="story-1"')[1].split("</article>")[0])[0]
    strip = _slots(today.split('id="story-3"')[1].split("</article>")[0])[0]
    strip_deep = _slots(page.split('id="view-deep-story-3"')[1]
                        .split("</section>")[0])[0]
    # the crossing is REAL in the markup this repo renders — not hypothetical
    assert card["data-origin"] == strip["data-story"]
    assert card["data-story"] != strip["data-story"]
    # the pre-fix rule swept them; the shipped rule does not, in either direction
    assert _union_sweeps(card, strip) is True
    assert _sweeps(card, strip) is False
    assert _sweeps(strip, card) is False
    # the contaminated DEEP mount is the one that carried the chip thread's acts
    # and re-aimed a real follow onto the ports story. Out of reach now.
    assert _union_sweeps(card, strip_deep) is True
    assert _sweeps(card, strip_deep) is False
    # …and the ports story's OWN two mounts still sweep together, which is the
    # whole point of the mechanism: typing narrows it, it does not disarm it.
    assert _sweeps(strip, strip_deep) is True


def test_f2_b_the_rename_arm_still_converges(tmp_paths):
    """CARRIED-INVARIANT (born green) — why typing the comparison is safe.

    A confident settle RENAMES the thread, and the rename is the normal path,
    not an edge case. It survives typing because data-story and data-origin are
    the STABLE SPINE: every mount of one story carries the same two, and the
    committed renderer rewrites data-topic ONLY. Both halves are true at HEAD —
    this pin is here so a later change cannot quietly move the spine and take
    the sweep's rename arm down with it."""
    con = _con()
    try:
        page = _collision_page(con)
    finally:
        con.close()
    strip = _slots(_today_view(page).split('id="story-3"')[1]
                   .split("</article>")[0])[0]
    deep = _slots(page.split('id="view-deep-story-3"')[1]
                  .split("</section>")[0])[0]
    assert strip["data-story"] == deep["data-story"] != ""
    assert strip["data-origin"] == deep["data-origin"] != ""
    committed = _js_code(_fn("flRenderCommitted"))
    assert "setAttribute('data-topic'" in committed
    assert "setAttribute('data-story'" not in committed
    assert "setAttribute('data-origin'" not in committed
