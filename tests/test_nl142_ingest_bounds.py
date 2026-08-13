"""NL-142 — INGEST BYTE BOUNDS, the last conditional owner of the proof bound.

NL-133 turned `analysis.PROMPT_MARGIN_CHARS` from an allowance into a BOUND by
capping cluster SHAPE. NL-139 closed two of the three FIELD-LENGTH owners the
gate named in the same breath (vendor Sonar fields, memory topic inserts) and
left ingest stated as the honest reason the bound was still CONDITIONAL. This
file closes it — and corrects the enumeration while it does.

WHAT THE RECORD SAID vs. WHAT THE TREE DOES (re-verified 2026-08-13 against
15,007 source_items, up from the 11,198 NL-133 swept):

  * "feed titles ... unclamped" — they are clamped, at `ingest.
    STORED_TITLE_MAX_CHARS` (500). A storage number, never derived against the
    bound: at 500 the worst map renders 93,228 chars against 78,621.
  * a FOURTH owner no record named: the S-key title is NOT the feed title. It
    is the FETCHED PAGE's <title> (`extract_article_text` -> `FetchRecord.
    title` -> the S# key), bounded by nothing but MAX_ARTICLE_BYTES =
    2,000,000. `test_the_page_title_owner_reaches_the_clamp_through_the_real_
    fetch_route` drives it.
  * "the outlet NAME ... is not in this residue class at all" (analysis.py's
    own comment, pre-NL-142) — true of the RSS door only. `discovery.
    _store_results` writes `urlparse(url).netloc`, a REMOTE host, into the same
    `source_items.outlet` column.

The close is at the RENDER door, not at ingest: the bound is a property of the
prompt this module builds, and only the render door covers the 15,007 rows
already written (a migration is a stop under this charter), both source_items
writers, and the S-key title that never passes through source_items at all.

EVERY worst case here is CONSTRUCTED FROM THE SHIPPED CONSTANTS and rendered
through the REAL constructors — NL-133's rule, kept for NL-133's reason.

BORN-RED CLASS (ENGINEERING.md) — MEASURED, not expected. The HEAD-export run
is 14 failed / 1 passed, and the fifteen split three ways. Each pin repeats its
own currency in its own docstring; the counts here were read off the run, not
drafted ahead of it:

  * BEHAVIOUR-RED at 61fef71 — 4 pins, failing on what HEAD DOES:
    `..._hostile_ingest_fields_cannot_reach_past_the_bound` (5,057,868 chars
    against a 78,621 bound), `..._rendered_labels_are_bounded_but_the_grouping_
    identity_is_not` (a 675-char C line), and both R-E-3 count pins.
  * NEW-SURFACE red — 10 pins, dying on a symbol this batch introduces
    (`MAP_TITLE_MAX_CHARS`, `MAP_LABEL_BUDGET_CHARS`, `clamp_map_labels`,
    `clamp_stored_url`, `ingest.STORED_TITLE_MAX_CHARS`) BEFORE their
    behaviour assert runs. Not behaviour currency, and each says so.
  * CARRIED-INVARIANT, born GREEN — 1: `..._shipped_derivation_is_untouched_at_
    the_observed_maxima`, labelled rather than counted as a red.

The full HEAD-run fail list is in research/2026-08-13--nl142-build.md.

FIX LOOP 2 (the NL-142 SHIP GATE's F-G0 BLOCK). The gate found the batch's own
owner (iii) — the article-URL host — reaching the prompt through a door this
file did not construct: a Sonar result on the CLUSTER'S OWN host mints an R key
whose outlet IS that host, joins the cluster's sibling list, and rendered past
the label budget, which only covered S and C keys. 81,972 chars against the
78,621 bound, over by 3,351, with every shipped clamp satisfied. The close is
the budget's SCOPE ("SCR" at both render doors), and it moves the arithmetic:
every number this file pins was measured against a worst case 3,456 chars
cheaper than the machine's.

BORN-RED CLASS, FIX LOOP 2 — measured at the pre-fix tree (d991adc + the build
and fix loop 1 work), which is the tree these pins had to be red against:

  * BEHAVIOUR-RED, 8 pins: the three shared-host parametrisations, the band
    sweep, the true cap ladder, the observed-maxima carried invariant (78,012
    -> 77,780), the past-the-clamps identity, and both documented-ceiling
    parametrisations. Every one fails on what the pre-fix tree DOES.
  * REWRITTEN, not value-swapped: `test_every_clamp_lands_strictly_inside_its_
    floor_and_its_ceiling` kept its claim and lost a walk that was vacuous —
    it bisected over the FIELD (which the clamp bounds, so it never breached)
    on an axis that is not monotone (the prompt peaks inside the sharing band).
    It passed at both trees for a reason unrelated to any ceiling; it is now
    behaviour-red at the pre-fix tree, where the clamps sit ABOVE their own
    ceilings.
"""
import urllib.error

import pytest

from newslens import analysis, discovery, ingest, memory, paths, ranking


