"""v7 M1 (Front Page shell) — QA extension pass (QA-owned).

Adversarial extensions beyond the implementer's contract tests
(test_v7_shell_m1.py), per the M1 QA dispatch:

  * Masthead ceremony ORDER with a real episode present — §4's fixed order
    (wordmark → dateline → dispatch strip → episode affordance) then the
    section line, byte-position-proved, not eyeballed.
  * NL-65 on the In-Brief snippet: the quick tier's $0 "Sources & context"
    entry sits in the SAME bottom position (after body, before furniture)
    as the analyst tier's "full picture" — and never demotes a briefed slot.
  * A8 teeth the implementer's tests don't reach: empty thread name → no
    line at all; and LOG-INDEPENDENCE — the strip reads story_slots fields,
    proven with the generation log absent AND with a hostile log entry that
    tries to inject a fabricated date (sandboxed log file, never the real one).
  * The id-alignment invariant: a still_tracking slot MID-list must not
    desync _render_briefing_body's story ids from _collect_deep_views'
    anchors (entry links open the RIGHT deep view; the skipped slot gets none).
  * Label-table liveness BREADTH: sentinel re-pins for the surfaces the
    implementer's two liveness tests don't touch (kicker, archive nav,
    still-tracking fallback, full-picture/sources-context entries, DEEP_OPEN).
  * Empty states under the v7 frame: day-one (empty DB, keyless — the autouse
    scrub) and the NL-11 no-edition-today state both render the masthead
    ceremony + section line with no killed chrome.
  * The skip-link pin (none existed before this file — webui carried the
    feature unpinned; gap found in this pass and closed here).

Everything here runs in the autouse sandbox (conftest): redirected
DATA_DIR/DB_PATH, loopback-only network, keyless env.
"""

from __future__ import annotations

import json
import wave
from html import escape

import pytest

from newslens import db, labels, paths, server, webui

# Labels render through server._e (HTML-escaped): "Sources & context" appears
# in output as "Sources &amp; context". Match the RENDERED form.
SC_RENDERED = escape(labels.SOURCES_CONTEXT, quote=True)

from test_v7_shell_m1 import TODAY, _con, _deep_doc, iso_now, seed, slot, story


# --- helpers -------------------------------------------------------------------


def _tiny_wav(path, secs=61):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(800)
        w.writeframes(b"\x00\x00" * (800 * secs))
    return str(path)


def _seed_with_audio(con, slots, stories, wav_path, date=TODAY):
    from newslens import generate, ranking
    inputs = {"slots": slots, "items_by_slot": {s["slot"]: [] for s in slots},
              "threads": [], "prior_ctx": None, "continuity_status": "none",
              "window_meta": None, "corroboration": {}}
    narrative = generate.assemble_narrative(date, "A", stories, inputs)
    con.execute(
        "INSERT INTO briefings (date, story_slots, corroboration_labels,"
        " narrative_text, audio_file_path, generated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (date, json.dumps(slots),
         json.dumps({"standing_caveat": ranking.CORROBORATION_CAVEAT,
                     "per_story": []}),
         narrative, wav_path, iso_now()))
    con.commit()


def _strip_segment(page: str) -> str:
    """The rendered still-tracking region, exactly as emitted."""
    start = page.index('<div class="still-tracking"')
    end = page.index("</div>", start) + len("</div>")
    return page[start:end]


# --- §4: the masthead ceremony order, episode affordance included ---------------


