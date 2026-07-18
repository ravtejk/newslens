"""v7 UI build — Milestone 2 (thread page + Following Spine + Archive calendar
+ the NL-29 consolidation folds + heading semantics) contract tests.

Liveness/contract pins for the v7-M2 dispatch — each fails against the pre-M2
render and only passes with the landed change (team/ENGINEERING.md: new
enforcement surfaces are born with the red test only the wiring can flip).
Fully offline; in-process render only; the autouse sandbox (conftest) redirects
DATA_DIR/DB_PATH so nothing here touches a real table. Every thread/edition
below is a FIXTURE shaped like the real archive, NEVER the live DB.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from newslens import db, labels, server, webui


DATE = "2026-07-10"          # the fixture's "latest edition"


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _con():
    db.migrate()
    return db.connect()


def _mem(con, topic, status="active", note=None, ref=None):
    cur = con.execute(
        "INSERT INTO memory (topic, status, principal_note,"
        " last_referenced_briefing_id, status_changed_at, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (topic, status, note, ref, iso_now(), iso_now(), iso_now()))
    return cur.lastrowid


def _briefing(con, date, story_slots=None, generated="2026-07-10T04:44:00.000Z"):
    cur = con.execute(
        "INSERT INTO briefings (date, story_slots, generated_at)"
        " VALUES (?, ?, ?)", (date, json.dumps(story_slots or []), generated))
    return cur.lastrowid


def _delta(con, tid, date, what, signif, verdict="advances", slot=1):
    con.execute(
        "INSERT INTO thread_deltas (thread_id, edition_date, slot, verdict,"
        " what_happened, significance, cites_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (tid, date, slot, verdict, what, signif, json.dumps(["S1"])))


def _state(con, tid, as_of, text):
    con.execute(
        "INSERT INTO thread_state (thread_id, as_of_date, state_text, cites_json)"
        " VALUES (?, ?, ?, ?)", (tid, as_of, text, json.dumps([as_of])))


def _seed_hormuz_shaped(con):
    """A fixture shaped like the real Iran/Hormuz arc: editions Jul 5/6/10, a
    thread updated THIS edition (Jul 10) with state + a multi-entry ledger, a
    quiet thread last updated Jul 6, a bare quiet thread, a dormant thread, a
    dismissed thread, and a day-one thread (no ledger, no state)."""
    _briefing(con, "2026-07-05")
    _briefing(con, "2026-07-06", generated="2026-07-06T09:46:00.000Z")
    _briefing(con, "2026-07-10")
    hz = _mem(con, "Strait of Hormuz", note="acute twin of the standing tag")
    _delta(con, hz, "2026-07-05", "Iran floated transit fees and special treatment.",
           "The contest was over terms of passage, not passage.")
    _delta(con, hz, "2026-07-06", "Khamenei’s funeral procession ran through Tehran.",
           "The standoff now runs through a leadership transition.")
    _delta(con, hz, "2026-07-10", "The U.S. and Iran exchanged strikes; the strait closed.",
           "The dispute became a war over passage itself.")
    _state(con, hz, "2026-07-10",
           "The U.S. and Iran are in direct military conflict; Iran has closed "
           "the Strait of Hormuz (2026-07-10).")
    uk = _mem(con, "Ukraine War")
    _delta(con, uk, "2026-07-06", "Front-line shifts reported.", "Grinding stalemate.")
    _mem(con, "Helium Shortage")                 # bare quiet: no ledger, no state
    _mem(con, "Redemption Gates", status="dormant")
    _mem(con, "Ceasefire", status="dismissed_user")
    return hz


# ===========================================================================
# 1. THE THREAD PAGE (the "Open thread" destination) — new surface
# ===========================================================================

def test_thread_page_renders_state_timeline_editions_and_verbs():
    con = _con()
    hz = _seed_hormuz_shaped(con)
    mrow = con.execute("SELECT * FROM memory WHERE id = ?", (hz,)).fetchone()
    html = server._render_thread_page(con, mrow)
    con.close()
    assert f'id="view-thread-{hz}"' in html
    # Where this stands — the standing state (h2) + its as-of stamp
    assert f'<h2 class="deep-section-label">{labels.WHERE_THIS_STANDS}</h2>' in html
    assert "direct military conflict" in html
    # The story so far — the FULL ledger, dated, oldest first (all three entries)
    assert f'<h2 class="deep-section-label">{labels.THE_STORY_SO_FAR}</h2>' in html
    for frag in ("JUL 5", "JUL 6", "JUL 10", "exchanged strikes"):
        assert frag in html
    # edition back-links (distinct dated editions, linked in-place)
    assert f'<h2 class="deep-section-label">{labels.THREAD_EDITIONS_LABEL}</h2>' in html
    assert "openEdition('2026-07-05', event)" in html
    # the verbs (active thread → Edit note + Stop) and the back affordance
    assert labels.VERB_EDIT_NOTE in html and labels.VERB_STOP in html
    assert labels.THREAD_BACK in html


def test_thread_page_day_one_no_arc_honest_empty():
    """Kill-test law: a day-one thread (no ledger, no state) gets NO arc/story-so-
    far — the honest empty notes, never a fabricated timeline."""
    con = _con()
    tid = _mem(con, "Brand New Thread")           # no deltas, no state
    mrow = con.execute("SELECT * FROM memory WHERE id = ?", (tid,)).fetchone()
    html = server._render_thread_page(con, mrow)
    con.close()
    assert labels.THREAD_NO_STATE in html          # no standing summary yet
    assert labels.THREAD_NO_ARC in html            # no earlier coverage
    assert "<li class=\"tl-entry\">" not in html   # no timeline rows fabricated
    assert f'id="{"thread-%d-editions" % tid}"' not in html  # no editions section


def test_thread_page_renders_from_persisted_rows_only_no_llm(monkeypatch):
    """The thread page reads thread_state/thread_deltas/memory only — never the
    network. A poisoned urllib would raise; the render must not call it."""
    import urllib.request
    con = _con()
    hz = _seed_hormuz_shaped(con)
    mrow = con.execute("SELECT * FROM memory WHERE id = ?", (hz,)).fetchone()

    def _boom(*a, **k):
        raise AssertionError("thread page made a network call")
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    html = server._render_thread_page(con, mrow)   # must not raise
    con.close()
    assert "Strait of Hormuz" in html


def test_thread_pages_collected_one_per_memory_row():
    con = _con()
    _seed_hormuz_shaped(con)
    n = con.execute("SELECT COUNT(*) c FROM memory").fetchone()["c"]
    html = server._collect_thread_pages(con)
    con.close()
    assert html.count('class="view"') == n         # a page per thread, every status


# ===========================================================================
# 2. FOLLOWING — the Spine (§7/§12.2/§12.4/§12.5)
# ===========================================================================

def test_following_spine_updated_row_anatomy():
    con = _con()
    hz = _seed_hormuz_shaped(con)
    html = server._render_following(con)
    con.close()
    # ●UPDATED · THIS EDITION · <date> stamp in the moved register
    assert labels.UPDATED_STAMP in html and labels.UPDATED_THIS_EDITION in html
    assert 'class="t-moved">● UPDATED' in html
    # the thread NAME is the single action → the thread page (name-as-action)
    assert f"openThread('{hz}', event)" in html
    assert f'title="{labels.THREAD_WHOLE}"' in html   # the fallback control label
    # the one-line delta (this edition's what_happened) renders on the updated row
    assert "exchanged strikes" in html


def test_following_quiet_fold_counted_and_names_are_actions():
    con = _con()
    _seed_hormuz_shaped(con)
    html = server._render_following(con)
    con.close()
    # ONE counted disclosure for the quiet threads (Ukraine War + Helium Shortage)
    assert 'class="quiet-fold"' in html
    fold = html.split('class="quiet-fold"')[1]
    summary = fold.split("</summary>")[0]
    assert "2 " + labels.QUIET_FOLD_NOUN in summary       # the real count
    assert labels.QUIET_FOLD_SUFFIX in summary
    # a quiet thread with a last date carries a LAST UPDATED stamp; bare one none
    assert labels.LAST_UPDATED in fold                    # Ukraine War (Jul 6)
    assert ">Ukraine War</a>" in fold and ">Helium Shortage</a>" in fold


def test_quiet_fold_defaults_open_on_zero_updated_morning():
    """§12.5: on a morning where NOTHING updated this edition, the fold defaults
    OPEN (a lone closed fold reads as an empty page)."""
    con = _con()
    _briefing(con, "2026-07-10")
    t = _mem(con, "Ukraine War")
    _delta(con, t, "2026-07-06", "Older move.", "Older significance.")  # not this ed
    html = server._render_following(con)
    con.close()
    assert re.search(r'<details class="quiet-fold"\s+open>', html)


def test_quiet_fold_closed_when_something_updated():
    con = _con()
    _seed_hormuz_shaped(con)                         # Hormuz updated Jul 10
    html = server._render_following(con)
    con.close()
    m = re.search(r'<details class="quiet-fold"( open)?>', html)
    assert m and m.group(1) is None                 # not defaulted open


def test_following_triad_and_lifecycle_sections():
    con = _con()
    _seed_hormuz_shaped(con)
    html = server._render_following(con)
    con.close()
    # the triad line (§12.4) — real links, current at 700 (aria-current)
    for lbl in (labels.FOLLOWING_TRIAD_THREADS, labels.FOLLOWING_TRIAD_TOPICS,
                labels.FOLLOWING_TRIAD_WRITERS):
        assert f">{lbl}</a>" in html
    assert 'aria-current="true"' in html
    # lifecycle sections are real h2s (heading semantics)
    assert f'<h2 class="section-h">{labels.FOLLOWING_DORMANT_H}</h2>' in html
    assert f'<h2 class="section-h">{labels.FOLLOWING_DISMISSED_H}</h2>' in html
    # the page title is the LOUD h1 (WAS the quiet view-title)
    assert f'<h1 class="page-title">{labels.NAV_FOLLOWING}</h1>' in html


# ===========================================================================
# 3. ARCHIVE — the §8 calendar (three day classes, list-below primary)
# ===========================================================================

def test_archive_calendar_three_day_classes():
    # CONSCIOUS FLIP (archive redesign APPROVED 2026-07-18, DIRECTION §14): the
    # three day-classes survive; the list-below (archive-list / al-date) is DEAD,
    # superseded by the day panel beside the grid. Contract pins moved here.
    con = _con()
    _seed_hormuz_shaped(con)                 # editions Jul 5, 6, 10
    html = server._render_archive(con)
    con.close()
    assert 'class="month-title">July' in html            # month title
    assert 'class="cal-cell cal-edition' in html         # edition day class
    assert 'class="cal-cell cal-gap"' in html            # gap-in-history (Jul 7-9)
    assert 'class="cal-cell cal-void"' in html           # pre-history / future
    # edition cells are BUTTONS with FULL accessible names (§14: only edition
    # days are focusable; the pick rides aria-pressed, not a link)
    assert re.search(r'aria-label="[A-Za-z]+, July \d+, 2026 — (edition|today’s edition)'
                     r' — show(?:ing)? headlines"', html)   # gate FIX-1: the action hint
    # the day panel beside the grid replaces the list-below (list stays dead)
    assert 'class="arch-cols"' in html
    assert 'class="day-panel"' in html
    assert 'class="archive-list"' not in html
    assert 'class="al-date"' not in html


def test_archive_today_is_terra_not_ringed_and_panel_carries_the_tag():
    # CONSCIOUS FLIP (DIRECTION §14): the terracotta RING is gone — today is a
    # terra numeral, nothing encloses it. cal-today stays as the class; the TODAY
    # tag moves from the dead list stamp into the day panel's stamp.
    con = _con()
    today = datetime.now().strftime("%Y-%m-%d")
    _briefing(con, today)
    html = server._render_archive(con)
    con.close()
    assert 'cal-edition cal-today' in html               # today-with-edition class
    assert 'border: 2px solid var(--terra)' not in webui.CSS   # the ring is gone
    assert labels.ARCHIVE_TODAY_TAG in html              # TODAY tag in the panel stamp


def test_archive_empty_state_honest():
    con = _con()
    html = server._render_archive(con)
    con.close()
    assert labels.ARCHIVE_EMPTY in html
    assert 'class="cal-grid"' not in html                # no calendar when no editions


# ===========================================================================
# 4. HEADING SEMANTICS — the NAMED GATE requirement
# ===========================================================================

def _brief():
    return {"pinned_facts": [{"fact": "A fact.", "cites": ["S1"]}],
            "mechanism": "Because [S1].", "effects": [], "unknowns": [],
            "watch": [],
            "sources": [{"key": "S1", "outlet": "The Hill", "title": "T",
                         "url": "http://x.invalid", "kind": "cluster-full-text",
                         "retrieved_at": "2026-07-10T00:00:00Z"}]}


def test_section_labels_are_real_h2_headings_deep_view():
    """The NAMED GATE: every deep-section-label is an <h2> (never a <p>)."""
    con = _con()
    html = server._render_deep_view("story-0", "H", {"header": {}, "brief": _brief()},
                                    DATE, con=con)
    con.close()
    tags = re.findall(r'<(\w+)[^>]*class="deep-section-label"', html)
    assert tags and all(t == "h2" for t in tags), tags
    assert '<p class="deep-section-label"' not in html


def test_section_labels_are_real_h2_headings_following():
    con = _con()
    _seed_hormuz_shaped(con)
    html = server._render_following(con)
    con.close()
    tags = re.findall(r'<(\w+)[^>]*class="section-h"', html)
    assert tags and all(t == "h2" for t in tags), tags
    assert '<p class="section-h"' not in html


def test_in_brief_label_dies_quick_tier_is_a_strip():
    """v8-M2 (item 1): the visible 'In brief' label DIES — scale and placement
    are the label, the h3 heading carries the tier for AT. A quick-tier item is
    now a hairline STRIP in the newspaper grid, no labelled region around it."""
    from newslens import ranking, generate

    con = _con()
    # a quick-tier snippet needs a slot at index >= 3 (positional tiers, empty log)
    slots = [{"slot": i, "story_title": "S%d" % i, "summary": "S.",
              "item_ids": [i], "outlets": ["O"], "matched_tags": [],
              "matched_memory": [], "matched_dormant": [], "followed_analyst": False,
              "personal_score": 0.0, "world_impact": 6, "world_impact_reason": "R",
              "combined_score": 0.5, "override": False, "override_label": None,
              "corroboration_count": 1, "corroboration_label": "Reported by 1",
              "wire_items_excluded": 0, "revived_threads": [],
              "still_tracking": False} for i in range(1, 5)]
    stories = ([{"tier": "full", "headline": "S1", "lede": "L1.", "my_read": None}]
               + [{"tier": "medium", "headline": "S%d" % i, "lede": "L.",
                   "why_label": "Why it matters", "watch_label": "Watch for",
                   "why_it_matters": "W.", "watch_for": "V.", "my_read": None}
                  for i in (2, 3)]
               + [{"tier": "quick", "headline": "S4", "lede": "L4.", "my_read": None}])
    today = datetime.now().strftime("%Y-%m-%d")
    narrative = generate.assemble_narrative(today, "A", stories,
                                            {"slots": slots,
                                             "items_by_slot": {s["slot"]: [] for s in slots},
                                             "threads": [], "prior_ctx": None,
                                             "continuity_status": "none",
                                             "window_meta": None, "corroboration": {}})
    con.execute("INSERT INTO briefings (date, story_slots, corroboration_labels,"
                " narrative_text, generated_at) VALUES (?, ?, ?, ?, ?)",
                (today, json.dumps(slots),
                 json.dumps({"standing_caveat": ranking.CORROBORATION_CAVEAT,
                             "per_story": []}), narrative, iso_now()))
    con.commit()
    page, _ = server.build_page(con)
    con.close()
    # Scope to the Today VIEW — the deep view legitimately keeps an "In brief"
    # SECTION label (NL-66b), a distinct concern from the retired Today region.
    today = page.split('id="view-today"')[1].split('id="view-following"')[0]
    assert 'class="in-brief"' not in today           # the labelled region is dead
    assert 'class="brief-label"' not in today        # ...and its heading
    assert labels.IN_BRIEF not in today              # the label text is gone from Today
    assert '<article class="strip' in today          # the quick tier is a strip now
    assert 'id="story-3"' in today                   # S4 (index 3) rendered


def test_today_view_has_exactly_one_h1():
    """One h1 per document view — the dateline (WAS: dateline + lead both h1)."""
    con = _con()
    _briefing(con, datetime.now().strftime("%Y-%m-%d"))
    page, _ = server.build_page(con)
    con.close()
    today_view = page.split('id="view-today"')[1].split('id="view-following"')[0]
    assert today_view.count("<h1") == 1                  # only the dateline
    assert '<h1 class="dateline"' in today_view
    assert '<h1 class="headline"' not in page            # the lead demoted to h2


def test_skip_link_targets_the_main_landmark():
    assert 'class="skip-link" href="#main"' in webui.PAGE
    assert '<main id="main" tabindex="-1">' in webui.PAGE


# ===========================================================================
# 5. THE FOLDS — byte-comparable content preservation (NL-29 consolidation)
# ===========================================================================

def _numbers_brief():
    return {"pinned_facts": [{"fact": "A fact.", "cites": ["S1"]}],
            "ledger": [{"claim": "The attack injured at least 46 people.",
                        "cites": ["S1"], "provenance": "cluster-single"}],
            "mechanism": "M [S1].", "effects": [], "unknowns": [], "watch": [],
            "sources": [{"key": "S1", "outlet": "The Hill", "title": "T",
                         "url": "http://x.invalid", "kind": "cluster-full-text",
                         "retrieved_at": "2026-07-10T00:00:00Z"}]}


def test_numeric_ledger_row_survives_the_fold_byte_comparably():
    """The numeric-ledger-claim ROW the retired 'The numbers' section rendered
    survives byte-for-byte, now inside 'The facts'. v8-M1 item 4 (CONSCIOUS
    FLIP): the row's attribution is the PLAIN end-of-line outlet count (the ▸
    cite-fold died with the rest of the inline apparatus), reconstructed the same
    way the sub-group now builds it."""
    con = _con()
    brief = _numbers_brief()
    html = server._render_deep_view("story-0", "H", {"header": {}, "brief": brief},
                                    DATE, con=con)
    con.close()
    src_by_key = {s["key"]: s for s in brief["sources"]}
    e = brief["ledger"][0]
    count = server._facts_outlet_count(e["cites"], src_by_key)
    expected_row = (f'<li>{server._e(e["claim"])}'
                    + (f' {count}' if count else "") + '</li>')
    assert expected_row in html                          # byte-for-byte
    facts = html.split('id="story-0-facts"')[1].split("</div>")[0]
    assert expected_row in facts                         # and it lives INSIDE the facts


def test_discrepancy_attribution_rows_survive_the_fold_byte_comparably():
    """The attributed discrepancy ROWS the retired 'Unresolved' section rendered
    survive byte-for-byte, now inside 'What's still open'."""
    con = _con()
    brief = _numbers_brief()
    brief["ledger"].append({"discrepancy": True,
                            "a": {"value": "9 dead", "cites": ["S1"]},
                            "b": {"value": "12 dead", "cites": ["S1"]},
                            "note": "tolls differ"})
    brief["unknowns"] = [{"question": "q", "why_material": "w", "would_resolve": "r"}]
    html = server._render_deep_view("story-0", "H", {"header": {}, "brief": brief},
                                    DATE, con=con)
    con.close()
    src_by_key = {s["key"]: s for s in brief["sources"]}
    disc = server._deep_discrepancy_subgroup(brief, src_by_key)   # the exact rows
    assert disc and disc in html                          # byte-for-byte, in the page
    open_sec = html.split('id="story-0-open"')[1]
    assert 'class="deep-open-discrepancies"' in open_sec
    assert "9 dead" in open_sec and "12 dead" in open_sec and "tolls differ" in open_sec


