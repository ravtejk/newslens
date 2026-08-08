"""NL-17 M1 — THE VOCABULARY SURFACES: amendment (i), and cases (a)/(b).

THE RULING THIS FILE ENFORCES. The principal's 2026-08-07 amendment (i) kills
"this story" as an offered follow choice ("too specific and it'll just
complicate data for no reason"). The product council (2026-08-08) read that as
an OFFER-kill, not a DATA-kill — because a $0 instant commit cannot know at tap
time whether anything broader will resolve, so killing the story-seeded LANDING
would mean every tap waits on a model, which the instant-commit law forbids —
and extended it to every seat the phrase held: "'This story' appears nowhere —
not as label, not as qualifier, not as rung."

So there are FOUR seats to check, and this file checks all four plus the door
underneath them:

  RUNG       the acts line's narrow switch          -> deleted, both renderers
  QUALIFIER  a Following row's "— this story"       -> deleted, row goes bare
  LABEL      any committed line                     -> already absent; pinned
  HANDLER    flPickNarrow                           -> deleted
  DOOR       /api/follow/at with altitude='narrow'  -> refused

The door is the part that makes this a data ruling rather than a chrome one. The
council's lifecycle distinction is the whole argument: a SYSTEM-owned seed state
(auto-widenable, uniform, below the vocabulary) is kept; a READER-chosen narrow
altitude that forks a thread's vocabulary forever is banned. Those two write the
same bytes today and differ only in who chose and what may happen next — so
'narrow' stays in STORED_ALTITUDES and leaves PICKABLE_ALTITUDES.

CASES (a) AND (b) ARE BOTH SILENCE, and the 07-18 failure copy is BURIED. The
burial is checked as a residue grep over the shipped source, because a string
that is retired in a comment and alive in a branch is not retired.

Offline by construction: autouse sandbox (conftest), no network, no key, $0.
"""

from __future__ import annotations

import re

import pytest

from newslens import db, labels, memory, paths, server, webui

from test_nl143_follow_surface_truth import _fn, _js_code


THIS_STORY = "this story"


@pytest.fixture()
def con():
    db.migrate(db_path=paths.DB_PATH)
    c = db.connect(paths.DB_PATH)
    yield c
    c.close()


class _AtHandler:
    _with_memory = server.Handler._with_memory
    _ref_id_for = server.Handler._ref_id_for
    _commit_altitude = server.Handler._commit_altitude
    _api_follow_at = server.Handler._api_follow_at

    def __init__(self):
        self.sent = []

    def _send_json(self, obj, status=200):
        self.sent.append((obj, status))
        return obj


# ===========================================================================
# 1 — THE RUNG (both renderers; the single-rendering law makes them twins)
# ===========================================================================

def test_the_narrow_rung_is_gone_from_the_server_acts_line(con):
    """BORN RED. Every settled thread used to carry "this story" as a rung. The
    acts line now carries the named alternative and Unfollow, and nothing else."""
    for alt in (
        {"altitude": "entity", "alt_label": "Volkswagen job cuts"},
        {"altitude": "storyline", "alt_label": "Volkswagen (company)"},
        {"altitude": "entity", "alt_label": ""},          # worded-fallback arm
        {"altitude": "narrow", "alt_label": ""},          # story-seeded
        {"altitude": "", "alt_label": ""},                # unmigrated
    ):
        acts = server._follow_acts_line(alt, "Volkswagen")
        assert THIS_STORY not in acts, alt
        assert "flPickNarrow" not in acts, alt
        assert labels.FOLLOW_UNFOLLOW in acts, alt


def test_the_narrow_rung_is_gone_from_the_client_acts_line():
    """BORN RED. The client twin — and it has to be a twin, or a reload changes
    what the reader is offered (the single-rendering law's whole point)."""
    code = _js_code(_fn("flActsLine"))
    assert "rungThisStory" not in code
    assert "flPickNarrow" not in code


def test_the_narrow_handler_is_deleted_and_nothing_calls_it():
    """BORN RED. An unreachable handler that can still perform a banned write is
    a door, not dead code — the amendment bans the ACT, so the act goes."""
    assert "function flPickNarrow(" not in webui.JS
    assert "flPickNarrow(" not in _js_code(webui.JS)
    assert "flPickNarrow" not in _js_code(webui.CSS)
    import inspect
    assert "flPickNarrow" not in inspect.getsource(server._follow_acts_line)


# ===========================================================================
# 2 — THE QUALIFIER (a Following row's "— this story")
# ===========================================================================

