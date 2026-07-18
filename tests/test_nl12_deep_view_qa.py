"""NL-12 QA — adversarial pass on the deep-view render milestone (QA-owned).

Conformance oracles: DECISIONS.md 2026-07-09 "Option A approved AS AMENDED"
items 1-6, and the register-spec addendum (D1-D5) at the end of
workspace/debates/2026-07-09--newslens--content.md. The implementer's own
pins live in test_nl12_deep_view.py; this file attacks the edges those pins
do not reach:

  * dedupe threshold DIRECTION (under-merge safe, over-merge not): near-miss
    survival at 0.89, the inclusive 0.9 boundary, cite-union on merge,
    one disclosure per collapse — and the word-order blind spot (RED).
  * date-extractor edges: bare years, quarters, weekday-only, mixed formats,
    month-without-day ordering, multi-date facts, malformed ISO-shaped
    tokens, same-date stability.
  * `settles` never surfaces: swept over the LIVE HTTP reader surfaces
    (/?date= page and /edition fragment), while the join key provably
    persists in brief_json and the writer view.
  * What's-still-open degenerates beyond the implementer's: multi-item
    structure (one <p> per unknown, ONE closing watch <p>), glue
    punctuation, empty optional fields, hostile-content escaping, and the
    all-empty-observables residue corner (RED).
  * arc-line edges: blank/missing prior date, P-cite behind non-prior cites,
    dangling P-cite, arc inside an ARCHIVED edition fragment (date-scoped
    ids + openEdition target pinned).
  * D1 "deleted, not restyled" swept over webui.CSS; fold markup per fact;
    collapse-after-injection ordering in webui.JS.

ACCEPTANCE REDS: two tests in this file are born failing, per the house
red-tests-as-acceptance-contracts pattern. Each carries its fix contract in
its docstring. Everything else must be green.

Fully offline (autouse sandbox + loopback guard); the real DB is never
opened here.
"""

from __future__ import annotations

import json
import re

from newslens import analysis, db, server, webui

from test_m3_qa import m3_brief
from test_analysis_brief_qa import qa_brief, validate
from test_server import ui, get, seed_briefing  # noqa: F401 (fixtures)
from test_nl11_qa import _seed_edition

DATE = "2026-07-07"


def _deep(brief, anchor="story-0", date=DATE):
    return server._render_deep_view(anchor, "H",
                                    {"header": {}, "brief": brief}, date)


def _section(html, anchor_id):
    return html.split(f'id="{anchor_id}"')[1].split("</div>")[0]


def _facts(clean):
    return [f["fact"] for f in clean["pinned_facts"]]


# ===========================================================================
# 1. Dedupe threshold — the trust edge (direction: over-merge is the failure)
# ===========================================================================

# 18 tokens each, differing in one mid-sentence token: Jaccard 17/19 = 0.8947.
_NEAR_A = ("The ministers gathered in Vienna on Monday to publicly review "
           "production quotas ahead of a vote next week.")
_NEAR_B = ("The ministers gathered in Vienna on Monday to privately review "
           "production quotas ahead of a vote next week.")
# 19 tokens each, one differing token: Jaccard 18/20 = 0.9 exactly.
_EDGE_A = ("The ministers gathered again in Vienna on Monday to publicly "
           "review production quotas ahead of a vote next week.")
_EDGE_B = ("The ministers gathered again in Vienna on Monday to privately "
           "review production quotas ahead of a vote next week.")