# ===========================================================================
# 6. THE ADJACENT-COPY SLATE — labels.py + NAV global absence (item 5)
# ===========================================================================

def test_nav_labels_global_absence_when_repinned(monkeypatch):
    """Item 5: the NAV liveness pins upgraded to GLOBAL absence — re-pinning a
    nav destination moves EVERY nav rendering of it (all three views' section
    lines), leaving the default value in no nav rendering (a hardcoded nav label
    anywhere would leak the default through). Scoped to the section-line nav —
    the word may still appear in unrelated verb copy (the follow toast), which is
    not a destination."""
    con = _con()
    _briefing(con, datetime.now().strftime("%Y-%m-%d"))
    monkeypatch.setattr(labels, "NAV_TODAY", "NAVT_SENT")
    monkeypatch.setattr(labels, "NAV_FOLLOWING", "NAVF_SENT")
    monkeypatch.setattr(labels, "NAV_ARCHIVE", "NAVA_SENT")
    page, _ = server.build_page(con)
    con.close()
    navs = re.findall(r'<nav class="section-line".*?</nav>', page, re.S)
    assert len(navs) >= 3
    joined = "".join(navs)
    for sent in ("NAVT_SENT", "NAVF_SENT", "NAVA_SENT"):
        assert joined.count(sent) >= 3               # present in every view's nav
    for default in ("Today", "Following", "Archive"):
        assert default not in joined                 # global absence of the old value
    # the Following page title reads the table too (not just the section line)
    following = page.split('id="view-following"')[1].split('id="view-archive"')[0]
    assert "NAVF_SENT" in following