# --------------------------------------------------------------------------
# The worst case, built from constants (extends NL-133's and NL-139's)
# --------------------------------------------------------------------------
# INGEST's all-time observed maxima, swept read-only from the founder DB on
# 2026-08-13 (15,007 source_items / 1,569 analysis_retrieval rows). Under
# NL-142 these are no longer the bound's PRECONDITION — the clamps are — but
# they remain each clamp's FLOOR, which is what makes the clamp values
# defensible rather than round.
TITLE_OBS = 183      # max(len(source_items.title)); p99.9 = 151
OUTLET_OBS = 41      # max(len(source_items.outlet)) — his own sources.yaml
HOST_OBS = 27        # max url host length


def _worst_source_map(*, title, host, outlet, sonar_host, fetched=True,
                      n_items=None, shared=False):
    """The most expensive source map one cluster can render, on every axis at
    once. `fetched` selects the key branch (S keys supersede their own C keys,
    and the S branch is the binding one); `sonar_host` / `shared` select the
    sibling-list branch. Sonar fields enter AT NL-139's clamps, because
    `clamp_sonar_results` runs before this map is ever built — feeding an
    unclamped vendor host here would measure a state the pipeline cannot
    reach.

    `shared` IS ITS OWN AXIS (fix loop 2, gate F-G0). The Sonar results sit on
    the CLUSTER'S OWN host — same host string, different path, which the
    R-mint's exact-URL dedupe (`if url in cluster_urls`) lets through — so the
    R keys join the cluster's sibling list instead of forming a second one.
    That is the binding branch and no harness in this product constructed it
    at a long host until the gate did: the two branches used to be selected by
    `sonar_host` alone, which made "shared" and "own" the SAME STRING whenever
    the two lengths matched, and left the whole 44..253 band unmeasured.
    """
    if n_items is None:
        n_items = ranking.MAX_CLUSTER_ITEMS
    h = "h" * (host - 4) + ".com"
    # A DISTINCT string for the own-host branch: same length, different bytes,
    # so `shared` is never silently a no-op.
    sh = h if shared else "g" * (sonar_host - 4) + ".com"
    t, o = "T" * title, "O" * outlet
    items = [{"outlet": o, "url": "https://%s/%d" % (h, i), "title": t,
              "raw_excerpt": "x" * 4000, "fetched_at": "2026-08-02T00:00Z",
              "published_at": "2026-08-02", "source_name": o, "tier": "full"}
             for i in range(n_items)]
    records = ([analysis.FetchRecord(
        url=it["url"], source_name=o, tier="full", outcome=analysis.OK,
        attempted=True, title=t, text="x" * 4000) for it in items]
        if fetched else [])
    sonar = [{"url": "https://%s/s%d" % (sh, i),
              "title": "T" * analysis.SONAR_TITLE_MAX_CHARS,
              "date": "2026-08-02", "snippet": "s" * 4000}
             for i in range(analysis.SONAR_MAX_RESULTS)]
    priors = [{"date": "2026-08-02", "text": "p" * 4000,
               "thread": "M" * memory.TOPIC_MAX_CHARS}
              for _ in range(memory.CONTEXT_CAP)]
    return analysis.build_source_map(records, items, sonar, priors)


def _prompt_chars(**kw):
    template = (paths.PROMPTS_DIR / "analysis_brief.txt").read_text(encoding="utf-8")
    sources = _worst_source_map(**kw)
    prompt = analysis._render_prompt(template, {
        "word_budget": str(analysis.word_budget_for("full")),
        "tier": "medium", "date": "2026-08-02", "slot": "1",
        # validate_payload's own hard caps on the two model-authored scalars
        "story_title": "S" * 300, "story_summary": "U" * 400,
        "memory_context": "\n".join(["M" * memory.TOPIC_MAX_CHARS]
                                    * memory.CONTEXT_CAP),
        "source_map": analysis.render_source_map(sources),
        "material": analysis.render_material(sources)})
    return len(prompt), analysis.brief_bound_chars(template)


def _worst(*, title, host, outlet, n_items=None):
    """Worst over BOTH sibling-list branches and BOTH key branches.

    Fix loop 2: the sibling branches are now (shared cluster host) and (own
    maximal host, a DISTINCT string) rather than two `sonar_host` lengths. The
    shared branch exists only where the vendor door can admit that host —
    `clamp_sonar_results` refuses a host past SONAR_HOST_MAX_CHARS, so a
    cluster host longer than the DNS maximum cannot be shared by any result,
    and that is why the absurd-host shape measures LESS than the at-the-clamps
    one (see `test_hostile_and_at_the_clamps_measure_the_SAME_prompt`)."""
    best = 0
    bound = 0
    branches = [(analysis.SONAR_HOST_MAX_CHARS, False)]
    if host <= analysis.SONAR_HOST_MAX_CHARS:
        branches.append((host, True))
    for sonar_host, shared in branches:
        for fetched in (True, False):
            n, bound = _prompt_chars(title=title, host=host, outlet=outlet,
                                     sonar_host=sonar_host, fetched=fetched,
                                     n_items=n_items, shared=shared)
            best = max(best, n)
    return best, bound


