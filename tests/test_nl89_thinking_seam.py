"""The thinking seam: the subscription lane finally honors SeatConfig.thinking.

THE BUG. The api transport has always read `cfg.thinking` — it sends the
`thinking` param only when a seat declares one. The subscription transport
never did. So every seat declaring `thinking=None` — a declaration meaning "no
extended thinking", made deliberately in the seat table — was silently getting
extended thinking anyway, and paying for it in wall-clock. Measured on real
production prompts, n=16 (debates/2026-07-26--newslens--engineering.md §3):
84-97% of these seats' output tokens were deliberation, while the ANSWER came
out the same size in both arms. The state seat spent ~14,000 output tokens to
write five sentences.

WHAT THIS FILE PINS, in the order the risk runs:
  1. The mechanism — MAX_THINKING_TOKENS=0 reaches the child, for armed seats.
  2. The SCOPE — that it reaches ONLY armed seats. The allowlist is a quality
     decision per seat, so a leak into an unarmed seat is not a harmless
     speed-up, it is an unauthorised flip. rank in particular is excluded by a
     ruling (n>=10 validity measurement outstanding) and must stay excluded.
  3. INJECTION, not pass-through — the principal's shell cannot set or clear
     it. This is the property that keeps product behaviour reproducible.
  4. The --bare pin — the flag that would break the $0-RUN LAW.
  5. The regression armor — token bands that fire if a CLI upgrade ever stops
     honoring the mechanism, warn-grade, on the subscription lane only.
"""

from __future__ import annotations

import dataclasses

import pytest

from newslens import llm

# ENG-M0 RE-PIN (2026-08-06). The roster INVERTED. state/script/editor left the
# allowlist in the same diff that flipped them Haiku -> Opus 4.8 with
# thinking="adaptive": suppressing deliberation on a seat promoted to do
# editorial and memory JUDGMENT would cancel the flip while still paying Opus
# prices. The allowlist is now exactly ONE seat — follow_altitude, the
# principal's flagged exception (an 8s reader-facing UI wall, measured
# thinking-off 1.85-2.89s vs thinking-on 9.38-46.1s).
ARMED = ("follow_altitude",)
UNARMED = ("rank", "writer", "analyst", "synthesis", "state", "script", "editor")


def _child_env(seat, parent=None):
    """The env a `claude -p` child would get for this seat."""
    return llm._subscription_env(dict(parent or {}), llm.SEATS[seat])


# ===========================================================================
# 1-2. The mechanism, and its scope
# ===========================================================================

@pytest.mark.parametrize("seat", ARMED)
def test_an_armed_seat_suppresses_thinking_in_the_child_env(seat):
    assert _child_env(seat).get("MAX_THINKING_TOKENS") == "0"


@pytest.mark.parametrize("seat", UNARMED)
def test_an_unarmed_seat_gets_no_thinking_variable_at_all(seat):
    """Absent, not "0" and not "": an unarmed seat's child must be
    byte-indistinguishable from today's, or this diff silently flipped a seat
    nobody authorised."""
    assert "MAX_THINKING_TOKENS" not in _child_env(seat)


def test_rank_is_excluded_by_name_and_by_behaviour():
    """Rook's gate, adopted by the principal: rank's off-arm first-attempt
    VALIDITY is unmeasured at usable n, a format miss costs a whole extra call,
    and a second miss kills the generate. It stays taxed until n>=10 per arm."""
    assert "rank" not in llm._THINKING_OFF_SUB_SEATS
    assert "MAX_THINKING_TOKENS" not in _child_env("rank")
    assert llm.SEATS["rank"].timeout_sub_s == 600      # the taxed wall stays with it


def test_no_pipeline_seat_is_armed_any_more():
    """This file owns the PIPELINE half of the allowlist: the three seats the
    principal authorised are in, and no other seat that runs inside `generate`
    is. The full roster (which also carries the resolver) is pinned by NL-99's
    own file — keeping the two assertions separate is what lets the two diffs
    land, and revert, independently."""
    # ENG-M0: the authorised set is now EMPTY on the pipeline side. Every seat
    # that runs inside `generate` declares its own thinking and the transport
    # obeys it — which is what the seam was built to make possible, and what
    # Ada's dissent (eng-4 §5.5) asked for.
    assert {"state", "script", "editor"} & llm._THINKING_OFF_SUB_SEATS == set()
    pipeline_seats = {"rank", "writer", "analyst", "editor", "script", "state"}
    assert pipeline_seats & llm._THINKING_OFF_SUB_SEATS == set()


