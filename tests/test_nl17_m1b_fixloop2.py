"""NL-17-M1b — FIX LOOP 2 born-red proofs (re-QA NO-GO 2026-07-18, loop 2 of 3).

FIX-LOOP BASELINE — "BORN RED" here means red against the CURRENT working tree
(the full picker build c7338d8 + uncommitted + fix-loop-1) MINUS this fix loop.
They are NOT red against pristine HEAD. Each flips only when its named fix lands.
Some proofs are red by ERROR pre-fix (a kwarg / attribute / unique-index that
does not exist yet) — labelled inline; the rest are red by assertion. The JS
pins are STRUCTURAL source pins on webui.JS — live keyboard / real-browser
verification is QA's re-check (dispatch: "as behavioral as the harness allows").

  R3  interactive resolve degrades on the FIRST timeout — the timeout class does
      NOT consume the corrected retry (a reader is waiting; a second window pins
      the follow-line at ~25s). The BATCH falsifier path keeps its transport
      retry. Control-flow pin (spy counter); QA re-times live.
  R2  a switch onto a name held by a DISMISSED row revive-merges the collision
      (ONE active row, coherent events) instead of a silent 500 on the 0005
      unique index; the client SURFACES a refusal instead of swallowing it.
  R1  after unfollow->refollow the re-tap resolves the STORY (the card's
      canonical topic, stamped as data-story) and stores the canonical origin —
      not the stale STORED follow name the committed render left in data-topic.

Offline by construction (conftest sandbox — no network, no real key, per-test DB).
"""

from __future__ import annotations

import inspect
import re
import types

import pytest

from newslens import (db, follow_altitude as fa, labels, llm, memory, paths,
                      server, webui)


# ---------------------------------------------------------------------------
# harness — mirrors test_nl17_m1b_fixloop1.py (drive the real follow endpoints
# through a lightweight Handler double; _send_json is captured, not socket-ed).
# ---------------------------------------------------------------------------

class _FollowHandler:
    _topic_arg = server.Handler._topic_arg
    _with_memory = server.Handler._with_memory
    _ref_id_for = server.Handler._ref_id_for
    _commit_altitude = server.Handler._commit_altitude
    _api_follow_resolve = server.Handler._api_follow_resolve
    _api_follow_at = server.Handler._api_follow_at
    _api_dismiss = server.Handler._api_dismiss

    def __init__(self):
        self.sent = []

    def _send_json(self, obj, status=200):
        self.sent.append((obj, status))
        return obj


def _seq_resolver(calls, specs):
    """A stub follow_altitude.resolve_altitude: records each call's subject and
    returns the next canned AltitudeResult-shaped object. $0 — no provider."""
    seq = list(specs)
    def _resolve(thread, **kwargs):
        calls.append(getattr(thread, "topic", None))
        spec = seq.pop(0) if seq else specs[-1]
        return types.SimpleNamespace(**spec)
    return _resolve


_ENTITY = dict(confidence="high", altitude="entity", primary_entity="Volkswagen",
               disclosure="Volkswagen (company)", alt_label="Volkswagen job cuts")


def _quiet_memory(monkeypatch):
    monkeypatch.setattr(memory, "sync_memory", lambda con: None)
    monkeypatch.setattr(memory, "write_memory_file", lambda con: None)


def _fresh_db():
    db.migrate(db_path=paths.DB_PATH)
    return db.connect(paths.DB_PATH)


def _fn_body(js: str, name: str) -> str:
    """The source of JS function `name` from webui.JS (the flat function table),
    from `function name(` to the next top-level `\\nfunction ` (or end)."""
    i = js.index("function " + name + "(")
    j = js.find("\nfunction ", i + 1)
    return js[i:(j if j != -1 else len(js))]


def _data_attr(html: str, name: str) -> str:
    m = re.search(r'data-' + name + r'="([^"]*)"', html)
    return m.group(1) if m else ""


# ===========================================================================
# R3 — the interactive resolve degrades on the FIRST timeout window
# ===========================================================================

def _timeout_spy(calls):
    def _spy(req):
        calls["n"] += 1
        raise TimeoutError(
            "claude -p (follow_altitude/claude-haiku-4-5) exceeded 12s "
            "— the child was killed")
    return _spy


def test_r3_interactive_timeout_does_not_consume_the_retry(monkeypatch):
    """The INTERACTIVE path (retry_transport=False) degrades on the FIRST provider
    timeout: exactly ONE provider call, then AltitudeError (the server degrade).
    BORN-RED (by ERROR): build-minus-loop's resolve_altitude has no
    retry_transport kwarg, and on the interactive path a timeout is caught as a
    generic transport error and RETRIED -> two calls / ~25s."""
    calls = {"n": 0}
    monkeypatch.setattr(llm, "chat", _timeout_spy(calls))
    monkeypatch.setattr(fa.time, "sleep", lambda *a, **k: None)   # no wall-clock
    with pytest.raises(fa.AltitudeError):
        fa.resolve_altitude(fa.ThreadInput(None, "Volkswagen job cuts"),
                            retry_transport=False)
    assert calls["n"] == 1        # ONE timeout window — degrade fires directly