def test_a_story_seeded_row_renders_its_name_bare(con):
    """BORN RED. The QUALIFIER seat. A story-seeded row used to append
    "— this story" to its name; it now renders bare, which is the ruling's own
    copy ("Following <thread name>", storyline name bare) and costs NO new
    string — one was removed, not added."""
    assert server._altitude_qualifier_html({"altitude": "narrow"}) == ""
    assert server._altitude_qualifier_html(
        {"altitude": "narrow", "disclosure": "Volkswagen (company)"}) == ""
    # …and the qualifier that survives is the CLASS parenthetical, unchanged:
    # the seat that was doing real work still does it.
    assert server._altitude_qualifier_html(
        {"altitude": "entity", "disclosure": "Volkswagen (company)"}
    ) == ' <span class="alt-q">(company)</span>'
    assert server._altitude_qualifier_html(
        {"altitude": "storyline", "disclosure": "Volkswagen job cuts"}) == ""


def test_the_phrase_reaches_no_following_surface(con):
    """BORN RED. The whole Following view, rendered over a mixed record: one
    entity follow, one storyline, one story-seeded, one unmigrated. The phrase
    appears on none of them, in any seat."""
    memory.add_thread_at_altitude(
        con, "Volkswagen", altitude="entity",
        disclosure="Volkswagen (company)", alt_label="Volkswagen job cuts",
        source="auto")
    memory.add_thread_at_altitude(
        con, "Volkswagen job cuts", altitude="storyline",
        disclosure="Volkswagen job cuts", source="auto")
    memory.add_thread_at_altitude(con, "Some headline", altitude="narrow",
                                  source="seed")
    memory.add_thread(con, "Old Bare Thread")
    html = server._following_threads_subview(server._following_rows(con))
    assert THIS_STORY not in html
    assert "Some headline" in html            # the row is still there, bare


# ===========================================================================
# 3 — THE DOOR (this is the data half of the ruling)
# ===========================================================================

def test_the_pick_door_refuses_a_reader_chosen_narrow_follow(con, monkeypatch):
    """BORN RED, AND IT IS THE TOOTH THAT MAKES THIS A DATA RULING.

    Deleting the rung removes the OFFER. This removes the ACT: no request — from
    a stale page, a replayed POST, a future renderer — can mint the banned class.
    That is the council's lifecycle distinction enforced rather than described.
    """
    monkeypatch.setattr(memory, "sync_memory", lambda con, **kw: memory.SyncResult())
    h = _AtHandler()
    h._api_follow_at({"name": "Some headline", "altitude": "narrow"})
    payload, status = h.sent[-1]
    assert status == 400 and payload["ok"] is False
    assert con.execute("SELECT COUNT(*) FROM memory").fetchone()[0] == 0


def test_pickable_is_narrower_than_stored_and_the_gap_is_the_ruling():
    """BORN RED. 'narrow' is still STORED — the seed writes it, and the seed is
    the instant-commit law's landing — but it is no longer CHOOSABLE. Pinning
    both tuples together is what states the distinction as a fact rather than a
    comment: same value, opposite lifecycle, and only one of them is an offer."""
    assert memory.STORED_ALTITUDES == ("entity", "storyline", "narrow")
    assert memory.PICKABLE_ALTITUDES == ("entity", "storyline")
    assert "narrow" not in memory.PICKABLE_ALTITUDES
    assert set(memory.PICKABLE_ALTITUDES) < set(memory.STORED_ALTITUDES)


def test_the_seed_still_lands_narrow_because_the_landing_was_never_banned(
        con, monkeypatch):
    """BORN RED (QA F-8 correction, 2026-08-08): the first draft labelled this
    CARRIED-INVARIANT and the HEAD run measured it RED — it asserts
    `memory.entity_id`, the 0024 column, so at HEAD it dies on
    `no such column: entity_id`. The build's own 28-red list already counted it;
    only the docstring was wrong, and it under-sold rather than inflated. Claim
    corrected to the measurement.

    It is the other half of the ruling — the half a zealous reading would break.
    The council ruled OFFER-kill, not DATA-kill: a tap still commits a
    story-seeded thread instantly, $0, with nothing consulted. If this ever goes
    red for a REASON OTHER than a missing column, the fix went too far."""
    monkeypatch.setattr(memory, "sync_memory", lambda con, **kw: memory.SyncResult())
    monkeypatch.setattr(memory, "write_memory_file", lambda con: 0)

    class _H:
        _topic_arg = server.Handler._topic_arg
        _raw_topic_arg = server.Handler._raw_topic_arg
        _with_memory = server.Handler._with_memory
        _ref_id_for = server.Handler._ref_id_for
        _commit_altitude = server.Handler._commit_altitude
        _api_follow_seed = server.Handler._api_follow_seed
        _seed_thread = server.Handler._seed_thread

        def __init__(self):
            self.sent = []

        def _send_json(self, obj, status=200):
            self.sent.append((obj, status))
            return obj

    h = _H()
    h._api_follow_seed({"topic": "Some headline", "origin": "Some headline"})
    out, _ = h.sent[-1]
    assert out["state"] == "committed" and out["seeded"] is True
    row = con.execute("SELECT altitude, altitude_source, entity_id"
                      " FROM memory").fetchone()
    assert row["altitude"] == "narrow" and row["altitude_source"] == "seed"
    assert row["entity_id"] is None      # first-class: no broader concept (yet)


