"""NL-17 M1 — F-4 (the refused mount keeps its control) and F-6 (the truth
sweep reaches every follow-state-derived node).

Both were routed here by the NL-143 gate (research/2026-08-08--nl143-gate.md
:167-183) as vocabulary-round input, and both are the same law failing one case
out: the M1c milestone's never-vanishes rule and NL-143's cross-mount sweep are
each correct except on one node they never covered.

  F-4  A write-refused follow rendered its reason and REPLACED the control with
       it. The refusal law was satisfied; the surface was dead until reload. A
       tap whose only outcome is an epitaph has vanished, just politely.
  F-6  The sweep walked .follow-slot nodes. Two nodes derived from follow state
       are not slots: a Following row's h2 chrome (stale name after a rename)
       and the tracked-ongoing marker (still claiming a follow after a remote
       unfollow). The mark lied until reload.

WHAT THIS FILE CAN AND CANNOT PROVE — carried verbatim in spirit from NL-143's
own note, because the constraint is unchanged: this repo runs no JS engine in
the suite (adding one is a new dependency and a machine-dependent suite), so the
client pins here are STRUCTURAL, read against COMMENT-STRIPPED source. The R4
lesson stands — a pin a comment can satisfy is not a pin. The behavioural proof
of both repros is the real-browser pass, which is QA's.

The SERVER halves — the identity attributes the sweep matches on, which are what
made these nodes unreachable in the first place — are proved for real, against
rendered markup.

Offline by construction: autouse sandbox (conftest), no network, no key, $0.
"""

from __future__ import annotations

import re

import pytest

from newslens import db, labels, memory, paths, server, webui

from test_nl143_follow_surface_truth import _fn, _js_code, _con
from test_ui_polish import slot, story, seed, TODAY


# ===========================================================================
# F-4 — THE REFUSED MOUNT KEEPS ITS CONTROL
# ===========================================================================

def test_f4_the_refusal_renders_its_reason_AND_remounts_the_control():
    """BORN RED — the defect itself, structurally.

    Pre-diff, flRenderRefusal set innerHTML to exactly two <p>s. The reason was
    there (the refusal law); the control was not (the never-vanishes law), and
    only a reload brought it back. Both halves are asserted together on purpose:
    a fix that dropped the reason to keep the control would be the opposite
    failure and must not pass this."""
    body = _js_code(_fn("flRenderRefusal"))
    assert "fl-refusal" in body and "fl-refusal-why" in body     # the reason
    assert "flRestingButton(slot)" in body                       # …and the control


def test_f4_the_remounted_control_is_the_ONE_resting_builder():
    """BORN RED. Two renderers now mount the resting CTA. A second hand-written
    copy is precisely how the four follow mounts drifted apart before NL-143, so
    the CTA lives in one builder and both callers ask it."""
    builder = _js_code(_fn("flRestingButton"))
    assert "followTap(this)" in builder
    assert "NL_LABELS.followInactive" in builder
    assert 'aria-expanded="false"' in builder
    resting = _js_code(_fn("flRenderResting"))
    assert "flRestingButton(slot)" in resting
    # …and no third copy of the button markup anywhere in the client
    assert _js_code(webui.JS).count("followTap(this)") == 1


def test_f4_a_re_tap_re_adjudicates_because_the_state_is_not_a_dead_end():
    """CARRIED-INVARIANT (born green) — measured, not assumed. The first draft
    called this BORN RED and the HEAD run corrected it: followTap never
    short-circuited on 'refused' pre-diff either. The defect was never the
    routing, it was that there was no control left to tap — which is what the
    two pins above measure. This one pins the OTHER half of the same property,
    and it is the half a plausible fix would break: mounting the control into a
    state followTap ignores would look identical in the markup and do nothing.

    The control has to be LIVE, not merely present: followTap
    short-circuits on committed/expanded, and 'refused' is neither — so a re-tap
    reaches flFollow and re-runs the act against the server. REFUSED
    (policy) re-adjudicates and honestly refuses again; DECLINED (transient)
    retries and succeeds. The client cannot tell those apart — which arm fired
    is the server's adjudication — so there is ONE behaviour here, and it is
    correct for both."""
    tap = _js_code(_fn("followTap"))
    assert "'committed'" in tap and "'expanded'" in tap
    assert "'refused'" not in tap             # never short-circuited
    assert "flFollow(slot)" in tap


