"""NL-58 full-app-review batch — implementer pins for the render/behavior
surfaces added or changed by the batch (DECISIONS 2026-07-10 "NL-58 FULL-APP
REVIEW VERDICT", rulings (2)–(7) + the bugs list).

Offline, sandboxed via the autouse conftest. Each new enforcement surface is
born with the pin only it can turn green (claims-of-wiring proof rule); if a
refactor unwires it, the offending test bites with its fix contract.

Diagnostic note (P1 caret + arc-line, P2 register): NOT re-implemented here —
those already shipped in NL-12 (commit 3ab8551) and were verified live against
a copy of the real DB. The principal's NL-58 screenshots showed the pre-NL-12
UI (a review tab open from before the 00:42 deploy, never reloaded). What this
file pins is the genuinely-new work: mechanism fold parity, prior-edition
source linking, the Following copy/date fixes, the merged story control, the
coverage window, the audio controls, and the caveat removal.
"""

from __future__ import annotations

import json

from newslens import db, paths, ranking, server

from test_ui_polish import slot, story, seed, TODAY, iso_now


def _deep_doc():
    return {"header": {}, "brief": {
        "pinned_facts": [{"fact": "A fact.", "cites": ["S1"]}],
        "mechanism": "The cause is X [S1] and then Y [S2].",
        "effects": [], "unknowns": [], "watch": [],
        "arc": {"delta": "advances", "what_changed": "Moved.", "cites": ["P1"]},
        "sources": [
            {"key": "S1", "outlet": "BBC", "title": "t", "url": "http://x",
             "retrieved_at": "2026-07-10T04:00Z", "kind": "cluster-full-text"},
            {"key": "S2", "outlet": "AP", "title": "u", "url": "http://y",
             "retrieved_at": "2026-07-10T04:00Z", "kind": "retrieved"},
            {"key": "P1", "outlet": "NewsLens (prior edition)",
             "title": "briefing 2026-07-06", "url": "",
             "retrieved_at": "2026-07-06", "kind": "prior-briefing"},
        ],
    }}


# --- P1a: mechanism citations fold exactly like facts' -----------------------

def test_mechanism_citations_render_a_trailing_source_cluster_not_inline_folds():
    """v8-M1 item 4 (2026-07-17, CONSCIOUS FLIP — the citation second-raise):
    mechanism-section citations no longer fold inline behind a caret. The [S#]
    keys are STRIPPED from the prose and one trailing SOURCE CLUSTER names the
    distinct outlets — prose never interrupted, no ▸ markers, no raw keys.
    (WAS: an inline _cite_fold per [S#].)"""
    html = server._render_deep_view("story-0", "H", _deep_doc(), "2026-07-10",
                                    back_label="B", return_view="view-today")
    mech = html[html.index('id="story-0-mechanism"'):
                html.index('id="story-0-sources"')]
    assert "cite-fold" not in mech                   # no inline fold apparatus
    assert '<span class="caret"' not in mech         # no ▸ marker mid-prose
    assert ' [S1]' not in mech and ' [S2]' not in mech  # raw keys stripped
    # one trailing cluster naming the distinct outlets (S1->BBC, S2->AP)
    assert '<p class="src-cluster">— BBC · AP</p>' in mech


# --- P1b: prior-edition sources name the edition and link via openEdition -----

def test_prior_edition_source_names_the_edition_and_links():
    """NL-58: a prior-briefing source row must say WHICH edition and open it in
    place (openEdition; the /?date= href is the no-JS fallback), replacing the
    machine title ('briefing 2026-07-06') and the empty url. Fix contract: the
    prior-briefing branch in the sources loop of _render_deep_view."""
    html = server._render_deep_view("story-0", "H", _deep_doc(), "2026-07-10",
                                    back_label="B", return_view="view-today")
    src = html[html.index('id="story-0-sources"'):]
    assert "NewsLens — Monday, July 6 edition" in src
    assert "openEdition('2026-07-06'" in src
    assert 'href="/?date=2026-07-06"' in src
    assert "briefing 2026-07-06" not in src  # machine title never surfaces


