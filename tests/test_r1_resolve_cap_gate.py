"""R1 — the interactive resolve's budget cap gate (PREFLIGHT gate order, 2026-07-25).

THE HOLE THIS GUARDS: `POST /api/follow/resolve` reached the PAID follow-altitude
resolver with no budget check of any kind. `follow_altitude.main`'s cumulative cap
gate only ever covered the batch falsifier CLI, so a UI tap billed the api lane
regardless of BUDGET_CAP_USD_PER_RUN. Found by the PREFLIGHT accuracy pass
(§1 "known gap"), ordered fixed by the gate before the NL-33 package ships.

BORN RED against HEAD (a5033a5) — measured 6 failed / 2 passed by running this
file against a pristine `git archive HEAD` tree with PYTHONPATH pinned to it
(newslens.__file__ proven to resolve inside that tree, not the working copy —
the editable-install hijack class). FIVE of the six fail by ASSERTION, i.e.
they demonstrate the hole behaviourally rather than merely missing a symbol:
  test_refuses_when_one_resolve_exceeds_the_cap   assert 200 == 409
  test_refusal_makes_no_resolver_call             assert ['Volkswagen job cuts'] == []
      ^ THE money proof: at HEAD a tight cap did NOT stop the paid resolve.
  test_refusal_commits_nothing                    assert 1 == 0
  test_malformed_cap_is_a_disclosed_refusal       assert 200 == 409
      ^ at HEAD even a `nan` cap passed straight through to the paid call.
  test_refusal_payload_lands_in_the_client_resting_branch   assert True is False
  test_gate_arithmetic_matches_the_falsifier      AttributeError (symbol absent)
The two CARRIED-INVARIANT (born-green) tests are labelled as such inline: they
pin behaviour that must NOT change (the under-cap pass-through, and the free
already-followed short-circuit staying AHEAD of the gate). Calling them born-red
would inflate the proof class — they are regression pins, and both would have
passed at HEAD.

Offline by construction (conftest sandbox — no network, no real key, per-test DB).
The resolver is stubbed in every test, so a gate REGRESSION shows up as a stub
call that should not have happened, never as real spend.
"""

from __future__ import annotations

import types

import pytest

from newslens import (config, db, follow_altitude as fa, labels, memory, paths,
                      server)


# ---------------------------------------------------------------------------
# harness — the established _FollowHandler double (test_nl17_m1b_fixloop1.py):
# borrow Handler's verb helpers, capture _send_json instead of writing a socket.
# ---------------------------------------------------------------------------

class _FollowHandler:
    _topic_arg = server.Handler._topic_arg
    _with_memory = server.Handler._with_memory
    _ref_id_for = server.Handler._ref_id_for
    _commit_altitude = server.Handler._commit_altitude
    _api_follow_resolve = server.Handler._api_follow_resolve

    def __init__(self):
        self.sent = []

    def _send_json(self, obj, status=200):
        self.sent.append((obj, status))
        return obj


_ENTITY = dict(confidence="high", altitude="entity", primary_entity="Volkswagen",
               disclosure="Volkswagen (company)", alt_label="Volkswagen job cuts")

# A cap far below one resolve's estimate (~$0.0032 at the Haiku seat table), and
# one far above it. Both are computed against the SAME estimator the gate uses,
# so this file cannot drift if the seat's prices move.
TIGHT_CAP = "0.0005"
ROOMY_CAP = "1.50"

STORY = "Volkswagen job cuts"


def _quiet_memory(monkeypatch):
    monkeypatch.setattr(memory, "sync_memory", lambda con: None)
    monkeypatch.setattr(memory, "write_memory_file", lambda con: None)


def _tripwire_resolver(calls):
    """A resolve_altitude stub that RECORDS every call. $0 — never reaches a
    provider. In the refusal tests an empty `calls` list is the proof that no
    spend was even attempted."""
    def _resolve(thread, **kwargs):
        calls.append(getattr(thread, "topic", None))
        return types.SimpleNamespace(**_ENTITY)
    return _resolve


def _active_count():
    con = db.connect(paths.DB_PATH)
    try:
        return con.execute(
            "SELECT COUNT(*) AS n FROM memory WHERE status = 'active'"
        ).fetchone()["n"]
    finally:
        con.close()


# ===========================================================================
# REFUSAL PATH — born red at HEAD
# ===========================================================================

