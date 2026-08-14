"""NL-99: the follow-altitude resolver comes home to the subscription lane.

THE STORY THIS FILE PINS, because the seat has now been moved twice and the
second move is only defensible if the first one's reasoning is on the record:

  2026-07-20 — the feature was 100% broken in production. A 12s subscription
  wall against a real ~48s `claude -p` resolve produced 4/4 source=degrade and
  ZERO source=auto commits, ever. The api lane did the same call in ~1.2s, so
  the seat was ruled onto api and became the one exception to the
  all-subscription default. That was a correct read of the SYMPTOM.

  2026-07-25 (eng-4 §6, n=13 + control) — the lane was never slow.
  `_subscription_provider` ignored `SeatConfig.thinking`, so every resolve paid
  for extended thinking it had explicitly declined. As-shipped 9.38-46.1s
  (p50 23.7s); with MAX_THINKING_TOKENS=0, same seat, same lane: 1.85-2.89s
  (p50 2.03s), $0 charged.

  2026-07-26 — THE $0-RUN LAW: nothing metered runs for what the subscription
  covers, and cents/month is still metered. The seat flips back, and it is
  armed in the thinking allowlist so the flip does not re-import the tax it was
  fleeing. That pairing is the whole safety argument: WITHOUT the seam fix this
  flip would restore the 100%-degrade bug.

WHAT IS DELIBERATELY NOT HERE: the accuracy falsifier. Thinking-off has never
faced Kass's pre-registered >1-in-5 primary-entity-miss gate — 24/24 was
measured with thinking ON. That is a LIVE run the principal reads, not a unit
test, and it is this milestone's ship condition.
"""

from __future__ import annotations

import pytest

from conftest import sandbox_bin_env
from newslens import llm


def _child_env(seat, parent=None):
    return llm._subscription_env(dict(parent or {}), llm.SEATS[seat])


# ===========================================================================
# The seat row
# ===========================================================================

def test_the_resolver_defaults_to_the_subscription_lane():
    cfg = llm.SEATS["follow_altitude"]
    assert cfg.lane == "subscription"
    assert cfg.provider == "anthropic"
    # NL-17 M3 fix loop 1 (F-2): Haiku 4.5 -> Sonnet 5. The no-Haiku law, and
    # QA's finding that this seat is the arm chain's FIRST LINK — the Follow tap
    # that lights entity steering resolves through here.
    assert cfg.model == "claude-sonnet-5"
    assert "haiku" not in cfg.model
    # sampling=False came WITH the model: Sonnet 5 rejects `temperature` with a
    # 400 and resolve_altitude sends RESOLVER_TEMPERATURE on every call. The
    # subscription lane never forwarded it, so only the api fall-over would have
    # broken — the lane you reach when things are already going wrong.
    assert cfg.sampling is False
    # UNCHANGED by either flip, and this is the NL-99 invariant: thinking stays
    # off. A model swap does not license re-importing the 9-46s tax.
    assert cfg.thinking is None and cfg.effort is None


def test_the_flip_is_paired_with_the_seam_fix_not_shipped_bare():
    """The load-bearing pin. Flipping this seat to subscription WITHOUT
    suppressing thinking would re-create the exact 9-46s wall that made the
    feature degrade 4/4 in production. The two changes are one decision."""
    assert llm.SEATS["follow_altitude"].lane == "subscription"
    assert "follow_altitude" in llm._THINKING_OFF_SUB_SEATS
    assert _child_env("follow_altitude")["MAX_THINKING_TOKENS"] == "0"


def test_the_full_thinking_allowlist_roster():
    """DIFF 2 owns the exact set (DIFF 1 owns the pipeline subset), so the two
    can land and revert independently without either pin lying."""
    # ENG-M0 RE-PIN: the three pipeline seats left in the seat-flip diff
    # (they declare adaptive thinking now). The resolver is the whole roster.
    assert llm._THINKING_OFF_SUB_SEATS == frozenset({"follow_altitude"})


def test_the_timeouts_the_dispatch_ruled():
    cfg = llm.SEATS["follow_altitude"]
    # RESOLVED: the gate (2026-07-26) ruled eng-4 C2's 20 — against the measured
    # 1.85-2.89s thinking-off resolve, 20 = ~6.9x headroom, inside the family
    # sizing rule (>=3x off-arm ceiling) alongside editor 3.3x / script 4.2x /
    # state 10x. The dispatch's 45 was conservatism, not evidence; a reader
    # waits out this wall before the proven degrade, and 20 halves that pin.
    assert cfg.timeout_sub_s == 20
    # The api escape hatch keeps its tight interactive wall.
    assert cfg.timeout_s == 8


def test_the_resolver_is_still_not_a_generate_step():
    """CARRIED INVARIANT (born-green): its output is a REPORT, never edition
    state or a selection weight. A silent default in seat_for_step would bill
    the Opus writer."""
    with pytest.raises(ValueError):
        llm.seat_for_step("follow_altitude")


# ===========================================================================
# The escape hatch — inverted again, and now out of law
# ===========================================================================

def test_the_api_lane_is_reachable_only_by_explicit_sanction():
    """The mechanism is unchanged and the MEANING inverts: =api now selects the
    METERED lane. It must still work — the principal may sanction it per
    instance — but nothing may reach it by accident."""
    cfg, reason = llm.effective_seat(
        "follow_altitude", {"NEWSLENS_LANE_FOLLOW_ALTITUDE": "api"})
    assert cfg.lane == "api"
    assert reason is None          # an explicit choice, not a fallback