# Far past every clamp, on every axis at once. The point of the number is that
# it is ABSURD: after NL-142 the rendered prompt does not know the difference.
HOSTILE = dict(title=100_000, host=2_000, outlet=2_000)

# The longest host the VENDOR DOOR admits, and therefore the longest one a
# Sonar result can SHARE with the cluster: `clamp_sonar_results` refuses a
# result whose host exceeds SONAR_HOST_MAX_CHARS. Fix loop 2 (gate F-G0): this
# is the axis the worst case actually peaks on, and no constructor in this
# product had walked it — the absurd host above is past it, and past it the
# sharing branch does not exist.
REACHABLE_HOST = analysis.SONAR_HOST_MAX_CHARS


# --------------------------------------------------------------------------
# 1. THE BOUND IS STRUCTURAL — the batch's whole contract
# --------------------------------------------------------------------------

def test_hostile_ingest_fields_cannot_reach_past_the_bound():
    """R-E-5, closed. A 100,000-char page title, a 2,000-char host and a
    2,000-char outlet render a prompt that fits — the bound stops depending on
    what ingest has been observed to produce.

    BEHAVIOUR-RED at 61fef71, measured: "hostile ingest fields render 5057868
    chars against a 78621-char bound (over by 4979247)". It is not a symbol
    failure; it is the defect."""
    worst, bound = _worst(**HOSTILE)
    assert worst <= bound, (
        "hostile ingest fields render %d chars against a %d-char bound (over "
        "by %d) — the ingest residue is open again" % (worst, bound, worst - bound))


def test_hostile_and_at_the_clamps_measure_the_SAME_prompt():
    """The structural claim stated as an identity rather than an inequality:
    once past the clamps, MORE input buys the prompt nothing. This is the
    difference between a bound and a bet, and it is what "no longer
    conditional on observed maxima" actually means.

    RE-DERIVED IN FIX LOOP 2 (gate F-G0). The identity used to be taken at the
    ABSURD shape (a 2,000-char host), and that shape is not the worst one: a
    host past SONAR_HOST_MAX_CHARS cannot be shared by any Sonar result,
    because `clamp_sonar_results` refuses that result at the door — so the
    absurd shape renders TWO sibling lists where the reachable worst renders
    ONE, and measures 3,384 chars LESS. The identity is therefore taken at the
    DNS maximum, the longest host the vendor door admits, and the absurd shape
    is asserted to be no worse rather than equal.

    THE NAME IS KEPT DELIBERATELY: `test_nl142_qa_findings.py` cites this pin
    by name from a docstring, and that file is byte-frozen for this loop. A
    rename here would have manufactured exactly the stale citation the gate's
    R-C ruling is about.

    BEHAVIOUR-RED at the pre-fix tree, measured: both sides read 81,972 and
    the `<= bound` arm fails (over by 3,351)."""
    reachable_worst, bound = _worst(title=100_000, host=REACHABLE_HOST,
                                    outlet=100_000)
    at_clamps, _ = _worst(title=analysis.MAP_TITLE_MAX_CHARS,
                          host=REACHABLE_HOST, outlet=OUTLET_OBS)
    assert reachable_worst == at_clamps, (
        "input past the clamps still moves the prompt (%d vs %d) — some "
        "ingest-owned field renders outside the clamped path"
        % (reachable_worst, at_clamps))
    assert reachable_worst <= bound, (
        "the identity holds at a value that BREACHES (%d vs %d) — an identity "
        "past the bound is the F-G0 state, not the close"
        % (reachable_worst, bound))
    beyond_reach, _ = _worst(**HOSTILE)
    assert beyond_reach <= reachable_worst, (
        "a host the vendor door cannot admit (%d chars) now renders MORE than "
        "the reachable worst (%d vs %d) — the sibling-list arithmetic moved"
        % (HOSTILE["host"], beyond_reach, reachable_worst))


# The ceilings this batch documents, in the SHIPPED regime (every clamped
# field at its clamp, on the binding sharing branch). Measured per run below,
# and quoted in analysis.py's clamp comments — the pin is what keeps the prose
# from drifting off the machine, which is the class that produced F-G0.
DOCUMENTED_CEILINGS = [
    (analysis, "MAP_TITLE_MAX_CHARS", 191, 2),
    (analysis, "MAP_LABEL_BUDGET_CHARS", 75, 1),
]


def _constant_ceiling(module, field, span=600):
    """First value of the CONSTANT at which the worst case breaches, walked
    LINEARLY from the shipped value at the saturating shape.

    Two defects in the walk this replaces, both of which hid F-G0:
      * it walked the FIELD, not the constant — and the clamp bounds the
        field, so the walk saturated at the clamp and never breached at all
        (it returned its own search ceiling, 3,999, and the assertion below it
        passed for a reason unrelated to any ceiling);
      * it used BINARY SEARCH over an axis that is not monotone — the prompt
        peaks in the sharing band (host 44..253) and falls again past the DNS
        maximum, so bisection stepped straight over the peak.
    """
    old = getattr(module, field)
    try:
        for v in range(old, old + span):
            setattr(module, field, v)
            worst, bound = _worst(title=100_000, host=REACHABLE_HOST,
                                  outlet=100_000)
            if worst > bound:
                return v - 1
    finally:
        setattr(module, field, old)
    return None