def test_distinct_parallel_facts_below_threshold_both_survive():
    """The trust edge itself: distinct-but-parallel facts just UNDER the 0.9
    threshold are two checkable claims and must both reach the reader — a
    false collapse silently deletes one. 0.8947 (one token of 18 differs)
    and the implementer's own ~0.75 outlet-parallel shape both survive,
    with zero near-duplicate warnings and cites untouched."""
    b = qa_brief()
    b["pinned_facts"] = [
        {"fact": _NEAR_A, "cites": ["S1"]},
        {"fact": _NEAR_B, "cites": ["C2"]},
        {"fact": "Outlet one reports the strike destroyed the depot.",
         "cites": ["R1"]},
        {"fact": "Outlet two reports the strike destroyed the depot.",
         "cites": ["R2"]},
    ]
    clean, warnings = validate(b)
    assert len(clean["pinned_facts"]) == 4
    assert not any("near-duplicate" in w for w in warnings)
    # cites stay exactly per-fact — nothing merged below the threshold
    assert [f["cites"] for f in clean["pinned_facts"]] == [
        ["S1"], ["C2"], ["R1"], ["R2"]]


def test_boundary_jaccard_exactly_point_nine_collapses_and_unions_cites():
    """Pins the shipped boundary semantics: the collapse test is INCLUSIVE
    (jac >= 0.9), so a pair at exactly 18/20 = 0.9 merges. The surviving band
    is [0, 0.9) — flagged to the gate as the direction-sensitive edge. On
    merge the loser's cites must UNION into the survivor (never drop), and
    the collapse is disclosed."""
    b = qa_brief()
    b["pinned_facts"] = [
        {"fact": _EDGE_A, "cites": ["S1"]},
        {"fact": "Analysts remain divided on the outlook.", "cites": ["C1"]},
        {"fact": _EDGE_B, "cites": ["R1", "S1"]},
    ]
    clean, warnings = validate(b)
    assert len(clean["pinned_facts"]) == 2
    survivor = clean["pinned_facts"][0]
    assert survivor["fact"] == _EDGE_A                  # first text wins
    assert survivor["cites"] == ["S1", "R1"]            # union, order-kept
    assert sum("near-duplicate" in w for w in warnings) == 1


def test_exact_duplicates_collapse_once_each_with_one_warning_per_collapse():
    """Disclosure fires PER collapse: two copies folding into one original =
    two warnings; the survivor's cites are the first-seen-order union of all
    three copies' cites."""
    b = qa_brief()
    fact = "OPEC increases output by 188000 barrels per day."
    b["pinned_facts"] = [
        {"fact": fact, "cites": ["S1"]},
        {"fact": fact, "cites": ["C2"]},
        {"fact": "Prices slipped after the decision.", "cites": ["C1"]},
        {"fact": fact, "cites": ["R1", "S1"]},
    ]
    clean, warnings = validate(b)
    assert _facts(clean) == [fact, "Prices slipped after the decision."]
    assert clean["pinned_facts"][0]["cites"] == ["S1", "C2", "R1"]
    assert sum("near-duplicate" in w for w in warnings) == 2


def test_word_order_permutation_facts_must_both_survive():
    """ACCEPTANCE RED (expected to FAIL until fixed) — the word-order blind
    spot in set-Jaccard. 'Iran sanctions US officials...' and 'US sanctions
    Iran officials...' have IDENTICAL token sets (Jaccard 1.0) but are two
    distinct, opposite checkable claims; today the second silently collapses
    into the first AND its cites are re-attached to a claim they may not
    support (provenance misattribution). Over-merge is the unsafe direction
    (dispatch surface 1; the implementer's own comment names this exact
    failure mode).

    FIX CONTRACT: the near-duplicate identity test must let word order
    participate — e.g. collapse only when normalized-exact OR (set-Jaccard
    >= 0.9 AND an order-sensitive agreement holds, such as token-bigram
    Jaccard >= 0.8). Under any such fix: this pair survives (bigram overlap
    3/9 = 0.33) while _EDGE_A/_EDGE_B still collapse (bigram 17/19 = 0.89)
    and exact duplicates still collapse — the green tests above must not
    move."""
    b = qa_brief()
    b["pinned_facts"] = [
        {"fact": "Iran sanctions US officials over the drone strikes.",
         "cites": ["S1"]},
        {"fact": "US sanctions Iran officials over the drone strikes.",
         "cites": ["R1"]},
        {"fact": "Analysts remain divided on the outlook.", "cites": ["C1"]},
    ]
    clean, _ = validate(b)
    facts = _facts(clean)
    assert "Iran sanctions US officials over the drone strikes." in facts
    assert "US sanctions Iran officials over the drone strikes." in facts
    assert len(facts) == 3