def test_r3_batch_default_keeps_the_transport_retry(monkeypatch):
    """CARRIED-INVARIANT: the BATCH falsifier path (the default) still rides out a
    timeout with its corrected retry — two provider calls. The fix is scoped to
    the interactive opt-out, never a blanket no-retry that would make an
    unattended run false-fail on one transient."""
    calls = {"n": 0}
    monkeypatch.setattr(llm, "chat", _timeout_spy(calls))
    monkeypatch.setattr(fa.time, "sleep", lambda *a, **k: None)
    with pytest.raises(fa.AltitudeError):
        fa.resolve_altitude(fa.ThreadInput(None, "Volkswagen job cuts"))  # default
    assert calls["n"] == 2        # two attempts — batch retry stands


def test_r3_interactive_entry_opts_out_of_transport_retry():
    """The interactive server entry (_api_follow_resolve) passes
    retry_transport=False, so a stuck resolve degrades in one window not two.
    BORN-RED: pre-fix the call site takes no such kwarg."""
    src = inspect.getsource(server.Handler._api_follow_resolve)
    assert "retry_transport=False" in src


# ===========================================================================
# R2 — the switch-collision revive-merge (no silent 500) + client surfacing
# ===========================================================================

def test_r2_switch_onto_dismissed_name_revive_merges(monkeypatch):
    """Switching an active follow onto a name held by a DISMISSED row revive-merges
    the collision instead of 500-ing on the 0005 unique index: ONE active row
    after, at the picked rung, with coherent events (a 'correct' off the old rung
    + a 'commit' at the new). BORN-RED (by ERROR): build-minus-loop's topic UPDATE
    hits the unique index and raises IntegrityError."""
    db.migrate(db_path=paths.DB_PATH)
    _quiet_memory(monkeypatch)
    con = db.connect(paths.DB_PATH)
    try:
        memory.add_thread_at_altitude(
            con, "Volkswagen", altitude="entity", primary_entity="Volkswagen",
            disclosure="Volkswagen (company)", alt_label="Volkswagen job cuts",
            confidence="high", source="auto",
            origin_story="Volkswagen plans significant job cuts")
        memory.add_thread_at_altitude(
            con, "Volkswagen job cuts", altitude="storyline",
            disclosure="Volkswagen job cuts", source="pick")
        memory.dismiss_thread(con, "Volkswagen job cuts")   # the dismissed holder
    finally:
        con.close()

    h = _FollowHandler()
    h._api_follow_at({                                      # the "Instead" switch
        "name": "Volkswagen job cuts", "altitude": "storyline",
        "disclosure": "Volkswagen job cuts", "alt_label": "Volkswagen (company)",
        "from_topic": "Volkswagen"})
    assert h.sent[-1][0].get("ok") is True                 # no 500
    assert h.sent[-1][0].get("state") == "committed"

    con = db.connect(paths.DB_PATH)
    try:
        active = con.execute(
            "SELECT topic, altitude FROM memory WHERE status='active'"
            " AND lower(topic) IN ('volkswagen', 'volkswagen job cuts')"
        ).fetchall()
        assert len(active) == 1                            # ONE active row
        assert active[0]["topic"] == "Volkswagen job cuts"
        assert active[0]["altitude"] == "storyline"
        kinds = [r["kind"] for r in con.execute(
            "SELECT kind FROM follow_altitude_events ORDER BY id").fetchall()]
        assert "correct" in kinds and kinds[-1] == "commit"   # coherent log
    finally:
        con.close()


def test_r2_switch_collision_rolls_back_clean_on_the_dismissed(monkeypatch):
    """The dismissed holder's identity is resolved coherently: after the merge the
    ONLY row carrying the storyline name is active (no dismissed duplicate left to
    re-collide). BORN-RED (by ERROR): the collision raises pre-fix."""
    db.migrate(db_path=paths.DB_PATH)
    _quiet_memory(monkeypatch)
    con = db.connect(paths.DB_PATH)
    try:
        memory.add_thread_at_altitude(
            con, "Volkswagen", altitude="entity", source="auto",
            disclosure="Volkswagen (company)",
            origin_story="Volkswagen plans significant job cuts")
        memory.add_thread_at_altitude(
            con, "Volkswagen job cuts", altitude="storyline",
            disclosure="Volkswagen job cuts", source="pick")
        memory.dismiss_thread(con, "Volkswagen job cuts")
    finally:
        con.close()
    h = _FollowHandler()
    h._api_follow_at({"name": "Volkswagen job cuts", "altitude": "storyline",
                      "disclosure": "Volkswagen job cuts", "from_topic": "Volkswagen"})
    con = db.connect(paths.DB_PATH)
    try:
        rows = con.execute(
            "SELECT status FROM memory WHERE lower(topic) = 'volkswagen job cuts'"
        ).fetchall()
        assert len(rows) == 1 and rows[0]["status"] == "active"   # merged, not duped
    finally:
        con.close()