def test_masthead_ceremony_order_with_episode(tmp_path):
    """DIRECTION-v5 §4 fixed order, proved by byte position on a page whose
    edition HAS audio: wordmark → dateline → dispatch strip → episode
    affordance, then the section line. (The implementer's smoke never renders
    an episode — no fixture edition carries a wav.)"""
    con = _con()
    wav = _tiny_wav(tmp_path / "ep.wav")
    _seed_with_audio(con, [slot(1, "Lead"), slot(2, "B")],
                     [story(1, "Lead"), story(2, "B", "medium")], wav)
    page, rendered = server.build_page(con)
    con.close()
    assert rendered == TODAY
    today = page[page.index('id="view-today"'):page.index('id="view-following"')]
    i_word = today.index('class="wordmark"')
    i_date = today.index('class="dateline"')
    i_strip = today.index('class="dispatch-strip"')
    i_ep = today.index('class="episode-affordance"')
    i_line = today.index('class="section-line"')
    i_grid = today.index('class="today-grid"')
    assert i_word < i_date < i_strip < i_ep < i_line < i_grid
    # the affordance is the edition player, inside the masthead — not a
    # top-level player (v7 killed the shared top bar / top-level player)
    assert today.index('class="page masthead"') < i_ep < today.index("</header>")


# --- NL-65 on the In-Brief snippet (sources & context, same bottom position) ----


def test_nl65_in_brief_snippet_sources_context_below_body():
    """The quick tier's $0 entry obeys the SAME NL-65 placement as the analyst
    entry: deck (follow only) under the title, body, then 'Sources & context'
    in .story-more, then the corroboration furniture."""
    con = _con()
    st = story(4, "Brief headline", "quick")
    sl = slot(5, "Brief headline")
    html = server._render_story(4, st, sl, "quick", set(), has_file=False,
                                slug="story-4", date=TODAY, con=con,
                                role="snippet")
    con.close()
    i_follow = html.index("Follow this story")
    i_body = html.index("LEDE-4")
    i_sc = html.index(SC_RENDERED)
    i_here = html.index("Here for")
    assert i_follow < i_body < i_sc < i_here
    assert SC_RENDERED not in html[:i_body]              # not in the deck region
    assert labels.FULL_PICTURE not in html               # never the analyst label
    assert 'class="story-more"' in html                  # the shared bottom slot


def test_nl65_briefed_slot_never_demoted_to_sources_context():
    """has_file wins: a briefed quick slot renders 'The full picture', never
    the $0 label (the _deep_entry_link contract, pinned from the render side)."""
    html = server._deep_entry_link(True, "quick", "story-9", "view-today")
    assert labels.FULL_PICTURE in html
    assert labels.SOURCES_CONTEXT not in html


# --- A8 teeth: empty thread, log absence, hostile log ---------------------------


def test_still_tracking_empty_thread_renders_no_line_and_no_strip():
    """Nothing honest to say -> nothing rendered: a blank thread name yields
    no line (unit), and if it was the only flagged slot, no strip region at
    all (integration)."""
    assert server._still_tracking_line(
        {"story_title": "   ", "still_tracking": True,
         "still_tracking_note": "no movement since Jul 6"}) == ""
    con = _con()
    slots = [slot(1, "Lead"),
             slot(2, "", still_tracking=True, still_note="since Jul 6")]
    slots[1]["story_title"] = "   "          # whitespace-only thread name
    stories = [story(1, "Lead"), story(2, "X", "quick")]
    seed(con, slots, stories)
    page, _ = server.build_page(con)
    con.close()
    assert 'class="still-tracking"' not in page
    assert labels.STILL_TRACKING_PREFIX not in page