# ===========================================================================
# 2. Date extractor — ordering must be stable and never crash
# ===========================================================================

def test_bare_years_quarters_and_weekdays_are_not_dates():
    """'by the end of 2026', 'Q3 2026' and bare weekdays are deliberately
    NOT chronology (too ambiguous to reorder on): order stays untouched."""
    b = qa_brief()
    b["pinned_facts"] = [
        {"fact": "Compliance is due by the end of 2026.", "cites": ["S1"]},
        {"fact": "Guidance lands in Q3 2026.", "cites": ["C2"]},
        {"fact": "The panel convenes Tuesday.", "cites": ["R1"]},
    ]
    clean, warnings = validate(b)
    assert _facts(clean) == [
        "Compliance is due by the end of 2026.",
        "Guidance lands in Q3 2026.",
        "The panel convenes Tuesday.",
    ]
    assert not any("near-duplicate" in w for w in warnings)


def test_mixed_date_formats_order_chronologically_iso_and_prose_alike():
    """ISO, 'D Month YYYY' and month-only prose forms all key the same
    chronology; dated facts fill the dated slots oldest-first."""
    b = qa_brief()
    b["pinned_facts"] = [
        {"fact": "Ministers meet on 3 June 2026 in Vienna.", "cites": ["S1"]},
        {"fact": "The review is slated for April 2026.", "cites": ["C2"]},
        {"fact": "The first tranche landed on 2026-05-01.", "cites": ["R1"]},
    ]
    clean, _ = validate(b)
    facts = _facts(clean)
    assert "April 2026" in facts[0]
    assert "2026-05-01" in facts[1]
    assert "3 June 2026" in facts[2]


def test_month_without_day_sorts_before_dated_days_that_month():
    """'May 2026' (day unknown -> 0) sorts before 'May 12, 2026'; the undated
    fact between them holds its slot."""
    b = qa_brief()
    b["pinned_facts"] = [
        {"fact": "The board votes on May 12, 2026.", "cites": ["S1"]},
        {"fact": "Analysts remain divided on the outlook.", "cites": ["C2"]},
        {"fact": "A decision window opens in May 2026.", "cites": ["R1"]},
    ]
    clean, _ = validate(b)
    facts = _facts(clean)
    assert facts[0] == "A decision window opens in May 2026."
    assert facts[1] == "Analysts remain divided on the outlook."
    assert facts[2] == "The board votes on May 12, 2026."


def test_multi_date_fact_keys_deterministically_on_the_iso_form():
    """A fact carrying BOTH a prose month and an ISO date keys on the ISO
    form (pattern-priority order, not string position) — pinned as the
    deterministic contract so any future re-keying is a conscious flip.
    Here the accord (March prose + 2026-05-01 ISO) sorts as May, AFTER the
    April ruling."""
    b = qa_brief()
    b["pinned_facts"] = [
        {"fact": "Signed in March 2026, the accord takes effect on "
                 "2026-05-01.", "cites": ["S1"]},
        {"fact": "The court ruling arrived on April 15, 2026.",
         "cites": ["C2"]},
        {"fact": "The panel convenes Tuesday.", "cites": ["R1"]},
    ]
    clean, _ = validate(b)
    facts = _facts(clean)
    assert "April 15, 2026" in facts[0]
    assert "the accord takes effect" in facts[1]
    assert facts[2] == "The panel convenes Tuesday."


