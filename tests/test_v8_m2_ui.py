"""v8-M2 — the Today-page pair lands in code (2026-07-18): the newspaper GRID
(item 1) + the slim memory STAMP (item 2).

Born-red where the law changes (revert the change → these fail):
  * the stamp's NO-PROSE rule: the Today body carries no arc prose block — the
    full arc register lives only in the deep view.
  * the grid's RANK-ORDER DOM invariant: visual placement (grid-column classes /
    the balance heuristic) never reorders the DOM — screen readers hear rank
    order 1→N, no wrapper column.
  * the COUNT-STATE behavior (7 / 6 / 5 and the no-strips floor): the grid is
    count-flexible; the strips are the grout.
  * the STRIP DEGRADATION of the stamp: full form on the lead + cards, degraded
    (ordinal dropped) inside strip smeta, absent below.
  * accessibility: the heading tree (h2 lead/card, h3 strip) carries the tier now
    that the visible "In brief" label is dead.

Offline by construction (autouse conftest sandbox + loopback guard); $0.
"""
from __future__ import annotations

import re

import pytest

from newslens import db, server

from test_ui_polish import slot, story, seed, TODAY
from test_nl68_batch import _seed_thread_with_ledger


def _con():
    db.migrate()
    return db.connect()


def _today_view(page: str) -> str:
    """The Today view slice only — excludes the deep-view sections (which keep
    their own arc + 'In brief' section label, distinct concerns)."""
    return page.split('id="view-today"')[1].split('id="view-following"')[0]


def _article_ids(html: str):
    return re.findall(r'<article[^>]*\bid="(story-\d+)"', html)


def _seed_thread_only(con, topic: str) -> int:
    """A followed thread with NO ledger (day-one) — no prior coverage."""
    now = "2026-07-01T00:00:00.000Z"
    tid = con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES (?, 'active', ?, ?, ?)", (topic, now, now, now)
    ).lastrowid
    con.commit()
    return tid


# ==========================================================================
# item 2 — the slim memory stamp: NO prose on Today
# ==========================================================================

def test_today_body_carries_no_arc_prose_block(tmp_paths):
    """BORN-RED: the arc PROSE block (`today-arc-line`, 'When we last covered
    this …') is gone from the Today body entirely — a slim machine STAMP rides
    the deck instead. The full arc register lives only in the deep view."""
    con = _con()
    _seed_thread_with_ledger(con, "Strait of Hormuz", prior_date="2026-07-05")
    seed(con, [slot(1, "Strikes exchanged", mem=("Strait of Hormuz",)),
               slot(2, "Second")],
         [story(1, "Strikes exchanged"), story(2, "Second", "medium")])
    page, _ = server.build_page(con)
    con.close()
    today = _today_view(page)
    assert "today-arc-line" not in today               # the prose block is gone
    assert "When we last covered this" not in today    # ...and its sentence
    assert 'class="memline"' in today                  # replaced by the slim stamp
    assert "entry on this thread" in today


def test_lead_stamp_full_form_and_marker_suppressed(tmp_paths):
    """The lead's followed thread moved with history → the FULL stamp rides the
    deck ('● Nth entry on this thread · last covered <date>') and the redundant
    tracked-ongoing marker is suppressed (the covered-before signal, once)."""
    con = _con()
    _seed_thread_with_ledger(con, "Strait of Hormuz", prior_date="2026-07-05")
    seed(con, [slot(1, "Strikes", mem=("Strait of Hormuz",)), slot(2, "S2")],
         [story(1, "Strikes"), story(2, "S2", "medium")])
    page, _ = server.build_page(con)
    con.close()
    lead = _today_view(page).split('<article class="lead')[1].split("</article>")[0]
    assert 'class="memline"' in lead
    assert "entry on this thread" in lead              # FULL form (ordinal kept)
    assert "last covered" in lead
    assert "Tracked ongoing story" not in lead         # marker suppressed by the stamp


def test_day_one_thread_gets_no_stamp_and_keeps_its_marker(tmp_paths):
    """A followed thread with NO prior coverage (day-one) gets NO stamp, ever —
    the arc's day-one silence, as furniture — so the tracked-ongoing marker is
    its sole covered-before signal (suppression is scoped to where a stamp shows)."""
    con = _con()
    _seed_thread_only(con, "Fresh")
    seed(con, [slot(1, "Fresh story", mem=("Fresh",)), slot(2, "S2")],
         [story(1, "Fresh story"), story(2, "S2", "medium")])
    page, _ = server.build_page(con)
    con.close()
    lead = _today_view(page).split('<article class="lead')[1].split("</article>")[0]
    assert 'class="memline"' not in lead               # no prior coverage → no stamp
    assert "Tracked ongoing story" in lead             # the marker is the sole signal


def test_stamp_ordinal_is_distinct_prior_editions_plus_today(tmp_paths):
    """The ordinal counts DISTINCT prior edition dates + 1 for today: a
    sanctioned split-day (two deltas, one date) collapses to ONE appearance.
    Two distinct prior dates (07-05, 07-06, with a 07-06 duplicate) → '3rd
    entry · last covered Jul 6'."""
    con = _con()
    now = "2026-07-01T00:00:00.000Z"
    tid = con.execute(
        "INSERT INTO memory (topic, status, status_changed_at, created_at,"
        " updated_at) VALUES ('T', 'active', ?, ?, ?)", (now, now, now)).lastrowid
    for d in ("2026-07-05", "2026-07-06", "2026-07-06"):   # the 07-06 dup = one appearance
        con.execute(
            "INSERT INTO thread_deltas (thread_id, edition_date, verdict,"
            " what_happened, significance, cites_json, slot) VALUES"
            " (?, ?, 'advances', 'x', 'y', '[]', NULL)", (tid, d))
    con.commit()
    seed(con, [slot(1, "Lead", mem=("T",)), slot(2, "S2")],
         [story(1, "Lead"), story(2, "S2", "medium")])
    page, _ = server.build_page(con)
    con.close()
    lead = _today_view(page).split('<article class="lead')[1].split("</article>")[0]
    assert "3rd entry on this thread" in lead
    assert "last covered Jul 6" in lead