def test_r2_flswitch_surfaces_a_refused_switch():
    """A server refusal on the switch is SURFACED, never silently swallowed (the
    reader's tap did nothing forever). flSwitch routes ok===false to a visible
    register line; the failure renderer + its label exist and are injected into
    the client label table. BORN-RED: pre-fix flSwitch's callback is
    `if (!d || d.ok === false) return;` (silent no-op)."""
    body = _fn_body(webui.JS, "flSwitch")
    assert "flSwitchFailed" in body
    assert "function flSwitchFailed" in webui.JS
    assert "switchFailed" in webui.JS                      # the NL_LABELS key
    assert labels.FOLLOW_SWITCH_FAILED                     # label defined
    assert "FOLLOW_SWITCH_FAILED" in inspect.getsource(server._nl_labels_js)


# ===========================================================================
# R1 — a re-tap after unfollow resolves the STORY, not the stale follow name
# ===========================================================================

def test_r1_server_stamps_data_story_canonical_topic():
    """The server stamps the card's canonical STORY topic as data-story on the
    follow-slot (resting AND committed), so the client can restore data-topic to
    the STORY after an unfollow. On a committed altitude-renamed follow, data-topic
    is the STORED name while data-story is the canonical topic. BORN-RED: no
    data-story attribute at build-minus-loop."""
    con = _fresh_db()
    try:
        memory.add_thread_at_altitude(
            con, "Volkswagen", altitude="entity", primary_entity="Volkswagen",
            disclosure="Volkswagen (company)", source="auto",
            origin_story="Volkswagen job cuts")
        committed = server._follow_control(
            {"headline": "Volkswagen plans significant job cuts"},
            {"story_title": "Volkswagen job cuts"}, [], {"volkswagen"},
            "2026-07-18", slug="s1", con=con)
        assert 'data-topic="Volkswagen"' in committed         # STORED name
        assert 'data-story="Volkswagen job cuts"' in committed  # canonical topic
        resting = server._follow_control(
            {"headline": "A fresh headline"}, {"story_title": "A fresh story"},
            [], set(), "2026-07-18", slug="s2", con=con)
        assert 'data-story="A fresh story"' in resting
    finally:
        con.close()


def test_r1_flrenderresting_restores_data_topic_from_data_story():
    """The client's flRenderResting restores data-topic from data-story, so a
    re-tap after unfollow resolves the STORY, not the stale stored follow name.
    Structural JS pin (live keyboard verification is QA's re-check). BORN-RED:
    pre-fix flRenderResting reads no data-story and never re-stamps data-topic."""
    body = _fn_body(webui.JS, "flRenderResting")
    assert "flDA(slot, 'story')" in body
    assert "setAttribute('data-topic'" in body


def test_r1_retap_after_unfollow_resolves_the_story(monkeypatch):
    """END-TO-END (as behavioral as the harness allows): follow a story whose
    altitude-renamed follow is stored under the resolver name, unfollow it, then
    re-tap. The re-tap SUBJECT is derived exactly as the fixed client derives it
    (data-story || data-topic). The second resolve must run on the STORY, and the
    re-follow must bridge back to this card on a re-render. BORN-RED: pre-fix there
    is no data-story attr, so the derived subject is the stale follow name and the
    second resolve runs on 'Volkswagen', not the story."""
    db.migrate(db_path=paths.DB_PATH)
    _quiet_memory(monkeypatch)
    calls = []
    monkeypatch.setattr(fa, "resolve_altitude",
                        _seq_resolver(calls, [_ENTITY, _ENTITY]))
    story = "Volkswagen job cuts"          # the card's canonical topic (story_title)
    headline = "Volkswagen plans significant job cuts"

    h = _FollowHandler()
    h._api_follow_resolve({"topic": story, "origin": headline})   # tap 1
    assert calls[-1] == story              # first resolve ran on the story

    con = db.connect(paths.DB_PATH)
    try:
        active = server._active_topics_lower(con)
        committed = server._follow_control(
            {"headline": headline}, {"story_title": story}, [], active,
            "2026-07-18", slug="story-1", con=con)
    finally:
        con.close()
    assert 'data-topic="Volkswagen"' in committed        # STORED name (renamed)

    # unfollow the stored follow (data-topic), then re-tap
    h._api_dismiss({"topic": _data_attr(committed, "topic")})   # "Volkswagen"
    retap_subject = _data_attr(committed, "story") or _data_attr(committed, "topic")
    h._api_follow_resolve({"topic": retap_subject, "origin": headline})   # tap 2
    assert calls[-1] == story              # RED pre-fix: the stale "Volkswagen"

    con = db.connect(paths.DB_PATH)
    try:
        active = server._active_topics_lower(con)
        rerender = server._follow_control(
            {"headline": headline}, {"story_title": story}, [], active,
            "2026-07-18", slug="story-1", con=con)
        # the canonical origin bridges the card back to committed after re-render
        assert 'data-state="committed"' in rerender
        bridged = server._origin_follow_row(con, story, headline)
        assert bridged and bridged.get("origin_story", "").lower() in (
            story.lower(), headline.lower())
    finally:
        con.close()