def test_malformed_iso_shaped_token_never_crashes_and_sorts_after_real_dates():
    """Garbage tolerance: '2026-13-45' is ISO-shaped nonsense; the validator
    must not crash and every fact must survive. It keys as (2026, 13, 45),
    deterministically after any real 2026 date."""
    b = qa_brief()
    b["pinned_facts"] = [
        {"fact": "A backlog entry cites 2026-13-45 in metadata.",
         "cites": ["S1"]},
        {"fact": "Delivery began on June 1, 2026.", "cites": ["C2"]},
        {"fact": "The panel convenes Tuesday.", "cites": ["R1"]},
    ]
    clean, _ = validate(b)
    facts = _facts(clean)
    assert len(facts) == 3
    assert "June 1, 2026" in facts[0]
    assert "2026-13-45" in facts[1]


def test_same_date_facts_keep_their_original_relative_order():
    """Equal keys -> stable sort: two facts dated August 2, 2026 keep their
    contract order."""
    b = qa_brief()
    b["pinned_facts"] = [
        {"fact": "OPEC meets on August 2, 2026, in Vienna.", "cites": ["S1"]},
        {"fact": "A parallel session on August 2, 2026, covers quotas.",
         "cites": ["C2"]},
        {"fact": "The panel convenes Tuesday.", "cites": ["R1"]},
    ]
    clean, _ = validate(b)
    facts = _facts(clean)
    assert facts[0].startswith("OPEC meets")
    assert facts[1].startswith("A parallel session")


# ===========================================================================
# 3. `settles` never surfaces — swept over the LIVE reader surfaces
# ===========================================================================

MARKER = "ZZSETTLESMARKERZZ never for the reader"


def _persist_marked_brief(con, date):
    brief = m3_brief()
    brief["watch"] = [{"observable": "communique by Thursday",
                       "settles": MARKER}]
    analysis.persist_brief(
        con, date, 1, "full", "valid", brief, "", 0.02,
        {"manifest": {}, "degraded": None},
        sources={"S1": {"kind": "cluster-full-text", "outlet": "The Hill",
                        "title": "Story", "url": "https://thehill.com/a",
                        "retrieved_at": "", "text": "body"}})
    return brief


def test_settles_absent_from_both_live_reader_surfaces_present_in_json(ui):
    """D3 end-to-end: the join key persists in brief_json (the future link
    field depends on it) and still reaches the WRITER view — but never the
    reader. Swept over the two real HTTP reader surfaces: the date-addressed
    page (/?date=, the no-JS path) and the /edition fragment (the openEdition
    injection path). The observable itself must render in both."""
    con = db.connect()
    date = "2026-07-06"
    _seed_edition(con, date, "Summit story", "Summit headline")
    _persist_marked_brief(con, date)

    # join key persisted in the forensic row
    row = con.execute(
        "SELECT brief_json FROM analysis_briefs WHERE date=? AND slot=1",
        (date,)).fetchone()
    assert MARKER in row[0]

    # writer view still shows the machinery (Sten's guard, untouched)
    doc = json.loads(row[0])
    assert MARKER in analysis.render_writer_view(doc["brief"])

    # reader surface 1: the full date-addressed page
    code, _, body = get(ui, f"/?date={date}")
    page = body.decode("utf-8")
    assert code == 200
    assert MARKER not in page
    assert "communique by Thursday" in page
    assert "What’s still open" in page

    # reader surface 2: the /edition fragment (archive in-place injection)
    code2, _, frag_body = get(ui, f"/edition?date={date}")
    frag = frag_body.decode("utf-8")
    assert code2 == 200
    assert MARKER not in frag
    assert "communique by Thursday" in frag
    con.close()


# ===========================================================================
# 4. What's-still-open — structure, glue, degenerates, escaping
# ===========================================================================

