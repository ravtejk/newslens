"""NL-68 v7.2 fix batch — implementer per-item pins (DECISIONS 2026-07-16 "THE
NL-68 REVIEW VERDICT" + the addendum items 13-14).

Each pin is red on the pre-fix code and green after; the enforcement surfaces
are born with the test only their wiring can flip (ENGINEERING.md claims-of-
wiring rule). Offline, sandboxed via the autouse conftest. Item 1 (MAX stories
6) was WITHDRAWN by the principal mid-batch — no pin here.

Scope map: items 3, 4, 5, 6, 7, 8, 10, 12, 14. Items 2/9/11 are design specs
(not built); item 7's prose-melding is the strategy council's (not pre-empted).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from newslens import analysis, db, paths, server, webui

from test_ui_polish import slot, story, seed, TODAY, iso_now


def _con():
    db.migrate()
    return db.connect()


def _month_day(offset_days: int) -> str:
    """A 'Month D' string offset from TODAY — for clock-independent stale/future
    watch-date tests (strip_stale_watch parses month-name+day)."""
    d = datetime.strptime(TODAY, "%Y-%m-%d") + timedelta(days=offset_days)
    return f"{d.strftime('%B')} {d.day}"


def _seed_thread_with_ledger(con, topic, prior_date="2026-07-05",
                             significance="A pricing dispute framed the strait."):
    now = "2026-07-01T00:00:00.000Z"
    tid = con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES (?, 'active', ?, ?, ?)", (topic, now, now, now)
    ).lastrowid
    con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, verdict,"
        " what_happened, significance, cites_json, slot) VALUES"
        " (?, ?, 'advances', 'Transit fees were imposed.', ?, '[\"S1\"]', NULL)",
        (tid, prior_date, significance))
    con.commit()
    return tid


# ===========================================================================
# Item 6 — "The Lead" label dies (the design carries the hierarchy)
# ===========================================================================

def test_lead_kicker_label_is_gone():
    con = _con()
    seed(con, [slot(1, "Lead"), slot(2, "Second")],
         [story(1, "Lead"), story(2, "Second", "medium")])
    page, _ = server.build_page(con)
    con.close()
    assert "The Lead" not in page
    assert 'class="kicker"' not in page


# ===========================================================================
# Item 8 — story titles click through to the deep view (keyboard-accessible)
# ===========================================================================

def test_story_title_is_a_real_link_to_its_deep_view():
    con = _con()
    seed(con, [slot(1, "Lead")], [story(1, "Lead")])
    # a valid brief so slot 1 gets a deep view
    analysis.persist_brief(
        con, TODAY, 1, "full", "valid",
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
    lead = page.split('<article class="lead"')[1].split("</article>")[0]
    # the headline is wrapped in a real <a> opening the SAME deep view as the entry
    assert 'class="headline-link"' in lead
    assert lead.count("openDeepView('story-0', event)") == 2  # title + bottom entry
    # no bare onclick div — real anchor semantics
    assert '<a class="headline-link" href="#"' in lead


def test_story_without_a_deep_view_has_no_title_link():
    """A degraded-hidden medium story (no brief, not quick) renders no deep view;
    its title must be a plain heading — never a dead link (item 8)."""
    con = _con()
    seed(con, [slot(1, "Lead"), slot(2, "Degraded")],
         [story(1, "Lead"), story(2, "Degraded", "medium")])
    page, _ = server.build_page(con)
    con.close()
    # slot 2 (story-1) has no brief and isn't quick -> no deep view, no headline-link
    assert "view-deep-story-1" not in page
    right = page.split('class="col-right"')[1]
    second = right.split('id="story-1"')[1].split("</article>")[0]
    assert "headline-link" not in second


# ===========================================================================
# Item 7 — the "we last covered this" double-render dies (renders ONCE)
# ===========================================================================

def _seed_arc_lead(con, topic="Strait of Hormuz"):
    _seed_thread_with_ledger(con, topic, prior_date="2026-07-05")
    slots = [{"slot": "1", "story_title": "Strikes exchanged", "summary": "s1",
              "item_ids": [], "outlets": ["The Hill"], "matched_tags": [],
              "matched_memory": [topic], "override": False,
              "corroboration_label": "Reported by 1 named outlet"}]
    con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                (TODAY, json.dumps(slots)))
    con.commit()
    entry = {"ts": iso_now(), "date": TODAY, "status": "ok", "sample": False,
             "tiers": ["full"],
             "stories": [{"headline": "Strikes exchanged",
                          "lede": "The strait closed."}]}
    (paths.DATA_DIR / "generation_log.jsonl").write_text(
        json.dumps(entry) + "\n", encoding="utf-8")


def test_covered_before_signal_renders_once_on_a_tracked_lead():
    """The arc line AND the tracked-ongoing marker both signal prior coverage;
    on a tracked lead that gets an arc, the marker is suppressed so the signal
    appears ONCE (the live 07-14 lead rendered it TWICE)."""
    con = _con()
    _seed_arc_lead(con)
    page, _ = server.build_page(con)
    con.close()
    lead = page.split('<article class="lead"')[1].split("</article>")[0]
    assert "When we last covered this" in lead        # the rich arc signal stays
    assert "Tracked ongoing story" not in lead         # the redundant marker is gone


def test_tracked_lead_without_an_arc_keeps_its_marker():
    """A tracked story with NO renderable arc (day-one thread, no ledger) keeps
    the marker as its sole covered-before signal — the suppression is scoped to
    the double-render case only."""
    con = _con()
    now = "2026-07-01T00:00:00.000Z"
    con.execute("INSERT INTO memory (topic, status, status_changed_at,"
                " created_at, updated_at) VALUES ('New Thread', 'active', ?, ?, ?)",
                (now, now, now))
    slots = [{"slot": "1", "story_title": "A story", "summary": "s",
              "item_ids": [], "outlets": ["The Hill"], "matched_tags": [],
              "matched_memory": ["New Thread"], "override": False,
              "corroboration_label": "Reported by 1 named outlet"}]
    con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                (TODAY, json.dumps(slots)))
    con.commit()
    entry = {"ts": iso_now(), "date": TODAY, "status": "ok", "sample": False,
             "tiers": ["full"], "stories": [{"headline": "A story",
                                             "lede": "New development."}]}
    (paths.DATA_DIR / "generation_log.jsonl").write_text(
        json.dumps(entry) + "\n", encoding="utf-8")
    page, _ = server.build_page(con)
    con.close()
    lead = page.split('<article class="lead"')[1].split("</article>")[0]
    assert "When we last covered this" not in lead     # no arc (day-one)
    assert "Tracked ongoing story" in lead             # marker is the sole signal


# ===========================================================================
# Item 3 — THE SUPERSET LAW
# ===========================================================================

def test_deep_view_opens_with_the_story_today_prose():
    """The analyst deep view opens with the story's OWN Today prose (lede + the
    Why-it-matters/Watch-for beats) before the analyst sections, so the deep view
    contains at least the Today story."""
    con = _con()
    s = story(1, "Lead")
    s["why_it_matters"] = "This escalation matters a great deal."
    seed(con, [slot(1, "Lead")], [s])
    analysis.persist_brief(
        con, TODAY, 1, "full", "valid",
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
    deep = page.split("view-deep-story-0")[1].split("</section>")[0]
    assert "deep-today-prose" in deep
    # the Today prose lands BEFORE the first analyst section ("The story so far"
    # / "The facts"): the prose block index is lower than any deep-section-label.
    assert deep.index("deep-today-prose") < deep.index("deep-section-label")
    assert "This escalation matters a great deal." in deep   # the Today why-it-matters


def _seed_quick_edition(con, lede):
    """A single quick-tier In-Brief slot with no analyst brief (so it gets the $0
    sources-&-context view) — a generation_log entry marks tier quick and carries
    the Today lede the In-Brief snippet shows."""
    slots = [{"slot": "1", "story_title": "Court story", "summary": "ranker sum",
              "item_ids": [], "outlets": ["The Hill"], "matched_tags": [],
              "matched_memory": [], "override": False,
              "corroboration_label": "Reported by 1 named outlet"}]
    con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                (TODAY, json.dumps(slots)))
    con.commit()
    entry = {"ts": iso_now(), "date": TODAY, "status": "ok", "sample": False,
             "tiers": ["quick"],
             "stories": [{"headline": "Court story", "lede": lede}]}
    (paths.DATA_DIR / "generation_log.jsonl").write_text(
        json.dumps(entry) + "\n", encoding="utf-8")


def test_sources_context_view_opens_with_the_today_blurb():
    """An In-Brief quick-tier sources-&-context view opens with the SAME blurb the
    Today In-Brief snippet shows (the story lede), before the sources list."""
    con = _con()
    _seed_quick_edition(con, "A distinctive court-story blurb the Today card shows.")
    page, _ = server.build_page(con)
    con.close()
    sec = page.split('id="view-deep-story-0"')[1].split("</section>")[0]
    assert "Sources &amp; context" in sec
    assert "A distinctive court-story blurb the Today card shows." in sec
    # the blurb opens BEFORE the sources section
    assert sec.index("court-story blurb") < sec.index("story-0-sources")


# ===========================================================================
# Item 4 — stale watch-for guard (a past date in a forward-looking beat)
# ===========================================================================

def test_strip_stale_watch_drops_past_date_keeps_future_and_dateless():
    edition = "2026-07-14"
    stale, dropped = analysis.strip_stale_watch(
        "Talks resume July 12. Oil prices stay watched.", edition)
    assert "July 12" not in stale                        # the past sentence stripped
    assert "Oil prices stay watched." in stale           # the dateless one kept
    assert dropped and "July 12" in dropped[0]
    # a FUTURE date passes untouched
    future, d2 = analysis.strip_stale_watch("Summit convenes July 20.", edition)
    assert future == "Summit convenes July 20." and d2 == []
    # unparseable / dateless passes (no false teeth)
    assert analysis.strip_stale_watch("Monitor the markets.", edition)[0] \
        == "Monitor the markets."


def test_today_watch_beat_strips_a_past_date():
    con = _con()
    s = story(1, "Lead")
    s["watch_for"] = f"Talks resume {_month_day(-4)}. Watch the oil markets."
    seed(con, [slot(1, "Lead")], [s])
    page, _ = server.build_page(con)
    con.close()
    lead = page.split('<article class="lead"')[1].split("</article>")[0]
    assert _month_day(-4) not in lead                    # the stale date is stripped
    assert "Watch the oil markets." in lead              # the forward clause survives


def test_deep_view_watch_observable_strips_a_past_date():
    con = _con()
    seed(con, [slot(1, "Lead")], [story(1, "Lead")])
    analysis.persist_brief(
        con, TODAY, 1, "full", "valid",
        {"pinned_facts": [{"fact": "A fact.", "cites": ["S1"]}], "ledger": [],
         "mechanism": "m [S1].", "effects": [], "arc": None, "unknowns": [],
         "watch": [{"observable": f"The vote lands {_month_day(-5)}.",
                    "settles": "outcome"},
                   {"observable": "Markets keep moving.", "settles": "reaction"}],
         "sources": [{"key": "S1", "outlet": "BBC", "title": "t", "url": "http://x",
                      "retrieved_at": "", "kind": "cluster-full-text"}],
         "notes_for_writer": ""},
        "", 0.0, {"manifest": {}, "degraded": None},
        sources={"S1": {"kind": "cluster-full-text", "outlet": "BBC", "title": "t",
                        "url": "http://x", "retrieved_at": "", "text": "b"}})
    page, _ = server.build_page(con)
    con.close()
    deep = page.split("view-deep-story-0")[1].split("</section>")[0]
    assert _month_day(-5) not in deep
    assert "Markets keep moving." in deep


# ===========================================================================
# Item 5 — discrepancy collapse by default + raise the bar
# ===========================================================================

def test_same_referent_numbers_folds_paraphrase_not_contradiction():
    assert analysis.same_referent_numbers("20%", "about 20 percent")
    assert analysis.same_referent_numbers("$1.2 billion", "$1.2B")
    assert analysis.same_referent_numbers("1,200", "1200")
    # NOT folded — same number, opposite meaning / genuine contradiction
    assert not analysis.same_referent_numbers("20% up", "20% down")
    assert not analysis.same_referent_numbers("20% closed", "20% open")
    assert not analysis.same_referent_numbers("20%", "30%")
    # a side with no number is never folded
    assert not analysis.same_referent_numbers("fully closed", "not closed")


def _brief_with_discrepancies(rows):
    return {"header": {}, "brief": {
        "pinned_facts": [{"fact": "A fact.", "cites": ["S1"]}],
        "mechanism": "m.", "effects": [], "unknowns": [], "watch": [], "arc": None,
        "ledger": rows,
        "sources": [{"key": "S1", "outlet": "BBC", "title": "t", "url": "http://x",
                     "retrieved_at": "", "kind": "cluster-full-text"},
                    {"key": "S2", "outlet": "AP", "title": "u", "url": "http://y",
                     "retrieved_at": "", "kind": "retrieved"}]}}


def test_substantive_discrepancy_survives_inside_a_collapsed_details():
    doc = _brief_with_discrepancies([
        {"discrepancy": True, "note": "closure status",
         "a": {"value": "Fully closed.", "cites": ["S1"]},
         "b": {"value": "Not fully closed.", "cites": ["S2"]}}])
    html = server._render_deep_view("story-0", "HL", doc, "2026-07-14")
    open_sec = html.split('id="story-0-open"')[1].split("</div></div>")[0]
    assert '<details class="deep-open-discrepancies">' in open_sec   # collapsed by default
    assert "Fully closed." in open_sec and "Not fully closed." in open_sec


def test_same_referent_figure_discrepancy_is_dropped_by_the_raised_bar():
    doc = _brief_with_discrepancies([
        {"discrepancy": True, "note": "toll",
         "a": {"value": "about 20 percent", "cites": ["S1"]},
         "b": {"value": "20%", "cites": ["S2"]}}])
    html = server._render_deep_view("story-0", "HL", doc, "2026-07-14")
    # the paraphrase pair is not a substantive contradiction -> no sub-group at all
    assert "deep-open-discrepancies" not in html


# ===========================================================================
# Item 10 — free-text follow hole + refresh renders the follow
# ===========================================================================

def test_story_follow_is_suggestions_only_no_free_text():
    con = _con()
    seed(con, [slot(1, "Lead")], [story(1, "Lead")])
    page, _ = server.build_page(con)
    con.close()
    following = page.split('id="view-following"')[1].split('id="view-archive"')[0]
    # the free-text story popup + input are gone
    assert "add-story-input" not in page
    assert "popup-add-story" not in page
    # a suggestions-only combobox took its place
    assert 'data-kind="story"' in following
    assert 'data-suggest-only="1"' in following
    # the JS enforces it: raw text no-ops; only followStory follows
    assert "function followStory" in webui.JS
    assert "function addStory" not in webui.JS
    assert "data-suggest-only" in webui.JS               # the client reads the flag


def test_refresh_path_expands_the_quiet_fold_for_a_new_follow():
    """The reload after a story-follow opens the quiet fold so the just-followed
    (delta-less, therefore quiet) thread is visible, not buried — and clamps the
    scroll restore so it never overshoots into blank space ('no threads')."""
    js = webui.JS
    # followStory reloads with the fold-expand flag
    region = js.split("function followStory", 1)[1].split("\nfunction ", 1)[0]
    assert "reloadPreservingView(true)" in region
    # restore opens the quiet fold when the flag is set, and clamps the scroll
    assert "st.expandQuiet" in js
    assert "details.quiet-fold" in js
    assert "scrollHeight - window.innerHeight" in js     # scroll clamp


# ===========================================================================
# Item 12 — topics search returns live topics (not deleted); picker stays
# ===========================================================================

def test_topic_suggestions_are_scoped_to_the_latest_edition():
    """The Topics search suggests LIVE topics (the latest edition's matched tags)
    minus what you follow — an OLD-edition-only tag no longer resurfaces (the
    'returns only deleted topics' class); the latest edition's is offered."""
    from types import SimpleNamespace
    con = _con()
    with con:
        con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                    ("2026-07-01", json.dumps([{"slot": "1", "matched_tags":
                        [{"name": "Old Deleted Topic"}]}])))
        con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                    ("2026-07-14", json.dumps([{"slot": "1", "matched_tags":
                        [{"name": "Live Topic"}]}])))
    cfg = SimpleNamespace(interests_broad=[], interests_granular=[],
                          sources=[], followed_analyst_sources=[])
    sugg = {o["v"] for o in server._topic_suggestions(con, cfg)}
    con.close()
    assert "Live Topic" in sugg                          # latest edition -> live
    assert "Old Deleted Topic" not in sugg               # old-only -> not resurfaced