# ===========================================================================
# 4 — CASE (b): THE 07-18 FAILURE COPY IS BURIED
# ===========================================================================

def test_the_failure_copy_is_retired_and_reaches_no_render_path():
    """BORN RED as a RESIDUE GREP. The ruling in case (b) is silence — no
    string at all on a failed or timed-out settle. The 07-18 copy was already
    dead twice (v11 rulings ①/④); amendment (i) makes the burial a ruling of
    record, and a burial is only real if no branch can still reach the body.

    Checked against the SHIPPED source of every module that can render, not
    against labels.py's own comments — a constant marked retired beside a live
    call site is not retired."""
    import inspect
    for mod in (server, webui):
        src = inspect.getsource(mod)
        for name in ("FOLLOW_DEGRADE_LEAD", "FOLLOW_DEGRADE_UPGRADE",
                     "FOLLOW_DEGRADE_COMMITTED", "FOLLOW_CAP_REFUSAL"):
            assert name not in src, (mod.__name__, name)
    # the literal prose, in the client's own label table and its JS
    for dead in ("Couldn't fetch broader follow", "choose it anytime",
                 "Following — this story"):
        assert dead not in webui.JS, dead
    payload = server._nl_labels_js()
    for dead in ("Couldn't fetch broader follow", "choose it anytime",
                 THIS_STORY):
        assert dead not in payload, dead


def test_the_client_label_table_cannot_render_the_phrase():
    """BORN RED. Removing the two keys from the injected table is not cosmetic:
    it is what makes the client STRUCTURALLY unable to render the phrase, so the
    burial cannot be undone by a stray branch reading a key that is still there.
    """
    payload = server._nl_labels_js()
    assert '"narrow"' not in payload and '"rungThisStory"' not in payload
    assert "NL_LABELS.narrow" not in _js_code(webui.JS)
    assert "NL_LABELS.rungThisStory" not in _js_code(webui.JS)


def test_the_retired_constants_are_kept_named_not_deleted():
    """CARRIED-INVARIANT of the retired-but-named convention (KICKER_LEAD
    precedent): the ruled strings stay on the record and nothing imports a
    dangling name. Deleting them would erase what was decided."""
    assert labels.FOLLOW_NARROW == THIS_STORY
    assert labels.FOLLOW_RUNG_THIS_STORY == THIS_STORY
    assert labels.FOLLOW_DEGRADE_UPGRADE.startswith("Couldn't fetch")


# ===========================================================================
# 5 — CASE (a): the terminal-none label, and what still renders
# ===========================================================================

def test_a_terminal_none_thread_keeps_the_object_seat_deictic(con):
    """CARRIED-INVARIANT (born green) — the two-referent noun law's surviving
    half (TAXONOMY §1.1). Amendment (i) empties the SCOPE seat; the OBJECT seat
    still takes `thread`, and the committed line for a story-seeded thread still
    reads "● Following — this thread". Pinned because "kill this story" is one
    careless grep away from also killing "this thread", which is a different
    word in a different seat."""
    inner = server._committed_verb_inner({"altitude": "narrow"})
    assert labels.FOLLOW_THREAD_SELF in inner
    assert THIS_STORY not in inner
    assert labels.FOLLOW_STEADY_PREFIX in inner


def test_a_settled_storyline_renders_its_name_bare_everywhere(con):
    """CARRIED-INVARIANT (born green) — measured, not assumed. The first draft
    of this docstring claimed BORN RED for the row and the HEAD run said
    otherwise: a descriptive storyline's disclosure carries no "(class)", so the
    pre-diff qualifier already returned bare through its `if cls:` arm and never
    reached the narrow branch. The claim is corrected rather than the
    measurement (born-red is proof-class currency — gate ruling 2026-07-18).

    It stays because it pins the case-(a) COPY in full — "Following <thread
    name>", storyline name bare — on a path the vocabulary work runs straight
    through."""
    memory.add_thread_at_altitude(
        con, "Volkswagen job cuts", altitude="storyline",
        disclosure="Volkswagen job cuts", source="auto")
    html = server._following_threads_subview(server._following_rows(con))
    assert "Volkswagen job cuts" in html
    assert 'class="alt-q"' not in html
    inner = server._committed_verb_inner(
        {"altitude": "storyline", "disclosure": "Volkswagen job cuts"})
    assert "Volkswagen job cuts" in inner and "(" not in inner