def test_one_paragraph_per_unknown_single_closing_watch_paragraph_in_order():
    """D2 mechanics under load: three unknowns -> three <p>, in contract
    order; three watch observables -> exactly ONE closing <p>, observables
    in contract order inside it; no interleaving."""
    b = m3_brief()
    b["unknowns"] = [
        {"question": "Alpha unknown stands", "why_material": "alpha bites",
         "would_resolve": "alpha test"},
        {"question": "Beta unknown stands", "why_material": "beta bites",
         "would_resolve": "beta test"},
        {"question": "Gamma unknown stands", "why_material": "gamma bites",
         "would_resolve": "gamma test"},
    ]
    b["watch"] = [
        {"observable": "First observable lands Monday.", "settles": "a"},
        {"observable": "Second observable lands Tuesday.", "settles": "b"},
        {"observable": "Third observable lands Thursday.", "settles": "c"},
    ]
    sec = _section(_deep(b), "story-0-open")
    assert sec.count("<p>") == 4                        # 3 unknowns + 1 watch
    order = ["Alpha unknown", "Beta unknown", "Gamma unknown",
             "First observable", "Second observable", "Third observable"]
    idx = [sec.index(m) for m in order]
    assert idx == sorted(idx)
    # all three observables share ONE paragraph
    assert "</p>" not in sec[sec.index("First observable")
                             :sec.index("Third observable")]


def test_glue_never_doubles_punctuation_and_adds_periods_only_when_needed():
    """D5 dumb glue: a field already ending in ./?/!/: is left alone; a bare
    field gains exactly one period. No '..', no '?.' artifacts."""
    b = m3_brief()
    b["unknowns"] = [{"question": "Who pays for the cleanup?",
                      "why_material": "the treaty text decides the bill.",
                      "would_resolve": "the annex publication"}]
    b["watch"] = [{"observable": "Annex text due Friday", "settles": "x"}]
    sec = _section(_deep(b), "story-0-open")
    assert "Who pays for the cleanup? the treaty text decides the bill." in sec
    assert "What would settle it — the annex publication." in sec
    assert "Annex text due Friday." in sec
    assert ".." not in sec and "?." not in sec and "!." not in sec


def test_unknown_with_empty_optional_fields_renders_only_what_exists():
    """why_material/would_resolve arrive as empty strings (type-checked only
    upstream — validator-reachable): the paragraph is the question alone,
    with no orphaned 'What would settle it —' glue and no empty sentences."""
    b = m3_brief()
    b["unknowns"] = [{"question": "The ministry has not named the holdouts",
                      "why_material": "", "would_resolve": ""}]
    b["watch"] = []
    sec = _section(_deep(b), "story-0-open")
    assert "<p>The ministry has not named the holdouts.</p>" in sec
    assert "What would settle it" not in sec


def test_open_section_escapes_hostile_unknown_and_watch_content():
    """The new prose path is innerHTML-adjacent (archive injection): model-
    authored unknown/watch text must render inert."""
    b = m3_brief()
    b["unknowns"] = [{"question": "<img src=x onerror=alert(1)> stands",
                      "why_material": "it <script>alert(2)</script> bites",
                      "would_resolve": "the \"quoted\" & <b>bold</b> test"}]
    b["watch"] = [{"observable": "<svg onload=alert(3)> lands Monday",
                   "settles": "x"}]
    html = _deep(b)
    sec = _section(html, "story-0-open")
    for live in ("<img", "<script", "<svg", "<b>"):
        assert live not in sec
    assert "&lt;img" in sec and "&lt;script" in sec and "&lt;svg" in sec


def test_all_empty_watch_observables_leave_no_headerless_residue():
    """ACCEPTANCE RED (expected to FAIL until fixed) — D4: 'absent halves
    leave no residue.' watch entries whose observables are all empty strings
    pass the type-only validator and persist; with no unknowns they render
    TODAY as a header-only 'What's still open' section plus a live jumplist
    anchor pointing at furniture with no content — a placeholder, which D4
    bans.

    FIX CONTRACT: emit the section and its jumplist anchor only when at
    least one rendered paragraph exists (compute the paragraphs first, then
    gate both the anchor and the section on them — the current truthiness
    check on the raw lists is the wrong signal). The implementer's
    absent-halves pins (real observables) must not move; a validator-side
    non-empty-observable tightening would also satisfy this test but must
    then be its own disclosed contract."""
    b = m3_brief()
    b["unknowns"] = []
    b["watch"] = [{"observable": "", "settles": "x"},
                  {"observable": "   ", "settles": "y"}]
    html = _deep(b)
    assert 'id="story-0-open"' not in html
    assert "What’s still open" not in html
    jump = html.split('class="deep-jumplist"')[1].split("</p>")[0]
    assert ">Still open</a>" not in jump


