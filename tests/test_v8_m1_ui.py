"""v8-M1 — the served-version UI review lands in code (2026-07-17).

Born-red where the law changes + render/DOM pins per the established server-test
patterns. Offline by construction (conftest autouse sandbox + loopback guard).
This file pins ONLY the SHIPPED items of the batch:

  * item 3 — the deep view's "story so far" timeline relocates to the bottom
    (second-from-last, before Sources) — one ordering decision.
  * item 4 — inline citation apparatus dies in the analysis sections; trailing
    per-paragraph source clusters instead (prose never interrupted).
  * item 5 — empty-thread label semantics (FOLLOWED, never LAST UPDATED off the
    follow-birth date).
  * item 8 — the archive "N editions this month" line dies (pinned in
    tests/test_nl68_batch.py::test_archive_interface_explainer_is_removed).

HELD to the next increment (the coupled live-Today-path pair — NOT pinned here):
  * item 1 — the newspaper front grid (desktop 7-slot areas; DOM = rank order;
    single column below 900px; "in brief" label dead; count-flexible 5-7).
  * item 2 — the slim memory stamp on Today (machine furniture; NO generated
    prose on Today) + strip degradation.
"""
from __future__ import annotations

import json
from datetime import datetime

from newslens import db, server

DATE = datetime.now().strftime("%Y-%m-%d")


def _con():
    db.migrate()
    return db.connect()


# ==========================================================================
# item 5 — empty-thread label semantics
# ==========================================================================

def test_item5_empty_thread_stamps_FOLLOWED_not_LAST_UPDATED(tmp_paths):
    """BORN-RED: an active thread with NO state, NO deltas, NO baseline has no
    content date — its quiet-fold stamp must read FOLLOWED <created> (the
    follow's birth), never LAST UPDATED <date> off the ref/join date (which is
    not coverage). Fails on the old `ref_date` fallback that stamped LAST
    UPDATED off a date with no content behind it."""
    con = _con()
    try:
        now = "2026-07-01T00:00:00.000Z"
        con.execute(
            "INSERT INTO briefings (id, date, story_slots) VALUES (1, ?, ?)",
            (DATE, json.dumps([])))
        # An EMPTY thread joined to that briefing (so ref_date is set, the old
        # bug's trigger) — but no thread_deltas, no thread_state, no baseline.
        con.execute(
            "INSERT INTO memory (topic, status, status_changed_at, created_at, "
            "updated_at, last_referenced_briefing_id) "
            "VALUES ('Quiet Empire', 'active', ?, ?, ?, 1)", (now, now, now))
        con.commit()
        html = server._render_following(con)
    finally:
        con.close()
    assert "Quiet Empire" in html
    assert "FOLLOWED" in html                       # the honest follow-birth stamp
    assert "LAST UPDATED" not in html               # never off a no-content date


def test_item5_thread_with_a_delta_keeps_LAST_UPDATED(tmp_paths):
    """The other direction: a thread that HAS content (a ledger delta) keeps the
    LAST UPDATED stamp — the fix touches only the empty case."""
    con = _con()
    try:
        now = "2026-07-01T00:00:00.000Z"
        con.execute(
            "INSERT INTO briefings (id, date, story_slots) VALUES (1, ?, ?)",
            (DATE, json.dumps([])))
        cur = con.execute(
            "INSERT INTO memory (topic, status, status_changed_at, created_at, "
            "updated_at, last_referenced_briefing_id) "
            "VALUES ('Covered Thread', 'active', ?, ?, ?, 1)", (now, now, now))
        tid = cur.lastrowid
        con.execute(
            "INSERT INTO thread_deltas (thread_id, edition_date, slot, verdict, "
            "what_happened, significance, cites_json) "
            "VALUES (?, '2026-07-05', 1, 'advances', 'It moved.', 'Matters.', "
            "'[\"S1\"]')", (tid,))
        con.commit()
        html = server._render_following(con)
    finally:
        con.close()
    assert "Covered Thread" in html
    assert "LAST UPDATED" in html                   # content date -> LAST UPDATED