def test_the_writer_and_analyst_declare_thinking_and_keep_it():
    """These two ASKED for deliberation. The seam makes the transport obey the
    seat rows — obeying them means leaving these alone."""
    for seat in ("writer", "analyst"):
        assert llm.SEATS[seat].thinking == "adaptive", seat
        assert "MAX_THINKING_TOKENS" not in _child_env(seat), seat


@pytest.mark.parametrize("seat", ARMED + UNARMED)
def test_the_seat_rows_themselves_are_untouched(seat):
    """ENG-M0 RE-PIN. This used to assert the 07-26 flip changed the TRANSPORT
    and not the declarations. ENG-M0 changes the DECLARATIONS: every live
    pipeline seat now asks for adaptive thinking, and the only seats still
    declaring None are the untouched resolver and the dormant gpt-4o seat."""
    cfg = llm.SEATS[seat]
    expected = None if seat in ("synthesis", "follow_altitude") else "adaptive"
    assert cfg.thinking == expected


# ===========================================================================
# 3. Injection, not pass-through
# ===========================================================================

def test_the_variable_is_injected_not_inherited_for_an_armed_seat():
    """A parent value must not survive into the child — not a larger one, not
    a zero, not anything. The code sets it; the environment does not."""
    # ENG-M0: "editor" left the allowlist, so the armed seat this asserts on is
    # now follow_altitude — the mechanism is unchanged, only its one subject is.
    child = _child_env("follow_altitude", {"MAX_THINKING_TOKENS": "99999"})
    assert child["MAX_THINKING_TOKENS"] == "0"


def test_a_parent_value_cannot_reach_an_unarmed_seat_either():
    """The other half, and the more dangerous one: if pass-through worked, the
    principal's shell could flip rank — the one seat a ruling excluded."""
    child = _child_env("rank", {"MAX_THINKING_TOKENS": "99999"})
    assert "MAX_THINKING_TOKENS" not in child


def test_the_variable_is_deliberately_absent_from_the_allowlist():
    """The structural reason the two tests above hold. If this name were ever
    added to _SUBSCRIPTION_ENV_ALLOW, pass-through would silently return."""
    assert "MAX_THINKING_TOKENS" not in llm._SUBSCRIPTION_ENV_ALLOW


def test_the_api_key_is_still_absent_from_every_child_env():
    """CARRIED INVARIANT (red-at-base: exercises the new symbol/signature) — the $0-RUN LAW's hard constraint. The
    seam added a key to this dict; it must not have loosened the pop."""
    for seat in ARMED + UNARMED:
        child = _child_env(seat, {"ANTHROPIC_API_KEY": "sk-should-never-travel"})
        assert "ANTHROPIC_API_KEY" not in child, seat


# ===========================================================================
# 4. The --bare pin ($0-RUN LAW)
# ===========================================================================

def test_bare_never_enters_the_subscription_argv(monkeypatch, tmp_path):
    """`--bare`'s own help text says it never reads OAuth/keychain — so it
    would force the METERED lane while the ledger recorded $0. It must never
    appear on any argv this transport builds."""
    fake_bin = tmp_path / "claude"
    fake_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_bin.chmod(0o755)
    monkeypatch.setattr(llm, "resolve_claude_bin", lambda env=None: (str(fake_bin), "test"))
    seen = {}

    def rec(args, **kw):
        seen["argv"] = list(args)
        raise RuntimeError("stop after argv capture")

    monkeypatch.setattr(llm.subprocess, "run", rec)
    for seat in ARMED + ("rank",):
        seen.clear()
        with pytest.raises(Exception):
            llm.chat(llm.LaneRequest(
                cfg=llm.SEATS[seat], prompt="p", temperature=0, max_tokens=10,
                json_mode=True, user_agent="ua", api_key=""))
        assert "--bare" not in seen["argv"], seat
    # And it is nowhere in the base flags either — belt and braces, because the
    # base flags are where a well-meaning "hermetic" edit would put it.
    assert "--bare" not in llm._SUBSCRIPTION_BASE_FLAGS


# ===========================================================================
# 5. The regression armor
# ===========================================================================

@pytest.mark.parametrize("seat,band", [("editor", 10000), ("script", 10000),
                                       ("state", 4000)])