# ===========================================================================
# 5. Arc-line edges
# ===========================================================================

def _arc_brief(cites, sources_extra):
    b = m3_brief(with_arc=True)
    b["arc"]["cites"] = cites
    b["sources"].extend(sources_extra)
    return b


def test_arc_p_cite_with_blank_date_renders_line_but_no_link():
    """A prior-briefing source whose retrieved_at is blank cannot name an
    edition: the continuity line still renders whole, with no link and no
    broken text — never a dead affordance."""
    b = _arc_brief(["P1"], [{"key": "P1", "kind": "prior-briefing",
                             "outlet": "NewsLens", "title": "prior",
                             "url": "", "retrieved_at": ""}])
    html = _deep(b)
    tb = html.split("deep-title-block")[1].split("</div>")[0]
    assert 'class="deep-arc-line"' in tb
    assert "Advances the thread" in tb and "staging became" in tb
    assert "deep-arc-link" not in tb and "openEdition(" not in html


def test_arc_p_cite_behind_non_prior_cites_still_links():
    """The link derives from the first PRIOR-BRIEFING cite, not the first
    cite: ['S1', 'P1'] must still produce the dated link."""
    b = _arc_brief(["S1", "P1"], [{"key": "P1", "kind": "prior-briefing",
                                   "outlet": "NewsLens", "title": "prior",
                                   "url": "",
                                   "retrieved_at": "2026-07-05T08:00:00Z"}])
    html = _deep(b)
    tb = html.split("deep-title-block")[1].split("</div>")[0]
    assert "openEdition('2026-07-05', event)" in tb
    assert 'href="/?date=2026-07-05"' in tb


def test_arc_dangling_p_cite_never_crashes_and_never_links():
    """An arc citing a key absent from the source table (legacy/degraded
    rows) renders the line unlinked — no KeyError, no dead link."""
    b = _arc_brief(["P9"], [])
    html = _deep(b)
    tb = html.split("deep-title-block")[1].split("</div>")[0]
    assert 'class="deep-arc-line"' in tb
    assert "deep-arc-link" not in tb


def test_arc_line_inside_archived_edition_fragment_scoped_and_navigable(ui):
    """Item 2 inside NL-11's archive-in-place surface: the /edition fragment
    carries date-scoped deep-view ids (ed<date>-story-0-*), the arc line, and
    an openEdition target for the PRIOR date — clicking continuity from
    within an archived edition replaces the mount with the prior edition
    (same in-place semantics; 'Back to Archive' stays the exit). The no-JS
    href falls back to full navigation."""
    con = db.connect()
    _seed_edition(con, "2026-07-05", "Prior story", "Prior headline")
    _seed_edition(con, "2026-07-06", "Summit story", "Summit headline")
    brief = _arc_brief(["P1"], [{"key": "P1", "kind": "prior-briefing",
                                 "outlet": "NewsLens", "title": "prior",
                                 "url": "", "retrieved_at": "2026-07-05"}])
    analysis.persist_brief(
        con, "2026-07-06", 1, "full", "valid", brief, "", 0.02,
        {"manifest": {}, "degraded": None},
        sources={"S1": {"kind": "cluster-full-text", "outlet": "The Hill",
                        "title": "Story", "url": "https://thehill.com/a",
                        "retrieved_at": "", "text": "body"}})
    code, _, body = get(ui, "/edition?date=2026-07-06")
    frag = body.decode("utf-8")
    assert code == 200
    assert "← Back to Archive" in frag
    assert 'id="ed2026-07-06-story-0-facts"' in frag     # date-scoped ids
    assert 'id="ed2026-07-06-story-0-open"' in frag
    assert "openEdition('2026-07-05', event)" in frag    # continuity target
    assert 'href="/?date=2026-07-05"' in frag            # no-JS fallback
    # v8-M1 item 4 (CONSCIOUS FLIP): the inline cite-fold apparatus DIES — the
    # archived fragment renders the same plain-count / source-cluster grammar as
    # a live deep view, no ▸ folds.
    assert "cite-fold" not in frag
    assert 'class="cite">(' in frag or 'class="src-cluster"' in frag
    con.close()


