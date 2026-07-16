"""NL-68 v7.2 fix batch — QA adversarial extensions (DECISIONS 2026-07-16
"THE NL-68 REVIEW VERDICT" + addendum; QA pass 2026-07-16).

Extends the implementer's per-item pins (test_nl68_batch.py) along the QA
dispatch's hammer list: item 4 date edges (abbreviations, year rollover,
vague/dateless, a date inside an attributed quote — behavior RULED and pinned
here), item 5 false-fold hostility (cross-unit magnitudes, direction words,
mid-sentence negation, the LIVE 07-14 rows replicated from the real ledger,
the <details> a11y contract, the validator-side wiring), item 7's sanctioned-
split-day interplay, item 3 on the MEDIUM tier (the implementer's own gap
admission), item 8 on the quick tier, item 10's one-shot flag + guard order +
no-JS degrade, item 12 hostile fixtures, and the item 6 source-level retirement.

KNOWN-RED: test_BUG36_* — the same-referent fold pairs number SETS, so a
multi-number order swap ("20 dead, 50 injured" vs "50 dead, 20 injured")
folds as noise despite being a genuine contested claim. Fix contract in the
test docstring.

Offline, sandboxed via the autouse conftest, like every other file here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from newslens import analysis, db, labels, paths, server, webui

from test_ui_polish import slot, story, seed, TODAY, iso_now
from test_nl68_batch import _seed_quick_edition, _seed_thread_with_ledger
from test_analysis_brief_qa import qa_brief, validate


def _con():
    db.migrate()
    return db.connect()


# ===========================================================================
# Item 4 — stale-watch guard: the date edges (QA hammer 1)
# ===========================================================================

def test_month_abbreviations_pass_through_unstripped():
    """_MONTH_DAY_RE matches FULL month names only — 'Jan 3' / 'Sept. 2' are
    not parsed, so abbreviated dates are never judged stale and the text
    passes through UNTOUCHED (the disclosed no-false-teeth failure mode:
    conservative, keeps possibly-stale text rather than risking a wrong
    strip). Pinned so a future regex widening is a conscious flip, not
    drift."""
    edition = "2026-07-14"
    for text in ("Talks resume Jan 3.", "Talks resume Jul. 12.",
                 "The report lands Sept. 2."):
        kept, stripped = analysis.strip_stale_watch(text, edition)
        assert kept == text, text
        assert stripped == [], text


def test_bare_month_day_resolves_to_the_nearest_year_across_rollover():
    """A bare month+day resolves to the calendar year NEAREST the edition:
    'January 3' read in July 2026 is next January (future -> kept), never
    seven months back; 'December 30' read in early January IS the stale one
    just past (stripped); 'January 2' read in late December is days ahead
    (kept). The year-rollover edges of the QA dispatch, clock-independent."""
    # forward rollover: mid-2026 -> next January is nearer than last January
    kept, stripped = analysis.strip_stale_watch(
        "Ministers reconvene January 3.", "2026-07-16")
    assert kept == "Ministers reconvene January 3." and stripped == []
    # backward rollover: early-January edition, a late-December date is stale
    kept, stripped = analysis.strip_stale_watch(
        "The waiver lapsed December 30. Watch the docket.", "2027-01-05")
    assert "December 30" not in kept
    assert "Watch the docket." in kept
    assert stripped and "December 30" in stripped[0]
    # forward across New Year: late-December edition, 'January 2' is ahead
    kept, stripped = analysis.strip_stale_watch(
        "Filings are due January 2.", "2026-12-28")
    assert kept == "Filings are due January 2." and stripped == []


def test_vague_and_dateless_watch_text_passes_untouched():
    """'early July', bare month names, relative phrases: no month+DAY token,
    so nothing is parsed and nothing is stripped — no false teeth (the
    dispatch's own words)."""
    edition = "2026-07-14"
    for text in ("Expect movement in early July.",
                 "A decision could come in July.",
                 "Watch for retaliation in the coming weeks.",
                 "July 2026 budget talks continue."):  # month + 4-digit year, no day
        kept, stripped = analysis.strip_stale_watch(text, edition)
        assert kept == text, text
        assert stripped == [], text


def test_stale_date_inside_an_attributed_quote_drops_the_whole_sentence():
    """QA RULING (hammer 1, pinned as the honest behavior): a stale forward-
    claim does not become renderable because it is quoted — presenting
    '"talks on July 12" she said' as a WATCH FOR item in the July-14 edition
    is exactly the defect class the principal flagged. The guard drops the
    ENTIRE sentence (attribution included) and never edits inside the quote —
    dropping a sentence misquotes nobody; rewriting one would. Neighboring
    sentences are untouched."""
    edition = "2026-07-14"
    text = ('The ministry set expectations. '
            '"We expect talks to resume July 12," the minister said. '
            'Watch the July 20 session for a vote.')
    kept, stripped = analysis.strip_stale_watch(text, edition)
    # the quoted-stale sentence is gone WHOLE — no fragment survives
    assert "July 12" not in kept
    assert "the minister said" not in kept
    assert "We expect talks" not in kept
    # neighbors intact, including the genuinely-future date
    assert "The ministry set expectations." in kept
    assert "Watch the July 20 session for a vote." in kept
    assert len(stripped) == 1 and "July 12" in stripped[0]


def test_far_past_bare_dates_read_forward_not_stale():
    """Gate hardening (item 4): a bare month-day whose nearest resolution is
    months past reads FORWARD in forward-looking text — only the recent-past
    window (90 days) strips. 'March 10' in a July edition means next March."""
    kept, stripped = analysis.strip_stale_watch(
        "The ruling is expected March 10.", "2026-07-14")
    assert kept == "The ruling is expected March 10." and stripped == []


def test_explicit_year_dates_resolve_verbatim_not_nearest():
    """Gate hardening (item 4, QA edge b): an explicit year is the writer's
    referent — an explicitly-past-year date is stale even when its month-day
    lies ahead of the edition; an explicit future year never strips."""
    kept, stripped = analysis.strip_stale_watch(
        "Talks were held July 20, 2025. Watch the docket.", "2026-07-14")
    assert "July 20, 2025" not in kept and "Watch the docket." in kept
    assert stripped and "2025" in stripped[0]
    kept2, s2 = analysis.strip_stale_watch(
        "The treaty review is set for March 10, 2027.", "2026-07-14")
    assert kept2 == "The treaty review is set for March 10, 2027." and s2 == []


# ===========================================================================
# Item 5 — the raised bar under fire (QA hammer 2)
# ===========================================================================

def test_cross_unit_same_magnitude_folds_different_magnitude_never():
    """'$1.2B' and '$1,200M' assert the same magnitude in different clothes —
    paraphrase, folds. A thousandfold difference never does."""
    assert analysis.same_referent_numbers("$1.2B", "$1,200M")
    assert analysis.same_referent_numbers("$1.2 billion", "$1,200 million")
    assert analysis.same_referent_numbers("$3.4bn", "$3,400m")
    assert not analysis.same_referent_numbers("$1.2B", "$1.2M")
    assert not analysis.same_referent_numbers("$81 billion", "$111 billion")
    # the LIVE 07-14 rounding pair: 1.776e9 != 1.8e9 at the disclosed
    # 4-significant-figure bar — KEPT (conservative; removal is one ruling away)
    assert not analysis.same_referent_numbers("$1.776 billion fund",
                                              "$1.8 billion fund")


def test_direction_words_and_mid_sentence_negation_keep_the_row():
    """Meaning-bearing residuals differ -> never folded: direction pairs,
    open/closed, rose/fell, and a negation buried mid-sentence."""
    assert not analysis.same_referent_numbers("up 20%", "down 20%")
    assert not analysis.same_referent_numbers("20% open", "20% closed")
    assert not analysis.same_referent_numbers("rose to 98", "fell to 98")
    assert not analysis.same_referent_numbers(
        "Officials say the strait is not closed, with 20% transiting",
        "Officials say the strait is closed, with 20% transiting")


def test_BUG36_multi_number_pairing_swap_must_not_fold():
    """CONFIRMED BUG (QA 2026-07-16, red by design — KNOWN-RED convention):
    _numeric_referents collapses each side to a SET of magnitudes and
    _residual_words to a SORTED bag, so two sides that pair the SAME numbers
    with DIFFERENT nouns — '20 dead, 50 injured' vs '50 dead, 20 injured', a
    genuine contested claim — compare equal and FOLD as paraphrase noise, at
    BOTH surfaces (render-side row drop + validate_brief entry drop). This
    violates the function's own documented contract ('conservative by
    construction: any doubt keeps the row').

    FIX CONTRACT: fold only when each side carries exactly ONE numeric
    referent (the entire disclosed paraphrase class — '20%' vs 'about 20
    percent', '$1.2B' vs '$1,200M', '1,200' vs '1200' — is single-figure);
    multi-number sides always keep the row. Alternative: compare ordered
    (number, trailing-word) pairs. Either way this test goes green with no
    disclosed fold case regressing (the three green folds above must hold).
    """
    assert not analysis.same_referent_numbers("20 dead, 50 injured",
                                              "50 dead, 20 injured")


def _brief_doc(rows):
    """A minimal render-ready doc: the shape server._render_deep_view reads."""
    return {"header": {}, "brief": {
        "pinned_facts": [{"fact": "A fact.", "cites": ["S1"]}],
        "mechanism": "m.", "effects": [], "unknowns": [], "watch": [],
        "arc": None, "ledger": rows,
        "sources": [{"key": "S1", "outlet": "BBC", "title": "t",
                     "url": "http://x", "retrieved_at": "",
                     "kind": "cluster-full-text"},
                    {"key": "S2", "outlet": "AP", "title": "u",
                     "url": "http://y", "retrieved_at": "",
                     "kind": "retrieved"}]}}


def test_live_0714_ledger_rows_all_survive_the_bar():
    """The REAL 2026-07-14 edition's three discrepancy rows (values copied
    from the live ledger, replicated in the sandbox): the Hormuz closure
    contradiction (no numbers -> never foldable), the $81B-vs-$111B contested
    figure, and the $1.776B-vs-$1.8B rounding pair (kept under the disclosed
    4-sig-fig bar). ALL THREE survive into the fold; the count says so. The
    live lead's substantive row surviving is the QA dispatch's named must."""
    rows = [
        {"discrepancy": True, "note": "closure status of the Strait of Hormuz",
         "a": {"value": "Iran fully closes Strait of Hormuz over US blockade.",
               "cites": ["S1"]},
         "b": {"value": "Iran has not fully closed the Strait of Hormuz.",
               "cites": ["S2"]}},
        {"discrepancy": True, "note": "Different reported values for the merger",
         "a": {"value": "$81 billion", "cites": ["S1"]},
         "b": {"value": "$111 billion", "cites": ["S2"]}},
        {"discrepancy": True, "note": "Fund amount differs",
         "a": {"value": "$1.776 billion fund", "cites": ["S1"]},
         "b": {"value": "$1.8 billion fund", "cites": ["S2"]}},
    ]
    html = server._render_deep_view("story-0", "HL", _brief_doc(rows),
                                    "2026-07-14")
    open_sec = html.split('id="story-0-open"')[1]
    assert "Iran fully closes Strait of Hormuz" in open_sec
    assert "Iran has not fully closed" in open_sec
    assert "$81 billion" in open_sec and "$111 billion" in open_sec
    assert "$1.776 billion fund" in open_sec
    assert f"3 {labels.DISCREPANCY_FOLD}" in open_sec


def test_discrepancy_fold_is_native_closed_and_counted():
    """The a11y contract of the collapse (QA hammer 2): a NATIVE <details>
    (keyboard-operable by construction: summary is focusable and toggles on
    Enter/Space), rendered CLOSED (no `open` attribute), the row count in the
    summary's accessible text, the decorative caret aria-hidden, and the rows
    INSIDE the element. Singular count uses the singular noun."""
    one = _brief_doc([
        {"discrepancy": True, "note": "n",
         "a": {"value": "Fully closed.", "cites": ["S1"]},
         "b": {"value": "Not fully closed.", "cites": ["S2"]}}])
    html = server._render_deep_view("story-0", "HL", one, "2026-07-14")
    m = re.search(r"<details class=\"deep-open-discrepancies\"[^>]*>", html)
    assert m and " open" not in m.group(0)          # closed by default
    details = html.split('<details class="deep-open-discrepancies"')[1] \
                  .split("</details>")[0]
    assert "<summary>" in details
    summary = details.split("<summary>")[1].split("</summary>")[0]
    assert f"1 {labels.DISCREPANCY_FOLD_ONE}" in summary   # count in the a11y name
    assert 'aria-hidden="true"' in summary                 # the caret is decoration
    assert "Fully closed." in details                      # rows live INSIDE
    # JS never forces it open: the fold is reader-controlled
    assert "deep-open-discrepancies" not in webui.JS


def test_generation_side_same_referent_figure_drop_is_wired_and_disclosed():
    """Item 5's OTHER surface (wiring proof, claims-of-wiring rule): new
    briefs drop the paraphrase pair AT VALIDATION with a disclosed warning,
    so the noise never persists; a substantive contested figure sails
    through."""
    b = qa_brief()
    b["ledger"] = [{"discrepancy": True,
                    "a": {"value": "about 20 percent", "cites": ["S1"]},
                    "b": {"value": "20%", "cites": ["C2"]},
                    "note": "restated share"}]
    clean, warnings = validate(b)
    assert all(not e.get("discrepancy") for e in clean["ledger"])
    assert any("restate the same figure" in w for w in warnings)
    b2 = qa_brief()
    b2["ledger"] = [{"discrepancy": True,
                     "a": {"value": "20 percent", "cites": ["S1"]},
                     "b": {"value": "30 percent", "cites": ["C2"]},
                     "note": "share disputed"}]
    clean2, warnings2 = validate(b2)
    assert any(e.get("discrepancy") for e in clean2["ledger"])
    assert not any("restate the same figure" in w for w in warnings2)


# ===========================================================================
# Item 7 — the sanctioned-split day: arc dedup x marker suppression (hammer 3)
# ===========================================================================

def test_split_day_renders_the_covered_before_signal_once_per_slot_never_twice():
    """Two same-thread slots in one edition (the sanctioned-split shape,
    BUG-35's dedup): the earliest slot carries the arc (marker suppressed);
    the sibling's arc is deduped so it KEEPS the marker as its sole signal.
    Net: each slot signals prior coverage exactly once, in exactly one form,
    and no slot ever renders both."""
    con = _con()
    topic = "Strait of Hormuz"
    _seed_thread_with_ledger(con, topic, prior_date="2026-07-05")
    slots = [
        {"slot": "1", "story_title": "Strikes exchanged", "summary": "s1",
         "item_ids": [], "outlets": ["The Hill"], "matched_tags": [],
         "matched_memory": [topic], "override": False,
         "corroboration_label": "Reported by 1 named outlet"},
        {"slot": "2", "story_title": "Insurance rates spike", "summary": "s2",
         "item_ids": [], "outlets": ["Reuters"], "matched_tags": [],
         "matched_memory": [topic], "override": False,
         "corroboration_label": "Reported by 1 named outlet"},
    ]
    con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                (TODAY, json.dumps(slots)))
    con.commit()
    entry = {"ts": iso_now(), "date": TODAY, "status": "ok", "sample": False,
             "tiers": ["full", "full"],
             "stories": [
                 {"headline": "Strikes exchanged", "lede": "The strait closed."},
                 {"headline": "Insurance rates spike",
                  "lede": "Premiums doubled overnight."}]}
    (paths.DATA_DIR / "generation_log.jsonl").write_text(
        json.dumps(entry) + "\n", encoding="utf-8")
    page, _ = server.build_page(con)
    con.close()
    today = page.split('id="view-today"')[1].split('id="view-following"')[0]
    assert today.count("When we last covered this") == 1     # arc dedup holds
    assert today.count("Tracked ongoing story") == 1         # sibling keeps marker
    articles = re.findall(r"<article[^>]*>.*?</article>", today, re.S)
    assert len(articles) >= 2
    for art in articles:
        has_arc = "When we last covered this" in art
        has_marker = "Tracked ongoing story" in art
        assert not (has_arc and has_marker)                  # never both in one slot
    arc_art = [a for a in articles if "When we last covered this" in a]
    marker_art = [a for a in articles if "Tracked ongoing story" in a]
    assert len(arc_art) == 1 and len(marker_art) == 1
    assert arc_art[0] is not marker_art[0]


