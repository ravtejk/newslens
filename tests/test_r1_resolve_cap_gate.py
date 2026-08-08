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

RE-POINTED BY NL-17-M1c (2026-07-27). `/api/follow/resolve` is retired and split:
the TAP (`/api/follow/seed`) is $0, local and reaches no gate at all, and the
COVERAGE LOOKUP (`/api/follow/settle`) carries the gate, because it is the only
half that can spend. Every proof here still guards the same behaviour on the
half that now owns it. ONE assertion FLIPPED BY RULING, and it is renamed and
annotated in place rather than deleted: `test_refusal_commits_nothing` becomes
`test_refusal_costs_the_broadening_never_the_follow`. The money proof — a tight
cap attempts NO transport — is unchanged and still the point of the file.
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
    # NL-139 fix loop 1: the seed/settle handlers now also read the
    # UNCLAMPED name for origin_story + the model's input, so the
    # double has to carry both accessors.
    _raw_topic_arg = server.Handler._raw_topic_arg
    _with_memory = server.Handler._with_memory
    _ref_id_for = server.Handler._ref_id_for
    _commit_altitude = server.Handler._commit_altitude
    # NL-17-M1c: the cap gate MOVED with the route it guarded. /api/follow/
    # resolve is retired and split — the TAP (seed) is $0/local and reaches no
    # gate at all; the SETTLE carries the gate, because the settle is the only
    # half that can spend. Every proof below is re-pointed at the half that now
    # owns the behaviour it was written to guard.
    _api_follow_seed = server.Handler._api_follow_seed
    _api_follow_settle = server.Handler._api_follow_settle
    _seed_thread = server.Handler._seed_thread
    _settle_onto = server.Handler._settle_onto
    # NL-17 M1: the settle now APPENDS its outcome (0025). The logger is a
    # handler method, so the double carries it — copied, never stubbed, so
    # these proofs keep exercising the real append path.
    _log_settle = server.Handler._log_settle

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
    # NL-81: the stub must honour sync_memory's RETURN contract now — the
    # server's _with_memory reads the SyncResult to decide whether the file may
    # be rewritten (a stale file degrades: verb runs, file untouched). A clean
    # SyncResult() is "lawful, nothing to disclose", i.e. exactly the quiet
    # no-op these tests want.
    monkeypatch.setattr(memory, "sync_memory",
                        lambda con, **kw: memory.SyncResult())
    monkeypatch.setattr(memory, "write_memory_file", lambda con: None)


def _tripwire_resolver(calls):
    """A resolve_altitude stub that RECORDS every call. $0 — never reaches a
    provider. In the refusal tests an empty `calls` list is the proof that no
    spend was even attempted."""
    def _resolve(thread, **kwargs):
        calls.append(getattr(thread, "topic", None))
        return types.SimpleNamespace(**_ENTITY)
    return _resolve


def _tap(h, topic=STORY, origin=STORY):
    """One reader tap on the M1c lane: seed (instant, $0, local, ungated) then
    settle (the coverage lookup — the only half the cap gate guards). Mirrors
    flFollow -> flSettle in webui.JS."""
    body = {"topic": topic, "origin": origin}
    h._api_follow_seed(dict(body))
    seeded = h.sent[-1][0]
    if seeded.get("ok") is False or seeded.get("seeded") is not True:
        return
    h._api_follow_settle(dict(body, topic_current=seeded.get("topic")))


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
    _tap(h)

    payload, status = h.sent[-1]
    assert status == 409
    assert payload["ok"] is False
    assert payload["state"] == "refused"
    # M1c: the refusal carries its CLASS, and the class is R-COVERAGE — the
    # follow stands, only the broadening was refused. FOLLOW_CAP_REFUSAL is NOT
    # on the wire any more (Arm A retired it from reader copy); a `detail` for
    # diagnostics is, and nothing renders it.
    assert payload["refusal"] == "coverage"
    assert payload["follow_stands"] is True
    assert labels.FOLLOW_CAP_REFUSAL not in str(payload)
    # machine-parseable: both figures ride as numbers, not only prose
    assert payload["cap_usd"] == pytest.approx(float(TIGHT_CAP))
    assert payload["est_usd"] > payload["cap_usd"]
    # honest detail: says plainly that nothing was spent and the follow stands
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

    _tap(_FollowHandler())

    assert calls == []        # zero transport attempts


def test_refusal_costs_the_broadening_never_the_follow(monkeypatch):
    """RE-PINNED BY RULING (NL-17-M1c / v11): this test asserted the OPPOSITE at
    HEAD — `test_refusal_commits_nothing`, "a refusal never attempted anything,
    so it must leave the record untouched". That was true of the pre-commit
    world and is FALSE of the thread model, which rules that the tap commits a
    story-seeded thread INSTANTLY and only the coverage lookup can be refused.

    The invariant the old pin was protecting — a refusal must not spend, and
    must not wear copy implying a retryable transient — is kept verbatim below.
    What flips is WHAT SURVIVES the refusal: the reader's follow, which cost
    nothing to make. The content pass's §5.1 Arm A rides exactly this
    precondition, which is why FOLLOW_CAP_REFUSAL retires rather than renders."""
    monkeypatch.setenv("BUDGET_CAP_USD_PER_RUN", TIGHT_CAP)
    db.migrate(db_path=paths.DB_PATH)
    _quiet_memory(monkeypatch)
    monkeypatch.setattr(fa, "resolve_altitude", _tripwire_resolver([]))
    before = _active_count()

    h = _FollowHandler()
    _tap(h)

    assert _active_count() == before + 1       # THE FOLLOW STANDS
    con = db.connect(paths.DB_PATH)
    try:
        row = con.execute(
            "SELECT altitude, altitude_source FROM memory"
            " WHERE lower(topic) = lower(?)", (STORY,)).fetchone()
    finally:
        con.close()
    assert row["altitude"] == "narrow"         # story-scoped, exactly as seeded
    assert row["altitude_source"] == "seed"    # not 'degrade' — nothing degraded
    payload, _ = h.sent[-1]
    assert labels.FOLLOW_DEGRADE_LEAD not in str(payload)
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
    _tap(h)

    payload, status = h.sent[-1]
    assert status == 409
    assert payload["ok"] is False
    assert payload["refusal"] == "coverage"
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
    _tap(h)
    payload, _ = h.sent[-1]

    assert payload.get("ok") is False          # the branch the client tests
    assert payload["refusal"] == "coverage"    # …and the CLASS it routes on


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
    _tap(h)

    assert calls == [STORY]                   # the settle looked up exactly once
    payload, status = h.sent[-1]
    assert status == 200
    assert payload["state"] == "committed"
    assert payload["altitude"] == "entity"
    assert _active_count() == 1               # MOVED, never a second row


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
    _tap(h)

    payload, status = h.sent[0]
    assert status == 200
    assert payload["state"] == "committed"    # recognized, NOT cap-refused
    assert payload["seeded"] is False         # …so no settle fires at all
    assert calls == []                        # and still no paid lookup