def test_follow_control_copy_is_centralized(monkeypatch):
    """The follow-control strings (server.py + the client JS via NL_LABELS) read
    from labels.py — a re-pin lands both server- and client-side."""
    from newslens import ranking
    monkeypatch.setattr(labels, "FOLLOW_STORY_INACTIVE", "ZZ-FOLLOW")
    monkeypatch.setattr(labels, "FOLLOW_STORY_CONFIRM", "ZZ-CONFIRM")
    st = {"headline": "H"}
    slot = {"story_title": "H"}
    # server-rendered initial button text
    ctl = server._follow_control(st, slot, [], set(), DATE)
    assert "ZZ-FOLLOW" in ctl
    # client-facing NL_LABELS blob carries the toast copy for the JS
    blob = server._nl_labels_js()
    assert "ZZ-CONFIRM" in blob and "ZZ-FOLLOW" in blob
    assert '<script>{nl_labels_js}</script>' in webui.PAGE   # injected before the JS


def test_deep_back_and_listen_labels_centralized():
    con = _con()
    html = server._render_deep_view("story-0", "H", {"header": {}, "brief": _brief()},
                                    DATE, con=con)
    con.close()
    assert labels.BACK_TO_TODAY in html
    # the edition-bar "Listen to the edition" comes from the table
    assert "LISTEN_TO_EDITION" in dir(labels) and labels.LISTEN_TO_EDITION


# --- Gate fixes (v7-M2 final gate, 2026-07-14): pins ----------------------------

def test_state_panel_headings_do_not_skip_levels():
    """Whole-document heading law in the no-edition state: the Today state
    panel's heading is an h2 under the dateline h1 (WAS h3 — an h1->h3 skip
    in every non-edition state; gate FIX-1, v7-M2)."""
    con = _con()
    page, _ = server.build_page(con)
    con.close()
    today_view = page.split('id="view-today"')[1].split('id="view-following"')[0]
    seq = [int(t) for t in re.findall(r"<h([1-6])", today_view)]
    assert seq and seq[0] == 1 and all(b - a <= 1 for a, b in zip(seq, seq[1:])), seq


def test_deep_back_default_reads_label_table_at_call_time(monkeypatch):
    """A BACK_TO_TODAY re-pin must reach callers relying on the DEFAULT —
    a def-time default captures the import-time value (gate FIX-2, v7-M2)."""
    monkeypatch.setattr(labels, "BACK_TO_TODAY", "ZZ-BACK")
    con = _con()
    html = server._render_deep_view("story-0", "H",
                                    {"header": {}, "brief": _brief()}, DATE, con=con)
    con.close()
    assert "ZZ-BACK" in html and "Back to today" not in html