# ===========================================================================
# Item 3 — THE SUPERSET LAW on the MEDIUM tier (hammer 5; their gap admission)
# ===========================================================================

def test_medium_tier_deep_view_obeys_the_superset_law():
    """The implementer proved lead(full) and quick; the MEDIUM tier rides the
    same _render_deep_view path — proven here: a briefed medium story's deep
    view opens with its OWN Today prose (lede + why-it-matters + watch-for),
    before the first analyst section."""
    con = _con()
    s2 = story(2, "Medium story", "medium")
    s2["why_it_matters"] = "A medium-tier consequence, stated plainly."
    s2["watch_for"] = "The medium-tier committee vote."
    seed(con, [slot(1, "Lead"), slot(2, "Medium story")],
         [story(1, "Lead"), s2])
    analysis.persist_brief(
        con, TODAY, 2, "medium", "valid",
        {"pinned_facts": [{"fact": "A fact.", "cites": ["S1"]}], "ledger": [],
         "mechanism": "m [S1].", "effects": [], "arc": None, "unknowns": [],
         "watch": [], "sources": [{"key": "S1", "outlet": "BBC", "title": "t",
                                   "url": "http://x", "retrieved_at": "",
                                   "kind": "cluster-full-text"}],
         "notes_for_writer": ""},
        "", 0.0, {"manifest": {}, "degraded": None},
        sources={"S1": {"kind": "cluster-full-text", "outlet": "BBC",
                        "title": "t", "url": "http://x", "retrieved_at": "",
                        "text": "b"}})
    page, _ = server.build_page(con)
    con.close()
    assert "view-deep-story-1" in page                       # medium got its view
    deep = page.split('id="view-deep-story-1"')[1].split("</section>")[0]
    assert "deep-today-prose" in deep
    assert deep.index("deep-today-prose") < deep.index("deep-section-label")
    assert "The lede sentence for this story." in deep       # the Today lede
    assert "A medium-tier consequence, stated plainly." in deep
    assert "The medium-tier committee vote." in deep