def test_refuses_when_one_resolve_exceeds_the_cap(monkeypatch):
    """Cap below one resolve's estimate -> the route REFUSES, disclosed.
    409 (the _api_generate staleness-refusal precedent: a well-formed request
    declined on policy), ok=False, the cap-refusal label, and a machine-parseable
    detail carrying both figures. BORN-RED at HEAD: no gate exists, so the stub
    resolves and the handler answers 200/committed."""
    monkeypatch.setenv("BUDGET_CAP_USD_PER_RUN", TIGHT_CAP)
    db.migrate(db_path=paths.DB_PATH)
    _quiet_memory(monkeypatch)
    monkeypatch.setattr(fa, "resolve_altitude", _tripwire_resolver([]))

    h = _FollowHandler()
    h._api_follow_resolve({"topic": STORY, "origin": STORY})

    assert len(h.sent) == 1
    payload, status = h.sent[0]
    assert status == 409
    assert payload["ok"] is False
    assert payload["state"] == "refused"
    assert payload["error"] == labels.FOLLOW_CAP_REFUSAL
    # machine-parseable: both figures ride as numbers, not only prose
    assert payload["cap_usd"] == pytest.approx(float(TIGHT_CAP))
    assert payload["est_usd"] > payload["cap_usd"]
    # honest copy: says plainly that nothing was spent and nothing was followed
    assert "no call" in payload["detail"]
    assert "BUDGET_CAP_USD_PER_RUN" in payload["detail"]


def test_refusal_makes_no_resolver_call(monkeypatch):
    """THE MONEY PROOF: on refusal the resolver is never entered — the gate sits
    BEFORE any transport, so there is no attempted spend to bill. BORN-RED at
    HEAD (the resolver is called once)."""
    monkeypatch.setenv("BUDGET_CAP_USD_PER_RUN", TIGHT_CAP)
    db.migrate(db_path=paths.DB_PATH)
    _quiet_memory(monkeypatch)
    calls = []
    monkeypatch.setattr(fa, "resolve_altitude", _tripwire_resolver(calls))

    _FollowHandler()._api_follow_resolve({"topic": STORY, "origin": STORY})

    assert calls == []        # zero transport attempts


def test_refusal_commits_nothing(monkeypatch):
    """A cap refusal is NOT the degrade path: the degrade commits this-story to
    preserve the reader's act, but a refusal never attempted anything, so it must
    leave the record untouched — and must not wear FOLLOW_DEGRADE copy, which
    would imply a retryable transient. BORN-RED at HEAD."""
    monkeypatch.setenv("BUDGET_CAP_USD_PER_RUN", TIGHT_CAP)
    db.migrate(db_path=paths.DB_PATH)
    _quiet_memory(monkeypatch)
    monkeypatch.setattr(fa, "resolve_altitude", _tripwire_resolver([]))
    before = _active_count()

    h = _FollowHandler()
    h._api_follow_resolve({"topic": STORY, "origin": STORY})

    assert _active_count() == before          # nothing followed
    payload, _ = h.sent[0]
    assert payload["error"] != labels.FOLLOW_DEGRADE_LEAD
    assert labels.FOLLOW_DEGRADE_UPGRADE not in str(payload)


def test_malformed_cap_is_a_disclosed_refusal(monkeypatch):
    """A non-finite / non-positive cap raises from the single validator
    (config.budget_cap_usd_per_run). The route must surface that as a disclosed
    409 — never a 500 stack, and never a silent pass to the paid call (the BUG-1
    class: a cap that cannot stop spending). BORN-RED at HEAD."""
    monkeypatch.setenv("BUDGET_CAP_USD_PER_RUN", "nan")
    db.migrate(db_path=paths.DB_PATH)
    _quiet_memory(monkeypatch)
    calls = []
    monkeypatch.setattr(fa, "resolve_altitude", _tripwire_resolver(calls))

    h = _FollowHandler()
    h._api_follow_resolve({"topic": STORY, "origin": STORY})

    payload, status = h.sent[0]
    assert status == 409
    assert payload["ok"] is False
    assert payload["error"] == labels.FOLLOW_CAP_REFUSAL
    assert calls == []                        # no spend on a config error