def test_f4_the_act_level_refusal_still_leaves_the_state_line_alone():
    """CARRIED-INVARIANT (born green). The OTHER refusal class is untouched by
    this fix and must stay untouched: a refused act over a STANDING follow
    appends its reason and never unwinds the ● line. Pinned because "always
    remount the control" is one careless generalisation away from mounting a
    Follow button over a live follow."""
    body = _js_code(_fn("flActRefusal"))
    assert "fl-act-refusal" in body
    assert "flRestingButton" not in body
    assert "innerHTML" not in body            # appends, never replaces


# ===========================================================================
# F-6 — THE SWEEP REACHES EVERY DERIVED NODE
# ===========================================================================

def test_f6_the_tracked_marker_is_a_slot_the_sweep_can_reach(tmp_paths):
    """BORN RED — fix-loop surprise 4, at the server half that made it possible.

    The marker is DERIVED from follow state: it renders because the story
    matched a thread the reader follows. It was a bare <span> outside every
    .follow-slot, so the sweep walked past it and it kept announcing a follow
    that no longer existed. It is a slot now, carrying the identity keys the
    sweep matches on — and its identity is the MARKS, which are thread names."""
    con = _con()
    try:
        memory.add_thread(con, "Hormuz")
        tracked = slot(5, "Hormuz shipping watch")
        tracked["matched_memory"] = ["Hormuz"]
        st = story(5, "Tankers reroute around the strait", "quick")
        html = server._follow_control(st, tracked, ["Hormuz"], set(), TODAY,
                                      slug="story-5", con=con)
    finally:
        con.close()
    assert 'class="follow-slot"' in html
    assert 'data-state="tracked"' in html
    assert 'data-marks="Hormuz"' in html
    assert 'data-topic="Hormuz"' in html                    # the THREAD name
    assert 'data-story="Hormuz shipping watch"' in html     # the STORY, for rest
    # still no offer on a tracked story (the F-1 tooth, unchanged)
    assert "<button" not in html and labels.FOLLOW_THREAD_INACTIVE not in html
    assert labels.TRACKED_ONGOING_PREFIX in html


def test_f6_a_multi_mark_story_carries_every_thread_name(tmp_paths):
    """BORN RED. One story can match several tracked threads, and the sweep has
    to find this node from ANY of them — unfollowing the second thread must
    truthen the marker as surely as unfollowing the first."""
    con = _con()
    try:
        memory.add_thread(con, "Hormuz")
        memory.add_thread(con, "Tanker insurance")
        tracked = slot(5, "Shipping watch")
        tracked["matched_memory"] = ["Hormuz", "Tanker insurance"]
        st = story(5, "Tankers reroute", "quick")
        html = server._follow_control(st, tracked, ["Hormuz", "Tanker insurance"],
                                      set(), TODAY, slug="story-5", con=con)
    finally:
        con.close()
    assert 'data-marks="Hormuz\nTanker insurance"' in html
    assert "Hormuz, Tanker insurance" in html        # the visible text is unchanged


def test_f6_the_following_row_h2_is_stamped_as_derived_chrome(tmp_paths):
    """BORN RED — the gate's own finding: "sweep truthens the row's slot, not
    its h2 chrome, on cross-surface rename". The h2 carries the thread's NAME
    and its qualifier, both read off the follow, and it is not a slot. It now
    carries the same identity keys, of the same types, so ONE predicate serves
    both node families.

    STAMPED ON THE LOUD UPDATED ROWS ONLY, and that scope is deliberate: those
    are the rows a live settle-rename can land on while the reader is looking at
    them. The quiet-fold and lifecycle rows render a plain link (not a heading)
    and are re-rendered from the server on their next open, so stamping them
    would add nodes to the sweep that nothing can make stale."""
    html = server._spine_updated_row({
        "id": 7, "topic": "Volkswagen", "altitude": "entity",
        "disclosure": "Volkswagen (company)", "alt_label": "Volkswagen job cuts",
        "this_delta": {"date": "2026-08-08", "what_happened": "Three plants named."},
    })
    h2s = re.findall(r'<h2 class="thread-name"[^>]*>', html)
    assert h2s, html[:400]
    assert 'data-follow-name="row"' in h2s[0], h2s
    assert 'data-topic="Volkswagen"' in h2s[0], h2s
    assert 'data-story="Volkswagen"' in h2s[0], h2s
    # the row's single action is untouched — a rename renames, it never moves
    assert "openThread('7', event)" in html