# ===========================================================================
# Item 8 — quick-tier titles click through too (extends their lead pin)
# ===========================================================================

def test_quick_snippet_title_links_to_its_sources_context_view():
    """An In-Brief quick item HAS a deep view (the $0 sources-&-context one),
    so its SNIPPET title is the same real-anchor click-through, targeting the
    SAME view as the bottom entry link. Seeded as slot 2 behind a full lead so
    the quick story renders in the true snippet role (a lone slot would be
    promoted to the grid's lead position)."""
    con = _con()
    slots = [{"slot": "1", "story_title": "Lead story", "summary": "s1",
              "item_ids": [], "outlets": ["The Hill"], "matched_tags": [],
              "matched_memory": [], "override": False,
              "corroboration_label": "Reported by 1 named outlet"},
             {"slot": "2", "story_title": "Court story", "summary": "s2",
              "item_ids": [], "outlets": ["AP"], "matched_tags": [],
              "matched_memory": [], "override": False,
              "corroboration_label": "Reported by 1 named outlet"}]
    con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                (TODAY, json.dumps(slots)))
    con.commit()
    entry = {"ts": iso_now(), "date": TODAY, "status": "ok", "sample": False,
             "tiers": ["full", "quick"],
             "stories": [{"headline": "Lead story", "lede": "The lead lede."},
                         {"headline": "Court story", "lede": "A blurb."}]}
    (paths.DATA_DIR / "generation_log.jsonl").write_text(
        json.dumps(entry) + "\n", encoding="utf-8")
    page, _ = server.build_page(con)
    con.close()
    snippet = page.split('<article class="snippet"')[1].split("</article>")[0]
    assert "Court story" in snippet
    assert 'class="headline-link"' in snippet
    assert snippet.count("openDeepView('story-1', event)") == 2  # title + entry
    assert "sources-context-link" in snippet