@pytest.mark.parametrize("module,field,ceiling,headroom", DOCUMENTED_CEILINGS)
def test_the_documented_ceiling_and_headroom_of_each_clamp_are_the_real_ones(
        module, field, ceiling, headroom):
    """The clamp VALUES, not just their existence — and the NUMBERS the
    comments quote, made executable.

    REPLACES `test_each_clamp_is_load_bearing_at_one_single_character` (fix
    loop 2). That pin asserted ONE character past either clamp breaches, which
    was true only of the incomplete worst case: with the label budget now
    covering R keys the true worst falls 3,456 chars, and the clamps carry 2
    and 1 characters of headroom respectively rather than none. Load-bearing
    is still the claim — a clamp with headroom this thin is one step from
    binding — but the honest form of it is the measured ceiling, not a
    knife-edge that stopped being one.

    BEHAVIOUR-RED at the pre-fix tree: the worst case breaches AT the shipped
    value, so the walk reports a ceiling BELOW the clamp (188 / 73) and this
    fails on the first assert."""
    measured = _constant_ceiling(module, field)
    value = getattr(module, field)
    assert measured is not None, (
        "%s has no ceiling any more — the worst case stopped depending on it, "
        "so RE-DERIVE the clamp rather than leaving it in place" % field)
    assert measured == ceiling, (
        "%s = %d: documented ceiling %d (first breach %d, headroom %d), "
        "measured ceiling %d (first breach %d, headroom %d). Re-derive the "
        "comment in place — the shipped regime moved."
        % (field, value, ceiling, ceiling + 1, headroom, measured,
           measured + 1, measured - value))
    assert measured - value == headroom


def test_the_worst_case_at_the_observed_maxima_is_the_sharing_branch():
    """The carried invariant, RE-DERIVED IN FIX LOOP 2. At the observed maxima
    — where NL-139 measured — the worst case is 77,780 chars against the same
    78,621 bound, on the branch where the Sonar keys share the cluster's host.

    It read 78,012 before the F-G0 close, on the branch where the Sonar keys
    hold a maximal host of their own; that branch cost 2 x (253 - 37) x 8
    label chars that the budget now covers, and collapsed to 74,556. 77,780 is
    NL-133's own harness number, which is the point: with the vendor host
    inside the label budget, the two derivations meet at the observed maxima
    instead of standing 232 chars apart.

    If this moves, every number in analysis.py's residue comment, in
    test_nl139_byte_clamps.py and in ranking.py's ceiling paragraph is stale.

    BEHAVIOUR-RED at the pre-fix tree: 78,012 vs the 77,780 pinned here."""
    worst, bound = _worst(title=TITLE_OBS, host=HOST_OBS, outlet=OUTLET_OBS)
    assert (worst, bound) == (77_780, 78_621), (
        "the worst case at the observed maxima moved: %d vs %d (was 77,780 "
        "vs 78,621)" % (worst, bound))


def test_every_clamp_lands_strictly_inside_its_floor_and_its_ceiling():
    """NL-133's clamp-design rule, applied and RE-DERIVED PER RUN: floor from
    observed data, ceiling from the bound, land strictly inside both. The
    ceilings are measured here rather than pinned as literals — the drift class
    this file inherits is exactly what a frozen number reproduces. (The
    literals the COMMENTS quote are pinned separately, above.)

    BEHAVIOUR-RED at the pre-fix tree (fix loop 2 rewrote the walk — see
    `_constant_ceiling` for the two defects that made the old one vacuous):
    the shipped clamps sit ABOVE their own ceilings there, 189 > 188 and
    74 > 73, which is F-G0 restated as a design-rule violation."""
    # FLOORS — no shape this product has ever produced is shortened.
    assert analysis.MAP_TITLE_MAX_CHARS > TITLE_OBS
    assert analysis.MAP_LABEL_BUDGET_CHARS > OUTLET_OBS + HOST_OBS

    # CEILINGS — measured by walking each CONSTANT until the bound breaks.
    for module, field, _ceiling, _headroom in DOCUMENTED_CEILINGS:
        measured = _constant_ceiling(module, field)
        assert getattr(module, field) <= measured, (
            "%s = %d sits ABOVE its own ceiling (%d): the worst case breaches "
            "at the shipped value" % (field, getattr(module, field), measured))


# --------------------------------------------------------------------------
# 1b. THE SHARED-HOST DOOR (gate F-G0, fix loop 2)
# --------------------------------------------------------------------------
# The whole finding in one sentence: the article-URL host reaches the analyst
# prompt a second time, as an R key's outlet, whenever a Sonar result sits on
# the cluster's own host — and R labels were outside the budget that closes
# owner (iii). These two pins hold that door, and they drive the Sonar results
# through the REAL `clamp_sonar_results` rather than handing them to
# `build_source_map`, because a state the pipeline's own door cannot produce
# is not a measurement (the build's first-probe lesson).