def test_the_token_bands_are_the_sizes_the_round_ruled(seat, band):
    assert llm._TOKEN_BANDS[seat] == band


@pytest.mark.parametrize("seat,band", [("editor", 10000), ("script", 10000),
                                       ("state", 4000)])
def test_a_band_trip_warns_and_names_the_cause(capsys, seat, band):
    """Warn-grade and diagnostic: the point is that a human reading the run
    output learns WHY, not just that a number was large."""
    llm.cost_fields(llm.SEATS[seat], {"prompt_tokens": 10,
                                      "completion_tokens": band + 1})
    err = capsys.readouterr().err
    assert "token-band alarm" in err
    assert seat in err
    assert "MAX_THINKING_TOKENS" in err


@pytest.mark.parametrize("seat", ("editor", "script", "state"))
def test_a_normal_call_is_silent(capsys, seat):
    """The alarm has to be quiet in the ordinary case or it will be ignored in
    the extraordinary one. Sized off the measured off-arm output."""
    llm.cost_fields(llm.SEATS[seat], {"prompt_tokens": 10,
                                      "completion_tokens": llm._TOKEN_BANDS[seat]})
    assert capsys.readouterr().err == ""


def test_the_taxed_arms_output_would_trip_every_band():
    """The bands are only armor if the thing they guard against blows them.
    These are the measured taxed-arm outputs (eng-6 §3.1).

    ENG-M0 RE-PIN — the MULTIPLE loosened, deliberately, and this is the honest
    reason. The bands were re-sized upward (6,000/4,000/1,200 ->
    10,000/10,000/4,000 — the shipped dict; the parametrize above is the
    source of truth) because the seats they guard now run Opus with adaptive
    thinking and legitimately emit ~1.4x their thinking-off volume. The taxed
    arms still blow every band — which is the property this test exists to
    assert — but editor's 22,384 is no longer 3x its 10,000 band, so demanding
    3x here would be asserting a margin the re-size knowingly spent. The
    detection claim is `>`; the 3x was never the contract."""
    for seat, taxed in (("editor", 22384), ("script", 15929), ("state", 13465)):
        assert taxed > llm._TOKEN_BANDS[seat], seat


def test_the_alarm_is_subscription_only(capsys):
    """On the api lane cfg.thinking has always been honored, so a large output
    there means something else entirely — and this alarm would be naming the
    wrong cause.

    NB the band assertion is load-bearing, not decoration: without it this test
    passes on any tree that has no alarm at all, which would make it a
    vacuous-green and not a real pin."""
    assert llm._TOKEN_BANDS["editor"] < 99999      # the alarm exists and WOULD fire
    api_editor = dataclasses.replace(llm.SEATS["editor"], lane="api")
    llm.cost_fields(api_editor, {"prompt_tokens": 10, "completion_tokens": 99999})
    assert capsys.readouterr().err == ""
    # ...and the same numbers on the subscription lane DO fire, which is what
    # makes the silence above meaningful rather than accidental.
    llm.cost_fields(llm.SEATS["editor"], {"prompt_tokens": 10,
                                          "completion_tokens": 99999})
    assert "token-band alarm" in capsys.readouterr().err


def test_an_unbanded_seat_never_alarms(capsys):
    """CARRIED INVARIANT (red-at-base: exercises the new symbol/signature) — the writer legitimately emits ~37,000
    output tokens. Banding it would produce a warning on every good edition."""
    llm.cost_fields(llm.SEATS["writer"], {"prompt_tokens": 10,
                                          "completion_tokens": 40000})
    assert capsys.readouterr().err == ""
    assert "writer" not in llm._TOKEN_BANDS


def test_the_alarm_never_changes_the_ledger(capsys):
    """It is observability, not a gate. The returned cost fields must be
    identical whether or not the band tripped."""
    quiet = llm.cost_fields(llm.SEATS["state"], {"prompt_tokens": 100,
                                                 "completion_tokens": 100})
    loud = llm.cost_fields(llm.SEATS["state"], {"prompt_tokens": 100,
                                                "completion_tokens": 99999})
    # The "loud" call must actually have been loud, or this proves nothing.
    assert "token-band alarm" in capsys.readouterr().err
    assert quiet["usd_charged"] == loud["usd_charged"] == 0.0
    assert set(quiet) == set(loud)