# ===========================================================================
# Item 10 — one-shot flag, guard order, no-JS degrade (hammer 4)
# ===========================================================================

def test_follow_suggestions_exclude_active_offer_recent_titles_and_dormant():
    """_story_follow_suggestions: recent edition titles first (deduped,
    recency order), dormant/dismissed threads offered with the 'an earlier
    thread' secondary, ACTIVE follows excluded case-insensitively."""
    con = _con()
    now = "2026-07-01T00:00:00.000Z"
    with con:
        con.execute("INSERT INTO memory (topic, status, status_changed_at,"
                    " created_at, updated_at) VALUES"
                    " ('strikes EXCHANGED', 'active', ?, ?, ?)", (now, now, now))
        con.execute("INSERT INTO memory (topic, status, status_changed_at,"
                    " created_at, updated_at) VALUES"
                    " ('Grain corridor', 'dismissed_user', ?, ?, ?)",
                    (now, now, now))
        con.execute(
            "INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
            ("2026-07-10", json.dumps([
                {"slot": "1", "story_title": "Strikes exchanged"},
                {"slot": "2", "story_title": "Port reopens"}])))
        con.execute(
            "INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
            ("2026-07-14", json.dumps([
                {"slot": "1", "story_title": "Port reopens"},
                {"slot": "2", "story_title": "Tariff ruling lands"}])))
    got = server._story_follow_suggestions(con)
    con.close()
    values = [o["v"] for o in got]
    assert "Strikes exchanged" not in values          # active (case-insensitive)
    assert values.count("Port reopens") == 1          # deduped across editions
    assert "Tariff ruling lands" in values
    assert {"v": "Grain corridor", "l": "Grain corridor",
            "s": "an earlier thread"} in got          # dormant re-follow offer
    assert values.index("Tariff ruling lands") < values.index("Grain corridor")