def test_nothing_falls_from_subscription_to_the_metered_lane_on_its_own():
    """The $0-RUN LAW's teeth on this seat. With no override the default holds,
    and the api fallback stays UNARMED (07-17 pure-fail-loud).

    NL-156: the world is a HEALTHY install — CLI present, no lane overrides —
    and it now says so. `{}` used to mean "no overrides, and let check_lane find
    a binary in os.environ"; with that seam closed, `{}` means a machine with no
    CLI at all, which fails the lane for a reason this pin is not about."""
    env = sandbox_bin_env()
    cfg, reason = llm.effective_seat("follow_altitude", env)
    assert cfg.lane == "subscription" and reason is None
    assert llm.fallback_armed(env) is False


def test_an_api_forced_resolve_gets_no_thinking_suppression_because_it_needs_none():
    """The api transport has always honored cfg.thinking. Injecting the var
    there would be cargo-culting — and would muddy what the alarm means."""
    cfg, _ = llm.effective_seat(
        "follow_altitude", {"NEWSLENS_LANE_FOLLOW_ALTITUDE": "api"})
    assert cfg.lane == "api"
    assert cfg.thinking is None    # honored natively by the api provider


# ===========================================================================
# The regression armor for this seat
# ===========================================================================

def test_the_resolver_token_band_is_the_measured_size():
    """eng-4 §5.4: an honest resolver answer is 51-61 tokens — a three-field
    JSON object. 200 is ~3.3x that and ~1/70th of the taxed arm."""
    assert llm._TOKEN_BANDS["follow_altitude"] == 200


def test_an_honest_resolve_is_silent(capsys):
    """61 tokens is the top of the measured honest range. The band assertion
    keeps this from being a vacuous pass on a tree with no alarm."""
    assert llm._TOKEN_BANDS["follow_altitude"] > 61
    llm.cost_fields(llm.SEATS["follow_altitude"],
                    {"prompt_tokens": 900, "completion_tokens": 61})
    assert capsys.readouterr().err == ""


def test_a_taxed_resolve_trips_the_band_and_names_the_cause(capsys):
    """If a `claude` release ever stops honoring MAX_THINKING_TOKENS, the
    resolver goes back to ~14,000 output tokens and 9-46s — and the reader just
    experiences a slow follow. This is what turns that into a sentence."""
    llm.cost_fields(llm.SEATS["follow_altitude"],
                    {"prompt_tokens": 900, "completion_tokens": 14000})
    err = capsys.readouterr().err
    assert "token-band alarm" in err
    assert "follow_altitude" in err
    assert "MAX_THINKING_TOKENS" in err


def test_the_band_does_not_fire_on_the_sanctioned_api_path(capsys):
    """On the api lane thinking was always honored, so a large output there
    means something else — the alarm would be naming the wrong cause."""
    assert llm._TOKEN_BANDS["follow_altitude"] < 14000   # it WOULD fire on sub
    cfg, _ = llm.effective_seat(
        "follow_altitude", {"NEWSLENS_LANE_FOLLOW_ALTITUDE": "api"})
    assert cfg.lane == "api"
    llm.cost_fields(cfg, {"prompt_tokens": 900, "completion_tokens": 14000})
    assert capsys.readouterr().err == ""


# ===========================================================================
# The $0-RUN LAW pins
# ===========================================================================

def test_bare_never_enters_the_resolver_argv(monkeypatch, tmp_path):
    """`--bare`'s own help text says it never reads OAuth/keychain — on this
    seat, now that subscription IS the default, that would silently force the
    metered lane while the ledger recorded $0. The single most expensive flag
    in the tree."""
    fake_bin = tmp_path / "claude"
    fake_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_bin.chmod(0o755)
    monkeypatch.setattr(llm, "resolve_claude_bin",
                        lambda env=None: (str(fake_bin), "test"))
    seen = {}

    def rec(args, **kw):
        seen["argv"] = list(args)
        raise RuntimeError("stop after argv capture")

    monkeypatch.setattr(llm.subprocess, "run", rec)
    with pytest.raises(Exception):
        llm.chat(llm.LaneRequest(
            cfg=llm.SEATS["follow_altitude"], prompt="p", temperature=0,
            max_tokens=10, json_mode=True, user_agent="ua", api_key=""))
    assert "--bare" not in seen["argv"]


def test_the_api_key_never_reaches_the_resolver_child():
    """CARRIED INVARIANT (red-at-base: exercises the new symbol/signature), and it matters more on this seat than
    any other: this is the seat that used to spend that key. A leak would let
    the child bill it while the ledger recorded $0 charged."""
    child = _child_env("follow_altitude",
                       {"ANTHROPIC_API_KEY": "sk-should-never-travel"})
    assert "ANTHROPIC_API_KEY" not in child


def test_a_default_resolve_charges_nothing_and_still_ledgers_shadow():
    """The money shape of the whole flip, in one assertion: $0 charged, full
    shadow. The cap binds on shadow (Onna's law), so the guard did not weaken."""
    fields = llm.cost_fields(llm.SEATS["follow_altitude"],
                             {"prompt_tokens": 900, "completion_tokens": 61})
    assert fields["usd_charged"] == 0.0
    assert fields["usd_shadow"] > 0.0