# ==========================================================================
# item 3 — the deep view's "story so far" timeline relocates to the bottom
# ==========================================================================

def test_item3_timeline_relocates_before_sources_with_a_jumplist_door(tmp_paths):
    """BORN-RED: the 'story so far' timeline moves to second-from-last, directly
    before Sources — and gains a jumplist door so it stays one tap from the top.
    Fails if it renders above the analysis sections (its old slot)."""
    con = _con()
    try:
        now = "2026-07-01T00:00:00.000Z"
        cur = con.execute(
            "INSERT INTO memory (topic, status, status_changed_at, created_at, "
            "updated_at) VALUES ('Iran War', 'active', ?, ?, ?)", (now, now, now))
        tid = cur.lastrowid
        for d in ("2026-07-05", "2026-07-06"):
            con.execute(
                "INSERT INTO briefings (date, story_slots) VALUES (?, '[]')", (d,))
            con.execute(
                "INSERT INTO thread_deltas (thread_id, edition_date, slot, verdict,"
                " what_happened, significance, cites_json) VALUES "
                "(?, ?, 1, 'advances', 'It moved.', 'Matters.', '[\"S1\"]')",
                (tid, d))
        con.commit()
        brief = {"pinned_facts": [{"fact": "F.", "cites": []}],
                 "mechanism": "M.", "sources": []}
        slot = {"matched_memory": ["Iran War"], "story_title": "Iran War"}
        html = server._render_deep_view(
            "story-0", "H", {"header": {}, "brief": brief},
            "2026-07-10", con=con, slot=slot)
    finally:
        con.close()
    assert 'id="story-0-timeline"' in html                       # the timeline renders
    # relocated: AFTER the analysis sections, BEFORE Sources
    assert html.index('id="story-0-timeline"') > html.index('id="story-0-mechanism"')
    assert html.index('id="story-0-timeline"') < html.index('id="story-0-sources"')
    # a jumplist door for it, before the Sources door
    jl = html.split('deep-jumplist')[1].split("</p>")[0]
    assert "#story-0-timeline" in jl
    assert jl.index("#story-0-timeline") < jl.index("#story-0-sources")


# ==========================================================================
# item 4 — inline citation apparatus dies; trailing source clusters
# ==========================================================================

def test_item4_analysis_sections_use_trailing_clusters_no_inline_apparatus(tmp_paths):
    """BORN-RED law (the citation second-raise): the inline collapsed-cite
    apparatus (▸ cite-fold, mid-prose '(via X)') DIES in the analysis sections.
    Each cited analysis paragraph closes with a trailing SOURCE CLUSTER; the
    facts carry a plain end-of-line outlet COUNT; prose is never interrupted."""
    brief = {
        "pinned_facts": [{"fact": "A verified fact stands.", "cites": ["S1"]}],
        "mechanism": "The chokepoint transmits shocks [S1] to global markets [S2].",
        "effects": [{"effect": "Escalation risk rises.", "cites": ["S2"]}],
        "sources": [
            {"key": "S1", "outlet": "Reuters"},
            {"key": "S2", "outlet": "Bloomberg"},
        ],
    }
    html = server._render_deep_view(
        "story-0", "H", {"header": {}, "brief": brief}, DATE)
    assert "cite-fold" not in html                  # no inline fold apparatus anywhere
    mech = html.split('id="story-0-mechanism"')[1].split("</div>")[0]
    assert "[S1]" not in mech and "[S2]" not in mech            # raw keys stripped
    assert '<p class="src-cluster">— Reuters · Bloomberg</p>' in mech
    eff = html.split('id="story-0-effects"')[1].split("</div>")[0]
    assert '<p class="src-cluster">— Bloomberg</p>' in eff      # per-paragraph cluster
    facts = html.split('id="story-0-facts"')[1].split("</div>")[0]
    assert '<span class="cite">(1 outlet)</span>' in facts      # plain count, no fold