def test_expand_flag_is_one_shot_and_suggest_guard_precedes_routing():
    """The fold-expand flag must NOT survive a second reload: nl-restore is
    removed from sessionStorage in the SAME statement that reads it, before
    any use — so a second reload sees no flag and the quiet fold falls back
    to its server-rendered (collapsed-when-updates-exist) state. And in
    suggestSubmit, the data-suggest-only no-op guard sits BEFORE the kind
    routing, so no path (typed, pasted, keyboard) reaches followStory with
    unvetted text."""
    js = webui.JS
    restore = js.split("function restoreViewAfterReload", 1)[1] \
                .split("\nfunction ", 1)[0]
    get_i = restore.index("sessionStorage.getItem('nl-restore')")
    rm_i = restore.index("sessionStorage.removeItem('nl-restore')")
    use_i = restore.index("st.expandQuiet")
    assert get_i < rm_i < use_i                       # consumed before any use
    # only the follow verb sets the flag; every other verb reloads without it
    assert js.count("reloadPreservingView(true)") == 1
    follow_region = js.split("function followStory", 1)[1] \
                      .split("\nfunction ", 1)[0]
    assert "reloadPreservingView(true)" in follow_region
    submit = js.split("function suggestSubmit", 1)[1].split("\nfunction ", 1)[0]
    guard_i = submit.index("data-suggest-only")
    routing_i = submit.index("var kind")
    assert guard_i < routing_i
    assert "if (!value || !match) return;" in submit  # the no-op, not a fallback
    # Enter with no highlighted option routes through the guarded submit —
    # there is no alternate keyboard path to followStory
    keydown = js.split("function suggestKeydown", 1)[1].split("\nfunction ", 1)[0]
    assert "suggestSubmit(container, inp.value)" in keydown
    assert "followStory" not in keydown