def _through_the_vendor_door(*, host, n_shared, fetched=True, title=None,
                             outlet=None):
    """(prompt_chars, bound, kept, dropped) with `n_shared` of the kept Sonar
    results on the CLUSTER'S OWN host — same host string, different path,
    which the R-mint's exact-URL dedupe admits."""
    if title is None:
        title = analysis.MAP_TITLE_MAX_CHARS
    if outlet is None:
        outlet = OUTLET_OBS
    h = "h" * (host - 4) + ".com"
    own = "g" * (analysis.SONAR_HOST_MAX_CHARS - 4) + ".com"
    t, o = "T" * title, "O" * outlet
    items = [{"outlet": o, "url": "https://%s/%d" % (h, i), "title": t,
              "raw_excerpt": "x" * 4000, "fetched_at": "2026-08-02T00:00Z",
              "published_at": "2026-08-02", "source_name": o, "tier": "full"}
             for i in range(ranking.MAX_CLUSTER_ITEMS)]
    records = ([analysis.FetchRecord(
        url=it["url"], source_name=o, tier="full", outcome=analysis.OK,
        attempted=True, title=t, text="x" * 4000) for it in items]
        if fetched else [])
    raw = [{"url": "https://%s/s%d" % (h if i < n_shared else own, i),
            "title": "T" * analysis.SONAR_TITLE_MAX_CHARS,
            "date": "2026-08-02", "snippet": "s" * 4000}
           for i in range(analysis.SONAR_MAX_RESULTS)]
    kept, _truncated, dropped = analysis.clamp_sonar_results(raw)
    priors = [{"date": "2026-08-02", "text": "p" * 4000,
               "thread": "M" * memory.TOPIC_MAX_CHARS}
              for _ in range(memory.CONTEXT_CAP)]
    sources = analysis.build_source_map(records, items, kept, priors)
    template = (paths.PROMPTS_DIR / "analysis_brief.txt").read_text(
        encoding="utf-8")
    prompt = analysis._render_prompt(template, {
        "word_budget": str(analysis.word_budget_for("full")),
        "tier": "medium", "date": "2026-08-02", "slot": "1",
        "story_title": "S" * 300, "story_summary": "U" * 400,
        "memory_context": "\n".join(["M" * memory.TOPIC_MAX_CHARS]
                                    * memory.CONTEXT_CAP),
        "source_map": analysis.render_source_map(sources),
        "material": analysis.render_material(sources)})
    return len(prompt), analysis.brief_bound_chars(template), kept, dropped


@pytest.mark.parametrize("n_shared,fetched", [(8, True), (1, True),
                                              (8, False)])
def test_a_sonar_result_on_the_clusters_own_host_cannot_reach_past_the_bound(
        n_shared, fetched):
    """F-G0, the finding that BLOCKED this batch, closed.

    A Sonar result on the cluster's own host mints an R key whose outlet IS
    that host (`_outlet_of`), joins the cluster's sibling list, and — before
    the fix — rendered 2 x 253 label chars on every R line, past a budget that
    only knew about S and C keys. Every clamp in the pipeline is satisfied in
    these shapes: 8 kept, 0 dropped, titles at the vendor clamp, the cluster at
    its cap, ingest fields at THEIR clamps.

    BEHAVIOUR-RED at the pre-fix tree, measured: 81,972 / 78,955 / 81,876
    chars against the 78,621 bound (over by 3,351 / 334 / 3,255). ONE shared
    result is enough."""
    prompt, bound, kept, dropped = _through_the_vendor_door(
        host=REACHABLE_HOST, n_shared=n_shared, fetched=fetched)
    assert (len(kept), dropped) == (analysis.SONAR_MAX_RESULTS, 0), (
        "this shape no longer passes the vendor door (%d kept, %d dropped) — "
        "it would be measuring a state the pipeline cannot reach"
        % (len(kept), dropped))
    assert prompt <= bound, (
        "%d Sonar result(s) on the cluster's own %d-char host render %d chars "
        "against a %d-char bound (over by %d) — the shared-host door is open "
        "again" % (n_shared, REACHABLE_HOST, prompt, bound, prompt - bound))


def test_the_whole_shared_host_band_fits_not_just_its_endpoints():
    """The band every earlier grid skipped. QA's 2,197-case sweep held no host
    between 45 and 253; the gate's found 628 breaching cases; the ceiling walk
    moved host and outlet in symmetric halves and never took the host past
    ~40 alone. So this walks the sharing axis at 1-char resolution through the
    onset region and coarsely to the DNS maximum, with the host and the outlet
    INDEPENDENT.

    BEHAVIOUR-RED at the pre-fix tree, measured: first breach at a 44-char
    shared host (78,628, over by 7), every host above it breaching, worst
    81,972 at 253."""
    band = list(range(34, 81)) + [90, 100, 150, 200, 252,
                                  analysis.SONAR_HOST_MAX_CHARS]
    worst = (0, None)
    for host in band:
        for n_shared in (1, analysis.SONAR_MAX_RESULTS):
            prompt, bound, _kept, _dropped = _through_the_vendor_door(
                host=host, n_shared=n_shared)
            if prompt > worst[0]:
                worst = (prompt, (host, n_shared))
    assert worst[0] <= bound, (
        "the shared-host band breaches at %r: %d chars against a %d-char "
        "bound (over by %d)" % (worst[1], worst[0], bound, worst[0] - bound))


