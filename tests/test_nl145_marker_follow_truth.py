"""NL-145 — THE MARKER'S RELOAD HALF (M1 gate ruling R2, own TRACKER row).

THE DEFECT. `slot["matched_memory"]` is an EDITION RECORD: what the ranker
matched that morning. The tracked-ongoing marker is a LIVE CLAIM: "you follow
this thread". NL-143 made the live page honest — a remote unfollow sweeps the
marker into a resting CTA — but a RELOAD re-renders it from the stored slot,
which knows nothing about an unfollow that happened afterwards. So the marker
came back from the dead, and it came back INSTEAD of the follow control, leaving
the story with no way to follow it (his 2026-08-07 directive ③).

ACCEPTANCE, VERBATIM FROM THE GATE ROW: after a remote unfollow the marker stays
dead across reload AND the card regains its follow control. Both halves are
pinned below, on both marker-class surfaces, THROUGH THE REAL RENDER PATH —
`_render_briefing_body`, not a hand-called `_render_story` — because the whole
defect was a caller that never passed follow state, and a pin that supplies it
by hand would pass over exactly the code that was broken.

THE SCOPE IS THE MARKER, AND ONLY THE MARKER. `matched_memory` has eight call
sites in server.py; six of them REPORT THE EDITION (archive keywords, the
why-chosen line, the memory stamp, the timeline, the arc line, the deep view's
"Tracked threads:") and are deliberately untouched — filtering those would
rewrite what an edition said, which is a different and worse bug. The two that
make a live follow claim are the two that read through the filter.

DORMANCY IS NOT AN UNFOLLOW, and the last pin here is the one that says so. The
filter reads `status != 'dismissed_user'`, not `status = 'active'`: a dormant
thread is one the reader still holds, and offering him "Follow this thread" for
it is the double-mint bug `_deep_follow_line`'s own header records.
"""

from __future__ import annotations

import json

import pytest

from newslens import db, labels, memory, server

from test_ui_polish import slot, story, seed, TODAY


THREAD = "Hormuz"
STORY_TITLE = "Hormuz shipping watch"
HEADLINE = "Tankers reroute around the strait"


def _con():
    db.migrate()
    return db.connect()


def _edition(con, *, tier="full"):
    """Seed a one-story edition whose slot carries a mark, then render it
    through the REAL body renderer — the caller that had to learn to pass
    follow state."""
    s = slot(1, STORY_TITLE)
    s["matched_memory"] = [THREAD]
    st = story(1, HEADLINE, tier)
    seed(con, [s], [st])
    row = con.execute("SELECT * FROM briefings WHERE date = ?",
                      (TODAY,)).fetchone()
    entry = {"stories": [st], "tiers": [tier]}
    return server._render_briefing_body(con, row, entry, {}, "", "view-today")


# ---------------------------------------------------------------------------
# The card — marker-class site 1
# ---------------------------------------------------------------------------

def test_card_shows_the_marker_while_the_thread_is_held(tmp_paths):
    """CARRIED-INVARIANT (born green). The fix must not cost the marker its
    normal life — a filter that killed it always would 'pass' the unfollow pin
    and break the product."""
    con = _con()
    try:
        memory.add_thread(con, THREAD)
        html = _edition(con)
    finally:
        con.close()
    assert 'class="tracked-marker"' in html
    assert labels.TRACKED_ONGOING_PREFIX in html
    assert 'data-state="tracked"' in html


def test_card_marker_stays_dead_across_reload_after_unfollow(tmp_paths):
    """BORN RED — the acceptance criterion's first half. The thread is
    unfollowed AFTER the edition was written; the stored slot still names it."""
    con = _con()
    try:
        memory.add_thread(con, THREAD)
        memory.dismiss_thread(con, THREAD)
        html = _edition(con)
    finally:
        con.close()
    assert 'class="tracked-marker"' not in html
    assert labels.TRACKED_ONGOING_PREFIX not in html
    assert 'data-state="tracked"' not in html


