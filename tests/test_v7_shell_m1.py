"""v7 UI build — Milestone 1 (the Front Page shell) contract tests.

Red-first for the contract-bearing changes (team/ENGINEERING.md: new enforcement
surfaces are born with the red test only the wiring can flip):

  * NL-65 — "The full picture" moves to the story BOTTOM (below the body, just
    before the sources/corroboration furniture); the follow control stays under
    the title.
  * The still-tracking render (inherited slot-contract requirement): a slot
    flagged still_tracking renders the compact register — state + "no movement
    since <date>" + next fixed point — with A8 no-fabrication teeth on missing
    data (never invents a date).
  * The label string table (NL-29 centralization) is LIVE: render sites read
    newslens.labels at call time, so a re-pin lands in one place.
  * Shell smoke: build_page renders the v7 masthead + sticky section-line and
    drops the killed chrome (top-bar logo, bottom tabs) across real-shaped
    editions — via sandbox fixtures, never real data.

In-process build_page / _render_* only; the autouse sandbox (conftest) redirects
DATA_DIR/DB_PATH so nothing here goes near a real table.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from newslens import db, labels, ranking, server


TODAY = datetime.now().strftime("%Y-%m-%d")


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def slot(n, title, tags=(), mem=(), override=False,
         still_tracking=False, still_note=""):
    return {
        "slot": n, "story_title": title, "summary": "S.", "item_ids": [n],
        "outlets": ["Outlet A"], "matched_tags": [dict(t) for t in tags],
        "matched_memory": list(mem), "matched_dormant": [],
        "followed_analyst": False, "personal_score": 1.0 if tags else 0.0,
        "world_impact": 6, "world_impact_reason": "R", "combined_score": 0.5,
        "override": override,
        "override_label": (ranking.OVERRIDE_LABEL_PREFIX + "big.") if override else None,
        "corroboration_count": 1, "corroboration_label": "Reported by 1 named outlet",
        "wire_items_excluded": 0, "revived_threads": [],
        "still_tracking": still_tracking, "still_tracking_note": still_note,
    }


def story(n, headline, tier="full"):
    st = {"tier": tier, "headline": headline,
          "lede": "LEDE-%d body sentence." % n,
          "why_label": "Why it matters", "watch_label": "Watch for",
          "why_it_matters": "Concrete effects for story %d." % n,
          "watch_for": "The vote.", "my_read": None}
    if tier == "quick":
        for k in ("why_it_matters", "watch_for", "why_label", "watch_label"):
            st.pop(k, None)
    return st


def seed(con, slots, stories, date=TODAY):
    from newslens import generate
    inputs = {"slots": slots, "items_by_slot": {s["slot"]: [] for s in slots},
              "threads": [], "prior_ctx": None, "continuity_status": "none",
              "window_meta": None, "corroboration": {}}
    narrative = generate.assemble_narrative(date, "A", stories, inputs)
    con.execute(
        "INSERT INTO briefings (date, story_slots, corroboration_labels,"
        " narrative_text, generated_at) VALUES (?, ?, ?, ?, ?)",
        (date, json.dumps(slots),
         json.dumps({"standing_caveat": ranking.CORROBORATION_CAVEAT,
                     "per_story": []}),
         narrative, iso_now()))
    con.commit()


def _con():
    db.migrate()
    return db.connect()


# --- NL-65: full picture at the bottom, follow under the title ----------------

def test_nl65_full_picture_below_body_follow_under_title():
    """The follow control stays under the title (before the body); the deep-view
    'full picture' entry moves to the story BOTTOM — below the body, just before
    the corroboration furniture ('Here for'/outlets)."""
    con = _con()
    st = story(1, "Right-column headline", "medium")
    sl = slot(2, "Right-column headline")          # not tracked, not followed
    html = server._render_story(1, st, sl, "medium", set(), has_file=True,
                                slug="story-1", date=TODAY, con=con)
    con.close()

    i_follow = html.index("Follow this story")      # the merged follow control
    i_body = html.index("LEDE-1")                   # the story body
    i_fp = html.index(labels.FULL_PICTURE)          # "The full picture"
    i_here = html.index("Here for")                 # corroboration furniture
    assert i_follow < i_body, "follow control must sit under the title, above body"
    assert i_body < i_fp, "NL-65: full picture must move BELOW the body"
    assert i_fp < i_here, "full picture sits just before the sources/corrob furniture"
    # And it is NOT in the under-title deck region anymore.
    assert labels.FULL_PICTURE not in html[:i_body]


def test_nl65_lead_story_same_placement():
    """The lead story (tier full, slot 0) obeys the same NL-65 placement."""
    con = _con()
    st = story(0, "Lead headline")
    sl = slot(1, "Lead headline")
    html = server._render_story(0, st, sl, "full", set(), has_file=True,
                                slug="story-0", date=TODAY, con=con)
    con.close()
    assert html.index("Follow this story") < html.index("LEDE-0")
    assert html.index("LEDE-0") < html.index(labels.FULL_PICTURE)
    assert html.index(labels.FULL_PICTURE) < html.index("Here for")


# --- The still-tracking render + A8 no-fabrication teeth -----------------------

def test_still_tracking_strip_renders_register():
    """A still_tracking slot renders the compact register: state + the dated
    'no movement since' note + the next-fixed-point clause."""
    con = _con()
    slots = [slot(1, "Lead story"),
             slot(2, "Strait of Hormuz", still_tracking=True,
                  still_note="no movement since Jul 6")]
    stories = [story(1, "Lead story"), story(2, "Strait of Hormuz", "quick")]
    seed(con, slots, stories)
    page, _ = server.build_page(con)
    con.close()
    assert labels.STILL_TRACKING_PREFIX in page          # "Still tracking"
    assert "Strait of Hormuz" in page
    assert "no movement since Jul 6" in page              # the dated note, verbatim
    assert labels.STILL_TRACKING_NO_DATE in page          # "No next date is set."


def test_still_tracking_a8_teeth_no_fabricated_date():
    """A8 teeth: a still_tracking slot with a MISSING note renders honestly —
    the state + the honest fixed-point fallback — and NEVER invents a date."""
    con = _con()
    slots = [slot(1, "Lead story"),
             slot(2, "Helium Shortage", still_tracking=True, still_note="")]
    stories = [story(1, "Lead story"), story(2, "Helium Shortage", "quick")]
    seed(con, slots, stories)
    page, _ = server.build_page(con)
    con.close()
    strip = page[page.index("Helium Shortage"):]
    assert labels.STILL_TRACKING_PREFIX in page
    assert "Helium Shortage" in page
    assert labels.STILL_TRACKING_NO_DATE in page
    # No fabricated date: with an empty note, no "since <date>" clause appears
    # for this thread.
    assert "since" not in strip.split(labels.STILL_TRACKING_NO_DATE)[0]


def test_still_tracking_absent_when_no_flag():
    """No still_tracking slot -> no strip at all (the real editions' state)."""
    con = _con()
    slots = [slot(1, "Lead"), slot(2, "Second", ), slot(3, "Third")]
    stories = [story(1, "Lead"), story(2, "Second", "medium"),
               story(3, "Third", "quick")]
    seed(con, slots, stories)
    page, _ = server.build_page(con)
    con.close()
    assert labels.STILL_TRACKING_PREFIX not in page


# --- The label table is live (NL-29 re-pin lands once) ------------------------

def _deep_doc():
    return {
        "brief": {
            "pinned_facts": [{"fact": "A fact.", "cites": ["S1"]}],
            "mechanism": "Because of the thing.",
            "sources": [{"key": "S1", "outlet": "Outlet A", "title": "T",
                         "url": "http://x.invalid", "kind": "cluster-full-text",
                         "retrieved_at": "2026-07-10T00:00:00Z"}],
            "effects": [], "unknowns": [], "watch": [],
        },
        "header": {},
    }


def test_label_table_liveness_deep_view(monkeypatch):
    """The wiring proof: the deep view reads labels.DEEP_FACTS at render time,
    so a re-pin appears in output and the old hardcode is gone."""
    con = _con()
    monkeypatch.setattr(labels, "DEEP_FACTS", "ZZ-FACTS-SENTINEL")
    html = server._render_deep_view("story-0", "HL", _deep_doc(), TODAY, con=con)
    con.close()
    assert "ZZ-FACTS-SENTINEL" in html
    assert "The facts" not in html


def test_label_table_liveness_shell(monkeypatch):
    """The shell's nav + In-Brief labels are read from the table too."""
    con = _con()
    monkeypatch.setattr(labels, "NAV_FOLLOWING", "ZZ-FOLLOWING")
    monkeypatch.setattr(labels, "IN_BRIEF", "ZZ-INBRIEF")
    # The empty log means tiers derive POSITIONALLY (0 full, 1-2 medium, 3+
    # quick), so a quick-tier In-Brief snippet needs a story at index >= 3.
    slots = [slot(i, "S%d" % i) for i in range(1, 5)]
    stories = ([story(1, "S1")]
               + [story(i, "S%d" % i, "medium") for i in (2, 3)]
               + [story(4, "S4", "quick")])
    seed(con, slots, stories)
    page, _ = server.build_page(con)
    con.close()
    assert "ZZ-FOLLOWING" in page       # section line nav
    assert "ZZ-INBRIEF" in page         # the In-Brief heading


# --- Shell smoke on real-shaped editions --------------------------------------

def _seed_five_editions(con):
    """Five editions mirroring the real archive's SHAPES (not its data):
    a full multi-story lead day, a thin one-story day, an override day, a
    still-tracking day, and a six-slot day."""
    shapes = {
        "2026-07-04": ([slot(1, "One")], [story(1, "One")]),
        "2026-07-05": ([slot(1, "Lead"), slot(2, "B", override=True)],
                       [story(1, "Lead"), story(2, "B", "medium")]),
        "2026-07-06": ([slot(1, "Lead"), slot(2, "B"), slot(3, "C")],
                       [story(1, "Lead"), story(2, "B", "medium"),
                        story(3, "C", "quick")]),
        "2026-07-10": ([slot(1, "Lead"),
                        slot(2, "Quiet", still_tracking=True,
                             still_note="no movement since Jul 6")],
                       [story(1, "Lead"), story(2, "Quiet", "quick")]),
        "2026-07-14": ([slot(i, "S%d" % i) for i in range(1, 7)],
                       [story(1, "S1")] + [story(i, "S%d" % i, "medium")
                                           for i in (2, 3)]
                       + [story(i, "S%d" % i, "quick") for i in (4, 5, 6)]),
    }
    for date, (slots, stories) in shapes.items():
        seed(con, slots, stories, date=date)
    return list(shapes)


def test_shell_smoke_all_editions_render_v7_frame():
    con = _con()
    dates = _seed_five_editions(con)
    try:
        for d in dates:
            html, _ = server.build_page(con, date=d)
            # the masthead ceremony is present
            assert 'class="dateline"' in html, d
            # the sticky section line carries the three destinations
            assert 'class="section-line"' in html, d
            assert labels.NAV_TODAY in html and labels.NAV_FOLLOWING in html \
                and labels.NAV_ARCHIVE in html, d
            # the killed chrome is gone (DIRECTION-v5 §4: no bottom tabs, no top bar)
            assert 'class="bottom-nav"' not in html, d
            assert "nav.bottom-nav" not in html, d
            assert "logo-placeholder" not in html, d
    finally:
        con.close()


def test_every_view_carries_its_own_section_line():
    """§4: navigation lives in the section line, and each view (Today, Following,
    Archive) renders its OWN with the correct aria-current — there is no shared
    chrome, so a view without its section line would be a nav dead end (the exact
    bug the render proof caught in _render_archive)."""
    con = _con()
    seed(con, [slot(1, "Lead"), slot(2, "B")],
         [story(1, "Lead"), story(2, "B", "medium")])
    page, _ = server.build_page(con)
    con.close()
    for view, label in (("view-today", labels.NAV_TODAY),
                        ("view-following", labels.NAV_FOLLOWING),
                        ("view-archive", labels.NAV_ARCHIVE)):
        seg = page.split(f'id="{view}"', 1)[1].split('id="view-', 1)[0]
        assert 'class="section-line"' in seg, f"{view} lost its section line"
        assert f'aria-current="page">{label}' in seg, f"{view} nav not marked current"


def test_shell_today_default_renders():
    """build_page() with no date (the real Today path) renders the v7 frame."""
    con = _con()
    seed(con, [slot(1, "Lead"), slot(2, "B", )],
         [story(1, "Lead"), story(2, "B", "medium")])
    html, rendered = server.build_page(con)
    con.close()
    assert rendered == TODAY
    assert 'class="dateline"' in html
    assert 'class="bottom-nav"' not in html


# --- Gate fixes (v7-M1 gate, 2026-07-14): pins ---------------------------------

def _dark_block():
    import re
    from newslens import webui
    m = re.search(r"body\.dark\s*\{([^}]*)\}", webui.CSS)
    assert m, "body.dark rule missing from webui.CSS"
    return m.group(1)


def _css_var(block, name):
    import re
    m = re.search(name + r":\s*(#[0-9A-Fa-f]{6})", block)
    return m.group(1) if m else None


def _contrast(fg, bg):
    def lum(hexcol):
        cs = []
        for i in (1, 3, 5):
            c = int(hexcol[i:i + 2], 16) / 255
            cs.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
        return 0.2126 * cs[0] + 0.7152 * cs[1] + 0.0722 * cs[2]
    l1, l2 = sorted((lum(fg), lum(bg)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def test_dark_danger_token_holds_the_aa_floor():
    """Gate FIX-1: body.dark must redefine --danger (the light #7A3B37 computes
    2.13:1 on dark paper — illegible failure-state text), and the dark value
    must clear WCAG AA (>=4.5:1) on BOTH dark grounds. This is the pin that
    keeps webui's 'holding the AA floor (§11)' claim comment TRUE."""
    block = _dark_block()
    danger = _css_var(block, "--danger")
    assert danger, "--danger not redefined in body.dark"
    paper = _css_var(block, "--paper")
    surface = _css_var(block, "--surface")
    assert _contrast(danger, paper) >= 4.5, (danger, paper)
    assert _contrast(danger, surface) >= 4.5, (danger, surface)


def test_label_table_liveness_why_seeing(monkeypatch):
    """Gate FIX-2: the sources-context view's 4th section label routes through
    labels.py — the one deep-section-label of 11 that was hardcoded."""
    con = _con()
    monkeypatch.setattr(labels, "DEEP_WHY_SEEING", "ZZ-WHYSEEING")
    slots = [slot(i, "S%d" % i) for i in range(1, 5)]
    stories = ([story(1, "S1")]
               + [story(i, "S%d" % i, "medium") for i in (2, 3)]
               + [story(4, "S4", "quick")])
    seed(con, slots, stories)
    page, _ = server.build_page(con)
    con.close()
    assert "ZZ-WHYSEEING" in page
    assert "Why you’re seeing this" not in page


def test_label_table_liveness_view_title_h1s(monkeypatch):
    """The Following/Archive view titles render from the table. v7-M2 (§8/§12.4)
    re-pins the classes: WAS h1.view-title for both; NOW the Following title is
    the LOUD h1.page-title (still reads NAV_FOLLOWING), and the Archive-with-
    editions title is the MONTH title (the §8 calendar law) — NAV_ARCHIVE's
    liveness moves to the section-line nav. The day-one empty archive still
    titles from the table (h1.page-title)."""
    con = _con()
    monkeypatch.setattr(labels, "NAV_FOLLOWING", "ZZ-FOLLOWING")
    monkeypatch.setattr(labels, "NAV_ARCHIVE", "ZZ-ARCHIVE")
    slots = [slot(1, "S1")]
    seed(con, slots, [story(1, "S1")])
    page, _ = server.build_page(con)
    con.close()
    assert '<h1 class="page-title">ZZ-FOLLOWING</h1>' in page
    assert '<h1 class="page-title">Following</h1>' not in page
    # NAV_ARCHIVE liveness rides the section-line nav (the archive h1 is the month)
    assert 'aria-current="page">ZZ-ARCHIVE' in page
    assert '<h1 class="view-title">Archive</h1>' not in page
    # a NAV_ARCHIVE re-pin still moves the archive's own nav mark (the same DB is
    # sandbox-shared with `con`, so this archive renders the calendar month, and
    # NAV_ARCHIVE's liveness lives in the section line — not an h1).
    con2 = _con()
    monkeypatch.setattr(labels, "NAV_ARCHIVE", "ZZ-ARC2")
    page2, _ = server.build_page(con2)
    con2.close()
    assert 'aria-current="page">ZZ-ARC2' in page2