def test_the_two_render_doors_agree_on_an_r_keys_outlet():
    """The guard fix loop 1's handoff asked for, now that it has a second key
    kind to lose. `render_source_map` and `_material_header` render the same
    key's outlet into the same prompt, and the map is introduced to the model
    as the closed citation vocabulary — one key wearing two outlet strings is
    the coherence defect QA's F-1 found on C keys.

    The two guards are separate literals in separate functions, and the
    material block SELF-CAPS at MATERIAL_BUDGET_CHARS, so widening only the
    map door leaves every byte-count pin in this file GREEN while the header
    goes back to printing a 253-char host next to a 37-char map label. That is
    F-1 exactly, one key kind over. Mutation-proven: reverting
    `_material_header`'s guard alone reds this pin and nothing else.

    BEHAVIOUR-RED at the pre-fix tree: 253 vs 37."""
    host = "h" * 249 + ".com"
    assert len(host) == analysis.SONAR_HOST_MAX_CHARS
    sources = analysis.build_source_map(
        [], [], [{"url": "https://%s/story" % host, "title": "t",
                  "snippet": "s"}], [])
    line = [ln for ln in analysis.render_source_map(sources).splitlines()
            if ln.startswith("[R1]")][0]
    map_label = line.split("] ", 1)[1].split(" — ", 1)[0]
    header_label = analysis._material_header(
        "R1", sources["R1"]).split("] ", 1)[1].split(" — ", 1)[0]
    assert header_label == map_label, (
        "one R key renders two outlet strings into one prompt: %d chars in "
        "the material header, %d in the map line"
        % (len(header_label), len(map_label)))
    assert len(map_label) <= analysis.MAP_LABEL_BUDGET_CHARS


def test_the_true_cap_ladder_puts_max_cluster_items_at_the_ceiling():
    """The cap ladder at the TRUE worst case, which is what ranking.py's
    ceiling paragraph now quotes — so the citation has a pin under it.

    NL-133's ladder (48 -> 77,780 / 49 -> 78,651 / 50 -> 79,532) is its own
    harness's: it puts the Sonar keys on a 27-char shared host, where no clamp
    engages. Here every clamped field is AT its clamp and the Sonar keys share
    a host the vendor door still admits — the binding shape. Same verdict,
    one third of the room.

    BEHAVIOUR-RED at the pre-fix tree: every rung breaches there (48 ->
    81,972, over by 3,351), so "48 is AT the ceiling" was a statement about a
    machine that did not exist until the F-G0 close landed."""
    at_cap, bound = _worst(title=analysis.MAP_TITLE_MAX_CHARS,
                           host=REACHABLE_HOST, outlet=OUTLET_OBS)
    assert (at_cap, bound) == (78_516, 78_621), (
        "the true worst case at the cap moved: %d vs %d (was 78,516 vs "
        "78,621, slack 105)" % (at_cap, bound))
    one_more, _ = _worst(title=analysis.MAP_TITLE_MAX_CHARS,
                         host=REACHABLE_HOST, outlet=OUTLET_OBS,
                         n_items=ranking.MAX_CLUSTER_ITEMS + 1)
    assert one_more > bound, (
        "ONE item past the cap still fits (%d vs %d) — the cap stopped being "
        "at the ceiling, so re-derive it rather than enjoying the slack"
        % (one_more, bound))


# --------------------------------------------------------------------------
# 2. THE FOUR OWNERS — each proven to route through the clamp
# --------------------------------------------------------------------------

def _page_fetch(pages):
    def fetch(url, timeout, cap=0, user_agent=""):
        if url.endswith("/robots.txt"):
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        return pages[url].encode()
    return fetch


