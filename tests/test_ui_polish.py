"""P1 UI polish batch (2026-07-06): glance restyle, ongoing recency, splash
logo. In-process build_page only (no server, no reads anywhere near a real
table); CSS/JS contracts pinned as markup so regressions surface here.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import pytest

from newslens import db, paths, ranking, server, webui


# NL-11: Today defaults to TODAY's edition (or empty) — never a stale one. So
# in-process build_page(con) tests must seed today's date to render an edition.
TODAY = datetime.now().strftime("%Y-%m-%d")


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def slot(n, title, tags=(), mem=(), override=False):
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
    }


def seed(con, slots, stories):
    from newslens import generate

    inputs = {"slots": slots, "items_by_slot": {s["slot"]: [] for s in slots},
              "threads": [], "prior_ctx": None, "continuity_status": "none",
              "window_meta": None, "corroboration": {}}
    narrative = generate.assemble_narrative(TODAY, "A", stories, inputs)
    con.execute(
        "INSERT INTO briefings (date, story_slots, corroboration_labels,"
        " narrative_text, generated_at) VALUES (?, ?, ?, ?, ?)",
        (TODAY, json.dumps(slots),
         json.dumps({"standing_caveat": ranking.CORROBORATION_CAVEAT,
                     "per_story": []}),
         narrative, iso_now()),
    )
    con.commit()


def story(n, headline, tier="full"):
    st = {"tier": tier, "headline": headline,
          "lede": "The lede sentence for this story.",
          "why_label": "Why it matters", "watch_label": "Watch for",
          "why_it_matters": "Concrete effects.", "watch_for": "The vote.",
          "my_read": None}
    if tier == "quick":
        st.pop("why_it_matters"), st.pop("watch_for")
        st.pop("why_label"), st.pop("watch_label")
    return st


@pytest.fixture
def page3(tmp_paths):
    """A three-story page: tagged, tagged+thread, and no-signal (fallback)."""
    db.migrate()
    con = db.connect()
    slots = [
        slot(1, "Tagged story", tags=({"name": "AI regulation", "level": "topic"},)),
        slot(2, "Thread story",
             tags=({"name": "economy", "level": "domain"},), mem=("Iran War",)),
        slot(3, "Zero-signal story", override=True),
    ]
    stories = [story(1, "Tagged story"), story(2, "Thread story", "medium"),
               story(3, "Zero-signal story", "medium")]
    seed(con, slots, stories)
    page, rendered = server.build_page(con)
    con.close()
    assert rendered == TODAY
    return page


# --- 1. glance REMOVED (NL-11) -----------------------------------------------------------

def test_glance_section_is_gone(page3):
    """NL-11: the 'In today’s briefing' glance section is removed (rework
    backlogged, NL-20). Neither the section wrapper nor its rows may render;
    the story ids the glance used to anchor still exist for the deep view."""
    assert 'class="glance"' not in page3
    assert 'glance-row' not in page3
    assert "In today’s briefing" not in page3
    # Today still opens straight onto the stories.
    today = page3.split('id="view-today"')[1].split("<section")[0]
    ids = re.findall(r'id="story-(\d+)"', today)
    assert sorted(ids) == ["0", "1", "2"]
    assert re.search(r'id="view-today"[^>]*class="[^"]*\bactive\b', page3)


def test_headlines_render_escaped(tmp_paths):
    db.migrate()
    con = db.connect()
    evil = 'Breaking <script>alert(1)</script> markets'
    slots = [slot(1, evil)]
    seed(con, slots, [story(1, evil)])
    page, _ = server.build_page(con)
    con.close()
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_scroll_affordances_and_reduced_motion_css():
    assert "html { scroll-behavior: smooth; }" in webui.CSS
    assert "article.story { scroll-margin-top:" in webui.CSS
    assert ("@media (prefers-reduced-motion: reduce) { html "
            "{ scroll-behavior: auto; } }") in webui.CSS


# --- 2. ongoing recency (display-order only) ----------------------------------------------

def _seed_threads(con):
    b = {}
    for date in ("2026-07-01", "2026-07-04"):
        cur = con.execute(
            "INSERT INTO briefings (date, generated_at) VALUES (?, ?)",
            (date, f"{date}T12:00:00.000Z"))
        b[date] = cur.lastrowid
    rows = [
        ("Old Pickup", "active", b["2026-07-01"]),
        ("New Pickup", "active", b["2026-07-04"]),
        ("Never One", "active", None),
        ("Never Two", "active", None),
        ("Tie A", "active", b["2026-07-04"]),
        ("Sleeping", "dormant", None),
        ("Stopped", "dismissed_user", None),
    ]
    for topic, status, ref in rows:
        con.execute(
            "INSERT INTO memory (topic, status, last_referenced_briefing_id,"
            " status_changed_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (topic, status, ref, iso_now(), iso_now(), iso_now()))
    con.commit()


def test_ongoing_sorts_by_recency_never_picked_sink_stable_ties(tmp_paths):
    db.migrate()
    con = db.connect()
    _seed_threads(con)
    grouped = server._following_rows(con)
    con.close()
    active = [t["topic"] for t in grouped["active"]]
    # Most recent pickup first; the 07-04 tie keeps insertion (id) order;
    # never-picked-up sink to the end in id order (stable sort).
    assert active == ["New Pickup", "Tie A", "Old Pickup", "Never One", "Never Two"]


def test_recency_is_display_only_lifecycle_untouched(tmp_paths):
    db.migrate()
    con = db.connect()
    _seed_threads(con)
    before = {r["topic"]: (r["status"], r["status_changed_at"])
              for r in con.execute(
                  "SELECT topic, status, status_changed_at FROM memory")}
    grouped = server._following_rows(con)
    after = {r["topic"]: (r["status"], r["status_changed_at"])
             for r in con.execute(
                 "SELECT topic, status, status_changed_at FROM memory")}
    con.close()
    assert after == before  # a render sorted nothing in the DB
    assert [t["topic"] for t in grouped["dormant"]] == ["Sleeping"]
    assert [t["topic"] for t in grouped["dismissed_user"]] == ["Stopped"]


# --- 3. splash logo -------------------------------------------------------------------------

def test_splash_css_contract():
    assert "body.splash .logo-placeholder" in webui.CSS
    # The dashed placeholder border lives in the BASE rule — both states
    # share it (it leaves only with the real logo, P4).
    base = webui.CSS.split(".logo-placeholder {")[1].split("}")[0]
    assert "1px dashed var(--rule)" in base
    splash_rule = webui.CSS.split("body.splash .logo-placeholder {")[1].split("}")[0]
    assert "border" not in splash_rule  # never overridden away in splash
    # NL-11: the splash opens ~40% larger than the previous 2.1rem.
    assert "font-size: 2.95rem" in splash_rule
    assert ("@media (prefers-reduced-motion: reduce) { .logo-placeholder "
            "{ transition: none; } }") in webui.CSS


def test_lead_story_has_room_below_the_episode_divider():
    """NL-11: with the glance gone, the lead story keeps breathing room below
    the Play-episode divider (principal 2026-07-09)."""
    assert "#view-today > article.story:first-child { margin-top:" in webui.CSS


def test_splash_js_is_idempotent_passive_and_two_way():
    js = webui.JS
    assert "var THRESH = 24;" in js
    assert "classList.toggle('splash', window.scrollY <= THRESH)" in js
    assert "{ passive: true }" in js
    # Exactly ONE scroll listener registration — no duplicate-listener risk,
    # and toggle(cls, bool) is idempotent under rapid scroll events.
    assert js.count("addEventListener('scroll'") == 1
    assert "syncSplash();" in js  # initial sync: opens large at the top


def test_server_never_pre_applies_splash(page3):
    body_tag = re.search(r"<body[^>]*>", page3).group(0)
    assert "splash" not in body_tag  # no JS -> class never appears -> static size


def test_dark_mode_and_splash_are_independent_body_classes():
    js = webui.JS
    assert "classList.toggle('dark'" in js
    assert "classList.add('dark')" in js
    # Neither feature assigns className wholesale — the classes coexist.
    assert "document.body.className" not in js