def test_card_regains_its_follow_control_after_unfollow(tmp_paths):
    """BORN RED — the acceptance criterion's second half, and the half that
    makes this a directive-③ defect rather than a cosmetic one. A dead marker
    that leaves no control behind is not a fix."""
    con = _con()
    try:
        memory.add_thread(con, THREAD)
        memory.dismiss_thread(con, THREAD)
        html = _edition(con)
    finally:
        con.close()
    assert 'class="follow-slot"' in html
    assert labels.FOLLOW_THREAD_INACTIVE in html


# ---------------------------------------------------------------------------
# The deep view — marker-class site 2
# ---------------------------------------------------------------------------

def _deep(con):
    s = slot(5, STORY_TITLE)
    s["matched_memory"] = [THREAD]
    return server._render_sources_context_view(
        "story-4", HEADLINE, story(5, HEADLINE, "quick"), s, con, TODAY)


def test_deep_view_marker_dies_and_the_mount_offers_the_follow(tmp_paths):
    """BORN RED. The deep view is M1c's MANAGEMENT HOME — the surface where
    Unfollow lives — so a stale `tracked` state there withholds precisely the
    control an unfollowed story needs."""
    con = _con()
    try:
        memory.add_thread(con, THREAD)
        memory.dismiss_thread(con, THREAD)
        deep = _deep(con)
    finally:
        con.close()
    line = deep.split('class="follow-line"')[1].split("</div>")[0]
    assert 'class="tracked-marker"' not in line
    assert 'data-state="tracked"' not in line
    assert 'class="follow-slot"' in line
    assert labels.FOLLOW_THREAD_INACTIVE in deep


def test_deep_view_keeps_the_marker_while_held(tmp_paths):
    """CARRIED-INVARIANT (born green) — the NL-143 behaviour this rider must
    not disturb."""
    con = _con()
    try:
        memory.add_thread(con, THREAD)
        deep = _deep(con)
    finally:
        con.close()
    line = deep.split('class="follow-line"')[1].split("</div>")[0]
    assert 'class="tracked-marker"' in line
    assert labels.TRACKED_ONGOING_PREFIX in line and THREAD in line


# ---------------------------------------------------------------------------
# The predicate itself — dormancy is not an unfollow
# ---------------------------------------------------------------------------

def test_a_dormant_thread_keeps_its_marker(tmp_paths):
    """THE OVER-CORRECTION THIS RIDER MUST NOT MAKE. Filtering on
    `status = 'active'` would also kill the marker for a DORMANT thread — one
    the reader never let go of, still listed under Following — and hand him a
    "Follow this thread" button that mints a SECOND row beside it. That is the
    double-mint bug, re-created by the fix for a different one."""
    con = _con()
    try:
        memory.add_thread(con, THREAD)
        con.execute("UPDATE memory SET status = 'dormant' WHERE topic = ?",
                    (THREAD,))
        con.commit()
        assert THREAD.lower() not in server._active_topics_lower(con)
        assert THREAD.lower() in server._held_topics_lower(con)
        html = _edition(con)
    finally:
        con.close()
    assert 'class="tracked-marker"' in html
    assert labels.FOLLOW_THREAD_INACTIVE not in html


def test_the_filter_degrades_to_the_edition_record_with_no_follow_state():
    """`held_topics=None` means "no follow state available" (a fixture render
    with no connection). Returning the marks unchanged is the honest degrade:
    the stored edition is then the only truth there is."""
    s = {"matched_memory": ["Hormuz", "", "Fed"]}
    assert server._live_marks(s, None) == ["Hormuz", "Fed"]
    assert server._live_marks(s, {"hormuz"}) == ["Hormuz"]
    assert server._live_marks(s, set()) == []


def test_only_the_marker_class_sites_filter(tmp_paths):
    """SCOPE. The six edition-reporting consumers of matched_memory keep telling
    the truth about the edition after an unfollow — the deep view's "Tracked
    threads:" line is the one checked here because it is the most marker-like of
    them and the easiest to over-filter by accident."""
    con = _con()
    try:
        memory.add_thread(con, THREAD)
        memory.dismiss_thread(con, THREAD)
        deep = _deep(con)
    finally:
        con.close()
    assert 'class="sc-threads"' in deep
    assert f"Tracked threads: {THREAD}" in deep