def test_the_page_title_owner_reaches_the_clamp_through_the_real_fetch_route():
    """OWNER (ii), the one no record named. This title never touches
    source_items: it is the fetched page's <title>, read by
    `extract_article_text`, carried on `FetchRecord.title`, and minted as the
    S# key. Bounded by nothing but MAX_ARTICLE_BYTES = 2,000,000 until now.

    Driven through the REAL route (fetch -> extract -> record -> map), because
    a pin that hands `build_source_map` a long string proves the clamp exists,
    not that anything reaches it.

    NEW-SURFACE red at 61fef71 (labelled): it dies on the absent
    `MAP_TITLE_MAX_CHARS` before the final assert. The fact it guards WAS
    measured on the HEAD export by hand — S1 title length 5,000, unclamped —
    and the route assert above it (title length 5,000 off the real fetch) is
    what makes this a route proof rather than a clamp proof."""
    url = "https://ingest-owner.example/a"
    html = ("<html><head><title>%s</title></head><body>%s</body></html>"
            % ("T" * 5_000, "<p>%s</p>" % ("word " * 400)))
    records = analysis.fetch_cluster_articles(
        [{"url": url, "source_name": "Owner", "tier": "full"}],
        fetch=_page_fetch({url: html}), sleep=lambda _s: None)
    assert records[0].outcome == analysis.OK, records[0].detail
    assert len(records[0].title) == 5_000, (
        "the fetch route no longer carries the page <title> — this pin is "
        "measuring something else")

    sources = analysis.build_source_map(
        records, [{"url": url, "outlet": "Owner", "title": "feed title",
                   "raw_excerpt": "x", "fetched_at": "2026-08-02T00:00Z",
                   "published_at": "2026-08-02"}], [], [])
    assert len(sources["S1"]["title"]) == analysis.MAP_TITLE_MAX_CHARS


def test_the_feed_title_owner_is_clamped_on_the_c_key():
    """OWNER (i): the feed title, from either source_items writer.

    NEW-SURFACE red at 61fef71 (labelled): dies on the absent
    `MAP_TITLE_MAX_CHARS`. Measured by hand on the HEAD export: C1 title
    length 500 — the storage clamp, 305 chars past the bound's ceiling."""
    sources = analysis.build_source_map(
        [], [{"url": "https://feed.example/a", "outlet": "Feed",
              "title": "T" * 500, "raw_excerpt": "x",
              "fetched_at": "2026-08-02T00:00Z", "published_at": "2026-08-02"}],
        [], [])
    assert len(sources["C1"]["title"]) == analysis.MAP_TITLE_MAX_CHARS


def test_the_host_and_the_outlet_share_one_budget_rather_than_two_clamps():
    """OWNERS (iii) + (iv). Both render on the same line, so the bound only
    ever integrates over their SUM — and a long source name beside a short
    host is a shape two separate clamps would refuse for a reason the
    arithmetic cannot name.

    NEW-SURFACE red at 61fef71 (`clamp_map_labels` does not exist)."""
    budget = analysis.MAP_LABEL_BUDGET_CHARS
    # A long name beside a short host: whole, both of them.
    long_name, short_host = "N" * (budget - 8), "h" * 8
    assert analysis.clamp_map_labels(long_name, short_host) == (long_name, short_host)
    # Two long ones: max-min fair, and the SUM is the budget.
    a, b = analysis.clamp_map_labels("N" * 400, "h" * 400)
    assert len(a) + len(b) == budget
    assert abs(len(a) - len(b)) <= 1, "max-min fair, not first-come: %r" % ((a, b),)
    # And the greedy case: a short field never subsidises nothing.
    a, b = analysis.clamp_map_labels("N" * 400, "h" * 4)
    assert (len(b), len(a) + len(b)) == (4, budget)


def test_the_rendered_labels_are_bounded_but_the_grouping_identity_is_not():
    """The correctness half of the label budget, and the reason it is applied
    at RENDER rather than to `_outlet_id`: two distinct long hosts that share a
    prefix must stay two outlets. Truncating the identity would merge them and
    DEFLATE the corroboration count the model reads off "DISTINCT OUTLETS IN
    THIS MAP" — a trust surface, from a byte clamp.

    BEHAVIOUR-RED at 61fef71, measured: "a C line renders an unbounded label:
    675"."""
    prefix = "h" * 300
    items = [{"url": "https://%s%d.example/a" % (prefix, i), "outlet": "O" * 300,
              "title": "t", "raw_excerpt": "x", "fetched_at": "2026-08-02T00:00Z",
              "published_at": "2026-08-02"} for i in (1, 2)]
    sources = analysis.build_source_map([], items, [], [])
    rendered = analysis.render_source_map(sources)
    for line in rendered.splitlines():
        if line.startswith("[C"):
            assert len(line) < 400, "a C line renders an unbounded label: %d" % len(line)
    assert "DISTINCT OUTLETS IN THIS MAP: 2." in rendered, (
        "two distinct hosts collapsed into one outlet — the grouping identity "
        "was truncated:\n%s" % rendered)


def test_one_stored_title_rule_two_writers(migrated_con, monkeypatch):
    """The storage clamp was the bare literal 500 at TWO addresses. Named, not
    changed — the value is right for what it actually does. Proven LIVE by
    moving the constant and watching both writers move with it, which a rename
    that missed one would fail.

    NEW-SURFACE red at 61fef71 (`ingest.STORED_TITLE_MAX_CHARS` is absent)."""
    monkeypatch.setattr(ingest, "STORED_TITLE_MAX_CHARS", 12)

    # Writer 1 — the RSS door.
    raw = ("""<?xml version="1.0"?><rss version="2.0"><channel>"""
           """<item><title>%s</title><link>https://a.example/1</link>"""
           """</item></channel></rss>""" % ("T" * 400)).encode()
    items, _skipped = ingest.parse_entries(raw)
    assert [len(i.title) for i in items] == [12]

    # Writer 2 — the discovery door.
    stored, _dropped = discovery._store_results(
        migrated_con, [{"url": "https://b.example/2026/01/01/story",
                        "title": "T" * 400}], "2026-08-13T00:00:00Z")
    assert stored == 1
    row = migrated_con.execute(
        "SELECT title, outlet FROM source_items WHERE url = ?",
        ("https://b.example/2026/01/01/story",)).fetchone()
    assert len(row["title"]) == 12
    # ...and the correction analysis.py's residue comment now carries: this
    # door writes a REMOTE HOST into `outlet`, so that column is not purely
    # the principal's own sources.yaml.
    assert row["outlet"] == "b.example"