def test_broad_specific_picker_is_still_offered():
    """The broad/specific picker STAYS — it is the standing Fable taxonomy
    contract (config keeps interests_broad/granular; the ranker weights topic
    1.0 / domain 0.5). The Opus-line kill (DECISIONS 2026-07-08) is NOT imported
    to this line (2026-07-09). FLAGGED for the principal in the report."""
    assert "Add as broad" in webui.POPUPS
    assert "Add as specific" in webui.POPUPS
    assert "addTopic('broad')" in webui.POPUPS and "addTopic('specific')" in webui.POPUPS


# ===========================================================================
# Item 14 — stop default-explaining (disclosures stay)
# ===========================================================================

def test_archive_interface_explainer_is_removed():
    con = _con()
    with con:
        con.execute("INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
                    (TODAY, json.dumps([{"slot": "1", "story_title": "S",
                        "summary": "s", "item_ids": [], "outlets": [],
                        "matched_tags": [], "matched_memory": []}])))
    html = server._render_archive(con)
    con.close()
    assert "The grid is an index of the list below it." not in html
    # the factual edition count (a caption, not condescension) stays
    assert "edition" in html and "this month" in html


def test_sources_context_footer_keeps_the_honesty_disclosure():
    """The two-lane HONESTY disclosure stays (not a full-picture analysis); only
    the 'This is the sources-and-context view…' interface narration is trimmed."""
    con = _con()
    _seed_quick_edition(con, "A blurb.")
    page, _ = server.build_page(con)
    con.close()
    sec = page.split('id="view-deep-story-0"')[1].split("</section>")[0]
    assert "not a full-picture analysis" in sec          # the disclosure LIVES
    assert "This is the sources-and-context view" not in sec  # narration trimmed


def test_topics_interface_hint_is_removed():
    con = _con()
    seed(con, [slot(1, "Lead")], [story(1, "Lead")])
    page, _ = server.build_page(con)
    con.close()
    assert "suggestions draw from everything coverage has matched" not in page
    assert "Suggestions recall writers the system already knows" not in page
    # item-14 BOUNDARY pin: the functional-consequence disclosure stays —
    # following a writer STEERS ranking (ranking.py FOLLOWED_BOOST); telling
    # the user that is honesty, not interface narration.
    assert "boosts their pieces in ranking" in page