def test_prior_edition_source_link_requires_a_real_calendar_date():
    """NL-60 hardening: the source-row link branch guarded the prior-edition date
    by ISO SHAPE only, so a shaped-but-impossible '2026-13-45' rendered a live
    dead-end 'NewsLens — 2026-13-45 edition' link. strptime must accept the date
    before the link branch is taken; a calendar-invalid date falls through to the
    plain unlinked title. Fix contract: _is_calendar_date guards the branch."""
    # valid date -> links (happy path stays green under the new guard)
    html = server._render_deep_view("story-0", "H", _deep_doc(), "2026-07-10",
                                    back_label="B", return_view="view-today")
    src = html[html.index('id="story-0-sources"'):]
    assert "openEdition('2026-07-06'" in src
    # calendar-invalid date -> plain text, no link, no crash
    bad = _deep_doc()
    bad["brief"]["sources"][2]["retrieved_at"] = "2026-13-45"   # P1, impossible
    html2 = server._render_deep_view("story-0", "H", bad, "2026-07-10",
                                     back_label="B", return_view="view-today")
    src2 = html2[html2.index('id="story-0-sources"'):]
    assert "openEdition(" not in src2          # no live link fired
    assert 'href="/?date=' not in src2         # no navigable edition href
    assert "NewsLens — " not in src2           # the linked-title form never renders


# CONSCIOUS FLIP (arc-line contract v1, 2026-07-18): the NL-60 arc-link
# dead-link guard is DELETED with its subject. The deep-view arc line no longer
# derives from brief['arc'] nor builds a prior-edition link from its cites (render
# swap, item 3) — it renders the memory pass's stored, authored thread_state.arc_line
# VERBATIM, with NO inline edition link. The NL-60 "no dead edition link"
# invariant survives on the story-so-far TIMELINE, whose calendar guard is pinned
# by test_nl63_memory_qa.py::TestServerRenders::test_timeline_calendar_guard_links_only_real_editions.
# (WAS test_arc_prior_date_link_requires_a_real_calendar_date.)


# --- P3a / P4b: the merged control lives in one row under the title ----------

def test_thread_tracked_story_shows_marker_in_the_affordances_row(tmp_paths):
    """Ruling 4 (v7/NL-65 flip): the tracked-ongoing marker sits in the
    under-title control row and a thread-tracked story shows the marker STATE
    there with no separate follow button. WAS: that row was .story-affordances
    (marker + "full picture" merged); NOW: it is the .deck (follow control
    only — NL-65 moved "full picture" to the story bottom)."""
    db.migrate()
    con = db.connect()
    con.execute("INSERT INTO memory (topic, status, created_at, updated_at)"
                " VALUES ('Iran War', 'active', ?, ?)", (iso_now(), iso_now()))
    seed(con, [slot(1, "Iran War", mem=("Iran War",))], [story(1, "Lead")])
    page, _ = server.build_page(con)
    con.close()
    today = page[page.index('id="view-today"'):page.index('id="view-following"')]
    row = today[today.index('class="deck"'):]
    row = row[:row.index('</p>')]               # the deck is a <p class="deck">…</p>
    assert 'class="tracked-marker"' in row      # marker in the under-title deck
    assert 'follow-story-btn' not in row         # no separate follow button
    # ...and the marker no longer floats above the title as its own eyebrow:
    head = today[:today.index('class="deck"')]
    assert 'class="tracked-marker"' not in head