# --------------------------------------------------------------------------
# 3. RIDER R-E-3
# --------------------------------------------------------------------------

def _sonar_payload(results):
    return {"usage": {"total_tokens": 100}, "search_results": results}


def test_an_empty_url_sonar_result_is_dropped_counted_and_named(monkeypatch):
    """R-E-3, first half. `build_source_map` has always skipped a result with
    no URL ("if not url: continue"), but `_sonar_verify` counted it — so
    `ok — N results` over-reported, and the count carried a result that could
    never render.

    BEHAVIOUR-RED at 61fef71, measured: `assert ['kept', 'no url'] ==
    ['kept']`."""
    monkeypatch.setattr(discovery, "call_sonar", lambda key, prompt: _sonar_payload([
        {"url": "https://ok.example/a", "title": "kept", "snippet": "s"},
        {"url": "   ", "title": "no url", "snippet": "s"}]))
    results, _cost, status = analysis._sonar_verify("pplx-fake", "A story", ["c"])
    assert [r["title"] for r in results] == ["kept"]
    assert "ok — 1 results" in status
    assert "1 dropped — no URL" in status


def test_the_reported_sonar_count_equals_the_keys_the_map_mints(monkeypatch):
    """R-E-3's bite, and the reason the first half is not cosmetic: the slot-3
    demotion gate reads this count (`len(sonar_results) < 2` in analyse_slot),
    so an empty-URL result could hold a slot at MEDIUM on material the model
    never received. The invariant that closes the class is an identity — what
    `_sonar_verify` reports IS what the map renders.

    BEHAVIOUR-RED at 61fef71, measured: `assert 2 == 1`."""
    monkeypatch.setattr(discovery, "call_sonar", lambda key, prompt: _sonar_payload([
        {"url": "https://ok.example/a", "title": "kept", "snippet": "s"},
        {"url": "", "title": "no url", "snippet": "s"}]))
    results, _cost, _status = analysis._sonar_verify("pplx-fake", "A story", ["c"])
    sources = analysis.build_source_map([], [], results, [])
    assert len(results) == len([k for k in sources if k[0] == "R"])


def test_the_stored_url_is_bounded_and_is_unmistakably_not_a_link():
    """R-E-3, second half. A URL longer than the storage bound is kept with its
    TRUE LENGTH NAMED rather than dropped or silently cut: a silently cut URL
    is a citation to somewhere else (NL-139's rule), while this string cannot
    be mistaken for one and still identifies the row.

    NEW-SURFACE red at 61fef71 (`clamp_stored_url` is absent)."""
    short = "https://ok.example/" + "p" * 100
    assert analysis.clamp_stored_url(short) == short
    long_url = "https://ok.example/" + "p" * 5_000
    stored = analysis.clamp_stored_url(long_url)
    assert len(stored) == analysis.RETRIEVAL_URL_MAX_CHARS
    assert stored.startswith("https://ok.example/pppp")
    assert stored.endswith("chars]") and str(len(long_url)) in stored


def test_the_storage_clamp_cannot_change_one_rendered_char(migrated_con):
    """The "storage-only" claim, proven rather than asserted: persist a map
    holding a 5,000-char URL, then show (a) the row is bounded, (b) the
    in-memory map still holds the URL whole, and (c) the rendered source map is
    byte-identical across the persist.

    NEW-SURFACE red at 61fef71 (labelled): dies on the absent
    `RETRIEVAL_URL_MAX_CHARS`. Measured by hand on the HEAD export: the row
    stores 5,019 chars, the whole input."""
    long_url = "https://ok.example/" + "p" * 5_000
    sources = analysis.build_source_map(
        [], [{"url": long_url, "outlet": "Feed", "title": "t",
              "raw_excerpt": "x", "fetched_at": "2026-08-02T00:00Z",
              "published_at": "2026-08-02"}], [], [])
    before = analysis.render_source_map(sources)
    analysis.persist_brief(migrated_con, "2026-08-13", 1, "medium", "valid",
                           {"b": 1}, "", 0.0, {"slot": 1}, sources=sources)
    row = migrated_con.execute(
        "SELECT url FROM analysis_retrieval WHERE key = 'C1'").fetchone()
    assert len(row["url"]) == analysis.RETRIEVAL_URL_MAX_CHARS
    assert sources["C1"]["url"] == long_url
    assert analysis.render_source_map(sources) == before