# ===========================================================================
# 6. Jumplist anchors resolve — property over content shapes
# ===========================================================================

def test_every_jumplist_anchor_resolves_across_content_shapes():
    shapes = [m3_brief(), m3_brief(with_arc=True)]
    b = m3_brief(); b["effects"] = []; shapes.append(b)
    b = m3_brief(); b["unknowns"] = []; b["watch"] = []; shapes.append(b)
    b = m3_brief(); b["effects"] = []; b["unknowns"] = []; b["watch"] = []
    shapes.append(b)
    for brief in shapes:
        html = _deep(brief)
        jump = html.split('class="deep-jumplist"')[1].split("</p>")[0]
        for anchor in re.findall(r'href="#([^"]+)"', jump):
            assert f'id="{anchor}"' in html, f"dead anchor #{anchor}"


def test_empty_pinned_facts_row_renders_without_crash_and_keeps_anchor():
    """analysis_briefs is append-only and forever: a degraded legacy row with
    zero pinned facts must render (empty list, live Facts anchor), never
    crash the whole deep view."""
    b = m3_brief()
    b["pinned_facts"] = []
    html = _deep(b)
    assert 'id="story-0-facts"' in html
    assert '<ul class="deep-facts-list"></ul>' in html


# ===========================================================================
# 7. Fold-away + D1 deleted-not-restyled (CSS/JS contracts)
# ===========================================================================

def test_one_fold_per_fact_each_keyboard_labelled_and_open():
    b = m3_brief()
    b["pinned_facts"] = [
        {"fact": "First fact stands.", "cites": ["S1"]},
        {"fact": "Second fact stands.", "cites": ["C1"]},
        {"fact": "Third fact stands.", "cites": ["R1"]},
    ]
    facts = _section(_deep(b), "story-0-facts")
    # v8-M1 item 4 (CONSCIOUS FLIP): the per-fact cite-fold DIES — each fact now
    # closes with a PLAIN end-of-line outlet count, no caret, no reveal.
    assert "cite-fold" not in facts
    assert 'aria-label="Show sources for this fact"' not in facts
    assert facts.count('<span class="cite">(1 outlet)</span>') == 3


def test_register_css_deleted_not_restyled_and_fold_css_present():
    """D1: '.unknown-q / .unknown-beat styling and the (settles:) span are
    deleted, not restyled' — swept over the shipped stylesheet, plus the
    reader-removed ledger/discrepancy/watch-item classes. CONSCIOUS FLIP
    (gate FIX-2, 2026-07-17): the cite-fold apparatus itself is now DELETED
    (v8-M1 item 4 killed inline citations), so the fold joins the
    deleted-not-restyled sweep instead of being asserted present."""
    css = webui.CSS
    for gone in ("unknown-q", "unknown-beat", "deep-discrepancy",
                 "deep-ledger", "deep-watch-item", "unresolved-tag",
                 "cite-fold", "fact-cite"):
        assert gone not in css
    assert ".deep-arc-line" in css


def test_collapse_apparatus_fully_removed_from_shipped_js():
    """CONSCIOUS FLIP (gate FIX-2, 2026-07-17): the ordering contract this
    test pinned died with the apparatus — collapseCiteFolds and both its call
    sites are REMOVED from the shipped JS (v8-M1 item 4; the absence pin in
    test_v8_m1_ui_qa is the cross-file guard)."""
    assert "collapseCiteFolds" not in webui.JS