def test_follow_recognized_across_title_drift_both_directions(tmp_paths):
    """P3a, both directions: a story-follow is recognized when EITHER its
    story_title OR its headline matches an active thread (title drifts across
    editions). Fix contract: the OR in _story_affordances' `followed`."""
    db.migrate()
    con = db.connect()
    # Followed thread stored under the HEADLINE phrasing; the slot's story_title
    # has drifted to something else — recognition must still fire.
    con.execute("INSERT INTO memory (topic, status, created_at, updated_at)"
                " VALUES ('the drifted headline', 'active', ?, ?)",
                (iso_now(), iso_now()))
    seed(con, [slot(1, "A Different Story Title", mem=())],
         [story(1, "The Drifted Headline")])
    page, _ = server.build_page(con)
    con.close()
    today = page[page.index('id="view-today"'):page.index('id="view-following"')]
    assert 'class="follow-slot"' in today               # NL-17-M1b single node
    assert 'data-state="committed"' in today            # recognized as followed
    # NL-60 gate F1: the ACTION half — when recognition fired via the headline,
    # data-topic must carry the STORED thread phrasing so unfollow's exact-match
    # dismiss finds the row (display-recognized but unfollowable = the NL-58
    # headline bug surviving inside its own fix).
    assert 'data-topic="The Drifted Headline"' in today


# --- P3b: future "last picked up" degrades to the honest never-state ---------

def test_future_last_picked_up_is_guarded(tmp_paths):
    """P3b: 'last picked up' is the DATE of the last-referenced briefing; a value
    later than today is corruption (a future-dated briefing) and must render as
    'not yet picked up', never as a raw future date. Fix contract: the today
    clamp in _following_rows."""
    from datetime import datetime, timedelta
    future = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
    db.migrate()
    con = db.connect()
    con.execute("INSERT INTO briefings (date, story_slots, corroboration_labels,"
                " narrative_text, generated_at) VALUES (?, '[]', '{}', '', ?)",
                (future, iso_now()))
    bid = con.execute("SELECT id FROM briefings WHERE date=?",
                      (future,)).fetchone()["id"]
    con.execute("INSERT INTO memory (topic, status, last_referenced_briefing_id,"
                " created_at, updated_at) VALUES ('T', 'active', ?, ?, ?)",
                (bid, iso_now(), iso_now()))
    con.commit()
    rows = server._following_rows(con)
    con.close()
    t = next(r for r in rows["active"] if r["topic"] == "T")
    assert t["last"] == ""                 # future date guarded away
    assert t["developing"] is False


# --- P3c: never-picked-up renders as its own honest phrase -------------------

def test_never_picked_up_renders_bare_no_broken_concatenation(tmp_paths):
    """v7-M2 spine: a thread with no pickup and no this-edition delta is a QUIET
    thread — it renders in the counted fold as a bare name link with NO date
    stamp (honest absence), never a fabricated 'picked up' phrase and never the
    old broken 'Last picked up not picked up yet' concatenation.
    WAS test_never_picked_up_copy_is_not_a_broken_concatenation (the dossier
    'Not yet picked up' copy retired with the Spine rebuild)."""
    db.migrate()
    con = db.connect()
    con.execute("INSERT INTO memory (topic, status, created_at, updated_at)"
                " VALUES ('Never', 'active', ?, ?)", (iso_now(), iso_now()))
    con.commit()
    html = server._render_following(con)
    con.close()
    assert "Last picked up not picked up yet" not in html   # the broken form stays gone
    assert "Not yet picked up" not in html                  # the retired dossier copy
    assert ">Never</a>" in html                             # name-as-action link
    fold = html.split('class="quiet-fold"')[1].split("</details>")[0]
    assert "Never" in fold and "LAST UPDATED" not in fold   # no stamp, honest absence


# --- P4c: the collection window is a quiet VISIBLE line -----------------------