def test_story_combobox_no_js_degrade_cannot_submit_free_text():
    """With JS off the story combobox degrades to an inert input: no <form>
    anywhere in the document to hijack Enter into a GET, the listbox is
    server-rendered hidden, and only the STORY combobox carries the
    suggest-only marker (topics/writers keep type-to-add)."""
    con = _con()
    seed(con, [slot(1, "Lead")], [story(1, "Lead")])
    page, _ = server.build_page(con)
    con.close()
    assert "<form" not in page
    following = page.split('id="view-following"')[1].split('id="view-archive"')[0]
    story_box = following.split('data-kind="story"')[0].rsplit("<div", 1)[0]
    # the story combobox region: marker present, list hidden until JS
    box = '<div class="suggest" data-kind="story" data-suggest-only="1">'
    assert box in following
    story_region = following.split(box)[1].split("</div>")[0]
    assert 'role="listbox" hidden' in story_region
    # topics and writers keep free-typing: NO suggest-only marker on them
    for kind in ("topic", "writer"):
        assert f'data-kind="{kind}" data-suggest-only' not in following
        assert f'data-kind="{kind}"' in following


# ===========================================================================
# Item 12 — latest-edition scoping under hostile fixtures (hammer 6)
# ===========================================================================

def test_topic_suggestions_hostile_shapes_never_crash_or_resurface():
    """Malformed latest-edition JSON -> [] (and no resurfacing of older
    editions' tags THROUGH the malformed latest); non-dict/nameless tag
    entries skipped; followed topics excluded case-insensitively; duplicate
    tags deduped keeping the first spelling; empty DB -> []."""
    from types import SimpleNamespace
    cfg = SimpleNamespace(interests_broad=["ai regulation"],
                          interests_granular=[], sources=[],
                          followed_analyst_sources=[])
    con = _con()
    assert server._topic_suggestions(con, cfg) == []          # no editions at all
    with con:
        con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                    ("2026-07-01", json.dumps([
                        {"slot": "1", "matched_tags": [{"name": "Old Only"}]}])))
        # RAW malformed JSON is schema-impossible (json_valid CHECK, 0003) —
        # the hostile latest shape that CAN exist is valid-JSON-wrong-type:
        con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                    ("2026-07-14", json.dumps({"hostile": "not a list"})))
    # wrong-type latest -> [] and the OLD edition's tag must NOT leak through
    assert server._topic_suggestions(con, cfg) == []
    with con:
        con.execute("DELETE FROM briefings WHERE date = '2026-07-14'")
        con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                    ("2026-07-15", json.dumps([{
                        "slot": "1",
                        "matched_tags": [
                            {"name": "AI Regulation"},        # followed -> excluded
                            {"name": "Grid Storage"},
                            {"name": "grid storage"},         # dupe, later spelling
                            {"noname": True}, "bare-string",  # hostile shapes
                            {"name": ""},                     # nameless
                        ]}])))
    got = server._topic_suggestions(con, cfg)
    con.close()
    assert got == [{"v": "Grid Storage", "l": "Grid Storage"}]


# ===========================================================================
# Item 6 — the retirement is source-deep (no renderer references remain)
# ===========================================================================

def test_kicker_constant_is_referenced_by_no_renderer():
    """labels.KICKER_LEAD is retired-but-kept (import safety); the wiring
    proof that nothing renders it: neither server.py nor webui.py mentions
    the name at all. (The rendered-page absence is the implementer's pin;
    this pins the source so a re-wiring is a conscious diff.)"""
    for mod in (server, webui):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "KICKER_LEAD" not in src, mod.__name__