def test_refusal_payload_lands_in_the_client_resting_branch(monkeypatch):
    """CLIENT CONTRACT PIN. webui's follow-tap callback branches
    `if (!d || d.ok === false) return flRenderResting(slot);` (webui.py:842),
    so a refusal must carry `ok: False` at the TOP level or the card would hang
    on "Resolving…" forever. This pins the shape that makes the existing branch
    fire.

    KNOWN GAP, deliberately not fixed here: that branch reverts the card to
    resting and renders NO reason, so FOLLOW_CAP_REFUSAL is returned but never
    shown. The reader sees a tap that quietly did nothing. This is the same
    already-silent bucket the route's pre-existing `topic required` 400 lands
    in — a new REASON in an old hole, not a new hole — but it is a real
    no-silent-no-op miss (the FOLLOW_SWITCH_FAILED concern, FIX LOOP 2 R2) and
    needs a content/design call on copy + placement, not an implementer's
    unilateral UI edit. Flagged to the gate."""
    monkeypatch.setenv("BUDGET_CAP_USD_PER_RUN", TIGHT_CAP)
    db.migrate(db_path=paths.DB_PATH)
    _quiet_memory(monkeypatch)
    monkeypatch.setattr(fa, "resolve_altitude", _tripwire_resolver([]))

    h = _FollowHandler()
    h._api_follow_resolve({"topic": STORY, "origin": STORY})
    payload, _ = h.sent[0]

    assert payload.get("ok") is False          # the branch the client tests
    # and the reason IS on the wire, ready for whoever renders it
    assert payload["error"] == labels.FOLLOW_CAP_REFUSAL


def test_gate_arithmetic_matches_the_falsifier(monkeypatch):
    """ONE arithmetic, two callers: resolve_cost_gate must return exactly the
    falsifier's _estimate_usd for the same thread at the same seat, and exactly
    config.budget_cap_usd_per_run for the cap. A second copy of this math is how
    BUG-1 shipped in two places. BORN-RED at HEAD (no such function)."""
    env = {"BUDGET_CAP_USD_PER_RUN": ROOMY_CAP}
    allowed, est, cap = fa.resolve_cost_gate(STORY, env)

    from newslens import llm
    cfg = llm.resolve_seat(fa.SEAT, env)
    expected = fa._estimate_usd(
        cfg, fa._system_law(), fa.ThreadInput(thread_id=None, topic=STORY))

    assert est == expected
    assert cap == config.budget_cap_usd_per_run(env)
    assert allowed is True


# ===========================================================================
# CARRIED INVARIANTS (born-GREEN — these passed at HEAD and must keep passing)
# ===========================================================================

def test_under_cap_resolves_exactly_as_before(monkeypatch):
    """CARRIED INVARIANT (born-green). The gate must be invisible in the normal
    case: under a roomy cap the resolver runs once and the HIGH-confidence
    auto-commit lands, byte-for-byte the prior behaviour."""
    monkeypatch.setenv("BUDGET_CAP_USD_PER_RUN", ROOMY_CAP)
    db.migrate(db_path=paths.DB_PATH)
    _quiet_memory(monkeypatch)
    calls = []
    monkeypatch.setattr(fa, "resolve_altitude", _tripwire_resolver(calls))

    h = _FollowHandler()
    h._api_follow_resolve({"topic": STORY, "origin": STORY})

    assert calls == [STORY]                   # resolved exactly once
    payload, status = h.sent[0]
    assert status == 200
    assert payload["state"] == "committed"
    assert payload["altitude"] == "entity"
    assert _active_count() == 1


def test_already_followed_shortcircuit_still_precedes_the_gate(monkeypatch):
    """CARRIED INVARIANT (born-green), and an ORDERING pin the gate could break:
    the already-followed guard is FREE (a read-only lookup) and returns without
    spending. It must stay AHEAD of the cap gate — otherwise a tight cap would
    start refusing taps that were never going to cost anything. Under an
    impossible cap this must STILL answer committed, not refused."""
    monkeypatch.setenv("BUDGET_CAP_USD_PER_RUN", TIGHT_CAP)
    db.migrate(db_path=paths.DB_PATH)
    _quiet_memory(monkeypatch)
    con = db.connect(paths.DB_PATH)
    try:
        memory.add_thread_at_altitude(
            con, "Volkswagen", altitude="entity", primary_entity="Volkswagen",
            disclosure="Volkswagen (company)", alt_label=STORY,
            confidence="high", source="auto", origin_story=STORY)
    finally:
        con.close()
    calls = []
    monkeypatch.setattr(fa, "resolve_altitude", _tripwire_resolver(calls))

    h = _FollowHandler()
    h._api_follow_resolve({"topic": STORY, "origin": STORY})

    payload, status = h.sent[0]
    assert status == 200
    assert payload["state"] == "committed"    # recognized, NOT cap-refused
    assert calls == []                        # and still no paid resolve