def test_coverage_window_line_parsing():
    """Ruling 6: the fetch window surfaces as a plain 'Covers items from X to Y'
    line. Fix contract: _coverage_window_line reads the same footer phrase."""
    assert server._coverage_window_line(
        ["Generated x. Covers items fetched 2026-07-06T09:47 → "
         "2026-07-10T04:43. foo"]) == "Covers items from Jul 6 to Jul 10"
    assert server._coverage_window_line(["no window here"]) == ""
    # NL-60 gate F3: garbage tokens must never render as fake dates on the
    # trust line — both tokens gate through _is_calendar_date, else "".
    # The REAL degraded emission ("window-start una", space inside) already
    # fails the regex — that's tokenization luck, documented here:
    assert server._coverage_window_line(
        ["Generated x. Covers items fetched window-start una → "
         "2026-07-10T04:43. foo"]) == ""
    # ...and this space-free garbage token is the non-vacuous guard case:
    assert server._coverage_window_line(
        ["Generated x. Covers items fetched garbage-token → "
         "2026-07-10T04:43. foo"]) == ""


def test_is_calendar_date_contract():
    """NL-60 gate F2: the helper's FULL contract pinned directly — strptime
    round-trip rejects calendar-invalid AND non-zero-padded (a simplification
    dropping the strftime half would accept '2026-7-6', which /?date=
    regex-rejects to a silent wrong-edition serve)."""
    assert server._is_calendar_date("2026-07-06") is True
    assert server._is_calendar_date("2026-13-45") is False
    assert server._is_calendar_date("2026-7-6") is False
    assert server._is_calendar_date("") is False
    assert server._is_calendar_date("not-a-date") is False


def test_coverage_window_renders_visibly_on_today(tmp_paths):
    """The window is VISIBLE, not only in the tap-away detail. Fix contract:
    the coverage-window <p> in _render_briefing_body's footer-tag. Uses a raw
    narrative whose footer carries the window line (the seed helper's
    window_meta is None, so it emits no window to surface)."""
    db.migrate()
    con = db.connect()
    narrative = ("Intro para.\n---\n**Headline**\n\nThe lede.\n---\n"
                 "*Generated now. Covers items fetched 2026-07-06T09:47 → "
                 "2026-07-10T04:43. NewsLens sees only configured sources.*")
    con.execute("INSERT INTO briefings (date, story_slots, corroboration_labels,"
                " narrative_text, generated_at) VALUES (?, '[]', '{}', ?, ?)",
                (TODAY, narrative, iso_now()))
    con.commit()
    page, _ = server.build_page(con)
    con.close()
    assert 'class="coverage-window"' in page
    assert "Covers items from Jul 6 to Jul 10" in page


# --- P4d: the audio player carries speed + skip controls ---------------------

def test_player_extra_controls_wire_speed_and_skip():
    """Ruling 7: speed (1x/1.25x/1.5x/2x) + skip +/-15s on top of the native
    player. Fix contract: _player_extra_controls emits the buttons; webui.JS
    defines skipAudio/cycleSpeed and toggleEpisodeEl reveals the row."""
    from newslens import webui
    ctl = server._player_extra_controls("episode-player")
    assert 'id="episode-player-extra"' in ctl
    assert "skipAudio('episode-player', -15)" in ctl
    assert "skipAudio('episode-player', 15)" in ctl
    assert "cycleSpeed('episode-player', this)" in ctl
    assert "function skipAudio(" in webui.JS
    assert "function cycleSpeed(" in webui.JS
    assert "AUDIO_SPEEDS = [1, 1.25, 1.5, 2]" in webui.JS
    assert "-extra')" in webui.JS  # toggleEpisodeEl reveals the row


# --- P4e: the spoken caveat is out of the podcast ----------------------------

def test_script_prompt_does_not_request_the_spoken_caveat():
    """Ruling 2: the script prompt no longer asks for the spoken caveat (the
    {spoken_caveat} placeholder is gone) and the transition ban is strengthened
    (P4f). Fix contract: prompts/script_adapt.txt."""
    tmpl = (paths.PROMPTS_DIR / "script_adapt.txt").read_text(encoding="utf-8")
    assert "{spoken_caveat}" not in tmpl
    assert "BANNED OUTRIGHT" in tmpl  # P4f strengthened transition rule
