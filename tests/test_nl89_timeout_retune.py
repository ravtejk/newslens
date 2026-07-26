"""The 2026-07-26 subscription-timeout re-tune (principal ruling: per-seat
subscription timeouts may be RAISED; watchdog-only).

Why this file exists separately from the timeout maps already pinned in
test_field_batch_20260717*.py: those pin the NUMBERS. This one pins the
REASONING — that each shipped wall still clears its own observed production
ceiling at the bottom of the measured throughput band. If a future seat starts
emitting more, or someone trims a wall back, the arithmetic breaks here with
the numbers in the failure message rather than in a comment nobody re-ran.

EVIDENCE, all of it recorded, none of it invented:
  * Throughput band 71-106 tok/s, mean 88, n=11 large-output subscription calls
    — workspace/debates/2026-07-26--newslens--engineering.md §3.3. A third
    independent datapoint (battery manifest, Opus writer) sits at 78.9 tok/s.
  * Per-CALL production output ceilings, read read-only from the principal's
    own record: editor_pass 28,772 (n=7) and script_adapt 22,707 (n=7) from
    data/generation_log.jsonl; rank_select 22,748 (n=6) from ranking_runs
    .meta.llm_attempts. These are per-call ledger rows, NOT per-edition sums —
    the eng-6 §4.2 table's script figure (40,828) is script_adapt + script_retry
    and must not be read as one call.
  * The editor seat took a REAL timeout at 300.02s in the thinking-tax probe.

WHAT IS NOT CHANGED HERE, deliberately: retry counts, thinking configuration,
lanes, models, prices, api-lane timeouts. A timeout is an airbag.
"""

from __future__ import annotations

import pytest

from newslens import llm

# eng-6 §3.3. The FLOOR is what a wall has to survive; the mean is not a
# guarantee about any individual call.
THROUGHPUT_FLOOR_TOK_S = 71
CLI_STARTUP_S = 1.0

# TWO ERAS, and every seat below belongs to exactly one of them. The taxed
# ceilings are what production emitted while the subscription lane was silently
# adding extended thinking; they are the right yardstick ONLY for a seat still
# in that state. For a flipped seat they are archaeology — the seat no longer
# emits anything like that, and holding its wall against a number it can no
# longer produce is how a 600s blindfold survives on a 45s path.

# TAXED seats — thinking still on. Per-call ceilings read read-only from the
# principal's own record.
TAXED_CEILING = {
    "rank":   (22748, "ranking_runs.meta.llm_attempts rank_select, n=6, 2026-07-25"),
    # writer DECLARES adaptive thinking: its deliberation was ordered, so it is
    # not "taxed" in the defect sense — it just belongs to the same yardstick.
    "writer": (36842, "generation_log.jsonl narrative_A, n=7, 2026-07-24"),
}

# FLIPPED seats — thinking suppressed 2026-07-26 (llm._THINKING_OFF_SUB_SEATS).
# Ceilings are the MEASURED off-arm observations, eng-6 §3.1 (n=2 per seat, on
# real production prompts), expressed in SECONDS because that is how they were
# observed — no token conversion needed or wanted.
FLIPPED_CEILING_S = {
    "editor": (54.5, "eng-6 §3.1 off-arm: 45.4s / 54.5s"),
    "script": (28.5, "eng-6 §3.1 off-arm: 28.5s / 18.6s"),
    "state":  (6.0,  "eng-6 §3.1 off-arm: 5.7s / 6.0s"),
}

# The margin each wall must keep at the bottom of the band. 1.25x is not a
# round number for its own sake: it is roughly one standard slow day inside the
# measured band (71 vs the 88 mean is already a 1.24x swing), so a wall that
# cannot clear 1.25x at the floor has no room for the variation we have
# actually observed.
REQUIRED_MARGIN = 1.25


def seconds_at_floor(out_tokens: int) -> float:
    return out_tokens / THROUGHPUT_FLOOR_TOK_S + CLI_STARTUP_S


@pytest.mark.parametrize("seat", sorted(TAXED_CEILING))
def test_a_taxed_seats_wall_clears_its_observed_token_ceiling(seat):
    tokens, provenance = TAXED_CEILING[seat]
    cfg = llm.SEATS[seat]
    wall = cfg.timeout_sub_s or cfg.timeout_s
    needed = seconds_at_floor(tokens)
    margin = wall / needed
    assert margin >= REQUIRED_MARGIN, (
        f"{seat}: wall {wall}s vs {needed:.0f}s needed for its observed "
        f"{tokens:,}-token ceiling at {THROUGHPUT_FLOOR_TOK_S} tok/s "
        f"= {margin:.2f}x margin (need {REQUIRED_MARGIN}x). Evidence: "
        f"{provenance}"
    )


# A flipped seat's wall is judged against its measured off-arm LATENCY, and the
# bar is higher (3x, not 1.25x) precisely because these walls came down: a tight
# wall on a fast path is cheap to get wrong, so it must be provably generous.
FLIPPED_REQUIRED_MARGIN = 3.0


