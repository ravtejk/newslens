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

# seat -> (observed per-call output-token ceiling, where it was read)
OBSERVED_CEILING = {
    "editor": (28772, "generation_log.jsonl editor_pass, n=7, 2026-07-18"),
    "script": (22707, "generation_log.jsonl script_adapt, n=7, 2026-07-23"),
    "rank":   (22748, "ranking_runs.meta.llm_attempts rank_select, n=6, 2026-07-25"),
    "writer": (36842, "generation_log.jsonl narrative_A, n=7, 2026-07-24"),
    # state has NO per-call record. 16,183 is an EDITION AVERAGE (worst
    # edition's derived total / that edition's rewrite count) — it is used here
    # only as a floor for the margin check, and it is explicitly NOT a ceiling:
    # an average cannot bound a single call, and on the subscription lane no
    # output cap does either (STATE_MAX_TOKENS is api-only, llm.py). That is
    # why state's wall is 600 like its siblings rather than held at 300.
    "state":  (16183, "EDITION AVERAGE, not a ceiling: total / rewrites, 2026-07-20"),
}

# The margin each wall must keep at the bottom of the band. 1.25x is not a
# round number for its own sake: it is roughly one standard slow day inside the
# measured band (71 vs the 88 mean is already a 1.24x swing), so a wall that
# cannot clear 1.25x at the floor has no room for the variation we have
# actually observed.
REQUIRED_MARGIN = 1.25


def seconds_at_floor(out_tokens: int) -> float:
    return out_tokens / THROUGHPUT_FLOOR_TOK_S + CLI_STARTUP_S


@pytest.mark.parametrize("seat", sorted(OBSERVED_CEILING))
def test_every_subscription_wall_clears_its_observed_ceiling(seat):
    tokens, provenance = OBSERVED_CEILING[seat]
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


def test_the_old_300s_wall_would_fail_that_check_for_three_seats():
    """The finding, executable: this is WHY the raise happened. editor, script
    and rank were all through a 300s wall at the bottom of the band — editor
    by 35%, and it has a real 300.02s timeout on the record to prove it."""
    breached = {
        seat: round(seconds_at_floor(tokens))
        for seat, (tokens, _) in OBSERVED_CEILING.items()
        if seconds_at_floor(tokens) > 300
    }
    assert breached == {"editor": 406, "script": 321, "rank": 321,
                        "writer": 520}, breached
    # ...and the writer was never in danger, because its wall was already 900.
    assert llm.SEATS["writer"].timeout_sub_s == 900


def test_the_raise_is_watchdog_only_and_touches_nothing_else():
    """The ruling's constraint, mechanically. A timeout re-tune that quietly
    moved a lane, a model, a price or an api-lane timeout would be a different
    diff wearing this one's clothes."""
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
    assert llm.SEATS["follow_altitude"].timeout_sub_s == 45


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
    assert seen["timeout"] == 600