# ==========================================================================
# item 1 — the newspaper grid: rank-order DOM, count-flex, strip degradation
# ==========================================================================

def test_grid_dom_stays_rank_order_over_column_placement(tmp_paths):
    """BORN-RED (the rank-order DOM invariant): the 7 slots are DIRECT children
    of .today-grid in DOM order 1→7 — no wrapper column — even though the strips
    are distributed across BOTH columns by the presentation-only balance
    heuristic. Screen readers hear rank order."""
    con = _con()
    slots = [slot(i, f"S{i}") for i in range(1, 8)]
    stories = [story(i, f"S{i}",
                     "full" if i == 1 else "medium" if i <= 3 else "quick")
               for i in range(1, 8)]
    seed(con, slots, stories)
    page, _ = server.build_page(con)
    con.close()
    today = _today_view(page)
    assert _article_ids(today) == [f"story-{i}" for i in range(7)]   # DOM = rank order
    assert 'class="col-right"' not in today            # no wrapper column
    assert "grid-col-a" in today and "grid-col-b" in today  # strips balanced by column class


@pytest.mark.parametrize("n,n_strips", [(7, 4), (6, 3), (5, 2)])
def test_count_state_flexes_the_strips_are_the_grout(tmp_paths, n, n_strips):
    """The count-state behavior: N slots render N-3 strips (the grout — lead +
    two cards + strips), all N in rank DOM order. Count-flexible, no reflow."""
    con = _con()
    slots = [slot(i, f"S{i}") for i in range(1, n + 1)]
    stories = [story(i, f"S{i}",
                     "full" if i == 1 else "medium" if i <= 3 else "quick")
               for i in range(1, n + 1)]
    seed(con, slots, stories)
    page, _ = server.build_page(con)
    con.close()
    today = _today_view(page)
    assert _article_ids(today) == [f"story-{i}" for i in range(n)]
    assert today.count('<article class="strip') == n_strips


def test_no_strips_floor_two_columns_end_naturally(tmp_paths):
    """The empty (no-strip) floor: lead + two cards, no strips at all — the two
    columns simply end (nothing to square off)."""
    con = _con()
    seed(con, [slot(1, "S1"), slot(2, "S2"), slot(3, "S3")],
         [story(1, "S1"), story(2, "S2", "medium"), story(3, "S3", "medium")])
    page, _ = server.build_page(con)
    con.close()
    today = _today_view(page)
    assert '<article class="strip' not in today         # no grout
    assert today.count('<article class="story') == 2    # two medium cards
    assert "grid-lead" in today                         # the lead still spans its column


def test_strip_stamp_degrades_ordinal_drops_into_smeta(tmp_paths):
    """BORN-RED (strip degradation): a strip whose followed thread moved carries
    the DEGRADED stamp inside its machine smeta — '● last covered <date>', the
    ordinal DROPPED (no 'entry on this thread'); a strip with no memory carries
    no stamp at all."""
    con = _con()
    _seed_thread_with_ledger(con, "Quiet Thread", prior_date="2026-07-05")
    slots = [slot(1, "S1"), slot(2, "S2"), slot(3, "S3"),
             slot(4, "Moved strip", mem=("Quiet Thread",))]
    stories = [story(1, "S1"), story(2, "S2", "medium"), story(3, "S3", "medium"),
               story(4, "Moved strip", "quick")]
    seed(con, slots, stories)
    page, _ = server.build_page(con)
    con.close()
    today = _today_view(page)
    strip = today.split('id="story-3"')[1].split("</article>")[0]
    assert 'class="smeta"' in strip
    assert 'class="mem-dot"' in strip                   # the ● dot leads the smeta
    assert "last covered" in strip                      # degraded stamp present
    assert "entry on this thread" not in strip          # ordinal DROPPED on a strip
    # a no-memory strip (story-1) carries no stamp
    plain = today.split('id="story-1"')[1].split("</article>")[0]
    assert "mem-dot" not in plain


def test_heading_hierarchy_carries_tier_for_at(tmp_paths):
    """Accessibility: the visible 'In brief' label is dead, so the heading tree
    carries the tier — lead + medium cards are h2 (under the dateline h1), the
    strips are h3."""
    con = _con()
    slots = [slot(i, f"S{i}") for i in range(1, 5)]
    stories = [story(1, "S1"), story(2, "S2", "medium"), story(3, "S3", "medium"),
               story(4, "S4", "quick")]
    seed(con, slots, stories)
    page, _ = server.build_page(con)
    con.close()
    today = _today_view(page)
    lead = today.split('<article class="lead')[1].split("</article>")[0]
    assert '<h2 class="headline"' in lead               # lead h2 (dateline is the h1)
    card = today.split('id="story-1"')[1].split("</article>")[0]
    assert '<h2 class="headline"' in card               # medium card h2
    strip = today.split('id="story-3"')[1].split("</article>")[0]
    assert '<h3 class="headline"' in strip              # strip h3 carries the tier