def test_f6_one_walk_one_predicate_two_renderers():
    """BORN RED. The fix must not be a second sweep with a second matching rule
    — that is how the four mounts drifted apart in the first place. One walk,
    one flSameThread, and a renderer chosen by what the node IS."""
    body = _js_code(_fn("flSyncOthers"))
    assert body.count("querySelectorAll") == 1
    assert ".follow-slot, [data-follow-name]" in body
    assert body.count("flSameThread(keys, o)") == 1
    assert "data-follow-name" in body and "chromeFn" in body
    # …and the chrome renderer is reached from the commit lane, which is where
    # a settle-rename arrives.
    assert "flRenderChrome" in _js_code(_fn("flCommitAll"))


def test_f6_marks_join_the_identity_typed_never_crossed():
    """BORN RED, and it is the guard on the guard. NL-143's fix loop 1 removed a
    cross-TYPE crossing from this predicate (a story key meeting an origin key
    rendered one story's follow over another's card, with live acts behind it).
    Marks are THREAD NAMES — topic-typed — so they meet marks and they meet
    topics, and nothing else. Adding them must not re-open what that fix
    closed."""
    ident = _js_code(_fn("flIdentity"))
    assert "'marks'" in ident or "data-marks" in ident or "'marks'" in ident
    meet = _js_code(_fn("flMarksMeet"))
    assert "b.topic" in meet and "a.topic" in meet
    assert "story" not in meet and "origin" not in meet      # never crossed
    same = _js_code(_fn("flSameThread"))
    assert "flMarksMeet(keys, id)" in same
    # the cross-type pairs fix loop 1 removed are still absent
    assert "keys.story, id.origin" not in same
    assert "keys.origin, id.story" not in same


def test_f6_a_rested_marker_drops_its_marks():
    """BORN RED. A marker that rests is the remote-unfollow case: the thread it
    was matched against is gone. Keeping its marks would leave the node syncing
    on a thread the reader dropped — the same false claim one layer in from the
    one just removed."""
    resting = _js_code(_fn("flRenderResting"))
    assert "removeAttribute('data-marks')" in resting


def test_f6_the_chrome_renderer_rewrites_the_name_and_not_the_action():
    """BORN RED. A rename does not MOVE a thread, it renames one (the mutation
    law) — so the row's single action must survive untouched while its label
    becomes true. And the client's qualifier has to be the server's twin, or a
    reload changes what the row says."""
    body = _js_code(_fn("flRenderChrome"))
    assert "querySelector('a')" in body
    assert "a.innerHTML" in body
    assert "onclick" not in body and "openThread" not in body
    q = _js_code(_fn("flAltQualifier"))
    assert "alt-q" in q
    assert "narrow" not in q             # the buried arm is not in the twin


def test_f6_unfollow_does_not_invent_an_unfollowed_row_treatment():
    """CARRIED-INVARIANT with a stated ruling. flRestAll passes NO chromeFn, and
    that is a decision rather than an omission: an unfollowed thread's Following
    row still carries that thread's NAME, and the name is still true. The row is
    a stale LISTING, not a false statement, and the server re-renders it on the
    next Following open. Rewriting it here would be inventing a treatment nobody
    ruled on. The node that WAS lying — the marker — is a slot now, so it rests
    through the ordinary renderer."""
    rest = _js_code(_fn("flRestAll"))
    assert "flSyncOthers(slot, keys, flRenderResting)" in rest
    assert "flRenderChrome" not in rest