def test_still_tracking_is_log_independent_absent_and_hostile():
    """The strip reads story_slots fields ONLY. Proven both ways, sandboxed:
    (1) baseline render with NO generation_log.jsonl in the sandbox data dir;
    (2) a HOSTILE log entry for the same date (fabricated 'Jul 99' note at
    entry level and inside every story, tier rewrites) leaves the rendered
    strip byte-identical and the fabricated date nowhere on the page."""
    con = _con()
    slots = [slot(1, "Lead"),
             slot(2, "Strait of Hormuz", still_tracking=True,
                  still_note="no movement since Jul 6"),
             slot(3, "C")]
    stories = [story(1, "Lead"), story(2, "Strait of Hormuz", "quick"),
               story(3, "C", "medium")]
    seed(con, slots, stories)

    log = paths.DATA_DIR / "generation_log.jsonl"
    assert not log.exists()                      # (1) the log-absent proof
    baseline, _ = server.build_page(con)
    strip_before = _strip_segment(baseline)
    assert "no movement since Jul 6" in strip_before
    assert labels.STILL_TRACKING_NO_DATE in strip_before

    hostile_story = {"headline": "HOSTILE HEADLINE", "lede": "HL.",
                     "still_tracking_note": "no movement since Jul 99",
                     "why_it_matters": "", "watch_for": ""}
    entry = {"date": TODAY, "tiers": ["quick", "quick", "quick"],
             "stories": [dict(hostile_story) for _ in range(3)],
             "still_tracking_note": "no movement since Jul 99",
             "total_usd": 0.42}
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    hostile_page, _ = server.build_page(con)
    con.close()
    assert _strip_segment(hostile_page) == strip_before   # byte-identical strip
    assert "Jul 99" not in hostile_page                   # nothing fabricated
    # sanity: the hostile log DID take effect elsewhere (stories are the log's
    # to supply by design) — proving the strip's independence is meaningful,
    # not vacuous.
    assert "HOSTILE HEADLINE" in hostile_page


# --- The id-alignment invariant (still_tracking mid-list) -----------------------


def test_still_tracking_mid_list_keeps_story_and_deep_view_ids_aligned():
    """A flagged slot at index 1 consumes its index but renders no story and
    collects no deep view; every LATER slot keeps its original story-{i} id,
    its entry link opens the deep view with the SAME anchor, and the persisted
    brief (slot_no = i+1) lands on the right story."""
    con = _con()
    slots = [slot(1, "Lead"),
             slot(2, "Strait of Hormuz", still_tracking=True,
                  still_note="no movement since Jul 6"),
             slot(3, "Deep story"), slot(4, "Q4"), slot(5, "Q5")]
    stories = [story(1, "Lead"),
               story(2, "Strait of Hormuz", "quick"),
               story(3, "Deep story", "medium"),
               story(4, "Q4", "quick"), story(5, "Q5", "quick")]
    seed(con, slots, stories)
    # a persisted analyst brief for the story at index 2 => slot_no 3
    con.execute(
        "INSERT INTO analysis_briefs (date, slot, tier, status, brief_json,"
        " model, cost_usd) VALUES (?, 3, 'medium', 'valid', ?, 'qa', 0)",
        (TODAY, json.dumps(_deep_doc())))
    con.commit()
    page, _ = server.build_page(con)
    con.close()

    # the flagged slot: no article, no deep view, only the strip
    assert 'id="story-1"' not in page
    assert 'id="view-deep-story-1"' not in page
    assert "no movement since Jul 6" in page

    # the briefed story AFTER the strip: id preserved, link and view agree
    art_start = page.index('id="story-2"')
    art = page[art_start:page.index("</article>", art_start)]
    assert "Deep story" in art
    assert "openDeepView('story-2'" in art
    assert labels.FULL_PICTURE in art
    deep_start = page.index('id="view-deep-story-2"')
    deep = page[deep_start:page.index("</section>", deep_start)]
    assert "Deep story" in deep                    # the RIGHT doc, no off-by-one
    assert labels.DEEP_EYEBROW in deep

    # the quick slots after it: sources-&-context views, anchors still aligned
    for i, headline in ((3, "Q4"), (4, "Q5")):
        art_start = page.index(f'id="story-{i}"')
        art = page[art_start:page.index("</article>", art_start)]
        assert headline in art
        assert f"openDeepView('story-{i}'" in art
        assert SC_RENDERED in art
        deep_start = page.index(f'id="view-deep-story-{i}"')
        deep = page[deep_start:page.index("</section>", deep_start)]
        assert headline in deep
        assert SC_RENDERED in deep                 # the $0 eyebrow, not analyst


# --- Label-table liveness breadth ------------------------------------------------