@pytest.mark.parametrize("seat", sorted(FLIPPED_CEILING_S))
def test_a_flipped_seats_wall_clears_its_measured_off_arm_latency(seat):
    ceiling_s, provenance = FLIPPED_CEILING_S[seat]
    cfg = llm.SEATS[seat]
    wall = cfg.timeout_sub_s or cfg.timeout_s
    margin = wall / ceiling_s
    assert margin >= FLIPPED_REQUIRED_MARGIN, (
        f"{seat}: wall {wall}s vs a measured {ceiling_s}s off-arm ceiling "
        f"= {margin:.2f}x (need {FLIPPED_REQUIRED_MARGIN}x). Evidence: "
        f"{provenance}"
    )


@pytest.mark.parametrize("seat", sorted(FLIPPED_CEILING_S))
def test_a_flipped_seats_wall_outlasts_its_own_token_band(seat):
    """The property that makes the alarm readable. If a CLI upgrade stops
    honoring MAX_THINKING_TOKENS, the seat's output jumps back into the taxed
    range — and the token-band alarm is supposed to SAY so. A wall shorter than
    the band's own runtime would kill that call first and report a timeout
    instead of the cause."""
    band = llm._TOKEN_BANDS[seat]
    wall = llm.SEATS[seat].timeout_sub_s
    needed = seconds_at_floor(band)
    assert wall > needed, (
        f"{seat}: wall {wall}s does not outlast its own >{band:,}-token band "
        f"({needed:.0f}s at {THROUGHPUT_FLOOR_TOK_S} tok/s) — a band trip "
        f"would surface as a timeout, hiding the cause it exists to name"
    )


def test_the_old_300s_wall_would_have_failed_for_the_taxed_seats():
    """The finding that produced the raise, kept executable. Every seat's TAXED
    output needed more than 300s at the bottom of the band — which is why the
    stopgap went to 600 before the flip made most of it unnecessary."""
    breached = {
        seat: round(seconds_at_floor(tokens))
        for seat, (tokens, _) in TAXED_CEILING.items()
        if seconds_at_floor(tokens) > 300
    }
    assert breached == {"rank": 321, "writer": 520}, breached
    # rank still lives there: it is excluded from the flip, so it keeps 600.
    assert llm.SEATS["rank"].timeout_sub_s == 600
    assert "rank" not in llm._THINKING_OFF_SUB_SEATS
    # ...and the writer was never in danger, because its wall was already 900.
    assert llm.SEATS["writer"].timeout_sub_s == 900


def test_the_retune_touches_nothing_but_watchdogs_and_the_thinking_env():
    """The constraint, mechanically. A timeout re-tune that quietly moved a
    lane, a model, a price or an api-lane timeout would be a different diff
    wearing this one's clothes. NOTE what is deliberately NOT asserted here any
    more: `thinking is None` on the seat rows is unchanged and still true — the
    flip did not touch the seat rows at all, it made the TRANSPORT obey them."""
    for seat in ("rank", "editor", "script", "state"):
        cfg = llm.SEATS[seat]
        assert cfg.lane == "subscription", seat
        assert cfg.model == "claude-haiku-4-5", seat
        assert cfg.thinking is None and cfg.effort is None, seat
    # api-lane timeouts UNCHANGED — no api pinned path moves with this diff.
    assert llm.SEATS["rank"].timeout_s == 90
    assert llm.SEATS["editor"].timeout_s == 120
    assert llm.SEATS["script"].timeout_s == 120
    assert llm.SEATS["state"].timeout_s == 60
    # The standing ratifications stand.
    assert llm.SEATS["writer"].timeout_sub_s == 900
    assert llm.SEATS["analyst"].timeout_sub_s == 540
    # The interactive seat is NOT a batch seat and must never drift into this
    # family — a reader waits on it (fix loop 1 FIX-3).
    assert llm.SEATS["follow_altitude"].timeout_s == 8
    assert llm.SEATS["follow_altitude"].timeout_sub_s == 20


def test_a_raised_wall_cannot_slow_a_healthy_call(monkeypatch):
    """The trade is one-sided by construction: the value reaches exactly one
    place — subprocess.run(..., timeout=) — so a healthy call returns when it
    returns. What doubles is only how long a HUNG call takes to detect."""
    seen = {}
    real_run = llm.subprocess.run

    def rec(args, **kw):
        seen["timeout"] = kw.get("timeout")
        return real_run(args, **kw)

    monkeypatch.setattr(llm.subprocess, "run", rec)
    llm.chat(llm.LaneRequest(
        cfg=llm.resolve_seat("editor"), prompt="p", temperature=0,
        max_tokens=10, json_mode=True, user_agent="ua", api_key="k"))
    assert seen["timeout"] == 180   # editor, post-flip
