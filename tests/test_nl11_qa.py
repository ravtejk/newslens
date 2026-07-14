"""NL-11 (UI/UX v2) adversarial QA pass — QA-owned companion to the
implementer's pins in test_server.py / test_ui_polish.py / test_backlog_qa.py.

Offline, sandboxed (autouse conftest: fake DB path, synthetic sources,
sandboxed memory.md, loopback-only network), zero real-DB reads. Teeth target
the dispatch's adversarial seams the implementer's own pins did not reach:

  * XSS fuzz through the /edition fragment (innerHTML-injected client-side, so
    an executing event-handler attribute or a </script> breakout WOULD run)
    and through the writer suggestion <script> payload — the topic payload and
    the Today headline are already pinned elsewhere; these are the untested
    twins.
  * Follow/thread coexistence at the render boundary: story_title == an active
    thread with NO matched_memory (the button must read PRESSED, matched
    case-insensitively) and the empty-title fallback; plus the case-insensitive
    UNFOLLOW that the pressed button drives.
  * /edition read-count honesty under archive rapid-fire — one raw read per
    genuine serve (the metric dedups; the rows are raw truth), future/absent/
    bad-calendar dates log nothing, and date-scoped deep-view ids don't collide.
  * Suggestion exclusion tracks LIVE state (case-variant + after a topic_add).
  * The mobile/touch pick path (onmousedown + preventDefault + blur timeout).

All GREEN: every behavior below traced as correctly wired; these freeze it.
If a future refactor unwires any of them the offending test bites (its
docstring carries the fix contract), per the KNOWN-RED convention.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from newslens import config, db, paths, ranking, server, webui

# Live-loopback harness + seed helpers (reused, single source of truth).
from test_server import ui, get, post, seed_briefing, event_rows, replica
# In-process build_page seeding (its slot/story/seed pin the NL-11 default-view
# TODAY behavior and the assemble->parse render path).
from test_ui_polish import slot, story, seed, TODAY, iso_now


# ---------------------------------------------------------------------------
# Local seeding — a past-dated edition whose headline is driven by the
# structured generation-log entry (so a hostile headline renders verbatim
# through the M7+ structured path, not the markdown fallback).
# ---------------------------------------------------------------------------

def _seed_edition(con, date, story_title, headline, matched_memory=()):
    slots = [{
        "slot": 1, "story_title": story_title, "summary": "S.", "item_ids": [1],
        "outlets": ["Outlet A"], "matched_tags": [],
        "matched_memory": list(matched_memory), "matched_dormant": [],
        "followed_analyst": False, "personal_score": 0.0, "world_impact": 6,
        "world_impact_reason": "R", "combined_score": 0.5, "override": False,
        "override_label": None, "corroboration_count": 1,
        "corroboration_label": "Reported by 1 named outlet",
        "wire_items_excluded": 0, "revived_threads": [],
    }]
    stories = [{
        "tier": "full", "headline": headline, "lede": "The lede.",
        "why_it_matters": "Effects.", "watch_for": "The vote.",
        "why_label": "Why it matters", "watch_label": "Watch for",
        "my_read": None,
    }]
    from newslens import generate
    inputs = {"slots": slots, "items_by_slot": {1: []}, "threads": [],
              "prior_ctx": None, "continuity_status": "none",
              "window_meta": None, "corroboration": {}}
    narrative = generate.assemble_narrative(date, "A", stories, inputs)
    con.execute(
        "INSERT INTO briefings (date, story_slots, corroboration_labels,"
        " narrative_text, generated_at) VALUES (?, ?, ?, ?, ?)",
        (date, json.dumps(slots),
         json.dumps({"standing_caveat": ranking.CORROBORATION_CAVEAT,
                     "per_story": []}),
         narrative, iso_now()),
    )
    con.commit()
    entry = {"date": date, "variant": "A", "sample": False, "status": "ok",
             "stories": stories}
    log = paths.DATA_DIR / "generation_log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ===========================================================================
# Seam 1 — XSS fuzz (the innerHTML-injected surfaces)
# ===========================================================================

def test_render_story_neutralizes_hostile_headline_title_lede_movement(tmp_paths):
    """_render_story is the shared body renderer for Today AND the archive-in-
    place edition; the edition path injects its output with innerHTML, so a
    live <img onerror>/<svg onload> or a </script> break in any dynamic field
    would execute. Every field must render through _e/_e_attr. Fix contract if
    this bites: escape the offending interpolation at its source in
    _render_story (never trust prose/tags/titles from the ranked web)."""
    hostile = 'x"><img src=x onerror=alert(1)>'
    head = '</script><svg onload=alert(2)>'
    html = server._render_story(
        0,
        {"headline": head, "lede": hostile,
         "movements": [{"label": hostile, "text": hostile}]},
        {"story_title": hostile, "matched_memory": []},
        "full", set(), date="2020-01-02")
    # No live element ever forms:
    assert "<img" not in html and "<svg" not in html
    assert "<script" not in html and "</script>" not in html
    # The hostile strings survive — faithfully, but inert (escaped):
    assert "&lt;/script&gt;&lt;svg onload=alert(2)&gt;" in html   # headline
    assert "&lt;img src=x onerror=alert(1)&gt;" in html           # lede/movement
    # The follow-button attribute can't break out of its quotes:
    assert 'data-topic="x&quot;&gt;&lt;img src=x onerror=alert(1)&gt;"' in html
    assert 'x"><img' not in html                                   # raw breakout absent


def test_edition_fragment_neutralizes_hostile_fields_when_served(tmp_paths):
    """Seam 1 end-to-end: build_edition_fragment (the /edition body the client
    drops into #edition-mount via innerHTML) must emit no executable injection
    for a hostile story_title (slot) or headline (structured log). Same fix
    contract as above — the fragment shares _render_story/_render_briefing_body.
    """
    db.migrate()
    con = db.connect()
    _seed_edition(con, "2020-01-02",
                  story_title='x"><img src=x onerror=alert(9)>',
                  headline='</script><svg onload=alert(8)>')
    html, rendered = server.build_edition_fragment(con, "2020-01-02")
    con.close()
    assert rendered == "2020-01-02"
    assert "<!DOCTYPE html>" not in html                  # a fragment, not a page
    assert 'id="ed2020-01-02-story-0"' in html            # date-scoped id present
    # nothing executable survives innerHTML injection:
    assert "<img" not in html and "<svg" not in html
    assert "<script" not in html and "</script>" not in html
    assert "&lt;svg onload=alert(8)&gt;" in html          # headline escaped, faithful
    assert 'data-topic="x&quot;&gt;&lt;img src=x onerror=alert(9)&gt;"' in html
    assert 'x"><img' not in html


def test_writer_suggestion_payload_escapes_hostile_recalled_names(tmp_paths):
    """Seam 1: the writer suggestion field embeds a JSON payload in a
    <script class="suggest-data"> element; a hostile recalled writer name must
    not close that element. (test_backlog_qa pins the TOPIC payload; writers
    carry the extra outlet field and the greedy-paren split, so they get their
    own pin.) Fix contract if this bites: harden the <>&->\\u00XX escape in
    server._render_suggest."""
    hostile = '</script><img src=x onerror=alert(1)>'
    # A "Pub (Name)" source, not followed -> enters suggestions via the paren
    # split with the hostile string as the NAME (the field a pick fills):
    src = SimpleNamespace(name='Real Pub (' + hostile + ')', followed_analyst=False)
    cfg = SimpleNamespace(sources=[src], followed_analyst_sources=[],
                          interests_broad=[], interests_granular=[])
    sugg = server._writer_suggestions(cfg)
    assert sugg and sugg[0]["v"] == hostile                # recalled faithfully...
    html = server._render_suggest("writer", "writer-suggest", "p", "a", sugg)
    payload = html.split('class="suggest-data">')[1].split("</script>")[0]
    assert "<" not in payload and ">" not in payload       # ...no raw angle brackets
    assert "\\u003c" in payload                            # hostile '<' encoded
    assert "<img src=x onerror=" not in html               # no executable form anywhere


# ===========================================================================
# Seam 2 — follow/thread coexistence at the render boundary
# ===========================================================================

def test_coexistence_title_equals_active_thread_without_matched_memory(tmp_paths):
    """The exact edge: a story whose story_title equals an ACTIVE thread but
    carries NO matched_memory. It must NOT show the tracked marker (that is
    reserved for a real matched_memory attribution); it shows the follow
    button, and because the title matches an active thread — matched
    case-insensitively (active_topics is lowercased) — the button reads the
    PRESSED 'Following this story' state and un-follows on tap. Fix contract if
    this bites: the `not marks` branch in _story_affordances sets pressed from
    `topic.lower() in active_topics or headline.lower() in active_topics`."""
    db.migrate()
    con = db.connect()
    # stored thread casing DIFFERS from the story title -> proves the match is
    # case-insensitive on the render side, not just the verb side:
    con.execute("INSERT INTO memory (topic, status, created_at, updated_at)"
                " VALUES ('iran war', 'active', ?, ?)", (iso_now(), iso_now()))
    seed(con, [slot(1, "Iran War", mem=())], [story(1, "Distinct Headline")])
    page, rendered = server.build_page(con)
    con.close()
    assert rendered == TODAY
    today = page[page.index('id="view-today"'):page.index('id="view-following"')]
    assert 'class="tracked-marker"' not in today       # no matched_memory -> no marker
    assert 'class="follow-story-btn' in today           # the merged follow control
    assert 'class="deck"' in today                      # v7/NL-65: under-title control row
    assert 'aria-pressed="true"' in today              # ...in the followed state
    assert "Following this story" in today
    assert 'data-topic="Iran War"' in today            # the story's own casing travels


def test_coexistence_unfollow_dismisses_the_case_insensitive_thread(ui):
    """The pressed button (above) sends the STORY's casing to /api/unfollow;
    the dismiss must resolve the differently-cased thread or the button would
    snap back and the thread would silently survive. Fix contract if this
    bites: memory.dismiss_thread matches `lower(topic)=lower(?)`."""
    post(ui, "/api/follow", {"topic": "Iran War"})          # writes memory.md
    code, obj = post(ui, "/api/unfollow", {"topic": "IRAN WAR"})  # different case
    assert obj == {"ok": True}
    con = db.connect()
    try:
        status = con.execute(
            "SELECT status FROM memory WHERE lower(topic)='iran war'"
        ).fetchone()["status"]
    finally:
        con.close()
    assert status == "dismissed_user"


def test_coexistence_empty_story_title_falls_back_to_headline(tmp_paths):
    """A degenerate slot with an empty story_title must not emit a blank-topic
    follow button: topic falls back to the headline (`story_title or headline
    or ""`). No crash, no data-topic="" affordance for a story that has a
    headline. Fix contract if this bites: the fallback chain in the
    _story_affordances `not marks` branch."""
    db.migrate()
    con = db.connect()
    seed(con, [slot(1, "", mem=())], [story(1, "Fallback Headline")])
    page, _ = server.build_page(con)
    con.close()
    today = page[page.index('id="view-today"'):page.index('id="view-following"')]
    assert 'class="follow-story-btn' in today
    assert 'data-topic="Fallback Headline"' in today   # headline, never a blank topic
    assert 'data-topic=""' not in today


# ===========================================================================
# Seam 3 — /edition read-count honesty under archive rapid-fire
# ===========================================================================

def test_edition_rapidfire_logs_one_raw_read_per_genuine_serve(ui):
    """Opening several editions quickly (and re-opening one) logs exactly one
    RAW read per genuine serve — reads are raw truth (ADR-0010: the day-30
    metric dedups by distinct day, the table does not). Each fragment carries
    its own date-scoped story ids, so nothing collides across the burst. Fix
    contract if this bites: _edition logs only when build_edition_fragment
    returns a real date, and the slug_prefix is `ed{date}-`."""
    con = db.connect()
    for d in ("2020-01-01", "2020-01-02", "2020-01-03"):
        seed_briefing(con, date=d)
    con.close()
    served = ["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-02"]  # reopen one
    for d in served:
        code, _, body = get(ui, "/edition?date=" + d)
        assert code == 200
        frag = body.decode("utf-8")
        assert 'id="ed%s-story-0"' % d in frag          # its own date-scoped id
    con = db.connect()
    try:
        rows = [(r["date"], r["kind"]) for r in event_rows(con)]
    finally:
        con.close()
    # four genuine serves -> four raw reads, in serve order, one per serve:
    assert rows == [(d, "read") for d in served]


def test_edition_future_absent_and_bad_calendar_dates_log_no_read(ui):
    """Every non-serve path must log nothing: a well-formed FUTURE date and a
    well-formed-but-impossible calendar date both miss the briefings table and
    render the honest 'unavailable' fragment; an absent ?date= is a 400. None
    is a read. Fix contract if this bites: build_edition_fragment returns
    (html, None) with no row, and _edition guards `if rendered`."""
    code1, _, body1 = get(ui, "/edition?date=2999-12-31")   # future, well-formed
    assert code1 == 200 and "unavailable" in body1.decode("utf-8")
    code2, _, body2 = get(ui, "/edition?date=2026-13-99")   # matches regex, no such day
    assert code2 == 200 and "unavailable" in body2.decode("utf-8")
    code3, _, _ = get(ui, "/edition")                       # absent param
    assert code3 == 400
    con = db.connect()
    try:
        assert event_rows(con) == []                        # nothing served -> no read
    finally:
        con.close()


# ===========================================================================
# Seam 4 — suggestion exclusion tracks LIVE state
# ===========================================================================

def test_topic_suggestion_exclusion_is_case_insensitive(tmp_paths):
    """A matched coverage tag whose casing differs from a followed interest is
    still excluded — the exclusion lowercases both sides. Fix contract if this
    bites: `name.lower() not in followed` in server._topic_suggestions with a
    lowercased `followed`."""
    db.migrate()
    con = db.connect()
    con.execute(
        "INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
        ("2026-07-01", json.dumps([{"slot": "1", "matched_tags":
            [{"name": "Fusion Power"}, {"name": "Helium Shortage"}]}])))
    con.commit()
    cfg = SimpleNamespace(interests_broad=["fusion power"], interests_granular=[],
                          sources=[], followed_analyst_sources=[])
    sugg = {o["v"] for o in server._topic_suggestions(con, cfg)}
    con.close()
    assert "Fusion Power" not in sugg          # excluded despite case diff vs interest
    assert "Helium Shortage" in sugg           # unfollowed tag still offered


def test_topic_suggestion_exclusion_tracks_live_state_after_add(replica):
    """The exclusion must reflect LIVE follow state, not page-load state: after
    topic_add mutates sources.yaml, a fresh render (config.load_sources() +
    _topic_suggestions, exactly what reloadPreservingView triggers) drops the
    newly-followed topic. Fix contract if this bites: _topic_suggestions reads
    the passed cfg, and every verb reloads through reloadPreservingView."""
    db.migrate()
    con = db.connect()
    con.execute(
        "INSERT INTO briefings (date, story_slots) VALUES (?, ?)",
        ("2026-07-01", json.dumps([{"slot": "1", "matched_tags":
            [{"name": "Helium Shortage"}]}])))
    con.commit()
    before = {o["v"] for o in server._topic_suggestions(con, config.load_sources())}
    assert "Helium Shortage" in before                    # not yet followed -> offered
    ok, msg = server.topic_add("Helium Shortage", "broad")
    assert ok, msg
    after = {o["v"] for o in server._topic_suggestions(con, config.load_sources())}
    con.close()
    assert "Helium Shortage" not in after                 # now followed -> excluded (live)


# ===========================================================================
# Seam 7 — the mobile/touch pick path (structural pin; device is NL-58)
# ===========================================================================

def test_touch_pick_path_uses_onmousedown_preventdefault_and_blur_timeout():
    """A suggestion option commits on onmousedown (fires before the input's
    blur), suggestPick calls preventDefault so the blur-hide can't cancel the
    pick, and suggestBlur hides the list on a short timeout so a tap still
    lands first. This is the pointer/touch heuristic; the real tap behavior on
    a device is the principal's to confirm (NL-58). Fix contract if this bites:
    keep the option's handler on onmousedown, not onclick."""
    js = webui.JS
    assert 'onmousedown="suggestPick(event,this)"' in js       # commit before blur
    pick = js.split("function suggestPick", 1)[1].split("\nfunction ", 1)[0]
    assert "e.preventDefault()" in pick                        # mousedown beats blur
    blur = js.split("function suggestBlur", 1)[1].split("\nfunction ", 1)[0]
    assert "setTimeout" in blur                                # hide AFTER the pick lands