def test_label_liveness_breadth_shell_surfaces(monkeypatch):
    """Re-pins land in rendered output for the surfaces the implementer's two
    liveness tests don't cover: the lead kicker, the Archive nav destination,
    and the still-tracking honest fallback."""
    con = _con()
    monkeypatch.setattr(labels, "KICKER_LEAD", "ZZ-KICKER")
    monkeypatch.setattr(labels, "NAV_ARCHIVE", "ZZ-ARCHIVE")
    monkeypatch.setattr(labels, "STILL_TRACKING_NO_DATE", "ZZ-NO-DATE.")
    slots = [slot(1, "Lead"),
             slot(2, "Hormuz", still_tracking=True, still_note="quiet")]
    stories = [story(1, "Lead"), story(2, "Hormuz", "quick")]
    seed(con, slots, stories)
    page, _ = server.build_page(con)
    con.close()
    assert "ZZ-KICKER" in page and "The Lead" not in page
    assert "ZZ-ARCHIVE" in page
    assert "ZZ-NO-DATE." in page and "No next date is set." not in page


def test_label_liveness_breadth_entry_links_and_deep_open(monkeypatch):
    """The two NL-65 entry labels and DEEP_OPEN read the table at call time."""
    monkeypatch.setattr(labels, "FULL_PICTURE", "ZZ-FULL")
    monkeypatch.setattr(labels, "SOURCES_CONTEXT", "ZZ-SC")
    assert "ZZ-FULL" in server._deep_entry_link(True, "full", "s", "view-today")
    assert "ZZ-SC" in server._deep_entry_link(False, "quick", "s", "view-today")
    monkeypatch.setattr(labels, "DEEP_OPEN", "ZZ-OPEN")
    con = _con()
    doc = _deep_doc()
    doc["brief"]["unknowns"] = [{"question": "Q?", "why_unknown": "W.",
                                 "cites": ["S1"]}]
    html = server._render_deep_view("story-0", "HL", doc, TODAY, con=con)
    con.close()
    assert "ZZ-OPEN" in html
    assert "What’s still open" not in html


# --- Empty states under the v7 frame ---------------------------------------------


def test_day_one_empty_db_renders_v7_frame_keyless():
    """Day-one (empty DB, keyless via the autouse scrub): the ceremony frame
    renders around the honest 'Nothing yet' state — no killed chrome, no grid."""
    con = _con()
    page, rendered = server.build_page(con)
    con.close()
    assert rendered is None
    assert "Nothing yet" in page
    assert 'class="dateline"' in page
    assert 'class="section-line"' in page
    assert 'class="wordmark"' in page
    assert 'class="today-grid"' not in page
    assert 'class="bottom-nav"' not in page
    assert "logo-placeholder" not in page
    assert 'class="dispatch-strip"' not in page    # nothing to receipt (A8)


def test_no_edition_today_with_archive_renders_v7_frame():
    """The NL-11 rule under the new frame: an older edition exists but Today
    is empty -> 'Nothing for today yet' + the Archive pointer, inside the full
    ceremony, never an old edition dressed as current."""
    con = _con()
    seed(con, [slot(1, "Old lead")], [story(1, "Old lead")],
         date="2026-07-06")
    page, rendered = server.build_page(con)
    con.close()
    assert rendered is None
    assert "Nothing for today yet" in page
    assert "Earlier editions are in your" in page
    assert 'class="dateline"' in page
    assert 'class="section-line"' in page
    assert "Old lead" not in page[page.index('id="view-today"'):
                                  page.index('id="view-following"')]
    assert 'class="bottom-nav"' not in page


# --- The skip-link pin (gap: the feature shipped unpinned) ------------------------


def test_skip_link_present_and_focusable():
    """A11y floor: the skip link targets the Today view and un-hides on focus.
    (No prior test pinned this — the v7 rebuild could have dropped it silently.)"""
    assert 'class="skip-link"' in webui.PAGE
    assert 'href="#view-today"' in webui.PAGE.split('class="skip-link"')[0][-200:] \
        or 'class="skip-link" href="#view-today"' in webui.PAGE
    assert ".skip-link:focus" in webui.CSS
